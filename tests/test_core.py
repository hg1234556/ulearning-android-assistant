"""Offline tests: subprocess is mocked; no emulator or account is contacted."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ulearning_assistant.core import (
    AdbClient, AdbError, UiNode, discover_adb,
    export_json, export_markdown, parse_ui_xml, select_unique,
)


SINGLE = b"List of devices attached\nemulator-5554 device product:phone model:LDPhone device:phone\n"
XML = b'''<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0">
<node text="Course" content-desc="Open" resource-id="cn.ulearning.yxy:id/course"
class="android.widget.TextView" package="cn.ulearning.yxy" bounds="[10,20][210,80]"
clickable="true" enabled="true" password="false" />
<node text="secret" bounds="[0,0][100,20]" password="true">
<node text="nested-secret" bounds="[0,0][1,1]" /></node>
</hierarchy>'''


def completed(output=b"", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=output, stderr=b"sensitive stderr")


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.discovery = patch("ulearning_assistant.core.adb.discover_adb", return_value="C:/adb.exe")
        self.discovery.start()
        self.addCleanup(self.discovery.stop)
        self.runner = patch("ulearning_assistant.core.adb.subprocess.run")
        self.run = self.runner.start()
        self.addCleanup(self.runner.stop)

    def test_devices_and_subprocess_settings(self):
        self.run.return_value = completed(SINGLE)
        device = AdbClient().select_device()
        self.assertEqual((device.serial, device.model), ("emulator-5554", "LDPhone"))
        options = self.run.call_args.kwargs
        self.assertEqual(options["args"], ["C:/adb.exe", "devices", "-l"])
        self.assertIs(options["shell"], False)
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        if os.name == "nt":
            self.assertEqual(options["creationflags"], subprocess.CREATE_NO_WINDOW)

    def test_multiple_devices_require_selection(self):
        self.run.return_value = completed(SINGLE + b"emulator-5556 device model:Second\n")
        with self.assertRaisesRegex(AdbError, "多台"):
            AdbClient().tap(1, 2)
        self.assertEqual(self.run.call_count, 1)

    def test_explicit_device_among_multiple(self):
        self.run.side_effect = [completed(SINGLE + b"emulator-5556 device\n"), completed()]
        AdbClient(serial="emulator-5556").tap(10, 20)
        self.assertEqual(self.run.call_args.kwargs["args"],
                         ["C:/adb.exe", "-s", "emulator-5556", "shell", "input", "tap", "10", "20"])

    def test_auto_selection_remains_bound_after_disconnect(self):
        self.run.side_effect = [completed(SINGLE), completed(b"List of devices attached\nemulator-5556 device\n")]
        client = AdbClient()
        client.select_device()
        with self.assertRaisesRegex(AdbError, "已断开"):
            client.tap(10, 10)
        self.assertEqual(client.serial, "emulator-5554")
        self.assertEqual(self.run.call_count, 2)

    def test_bad_states_are_not_operated(self):
        for state, message in (("offline", "离线"), ("unauthorized", "未授权"), ("recovery", "未就绪")):
            with self.subTest(state=state):
                self.run.return_value = completed(f"List of devices attached\nemulator-5554 {state}\n".encode())
                with self.assertRaisesRegex(AdbError, message):
                    AdbClient().keyevent("back")

    def test_no_device_is_actionable(self):
        self.run.return_value = completed(b"List of devices attached\n")
        with self.assertRaisesRegex(AdbError, "未检测到"):
            AdbClient().select_device()

    def test_duplicate_devices_rejected(self):
        self.run.return_value = completed(SINGLE + b"emulator-5554 device\n")
        with self.assertRaisesRegex(AdbError, "重复"):
            AdbClient().select_device()

    def test_shell_injection_rejected_before_subprocess(self):
        for value in ("-s", "emulator-5554;id", "127.0.0.1:5555\n", "$(id)"):
            with self.subTest(serial=value), self.assertRaises(AdbError):
                AdbClient(serial=value)
        client = AdbClient()
        for value in ("cn.test;id", "--user", "cn.test\nwhoami", "cn.test/Activity"):
            with self.subTest(package=value), self.assertRaises(AdbError):
                client.launch_app(value)
        for value in ("back;id", "KEYCODE_ENTER", 4, None):
            with self.subTest(key=value), self.assertRaises(AdbError):
                client.keyevent(value)
        for x, y in ((-1, 2), (True, 2), ("1;id", 2), (1, 1.5), (32768, 1)):
            with self.subTest(coordinate=(x, y)), self.assertRaises(AdbError):
                client.tap(x, y)
        for duration in (0, -1, "400;id", True, 60001):
            with self.subTest(duration=duration), self.assertRaises(AdbError):
                client.swipe(0, 0, 1, 1, duration)
        self.run.assert_not_called()

    def test_timeout_and_error_do_not_expose_output(self):
        self.run.side_effect = subprocess.TimeoutExpired(["adb"], 1, output=b"sensitive password")
        with self.assertRaisesRegex(AdbError, "超时") as error:
            AdbClient().version()
        self.assertNotIn("sensitive", str(error.exception))
        self.run.side_effect = None
        self.run.return_value = completed(b"sensitive password", returncode=1)
        with self.assertRaises(AdbError) as error:
            AdbClient().version()
        self.assertNotIn("sensitive", str(error.exception))
        self.run.side_effect = FileNotFoundError("private file path")
        with self.assertRaisesRegex(AdbError, "无法启动"):
            AdbClient().version()

    def test_timeout_validation(self):
        for timeout in (0, -1, True, "20", float("nan"), float("inf"), 601):
            with self.subTest(timeout=timeout), self.assertRaises(AdbError):
                AdbClient(timeout=timeout)

    def test_ui_dump_unique_path_cleanup_and_password_filter(self):
        self.run.side_effect = [completed(SINGLE), completed(), completed(SINGLE), completed(XML),
                                completed(SINGLE), completed()]
        nodes = AdbClient().dump_ui()
        self.assertEqual([node.text for node in nodes], ["Course"])
        dump_args = self.run.call_args_list[1].kwargs["args"]
        cat_args = self.run.call_args_list[3].kwargs["args"]
        cleanup_args = self.run.call_args_list[5].kwargs["args"]
        remote = dump_args[-1]
        self.assertRegex(remote, r"^/data/local/tmp/ulearning-ui-[a-f0-9]{32}\.xml$")
        self.assertEqual(cat_args[-1], remote)
        self.assertEqual(cleanup_args[-3:], ["rm", "-f", remote])

    def test_ui_dump_cleans_up_after_parse_error(self):
        self.run.side_effect = [completed(SINGLE), completed(), completed(SINGLE), completed(b"bad XML"),
                                completed(SINGLE), completed()]
        with self.assertRaises(AdbError):
            AdbClient().dump_ui()
        self.assertEqual(self.run.call_count, 6)
        self.assertIn("rm", self.run.call_args.kwargs["args"])

    def test_screenshot_binary_and_bad_output(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\0" * 24
        self.run.side_effect = [completed(SINGLE), completed(png)]
        self.assertEqual(AdbClient().screenshot(), png)
        self.run.side_effect = [completed(SINGLE), completed(b"not a PNG")]
        with self.assertRaisesRegex(AdbError, "PNG"):
            AdbClient().screenshot()

    def test_info_only_returns_expected_properties(self):
        properties = (b"[ro.product.model]: [LDPhone]\n[ro.product.manufacturer]: [LD]\n"
                      b"[ro.build.version.release]: [9]\n[ro.build.version.sdk]: [28]\n"
                      b"[private.token]: [do not return]\n")
        self.run.side_effect = [completed(SINGLE), completed(SINGLE), completed(properties),
                                completed(SINGLE), completed(b"Physical size: 1080x1920\nOverride size: 720x1280\n")]
        info = AdbClient().device_info()
        self.assertEqual((info["width"], info["height"]), (720, 1280))
        self.assertEqual(info["android_version"], "9")
        self.assertNotIn("private", repr(info))

    def test_app_info_checks_exact_package(self):
        self.run.side_effect = [completed(SINGLE), completed(b"package:cn.ulearning.yxy.extra\n")]
        self.assertIs(AdbClient().app_info()["installed"], False)
        self.run.side_effect = [completed(SINGLE), completed(b"package:cn.ulearning.yxy\n"),
                                completed(SINGLE), completed(b"  versionName=2.0\n versionCode=20 minSdk=21\n codePath=/data/app/base\n")]
        result = AdbClient().app_info()
        self.assertEqual((result["version_name"], result["version_code"]), ("2.0", "20"))


class UiTests(unittest.TestCase):
    def test_parse_password_filter_and_center(self):
        nodes = parse_ui_xml(XML)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].center, (110, 50))
        self.assertTrue(nodes[0].clickable)

    def test_reject_malformed_large_and_declared_xml(self):
        for value in (b"", b"x" * (4 * 1024 * 1024 + 1), b"<node/>",
                      b'<!DOCTYPE hierarchy [<!ENTITY x "expand">]><hierarchy/>', b"<hierarchy>",
                      '<!DOCTYPE hierarchy><hierarchy/>'.encode("utf-16")):
            with self.subTest(size=len(value)), self.assertRaises(AdbError):
                parse_ui_xml(value)

    def test_extreme_bounds_ignored(self):
        xml = '<hierarchy><node text="bad" bounds="[' + '9' * 5000 + ',0][1,1]"/></hierarchy>'
        self.assertEqual(parse_ui_xml(xml), [])

    def test_unique_exact_and_intersection(self):
        nodes = [UiNode(text="Next", resource_id="a", enabled=True),
                 UiNode(text="Next", resource_id="b", enabled=True),
                 UiNode(text="Next page", resource_id="c")]
        with self.assertRaisesRegex(AdbError, "2 个"):
            select_unique(nodes, text="Next")
        self.assertEqual(select_unique(nodes, text="Next", resource_id="b"), nodes[1])
        for selectors in ({}, {"text": ""}, {"text": "Missing"}, {"text": "Ne"}):
            with self.subTest(selectors=selectors), self.assertRaises(AdbError):
                select_unique(nodes, **selectors)

    def test_disabled_and_password_nodes_never_selected_or_exported(self):
        nodes = [UiNode(text="secret", password=True), UiNode(text="Next", enabled=False)]
        with self.assertRaises(AdbError):
            select_unique(nodes, text="secret")
        with self.assertRaises(AdbError):
            select_unique(nodes, text="Next")
        self.assertNotIn("secret", export_json(nodes))
        self.assertNotIn("secret", export_markdown(nodes))
        self.assertEqual(len(json.loads(export_json(nodes))), 1)

    def test_markdown_escapes_and_deduplicates(self):
        node = UiNode(text="A|B\nC", description="标注")
        output = export_markdown([node, node])
        self.assertEqual(output.count("A\\|B<br>C"), 1)
        self.assertIn("标注", output)


class DiscoveryTests(unittest.TestCase):
    def test_explicit_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "adb.exe")
            path.write_bytes(b"test")
            if os.name != "nt":
                path.chmod(0o700)
            with patch.dict(os.environ, {"ULEARNING_ADB": "invalid"}):
                self.assertEqual(discover_adb(path), str(path.resolve()))
            with patch.dict(os.environ, {"ULEARNING_ADB": str(path)}):
                self.assertEqual(discover_adb(), str(path.resolve()))
            with self.assertRaises(AdbError):
                discover_adb(Path(directory, "missing.exe"))


if __name__ == "__main__":
    unittest.main()

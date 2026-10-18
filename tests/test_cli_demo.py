import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from ulearning_assistant.cli import build_parser, main
from ulearning_assistant.demo import DemoClient
from ulearning_assistant.workflows import Inspector, PageCollector, Profile


class DemoIntegrationTests(unittest.TestCase):
    def test_options_work_before_and_after_command(self):
        for argv in (["--demo", "devices"], ["devices", "--demo"]):
            self.assertTrue(build_parser().parse_args(argv).demo)
        self.assertEqual(build_parser().parse_args(["--serial", "emulator-5554", "inspect"]).serial,
                         "emulator-5554")

    def test_demo_devices_never_require_adb(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["devices", "--demo"]), 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["demo"])
        self.assertEqual(payload["devices"][0]["serial"], "demo-local")

    def test_demo_export_creates_both_artifacts(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["export", "--demo", "--output", directory]), 0)
            result = json.loads(output.getvalue())
            self.assertTrue(Path(result["markdown"]).exists())
            self.assertTrue(Path(result["json"]).exists())
            self.assertIn("虚构", Path(result["markdown"]).read_text(encoding="utf-8"))

    def test_demo_has_realistic_multi_page_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            result = PageCollector(Inspector(DemoClient(), Profile.load())).collect(
                directory, max_pages=10, interval=0.2, max_duration=10)
            self.assertEqual(result.count, 3)
            self.assertEqual(len(set(p.text_hash for p in result.pages)), 3)

    def test_default_actions_work_on_demo(self):
        client = DemoClient()
        inspector = Inspector(client, Profile.load())
        for action in ("play", "speed_menu", "speed_1_5"):
            inspector.apply(inspector.preview(action))
        self.assertTrue(client.playing)
        self.assertEqual(client.speed, "1.5倍")

    def test_collect_reports_configuration_failure_with_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile.json"
            profile.write_text('{"actions": {}}', encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["--demo", "--profile", str(profile), "collect", "--output", directory])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue())["stop_reason"], "unknown_action")


if __name__ == "__main__":
    unittest.main()

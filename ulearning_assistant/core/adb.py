"""Small, dependency-free ADB client for an explicitly selected emulator."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from .ui import AdbError, UiNode, parse_ui_xml


DEFAULT_PACKAGE = "cn.ulearning.yxy"
_SERIAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}\Z")
_PACKAGE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\Z")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    model: str = ""


def _checked_file(value: str | os.PathLike[str], source: str) -> str:
    try:
        raw = os.fspath(value)
        if not isinstance(raw, str) or not raw or any(char in raw for char in "\x00\r\n"):
            raise ValueError
        candidate = Path(os.path.expandvars(raw)).expanduser().resolve()
        if not candidate.is_file():
            raise ValueError
        if os.name != "nt" and not os.access(candidate, os.X_OK):
            raise ValueError
        return str(candidate)
    except (OSError, ValueError, TypeError) as exc:
        raise AdbError(f"{source}指定的 ADB 文件不存在或无法执行，请选择 adb.exe。") from exc


def discover_adb(adb_path: str | os.PathLike[str] | None = None) -> str:
    """Find ADB: explicit path, ULEARNING_ADB, PATH, then LDPlayer paths."""
    if adb_path is not None:
        return _checked_file(adb_path, "手动")
    environment_path = os.environ.get("ULEARNING_ADB")
    if environment_path:
        return _checked_file(environment_path, "ULEARNING_ADB 环境变量")
    found = shutil.which("adb")
    if found:
        return _checked_file(found, "PATH 中")
    for candidate in (
        r"C:\leidian\LDPlayer14\adb.exe",
        r"C:\leidian\LDPlayer9\adb.exe",
        r"C:\leidian\LDPlayer64\adb.exe",
        r"C:\leidian\LDPlayer\adb.exe",
        r"C:\Program Files\leidian\LDPlayer9\adb.exe",
        r"D:\leidian\LDPlayer14\adb.exe",
        r"D:\leidian\LDPlayer9\adb.exe",
        r"D:\leidian\LDPlayer64\adb.exe",
    ):
        if Path(candidate).is_file():
            return _checked_file(candidate, "雷电目录中")
    raise AdbError("未找到 ADB。请在设置中选择雷电安装目录里的 adb.exe，或设置 ULEARNING_ADB。")


def _validate_serial(serial: str) -> str:
    if not isinstance(serial, str) or not _SERIAL.fullmatch(serial):
        raise AdbError("设备序列号无效，请从设备列表中选择。")
    return serial


def _validate_package(package: str) -> str:
    if not isinstance(package, str) or len(package) > 255 or not _PACKAGE.fullmatch(package):
        raise AdbError("App 包名无效，请使用类似 cn.ulearning.yxy 的 Android 包名。")
    return package


def _coordinate(value: int) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 32767:
        raise AdbError("点击和滑动坐标必须是 0 到 32767 之间的整数。")
    return str(value)


class AdbClient:
    """Each operation checks device state; an automatic selection stays bound."""

    def __init__(self, adb_path: str | os.PathLike[str] | None = None,
                 serial: str | None = None, timeout: float = 20):
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 600):
            raise AdbError("ADB 超时必须是 0 到 600 秒之间的有效数字。")
        if serial is not None:
            _validate_serial(serial)
        self.adb_path = discover_adb(adb_path)
        self.serial = serial
        self.timeout = float(timeout)

    def _run(self, arguments: list[str]) -> bytes:
        options = dict(
            args=[self.adb_path, *arguments],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=self.timeout, shell=False,
        )
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            completed = subprocess.run(**options)
        except subprocess.TimeoutExpired as exc:
            raise AdbError("ADB 操作超时，请确认模拟器已启动且页面可以响应。") from exc
        except OSError as exc:
            raise AdbError("无法启动 ADB，请检查 adb.exe 路径和文件权限。") from exc
        if completed.returncode != 0:
            raise AdbError(f"ADB 操作失败（退出码 {completed.returncode}），请检查模拟器连接后重试。")
        return completed.stdout

    @staticmethod
    def _text(data: bytes) -> str:
        return data.decode("utf-8", errors="replace").strip()

    def version(self) -> str:
        return self._text(self._run(["version"]))

    def devices(self) -> list[Device]:
        output = self._text(self._run(["devices", "-l"]))
        result: list[Device] = []
        seen: set[str] = set()
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith(("List of devices", "*", "adb server")):
                continue
            fields = line.split()
            if len(fields) < 2:
                continue
            serial = _validate_serial(fields[0])
            if serial in seen:
                raise AdbError("ADB 返回重复设备序列号，请重启模拟器的 ADB 后重试。")
            seen.add(serial)
            model = next((field[6:] for field in fields[2:] if field.startswith("model:")), "")
            state = "no permissions" if fields[1:3] == ["no", "permissions"] else fields[1]
            result.append(Device(serial, state, model))
        return result

    def select_device(self) -> Device:
        candidates = self.devices()
        if self.serial is not None:
            _validate_serial(self.serial)
            candidates = [device for device in candidates if device.serial == self.serial]
            if not candidates:
                raise AdbError("所选设备已断开，请重新连接或从设备列表中选择。")
        elif len(candidates) > 1:
            raise AdbError("检测到多台设备，请先明确选择雷电模拟器的序列号。")
        if not candidates:
            raise AdbError("未检测到设备，请启动雷电并开启其本地 ADB 调试。")
        device = candidates[0]
        if device.state == "unauthorized":
            raise AdbError("设备未授权，请在模拟器中允许 USB 调试后重试。")
        if device.state == "offline":
            raise AdbError("设备处于离线状态，请重新连接模拟器后重试。")
        if device.state != "device":
            raise AdbError("所选设备尚未就绪，请检查模拟器的 ADB 调试设置。")
        self.serial = device.serial
        return device

    def _device_run(self, arguments: list[str]) -> bytes:
        device = self.select_device()
        return self._run(["-s", device.serial, *arguments])

    def device_info(self) -> dict:
        device = self.select_device()
        output = self._text(self._device_run(["shell", "getprop"]))
        properties = dict(re.findall(r"^\[([^\]]+)\]: \[(.*?)\]$", output, re.MULTILINE))
        sizes = self._text(self._device_run(["shell", "wm", "size"]))
        matches = re.findall(r"(?:Physical|Override) size:\s*(\d+)x(\d+)", sizes)
        width, height = (tuple(int(value) for value in matches[-1]) if matches else (None, None))
        return {
            "serial": device.serial, "state": device.state,
            "model": properties.get("ro.product.model", device.model),
            "manufacturer": properties.get("ro.product.manufacturer", ""),
            "brand": properties.get("ro.product.brand", ""),
            "android_version": properties.get("ro.build.version.release", ""),
            "sdk": properties.get("ro.build.version.sdk", ""),
            "width": width, "height": height,
            "screen_size": f"{width}x{height}" if width is not None else "",
        }

    def app_info(self, package: str = DEFAULT_PACKAGE) -> dict:
        package = _validate_package(package)
        packages = self._text(self._device_run(["shell", "pm", "list", "packages", package]))
        installed = f"package:{package}" in packages.splitlines()
        result = {"package": package, "installed": installed,
                  "version_name": "", "version_code": "", "path": ""}
        if not installed:
            return result
        details = self._text(self._device_run(["shell", "dumpsys", "package", package]))
        for key, pattern in (
            ("version_name", r"^\s*versionName=([^\r\n]+)"),
            ("version_code", r"\bversionCode=(\d+)"),
            ("path", r"^\s*codePath=([^\r\n]+)"),
        ):
            match = re.search(pattern, details, re.MULTILINE)
            if match:
                result[key] = match.group(1).strip()
        return result

    def dump_ui(self) -> list[UiNode]:
        remote_path = f"/data/local/tmp/ulearning-ui-{uuid.uuid4().hex}.xml"
        try:
            self._device_run(["shell", "uiautomator", "dump", remote_path])
            snapshot = self._device_run(["exec-out", "cat", remote_path])
            return parse_ui_xml(snapshot)
        finally:
            try:
                self._device_run(["shell", "rm", "-f", remote_path])
            except AdbError:
                pass

    def screenshot(self) -> bytes:
        data = self._device_run(["exec-out", "screencap", "-p"])
        if not data.startswith(_PNG_SIGNATURE) or len(data) < 24:
            raise AdbError("截图未返回有效 PNG，请确认模拟器画面正常后重试。")
        return data

    def tap(self, x: int, y: int) -> None:
        x_value, y_value = _coordinate(x), _coordinate(y)
        self._device_run(["shell", "input", "tap", x_value, y_value])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 400) -> None:
        coordinates = [_coordinate(value) for value in (x1, y1, x2, y2)]
        if (isinstance(duration_ms, bool) or not isinstance(duration_ms, int)
                or not 1 <= duration_ms <= 60000):
            raise AdbError("滑动时长必须是 1 到 60000 毫秒之间的整数。")
        self._device_run(["shell", "input", "swipe", *coordinates, str(duration_ms)])

    def launch_app(self, package: str = DEFAULT_PACKAGE) -> None:
        package = _validate_package(package)
        output = self._text(self._device_run([
            "shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1",
        ]))
        if "No activities found" in output or "monkey aborted" in output.lower():
            raise AdbError("未找到 App 的启动页面，请确认已在模拟器中安装优学院。")

    def keyevent(self, key: str) -> None:
        mapping = {"back": "4", "home": "3"}
        if not isinstance(key, str) or key not in mapping:
            raise AdbError("仅支持返回 back 或主页 home 按键。")
        self._device_run(["shell", "input", "keyevent", mapping[key]])

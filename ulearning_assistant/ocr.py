"""Read only the caller's PNG with Windows' locally installed OCR engine.

This module never captures a screen, operates a device, or sends image data to a
service. The PowerShell bridge reads a temporary PNG and returns JSON on stdout.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile


MAX_PNG_BYTES = 20 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class OcrError(RuntimeError):
    """An OCR failure that can safely be displayed without image contents."""


@dataclass(frozen=True)
class OcrWord:
    text: str
    bounds: tuple[float, float, float, float]


@dataclass(frozen=True)
class OcrLine:
    text: str
    words: tuple[OcrWord, ...]


@dataclass(frozen=True)
class OcrResult:
    width: int
    height: int
    language: str
    lines: tuple[OcrLine, ...]

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


_BRIDGE_ERRORS = {
    "OCR_UNAVAILABLE": "Windows 未找到可用的本地 OCR 语言，请检查系统已安装的 OCR 语言。",
    "IMAGE_TOO_LARGE": "图片尺寸超过 Windows OCR 引擎上限，请使用较小的 PNG。",
    "IMAGE_READ_FAILED": "Windows 无法读取此 PNG，请确认图片文件完整。",
    "ASYNC_BRIDGE_UNAVAILABLE": "Windows Runtime OCR 桥接不可用，请使用 Windows PowerShell 5.1。",
    "OCR_FAILED": "Windows 本地 OCR 未能识别此图片，请重试。",
}


def _parse_result(content: bytes) -> OcrResult:
    try:
        value = json.loads(content.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OcrError("Windows OCR 返回了无法解析的数据。") from exc
    if not isinstance(value, dict):
        raise OcrError("Windows OCR 返回的数据格式有误。")
    if "error" in value:
        code = value["error"] if isinstance(value["error"], str) else None
        raise OcrError(_BRIDGE_ERRORS.get(code, "Windows 本地 OCR 未能完成。"))
    width, height = value.get("width"), value.get("height")
    if any(isinstance(n, bool) or not isinstance(n, int) or not 0 < n <= 32768
           for n in (width, height)):
        raise OcrError("Windows OCR 返回的图片尺寸无效。")
    language, raw_lines = value.get("language"), value.get("lines")
    if not isinstance(language, str) or not language or not isinstance(raw_lines, list):
        raise OcrError("Windows OCR 返回的语言或文本格式无效。")
    if len(raw_lines) > 20000:
        raise OcrError("Windows OCR 返回的文本行过多。")
    lines = []
    word_count = 0
    for raw_line in raw_lines:
        if (not isinstance(raw_line, dict) or not isinstance(raw_line.get("text"), str)
                or not isinstance(raw_line.get("words"), list)):
            raise OcrError("Windows OCR 返回的文本行格式无效。")
        words = []
        for raw_word in raw_line["words"]:
            word_count += 1
            if word_count > 100000:
                raise OcrError("Windows OCR 返回的词条过多。")
            if not isinstance(raw_word, dict) or not isinstance(raw_word.get("text"), str):
                raise OcrError("Windows OCR 返回的词条格式无效。")
            bounds = raw_word.get("bounds")
            if (not isinstance(bounds, list) or len(bounds) != 4
                    or any(isinstance(n, bool) or not isinstance(n, (int, float))
                           or not 0 <= n <= 32768 or not math.isfinite(n) for n in bounds)):
                raise OcrError("Windows OCR 返回的文字位置无效。")
            words.append(OcrWord(raw_word["text"], tuple(float(n) for n in bounds)))
        lines.append(OcrLine(raw_line["text"], tuple(words)))
    return OcrResult(width, height, language, tuple(lines))


class WindowsOcr:
    """Run the OS OCR bridge without changing system or PowerShell settings."""

    def __init__(self, timeout: float = 30.0,
                 powershell_path: str | os.PathLike[str] | None = None) -> None:
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 600):
            raise OcrError("OCR 超时必须是 0 到 600 秒之间的有限数值。")
        self.timeout = float(timeout)
        self.powershell_path = (Path(powershell_path) if powershell_path is not None else
            Path(os.environ.get("SystemRoot", r"C:\Windows")) /
            "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
        self.script_path = Path(__file__).resolve().parent / "assets" / "ocr-image.ps1"

    def recognize(self, png: bytes) -> OcrResult:
        if sys.platform != "win32":
            raise OcrError("本地 OCR 组件仅支持 Windows。")
        if (not isinstance(png, bytes) or len(png) < 24
                or not png.startswith(_PNG_SIGNATURE)
                or png[8:16] != b"\x00\x00\x00\rIHDR"):
            raise OcrError("请传入完整的 PNG 图片字节。")
        if len(png) > MAX_PNG_BYTES:
            raise OcrError("OCR 图片不得超过 20 MB。")
        if not self.powershell_path.is_file():
            raise OcrError("未找到 Windows PowerShell 5.1 的 powershell.exe。")
        if not self.script_path.is_file():
            raise OcrError("缺少 assets/ocr-image.ps1，请使用完整的助手文件夹。")
        try:
            with tempfile.TemporaryDirectory(prefix="ulearning-ocr-") as directory:
                image_path = Path(directory) / "input.png"
                image_path.write_bytes(png)
                arguments = [str(self.powershell_path), "-NoLogo", "-NoProfile", "-NonInteractive",
                             "-ExecutionPolicy", "Bypass", "-File", str(self.script_path),
                             "-ImagePath", str(image_path)]
                # ExecutionPolicy applies only to this process; nothing is persisted.
                completed = subprocess.run(
                    arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, timeout=self.timeout, shell=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
        except subprocess.TimeoutExpired as exc:
            raise OcrError("Windows OCR 识别超时，请稍后重试。") from exc
        except OSError as exc:
            raise OcrError("无法启动或读取 Windows OCR，请检查系统和临时文件权限。") from exc
        if len(completed.stdout) > 40 * 1024 * 1024:
            raise OcrError("Windows OCR 返回的数据过大。")
        if completed.returncode != 0:
            # The bridge emits a fixed error code, never a path or recognized text.
            try:
                failure = json.loads(completed.stdout.decode("utf-8-sig"))
            except (UnicodeError, json.JSONDecodeError):
                failure = None
            code = failure.get("error") if isinstance(failure, dict) else None
            if not isinstance(code, str):
                code = None
            raise OcrError(_BRIDGE_ERRORS.get(code, "Windows 本地 OCR 运行失败。"))
        return _parse_result(completed.stdout)

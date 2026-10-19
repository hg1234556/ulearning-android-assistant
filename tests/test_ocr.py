import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from ulearning_assistant.ocr import MAX_PNG_BYTES, OcrError, WindowsOcr


PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (100).to_bytes(4, "big") * 2
VALID = {
    "width": 100, "height": 100, "language": "zh-Hans-CN",
    "lines": [{"text": "1 / 376", "words": [
        {"text": "1", "bounds": [10, 20, 5, 8]},
        {"text": "/", "bounds": [17, 20, 3, 8]},
        {"text": "376", "bounds": [22, 20, 15, 8]},
    ]}],
}


class OcrTests(unittest.TestCase):
    def recognize(self, content=None, *, returncode=0, side_effect=None):
        encoded = json.dumps(VALID if content is None else content).encode("utf-8")
        completed = subprocess.CompletedProcess([], returncode, encoded, b"private diagnostic")
        with patch("ulearning_assistant.ocr.sys.platform", "win32"), \
                patch.object(Path, "is_file", return_value=True), \
                patch("ulearning_assistant.ocr.subprocess.run", return_value=completed,
                      side_effect=side_effect) as run:
            result = WindowsOcr().recognize(PNG)
        return result, run

    def test_result_dimensions_words_and_text(self):
        result, _ = self.recognize()
        self.assertEqual((result.width, result.height, result.language), (100, 100, "zh-Hans-CN"))
        self.assertEqual(result.text, "1 / 376")
        self.assertEqual(result.lines[0].words[0].bounds, (10.0, 20.0, 5.0, 8.0))

    def test_subprocess_uses_argument_array_and_removes_temporary_png(self):
        image_paths = []
        def bridge(arguments, **kwargs):
            self.assertIsInstance(arguments, list)
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["timeout"], 30.0)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertIn("-NoProfile", arguments)
            self.assertIn("-NonInteractive", arguments)
            image_path = Path(arguments[arguments.index("-ImagePath") + 1])
            self.assertEqual(image_path.read_bytes(), PNG)
            image_paths.append(image_path)
            return subprocess.CompletedProcess(arguments, 0, json.dumps(VALID).encode(), b"")
        self.recognize(side_effect=bridge)
        self.assertEqual(len(image_paths), 1)
        self.assertFalse(image_paths[0].exists())

    def test_other_platform_fails_without_launch(self):
        with patch("ulearning_assistant.ocr.sys.platform", "linux"), \
                patch("ulearning_assistant.ocr.subprocess.run") as run:
            with self.assertRaisesRegex(OcrError, "仅支持 Windows"):
                WindowsOcr().recognize(PNG)
            run.assert_not_called()

    def test_png_validation_and_size_limit(self):
        with patch("ulearning_assistant.ocr.sys.platform", "win32"), \
                patch("ulearning_assistant.ocr.subprocess.run") as run:
            for invalid in (None, "png", b"", b"not a PNG", PNG[:20], PNG[:8] + b"bad header" * 3):
                with self.subTest(invalid=type(invalid)):
                    with self.assertRaises(OcrError):
                        WindowsOcr().recognize(invalid)
            with self.assertRaisesRegex(OcrError, "20 MB"):
                WindowsOcr().recognize(PNG + b"\x00" * MAX_PNG_BYTES)
            run.assert_not_called()

    def test_missing_executable_or_script(self):
        for available in ([False], [True, False]):
            with patch("ulearning_assistant.ocr.sys.platform", "win32"), \
                    patch.object(Path, "is_file", side_effect=available), \
                    patch("ulearning_assistant.ocr.subprocess.run") as run:
                with self.assertRaises(OcrError):
                    WindowsOcr().recognize(PNG)
                run.assert_not_called()

    def test_timeout_is_friendly_and_does_not_echo_output(self):
        with self.assertRaisesRegex(OcrError, "超时") as error:
            self.recognize(side_effect=subprocess.TimeoutExpired("powershell", 30,
                                                                 output=b"private OCR text"))
        self.assertNotIn("private", str(error.exception))

    def test_process_error_is_sanitized(self):
        with self.assertRaisesRegex(OcrError, "运行失败") as error:
            self.recognize({"private": "sensitive data"}, returncode=1)
        self.assertNotIn("sensitive", str(error.exception))
        with self.assertRaisesRegex(OcrError, "语言"):
            self.recognize({"error": "OCR_UNAVAILABLE"}, returncode=1)

    def test_invalid_json_is_sanitized(self):
        def invalid(*_args, **_kwargs):
            return subprocess.CompletedProcess([], 0, b"private non-JSON output", b"")
        with self.assertRaisesRegex(OcrError, "无法解析") as error:
            self.recognize(side_effect=invalid)
        self.assertNotIn("private", str(error.exception))

    def test_result_validation(self):
        invalid_values = [
            [], {}, {"error": []}, {**VALID, "width": True}, {**VALID, "height": 0},
            {**VALID, "language": None}, {**VALID, "lines": {}},
            {**VALID, "lines": [{"text": "page", "words": None}]},
            {**VALID, "lines": [{"text": "page", "words": [{"text": "x", "bounds": [0, -1, 2, 3]}]}]},
            {**VALID, "lines": [{"text": "page", "words": [{"text": "x", "bounds": [0, 0, 2]}]}]},
            {**VALID, "lines": [{"text": "page", "words": [{"text": "x", "bounds": [True, 0, 2, 3]}]}]},
            {**VALID, "lines": [{"text": "page", "words": [{"text": "x", "bounds": [10**1000, 0, 2, 3]}]}]},
            {**VALID, "lines": [{"text": "page", "words": [{"text": "x", "bounds": [float("nan"), 0, 2, 3]}]}]},
        ]
        for invalid in invalid_values:
            with self.subTest(invalid=invalid):
                with self.assertRaises(OcrError):
                    self.recognize(invalid)

    def test_blank_image_has_valid_empty_lines(self):
        result, _ = self.recognize({**VALID, "lines": []})
        self.assertEqual(result.lines, ())
        self.assertEqual(result.text, "")

    def test_timeout_validation(self):
        for timeout in (True, 0, -1, float("inf"), float("nan"), 601, "30"):
            with self.subTest(timeout=timeout):
                with self.assertRaises(OcrError):
                    WindowsOcr(timeout=timeout)


if __name__ == "__main__":
    unittest.main()

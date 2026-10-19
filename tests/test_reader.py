import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ulearning_assistant.core import AdbClient, AdbError, Device, UiNode
from ulearning_assistant.ocr import OcrLine, OcrResult, OcrWord
from ulearning_assistant.reader import (
    ReaderConfig, ReaderError, ReaderState, ReadingSession, VisualReader, recognize_pause, recognize_state,
)
from ulearning_assistant.workflows import Inspector, WorkflowError


def observation(page=1, total=376, title="《 资 本 论 》", *, width=1920):
    return OcrResult(width=width, height=1080, language="zh-Hans", lines=(
        OcrLine(text=title, words=(OcrWord(text=title, bounds=(898, 78, 125, 30)),)),
        OcrLine(text=f"{page} / {total}", words=(OcrWord(text=f"{page} / {total}", bounds=(915, 234, 95, 20)),)),
        OcrLine(text="下一页", words=(OcrWord(text="下一页", bounds=(1630, 1005, 95, 35)),)),
    ))


class FakeClient:
    def __init__(self):
        self.package = "cn.ulearning.yxy"
        self.taps = []
        self.captures = 0

    def foreground_package(self):
        return self.package

    def screenshot(self):
        self.captures += 1
        return b"fake screenshot"

    def tap(self, x, y):
        self.taps.append((x, y))


class FakeOcr:
    def __init__(self, *states):
        self.states = list(states) or [observation()]

    def recognize(self, png):
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]


def pause_observation(*, reason="走神太久", title="《 资 本 论 》",
                      status="计时学习已暂停", buttons=((890, 750, 140, 50),)):
    """A visible pause dialog, distinct from the PDF's bottom navigation."""
    base = observation(title=title)
    lines = list(base.lines)
    for label, bounds in ((reason, (700, 500, 400, 40)),
                          (status, (700, 550, 400, 40))):
        if label:
            lines.append(OcrLine(label, (OcrWord(label, bounds),)))
    lines.extend(OcrLine("继续学习", (OcrWord("继续学习", bounds),)) for bounds in buttons)
    return OcrResult(base.width, base.height, base.language, tuple(lines))


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ClockEvent:
    """Advance a virtual clock for waits instead of blocking the test process."""
    def __init__(self, clock):
        self.clock = clock
        self.cancelled = False

    def is_set(self):
        return self.cancelled

    def set(self):
        self.cancelled = True

    def wait(self, timeout):
        if not self.cancelled:
            self.clock.advance(timeout)
        return self.cancelled


class ReaderTests(unittest.TestCase):
    def test_region_selection_ignores_bottom_next_book(self):
        state = recognize_state(observation(), ReaderConfig())
        self.assertEqual((state.title, state.page, state.total), ("《资本论》", 1, 376))

    def test_resolution_and_missing_or_ambiguous_counter_rejected(self):
        with self.assertRaisesRegex(ReaderError, "分辨率"):
            recognize_state(observation(width=1080), ReaderConfig())
        sample = observation()
        for lines in (sample.lines[:1], (*sample.lines, sample.lines[1])):
            with self.assertRaisesRegex(ReaderError, "唯一识别"):
                recognize_state(OcrResult(1920, 1080, "zh-Hans", lines), ReaderConfig())

    def test_successful_single_next_and_previous_use_toolbar(self):
        for direction, start, end, point in ((1, 1, 2, (1651, 239)), (-1, 2, 1, (1581, 239))):
            with self.subTest(direction=direction):
                client = FakeClient()
                reader = VisualReader(client, ocr=FakeOcr(observation(start), observation(end)))
                self.assertEqual(reader.move(direction).page, end)
                self.assertEqual(client.taps, [point])

    def test_first_and_last_pages_never_click(self):
        for direction, page in ((-1, 1), (1, 376)):
            client = FakeClient()
            with self.assertRaisesRegex(ReaderError, "首页或末页"):
                VisualReader(client, ocr=FakeOcr(observation(page))).move(direction)
            self.assertFalse(client.taps)

    def test_wrong_app_does_not_capture_or_click(self):
        client = FakeClient()
        client.package = "com.android.launcher3"
        with self.assertRaisesRegex(ReaderError, "前台"):
            VisualReader(client, ocr=FakeOcr()).move(1)
        self.assertEqual(client.captures, 0)
        self.assertFalse(client.taps)

    def test_stale_preview_and_bad_direction_never_click(self):
        client = FakeClient()
        reader = VisualReader(client, ocr=FakeOcr(observation(2)))
        with self.assertRaisesRegex(ReaderError, "已变化"):
            reader.move(1, expected=ReaderState("《资本论》", 1, 376, 1920, 1080))
        with self.assertRaises(ReaderError):
            reader.move(True)
        self.assertFalse(client.taps)

    def test_cancel_during_ocr_never_clicks(self):
        client, cancel = FakeClient(), threading.Event()
        class CancellingOcr:
            def recognize(self, png):
                cancel.set()
                return observation()
        with self.assertRaisesRegex(ReaderError, "停止"):
            VisualReader(client, ocr=CancellingOcr()).move(1, cancel_event=cancel)
        self.assertFalse(client.taps)

    def test_deadline_expiring_during_ocr_never_clicks(self):
        client = FakeClient()
        with patch("ulearning_assistant.reader.time.monotonic", side_effect=[0, 20]):
            with self.assertRaisesRegex(ReaderError, "时限"):
                VisualReader(client, ocr=FakeOcr()).move(1, deadline=10)
        self.assertFalse(client.taps)

    def test_changed_book_or_skipped_page_stops_after_one_click(self):
        for after in (observation(1, 220, "《另一本文档》"), observation(3)):
            client = FakeClient()
            with self.assertRaises(ReaderError):
                VisualReader(client, ocr=FakeOcr(observation(), after)).move(1)
            self.assertEqual(len(client.taps), 1)

    def test_no_page_change_times_out_without_retry(self):
        client = FakeClient()
        with patch("ulearning_assistant.reader.time.monotonic", side_effect=[0, 0, 11]), patch(
                "ulearning_assistant.reader.time.sleep"):
            with self.assertRaisesRegex(ReaderError, "不会重试"):
                VisualReader(client, ocr=FakeOcr()).move(1)
        self.assertEqual(len(client.taps), 1)

    def test_timing_excludes_paused_gap_and_changed_book(self):
        now = [0]
        clock = lambda: now[0]
        state = ReaderState("《资本论》", 1, 376, 1920, 1080)
        session = ReadingSession(clock)
        session.start(state)
        now[0] = 3
        session.observe(state)
        session.pause()
        now[0] = 10
        session.start(state)
        now[0] = 11
        session.observe(state)
        self.assertEqual(session.elapsed_seconds, 4)
        now[0] = 30
        with self.assertRaises(ReaderError):
            session.observe(ReaderState("《另一本文档》", 1, 30, 1920, 1080))
        self.assertFalse(session.active)
        self.assertEqual(session.elapsed_seconds, 4)

    def test_timer_run_writes_local_only_log_without_navigation(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            result = VisualReader(client, ocr=FakeOcr()).run(
                duration_seconds=0.01, max_turns=0, output_dir=directory)
            payload = json.loads(result.log_path.read_text(encoding="utf-8"))
        self.assertEqual(result.stop_reason, "duration_limit")
        self.assertFalse(client.taps)
        self.assertEqual(payload["source"], "local")
        self.assertIs(payload["platform_confirmed"], False)

    def test_cancel_from_progress_and_bad_parameters(self):
        client, event = FakeClient(), threading.Event()
        result = VisualReader(client, ocr=FakeOcr()).run(
            cancel_event=event, on_update=lambda status: event.set())
        self.assertEqual(result.stop_reason, "cancelled")
        self.assertFalse(client.taps)
        for kwargs in ({"duration_seconds": float("nan")}, {"max_turns": True}, {"turn_interval": 0}):
            with self.assertRaises(ReaderError):
                VisualReader(client, ocr=FakeOcr()).run(**kwargs)

    def test_scheduled_run_verifies_one_turn_then_stops_at_limit(self):
        client = FakeClient()
        class ResponsiveOcr:
            def recognize(self, png):
                return observation(2 if client.taps else 1)
        reader = VisualReader(client, ocr=ResponsiveOcr())
        result = reader.run(duration_seconds=2, turn_interval=0.01, max_turns=1)
        self.assertEqual((result.stop_reason, result.turns, result.last_state.page), ("turn_limit", 1, 2))
        self.assertEqual(client.taps, [(1651, 239)])

    def test_cancelled_uncertain_move_does_not_report_stale_page(self):
        class CancelledReader(VisualReader):
            def inspect(self):
                return ReaderState("《资本论》", 1, 376, 1920, 1080)
            def move(self, *args, **kwargs):
                raise ReaderError("cancelled", "已停止")
        result = CancelledReader(FakeClient(), ocr=FakeOcr()).run(
            duration_seconds=2, turn_interval=0.01, max_turns=1)
        self.assertEqual(result.stop_reason, "cancelled")
        self.assertIsNone(result.last_state)

    def test_live_crlf_properties_are_parsed(self):
        with patch("ulearning_assistant.core.adb.discover_adb", return_value="adb.exe"):
            client = AdbClient()
        with patch.object(client, "select_device", return_value=Device("emulator-5554", "device")), patch.object(
                client, "_device_run", side_effect=[
                    b"[ro.build.version.release]: [14]\r\n[ro.build.version.sdk]: [34]\r\n[ro.product.brand]: [test]\r\n",
                    b"Physical size: 1920x1080\r\n"]):
            info = client.device_info()
        self.assertEqual((info["android_version"], info["sdk"], info["brand"]), ("14", "34", "test"))

    def test_resumed_activity_is_read_without_launching_app(self):
        with patch("ulearning_assistant.core.adb.discover_adb", return_value="adb.exe"):
            client = AdbClient()
        with patch.object(client, "_device_run", return_value=b"topResumedActivity=ActivityRecord{abcdef u0 cn.ulearning.yxy/courselib.player.CoursePlayerActivity t8}"):
            self.assertEqual(client.foreground_package(), "cn.ulearning.yxy")
        with patch.object(client, "_device_run", return_value=b"no resumed activity"):
            with self.assertRaises(AdbError):
                client.foreground_package()

    def test_wrong_calibration_points_are_rejected(self):
        with self.assertRaises(ReaderError):
            ReaderConfig(next_point=(1700, 1020))

    def test_generic_inspector_stops_at_opaque_webview_or_pdf_reader(self):
        class UiClient:
            def dump_ui(self):
                return [UiNode(class_name="android.webkit.WebView", package="cn.ulearning.yxy")]
        with self.assertRaises(WorkflowError) as error:
            Inspector(UiClient()).refresh()
        self.assertEqual(error.exception.code, "opaque_webview")
        class TextClient:
            def dump_ui(self):
                return [UiNode(text=text, package="cn.ulearning.yxy") for text in
                        ("返回课程章节", "展开目录", "1 / 376", "《资本论》", "下一页")]
        with self.assertRaises(WorkflowError) as error:
            Inspector(TextClient()).refresh()
        self.assertEqual(error.exception.code, "reader_page")


class PauseRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.config = ReaderConfig()
        self.state = ReaderState("《资本论》", 1, 376, 1920, 1080)

    def test_pause_requires_both_timing_status_and_known_reason(self):
        for reason in ("走神太久", "计时君停止工作"):
            with self.subTest(reason=reason):
                prompt = recognize_pause(pause_observation(reason=reason), self.config)
                self.assertEqual((prompt.title, prompt.button_point), ("《资本论》", (960, 775)))
        for sample in (pause_observation(reason=""), pause_observation(status=""),
                       pause_observation(reason="普通通知"), observation()):
            with self.subTest(sample=sample.lines[-1].text):
                self.assertIsNone(recognize_pause(sample, self.config))

    def test_ordinary_continue_learning_is_not_a_pause_dialog(self):
        sample = pause_observation(reason="", status="")
        self.assertIsNone(recognize_pause(sample, self.config))
        self.assertEqual(recognize_state(sample, self.config), self.state)

    def test_pause_button_must_be_unique_and_central(self):
        samples = (
            pause_observation(buttons=()),
            pause_observation(buttons=((1600, 1000, 140, 50),)),
            pause_observation(buttons=((800, 750, 140, 50), (1000, 750, 140, 50))),
        )
        for sample in samples:
            with self.subTest(buttons=len(sample.lines)):
                with self.assertRaises(ReaderError) as error:
                    recognize_pause(sample, self.config)
                self.assertEqual(error.exception.code, "pause_ambiguous")
        # An unrelated footer label cannot make a single central button ambiguous.
        prompt = recognize_pause(pause_observation(
            buttons=((1600, 1000, 140, 50), (890, 750, 140, 50))), self.config)
        self.assertEqual(prompt.button_point, (960, 775))

    def test_pause_prevents_normal_page_recognition(self):
        with self.assertRaises(ReaderError) as error:
            recognize_state(pause_observation(), self.config)
        self.assertEqual(error.exception.code, "platform_paused")

    def test_resume_clicks_once_and_waits_for_dialog_disappearance(self):
        client = FakeClient()
        reader = VisualReader(client, ocr=FakeOcr(
            pause_observation(), pause_observation(), observation()))
        with patch("ulearning_assistant.reader.time.sleep") as sleep:
            result = reader.resume_pause(expected=self.state)
        self.assertEqual(result, self.state)
        self.assertEqual(client.taps, [(960, 775)])
        self.assertEqual(client.captures, 3)
        sleep.assert_called_once_with(0.25)

    def test_dialog_missing_before_resume_does_not_click(self):
        client = FakeClient()
        with self.assertRaises(ReaderError) as error:
            VisualReader(client, ocr=FakeOcr(observation())).resume_pause(expected=self.state)
        self.assertEqual(error.exception.code, "pause_missing")
        self.assertEqual(client.taps, [])

    def test_changed_book_before_resume_does_not_click(self):
        client = FakeClient()
        with self.assertRaises(ReaderError) as error:
            VisualReader(client, ocr=FakeOcr(pause_observation(
                title="《另一本文档》"))).resume_pause(expected=self.state)
        self.assertEqual(error.exception.code, "book_changed")
        self.assertEqual(client.taps, [])

    def test_changed_book_or_page_after_resume_stops_after_one_click(self):
        for after, code in ((observation(title="《另一本文档》"), "book_changed"),
                            (observation(page=2), "stale_state"),
                            (observation(total=400), "stale_state")):
            with self.subTest(code=code, page=after.lines[1].text):
                client = FakeClient()
                reader = VisualReader(client, ocr=FakeOcr(pause_observation(), after))
                with self.assertRaises(ReaderError) as error:
                    reader.resume_pause(expected=self.state)
                self.assertEqual(error.exception.code, code)
                self.assertEqual(client.taps, [(960, 775)])

    def test_resume_timeout_never_retries_button(self):
        client = FakeClient()
        with patch("ulearning_assistant.reader.time.monotonic", side_effect=[0, 0, 11]), \
                patch("ulearning_assistant.reader.time.sleep"):
            with self.assertRaises(ReaderError) as error:
                VisualReader(client, ocr=FakeOcr(pause_observation())).resume_pause(expected=self.state)
        self.assertEqual(error.exception.code, "resume_timeout")
        self.assertEqual(client.taps, [(960, 775)])

    def test_cancel_before_or_during_pause_detection_never_clicks(self):
        for cancel_during_ocr in (False, True):
            with self.subTest(cancel_during_ocr=cancel_during_ocr):
                client, cancel = FakeClient(), threading.Event()
                class CancellingOcr:
                    def recognize(self, png):
                        cancel.set()
                        return pause_observation()
                if not cancel_during_ocr:
                    cancel.set()
                reader = VisualReader(client, ocr=CancellingOcr())
                with self.assertRaises(ReaderError) as error:
                    reader.resume_pause(expected=self.state, cancel_event=cancel)
                self.assertEqual(error.exception.code, "cancelled")
                self.assertEqual(client.taps, [])
                self.assertEqual(client.captures, int(cancel_during_ocr))

    def test_automatic_resume_statistics_exclude_resume_gap_from_local_timer(self):
        clock = FakeClock()
        cancel = ClockEvent(clock)
        class SlowResumeClient(FakeClient):
            def tap(self, x, y):
                super().tap(x, y)
                clock.advance(7)
        client = SlowResumeClient()
        reader = VisualReader(client, ocr=FakeOcr(
            pause_observation(), pause_observation(), observation(), observation()))
        updates = []
        def on_update(status):
            updates.append(status)
            if len(updates) == 2:
                cancel.set()
        with tempfile.TemporaryDirectory() as directory, \
                patch("ulearning_assistant.reader.time.monotonic", side_effect=clock), \
                patch("ulearning_assistant.reader.ReadingSession",
                      side_effect=lambda: ReadingSession(clock=clock)):
            result = reader.run(duration_seconds=30, max_turns=0, cancel_event=cancel,
                                on_update=on_update, output_dir=directory)
            payload = json.loads(result.log_path.read_text(encoding="utf-8"))
        self.assertEqual((result.stop_reason, result.resume_count, result.turns), ("cancelled", 1, 0))
        self.assertEqual(result.paused_seconds, 7)
        self.assertEqual(result.elapsed_seconds, 1)
        self.assertEqual(result.last_state, self.state)
        self.assertEqual(client.taps, [(960, 775)])
        self.assertEqual((payload["resume_count"], payload["paused_seconds"]), (1, 7))
        self.assertFalse(payload["platform_confirmed"])
        self.assertEqual(updates[-1]["resume_count"], 1)

    def test_disabled_recovery_or_zero_limit_never_clicks(self):
        for options, code in (({"auto_resume": False}, "platform_paused"),
                              ({"max_resumes": 0}, "resume_limit")):
            with self.subTest(options=options):
                client = FakeClient()
                reader = VisualReader(client, ocr=FakeOcr(pause_observation()))
                result = reader.run(max_turns=0, **options)
                self.assertEqual(result.stop_reason, code)
                self.assertEqual(result.resume_count, 0)
                self.assertEqual(client.taps, [])

    def test_resume_limit_stops_before_second_recovery_click(self):
        clock = FakeClock()
        client = FakeClient()
        reader = VisualReader(client, ocr=FakeOcr(
            observation(), pause_observation(), pause_observation(), observation(), pause_observation()))
        with patch("ulearning_assistant.reader.time.monotonic", side_effect=clock), \
                patch("ulearning_assistant.reader.ReadingSession",
                      side_effect=lambda: ReadingSession(clock=clock)):
            result = reader.run(duration_seconds=30, max_turns=0, max_resumes=1,
                                cancel_event=ClockEvent(clock))
        self.assertEqual((result.stop_reason, result.resume_count), ("resume_limit", 1))
        self.assertEqual(client.taps, [(960, 775)])

    def test_pause_after_uncertain_page_turn_does_not_recover_or_click_again(self):
        clock = FakeClock()
        client = FakeClient()
        class PauseAfterTapOcr:
            def recognize(self, png):
                return pause_observation() if client.taps else observation()
        reader = VisualReader(client, ocr=PauseAfterTapOcr())
        with patch("ulearning_assistant.reader.time.monotonic", side_effect=clock), \
                patch("ulearning_assistant.reader.ReadingSession",
                      side_effect=lambda: ReadingSession(clock=clock)), \
                patch.object(reader, "resume_pause") as resume:
            result = reader.run(duration_seconds=30, turn_interval=1, max_turns=3,
                                cancel_event=ClockEvent(clock))
        self.assertEqual((result.stop_reason, result.turns, result.resume_count), ("platform_paused", 0, 0))
        self.assertIsNone(result.last_state)
        self.assertEqual(client.taps, [(1651, 239)])
        resume.assert_not_called()


class RunObservationRegressionTests(unittest.TestCase):
    def test_ocr_finishing_after_deadline_does_not_credit_overrun_or_navigate(self):
        clock = FakeClock()
        client = FakeClient()
        class SlowSecondObservation:
            def recognize(self, png):
                if client.captures == 2:
                    # Reading the screen began before the deadline, but finished after it.
                    clock.advance(10)
                return observation()
        reader = VisualReader(client, ocr=SlowSecondObservation())
        with patch("ulearning_assistant.reader.time.monotonic", side_effect=clock), \
                patch("ulearning_assistant.reader.ReadingSession",
                      side_effect=lambda: ReadingSession(clock=clock)):
            result = reader.run(duration_seconds=5, turn_interval=4, max_turns=1,
                                cancel_event=ClockEvent(clock))
        self.assertEqual(result.stop_reason, "duration_limit")
        self.assertEqual(result.elapsed_seconds, 0)
        self.assertEqual(result.turns, 0)
        self.assertEqual(client.captures, 2)
        self.assertEqual(client.taps, [])

    def test_unscheduled_page_change_stops_before_following_navigation(self):
        clock = FakeClock()
        client = FakeClient()
        reader = VisualReader(client, ocr=FakeOcr(observation(1), observation(2)))
        with patch("ulearning_assistant.reader.time.monotonic", side_effect=clock), \
                patch("ulearning_assistant.reader.ReadingSession",
                      side_effect=lambda: ReadingSession(clock=clock)):
            result = reader.run(duration_seconds=5, turn_interval=2, max_turns=1,
                                cancel_event=ClockEvent(clock))
        self.assertEqual(result.stop_reason, "stale_state")
        self.assertEqual(result.elapsed_seconds, 0)
        self.assertEqual(result.turns, 0)
        self.assertEqual(client.captures, 2)
        self.assertEqual(client.taps, [])


if __name__ == "__main__":
    unittest.main()

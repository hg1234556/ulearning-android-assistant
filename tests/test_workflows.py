import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from ulearning_assistant.core import UiNode
from ulearning_assistant.workflows import (
    DEFAULT_PACKAGE, Inspector, PageCollector, Profile, WorkflowError,
)


def node(text="", *, description="", resource_id="", package=DEFAULT_PACKAGE,
         clickable=False, enabled=True, password=False, bounds=(0, 0, 200, 50)):
    return UiNode(text=text, description=description, resource_id=resource_id,
                  class_name="android.widget.TextView", package=package,
                  bounds=bounds, clickable=clickable, enabled=enabled, password=password)


def page(title, *, next_button=True):
    result = [node(title)]
    if next_button:
        result.append(node("下一页", clickable=True, resource_id="cn.ulearning.yxy:id/next", bounds=(0, 60, 200, 110)))
    return result


class FakeClient:
    def __init__(self, frames):
        self.frames = frames
        self.index = 0
        self.taps = []
        self.dump_count = 0

    def dump_ui(self):
        self.dump_count += 1
        return list(self.frames[self.index])

    def tap(self, x, y):
        self.taps.append((x, y))
        self.index = min(self.index + 1, len(self.frames) - 1)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "exports"

    def inspector(self, frames):
        client = FakeClient(frames)
        return client, Inspector(client, Profile())

    def test_profile_validation_and_load(self):
        path = Path(self.temporary.name) / "profile.json"
        path.write_text(json.dumps({"actions": {"next_page": {"text": "下一页", "resource_id": "next"}}}), encoding="utf-8")
        profile = Profile.load(path)
        self.assertEqual(profile.package, DEFAULT_PACKAGE)
        self.assertEqual(profile.actions["next_page"]["resource_id"], "next")
        for bad in ({"package": "not a package"}, {"actions": {"next_page": {"x": 1}}}, {"actions": {"play": {"text": ""}}}):
            with self.assertRaises(WorkflowError):
                Profile.from_dict(bad)

    def test_preview_only_matches_target_and_rechecks_before_tap(self):
        client, inspector = self.inspector([[node("下一页", clickable=True), node("下一页", package="other.app", clickable=True)]])
        inspector.refresh()
        self.assertEqual(len(inspector.filter({"text": "下一页"})), 1)
        preview = inspector.preview("next_page")
        self.assertEqual(client.taps, [])
        inspector.apply(preview)
        self.assertEqual(client.dump_count, 2)
        self.assertEqual(client.taps, [(100, 25)])

    def test_stale_preview_cannot_click(self):
        client, inspector = self.inspector([page("first"), page("second")])
        preview = inspector.preview("next_page")
        client.index = 1
        with self.assertRaises(WorkflowError) as caught:
            inspector.apply(preview)
        self.assertEqual(caught.exception.code, "stale_snapshot")
        self.assertEqual(client.taps, [])

    def test_collect_single_page_paths_and_no_overwrite(self):
        _, inspector = self.inspector([[node("课程标题"), node("可见正文"), node("其他应用秘密", package="other.app")]])
        first = inspector.collect(self.output)
        second = inspector.collect(self.output)
        self.assertTrue(first.markdown_path.is_file())
        self.assertTrue(first.json_path.is_file())
        self.assertNotEqual(first.markdown_path, second.markdown_path)
        self.assertIn("可见正文", first.markdown_path.read_text(encoding="utf-8"))
        self.assertNotIn("其他应用秘密", first.json_path.read_text(encoding="utf-8"))
        json.loads(first.json_path.read_text(encoding="utf-8"))
        with self.assertRaises(WorkflowError):
            inspector.collect(self.output, "../escape")

    def test_cancel_before_read(self):
        client, inspector = self.inspector([page("first")])
        cancel = threading.Event()
        cancel.set()
        result = PageCollector(inspector).collect(self.output, cancel_event=cancel)
        self.assertEqual((result.stop_reason, result.count), ("cancelled", 0))
        self.assertEqual(client.dump_count, 0)

    def test_cancel_from_callback_never_clicks_next(self):
        client, inspector = self.inspector([page("first"), page("second")])
        cancel = threading.Event()
        result = PageCollector(inspector).collect(self.output, interval=0, cancel_event=cancel, on_page=lambda _: cancel.set())
        self.assertEqual((result.stop_reason, result.count), ("cancelled", 1))
        self.assertEqual(client.taps, [])

    def test_cancel_during_action_recheck_never_clicks(self):
        client, inspector = self.inspector([page("first")])
        cancel = threading.Event()
        original_dump = client.dump_ui
        def cancel_second_dump():
            nodes = original_dump()
            if client.dump_count == 2:
                cancel.set()
            return nodes
        client.dump_ui = cancel_second_dump
        result = PageCollector(inspector).collect(self.output, interval=0, cancel_event=cancel)
        self.assertEqual((result.stop_reason, result.count), ("cancelled", 1))
        self.assertEqual(client.taps, [])

    def test_deadline_during_action_recheck_never_clicks(self):
        client, inspector = self.inspector([page("first")])
        elapsed = [0.0]
        original_dump = client.dump_ui
        def delayed_second_dump():
            nodes = original_dump()
            if client.dump_count == 2:
                elapsed[0] = 2.0
            return nodes
        client.dump_ui = delayed_second_dump
        with patch("ulearning_assistant.workflows.time.monotonic", side_effect=lambda: elapsed[0]):
            result = PageCollector(inspector).collect(self.output, interval=0, max_duration=1)
        self.assertEqual((result.stop_reason, result.count), ("max_duration", 1))
        self.assertEqual(client.taps, [])

    def test_repeated_page_stops_after_one_attempt(self):
        client, inspector = self.inspector([page("same"), page("same")])
        result = PageCollector(inspector).collect(self.output, interval=0)
        self.assertEqual((result.stop_reason, result.count), ("repeated_page", 1))
        self.assertEqual(len(client.taps), 1)

    def test_ambiguous_next_stops_without_click(self):
        client, inspector = self.inspector([page("first") + [node("下一页", clickable=True)]])
        result = PageCollector(inspector).collect(self.output, interval=0)
        self.assertEqual((result.stop_reason, result.count), ("next_ambiguous", 1))
        self.assertEqual(client.taps, [])

    def test_missing_next_and_max_pages_are_not_completion(self):
        client, inspector = self.inspector([page("only", next_button=False)])
        result = PageCollector(inspector).collect(self.output, interval=0)
        self.assertEqual((result.stop_reason, result.count), ("next_missing", 1))
        client, inspector = self.inspector([page("first"), page("second")])
        result = PageCollector(inspector).collect(self.output, interval=0, max_pages=1)
        self.assertEqual((result.stop_reason, result.count), ("max_pages", 1))
        self.assertEqual(client.taps, [])

    def test_wrong_app_stops_before_export(self):
        client, inspector = self.inspector([[node("Launcher", package="com.android.launcher")]])
        result = PageCollector(inspector).collect(self.output, interval=0)
        self.assertEqual((result.stop_reason, result.count), ("wrong_app", 0))
        self.assertFalse(self.output.exists())

    def test_login_exam_and_submit_confirmation_stop(self):
        cases = [
            ([node("登录"), node("my password", password=True)], "login_page"),
            ([node("期末考试"), node("剩余时间 10:00")], "exam_page"),
            ([node("确认提交"), node("确定", clickable=True)], "submission_confirmation"),
        ]
        for frame, reason in cases:
            with self.subTest(reason=reason):
                client, inspector = self.inspector([frame])
                result = PageCollector(inspector).collect(self.output, interval=0)
                self.assertEqual((result.stop_reason, result.count), (reason, 0))
                self.assertEqual(client.taps, [])
        _, inspector = self.inspector([[node("课程列表"), node("考试安排"), node("英语作业")]])
        self.assertEqual(len(inspector.refresh().nodes), 3)

    def test_submit_action_refused_even_with_resource_selector(self):
        client, inspector = self.inspector([[node("提交", resource_id="submit", clickable=True)]])
        with self.assertRaises(WorkflowError) as caught:
            inspector.preview({"resource_id": "submit"})
        self.assertEqual(caught.exception.code, "unsafe_action")
        self.assertEqual(client.taps, [])

    def test_callback_failure_is_explicit(self):
        client, inspector = self.inspector([page("first")])
        def fail(_):
            raise RuntimeError("callback failed")
        result = PageCollector(inspector).collect(self.output, interval=0, on_page=fail)
        self.assertEqual((result.stop_reason, result.count), ("callback_error", 1))
        self.assertIn("callback failed", result.detail)
        self.assertEqual(client.taps, [])

    def test_finite_limits_and_timeout(self):
        _, inspector = self.inspector([page("first")])
        collector = PageCollector(inspector)
        for kwargs in ({"max_pages": 0}, {"max_pages": float("inf")}, {"max_duration": float("inf")}, {"interval": -1}):
            with self.assertRaises(ValueError):
                collector.collect(self.output, **kwargs)
        with patch("ulearning_assistant.workflows.time.monotonic", side_effect=[0.0, 2.0, 2.0]):
            result = collector.collect(self.output, max_duration=1)
        self.assertEqual((result.stop_reason, result.count), ("max_duration", 0))

    def test_non_text_automatic_navigation_is_refused(self):
        client, inspector = self.inspector([page("first")])
        inspector.profile = Profile(actions={"next_page": {"resource_id": "cn.ulearning.yxy:id/next"}})
        result = PageCollector(inspector).collect(self.output, interval=0)
        self.assertEqual((result.stop_reason, result.count), ("invalid_profile", 1))
        self.assertEqual(client.taps, [])


if __name__ == "__main__":
    unittest.main()

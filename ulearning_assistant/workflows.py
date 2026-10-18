"""Configurable actions and exports of the Android app's visible UI.

Selectors are exact and must identify one enabled, clickable element. A preview
is valid only while the complete target-app UI snapshot remains unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
from typing import Callable, Mapping

from .core import AdbClient, AdbError, UiNode, export_json, export_markdown, select_unique


DEFAULT_PACKAGE = "cn.ulearning.yxy"
DEFAULT_ACTIONS = {
    "next_page": {"text": "下一页"},
    "play": {"description": "播放"},
    "speed_menu": {"text": "倍速"},
    "speed_1_5": {"text": "1.5倍"},
    "speed_2": {"text": "2.0倍"},
}
STOP_MESSAGES = {
    "max_pages": "已到达本次采集页数上限；不表示课程已完成。",
    "max_duration": "已到达本次采集时间上限。",
    "cancelled": "用户已取消采集。",
    "repeated_page": "当前可见文字与已采集页面重复，已停止翻页。",
    "next_missing": "当前页面没有唯一可用的“下一页”按钮。",
    "next_ambiguous": "“下一页”匹配多个控件，已停止。",
    "wrong_app": "当前界面中没有目标 App 的可用控件。",
    "login_page": "检测到登录或密码输入页面，已停止。",
    "exam_page": "检测到考试或交卷操作页面，已停止。",
    "submission_confirmation": "检测到提交确认页面，已停止。",
    "unsafe_action": "该操作涉及提交，不在此助手支持的动作范围内。",
    "stale_snapshot": "页面已变化；旧预览已失效，请重新读取。",
    "not_clickable": "匹配控件不可点击、已禁用或没有有效边界。",
    "no_visible_text": "当前页面没有可导出的可见文字。",
    "unknown_action": "配置中没有该动作。",
    "invalid_profile": "动作配置无效。",
    "device_error": "读取或操作设备失败。",
    "export_error": "保存页面文件失败。",
    "callback_error": "页面进度回调失败，已停止。",
}


class WorkflowError(RuntimeError):
    """An actionable workflow failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or STOP_MESSAGES.get(code, code))


def _selector(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise WorkflowError("invalid_profile", "选择器必须是非空对象。")
    allowed = {"text", "resource_id", "description"}
    if set(value) - allowed:
        raise WorkflowError("invalid_profile", "选择器仅支持 text、resource_id、description。")
    if any(not isinstance(v, str) or not v.strip() for v in value.values()):
        raise WorkflowError("invalid_profile", "精确选择器的值必须是非空字符串。")
    return dict(value)


@dataclass(frozen=True)
class Profile:
    package: str = DEFAULT_PACKAGE
    actions: dict[str, dict[str, str]] = field(
        default_factory=lambda: {name: dict(value) for name, value in DEFAULT_ACTIONS.items()}
    )

    def __post_init__(self) -> None:
        if not isinstance(self.package, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", self.package
        ):
            raise WorkflowError("invalid_profile", "package 必须是有效的 Android 包名。")
        if not isinstance(self.actions, dict):
            raise WorkflowError("invalid_profile", "actions 必须是动作名称与选择器的映射。")
        checked = {}
        for name, selector in self.actions.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
                raise WorkflowError("invalid_profile", "动作名称只能使用英文、数字和下划线。")
            checked[name] = _selector(selector)
        object.__setattr__(self, "actions", checked)

    @classmethod
    def from_dict(cls, data: Mapping) -> Profile:
        if not isinstance(data, Mapping) or set(data) - {"package", "actions"}:
            raise WorkflowError("invalid_profile", "配置仅支持 package 和 actions 字段。")
        actions = data.get("actions", DEFAULT_ACTIONS)
        return cls(package=data.get("package", DEFAULT_PACKAGE), actions=actions)

    @classmethod
    def load(cls, path: str | Path | None = None) -> Profile:
        if path is None:
            path = Path(__file__).resolve().parent.parent / "profiles" / "default.json"
            if not path.is_file():
                return cls()
        try:
            return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8-sig")))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorkflowError("invalid_profile", f"无法读取配置：{exc}") from exc


@dataclass(frozen=True)
class Snapshot:
    nodes: tuple[UiNode, ...]
    fingerprint: str
    text_hash: str
    captured_at: float
    title: str


@dataclass(frozen=True)
class ActionPreview:
    action: str
    selector: dict[str, str]
    node: UiNode
    fingerprint: str


@dataclass(frozen=True)
class ExportResult:
    markdown_path: Path
    json_path: Path
    title: str
    text_hash: str
    node_count: int


@dataclass(frozen=True)
class CollectionResult:
    stop_reason: str
    count: int
    pages: list[ExportResult]
    elapsed: float
    detail: str = ""


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _visible_text(nodes: tuple[UiNode, ...]) -> list[str]:
    seen: set[str] = set()
    result = []
    for node in nodes:
        if node.password:
            continue
        for value in (node.text, node.description):
            value = value.strip()
            if value and value not in seen:
                result.append(value)
                seen.add(value)
    return result


def _blocked_page(nodes: tuple[UiNode, ...]) -> str | None:
    values = _visible_text(nodes)
    normalized = [re.sub(r"\s+", "", value).casefold() for value in values]
    joined = "\n".join(normalized)
    login = any(value in {"登录", "登陆", "login", "signin"} for value in normalized)
    credentials = any(word in joined for word in ("密码", "验证码", "账号", "学号", "手机号", "password", "username"))
    if any(node.password for node in nodes) or (login and credentials):
        return "login_page"
    confirmation_phrases = (
        "确认提交", "确定提交", "是否提交", "确定要提交", "确认交卷", "确定交卷",
        "是否交卷", "确定要交卷", "提交后无法修改", "提交后不可修改", "confirmsubmission",
    )
    confirmation_buttons = (
        any(value in {"确定", "确认"} for value in normalized)
        and "取消" in normalized
        and any(value in {"提交", "提交答案", "提交作业", "交卷"} for value in normalized)
    )
    if any(phrase in joined for phrase in confirmation_phrases) or confirmation_buttons:
        return "submission_confirmation"
    exam_context = any(word in joined for word in ("考试", "试卷", "测验", "答题卡", "单选题", "多选题", "判断题"))
    exam_controls = any(word in joined for word in ("交卷", "提交试卷", "开始考试", "剩余时间", "答题倒计时"))
    if exam_context and exam_controls:
        return "exam_page"
    return None


def _check_action_node(node: UiNode) -> None:
    labels = " ".join((node.text, node.description)).casefold()
    resource = node.resource_id.rsplit("/", 1)[-1].casefold()
    if any(word in labels for word in ("提交", "交卷", "submit")) or re.search(
        r"(?:^|[_-])(?:submit|submission|handin)(?:$|[_-])", resource
    ):
        raise WorkflowError("unsafe_action")
    left, top, right, bottom = node.bounds
    if not node.enabled or not node.clickable or right <= left or bottom <= top:
        raise WorkflowError("not_clickable")


class Inspector:
    def __init__(self, client: AdbClient, profile: Profile | None = None) -> None:
        self.client = client
        self.profile = profile or Profile.load()
        self._snapshot: Snapshot | None = None

    def refresh(self) -> Snapshot:
        # Invalidate the cache first so a failed read cannot leave usable old UI.
        self._snapshot = None
        try:
            nodes = tuple(node for node in self.client.dump_ui() if node.package == self.profile.package)
        except AdbError as exc:
            raise WorkflowError("device_error", str(exc)) from exc
        if not nodes:
            raise WorkflowError("wrong_app")
        blocked = _blocked_page(nodes)
        if blocked:
            raise WorkflowError(blocked)
        nodes = tuple(node for node in nodes if not node.password)
        visible = _visible_text(nodes)
        fingerprint = _hash([
            (n.text, n.description, n.resource_id, n.class_name, n.package,
             n.bounds, n.clickable, n.enabled, n.password) for n in nodes
        ])
        snapshot = Snapshot(
            nodes=nodes, fingerprint=fingerprint, text_hash=_hash(visible),
            captured_at=time.time(), title=visible[0] if visible else "当前可见页面",
        )
        self._snapshot = snapshot
        return snapshot

    def filter(self, selector: Mapping[str, str] | None = None) -> list[UiNode]:
        snapshot = self._snapshot or self.refresh()
        if selector is None:
            return list(snapshot.nodes)
        checked = _selector(selector)
        return [node for node in snapshot.nodes if all(getattr(node, key) == value for key, value in checked.items())]

    def _resolve(self, nodes: tuple[UiNode, ...], selector: dict[str, str]) -> UiNode:
        matches = [node for node in nodes if all(getattr(node, key) == value for key, value in selector.items())]
        if not matches:
            raise WorkflowError("next_missing", "精确选择器没有匹配到任何目标 App 控件。")
        if len(matches) > 1:
            raise WorkflowError("next_ambiguous", f"精确选择器匹配到 {len(matches)} 个控件，请补充 resource_id。")
        try:
            node = select_unique(nodes, **selector)
        except AdbError as exc:
            raise WorkflowError("device_error", str(exc)) from exc
        _check_action_node(node)
        return node

    def preview(self, action_or_selector: str | Mapping[str, str]) -> ActionPreview:
        snapshot = self._snapshot or self.refresh()
        if isinstance(action_or_selector, str):
            action = action_or_selector
            if action not in self.profile.actions:
                raise WorkflowError("unknown_action", f"配置中未定义动作：{action}")
            selector = _selector(self.profile.actions[action])
        else:
            action = "custom"
            selector = _selector(action_or_selector)
        node = self._resolve(snapshot.nodes, selector)
        return ActionPreview(action=action, selector=selector, node=node, fingerprint=snapshot.fingerprint)

    def apply(
        self,
        preview: ActionPreview,
        *,
        cancel_event: threading.Event | None = None,
        deadline: float | None = None,
    ) -> UiNode:
        def check_limits() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise WorkflowError("cancelled")
            if deadline is not None and time.monotonic() >= deadline:
                raise WorkflowError("max_duration")

        check_limits()
        current = self.refresh()
        check_limits()
        if current.fingerprint != preview.fingerprint:
            raise WorkflowError("stale_snapshot")
        node = self._resolve(current.nodes, _selector(preview.selector))
        try:
            check_limits()
            self.client.tap(*node.center)
        except AdbError as exc:
            raise WorkflowError("device_error", str(exc)) from exc
        finally:
            self._snapshot = None
        return node

    def collect(self, output_dir: str | Path, stem: str = "page") -> ExportResult:
        return self._export(self.refresh(), output_dir, stem)

    def _export(self, snapshot: Snapshot, output_dir: str | Path, stem: str) -> ExportResult:
        if not re.fullmatch(r"[\w-]{1,120}", stem, flags=re.UNICODE):
            raise WorkflowError("export_error", "文件名只能使用字母、数字、下划线或连字符。")
        nodes = tuple(node for node in snapshot.nodes if not node.password)
        if not _visible_text(nodes):
            raise WorkflowError("no_visible_text")
        try:
            markdown = export_markdown(nodes)
            json_content = export_json(nodes)
            destination = Path(output_dir).expanduser()
            destination.mkdir(parents=True, exist_ok=True)
            selected_stem = stem
            suffix = 2
            while (destination / f"{selected_stem}.md").exists() or (destination / f"{selected_stem}.json").exists():
                selected_stem = f"{stem}-{suffix}"
                suffix += 1
            markdown_path = destination / f"{selected_stem}.md"
            json_path = destination / f"{selected_stem}.json"
            markdown_path.write_text(markdown, encoding="utf-8")
            json_path.write_text(json_content, encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise WorkflowError("export_error", f"页面文件保存失败：{exc}") from exc
        return ExportResult(
            markdown_path=markdown_path, json_path=json_path, title=snapshot.title,
            text_hash=snapshot.text_hash, node_count=len(nodes),
        )


class PageCollector:
    def __init__(self, inspector: Inspector) -> None:
        self.inspector = inspector

    def collect(
        self,
        output_dir: str | Path,
        *,
        max_pages: int = 20,
        max_duration: float = 120.0,
        interval: float = 1.0,
        cancel_event: threading.Event | None = None,
        next_action: str = "next_page",
        on_page: Callable[[ExportResult], None] | None = None,
    ) -> CollectionResult:
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= 1000:
            raise ValueError("max_pages 必须是 1 至 1000 的整数。")
        for name, value, allow_zero in (("max_duration", max_duration, False), ("interval", interval, True)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} 必须是有限数值。")
            if value < 0 or (not allow_zero and value == 0):
                raise ValueError(f"{name} 必须{'不小于' if allow_zero else '大于'} 0。")
        cancel = cancel_event if cancel_event is not None else threading.Event()
        started = time.monotonic()
        pages: list[ExportResult] = []
        seen: set[str] = set()

        def result(reason: str, detail: str = "") -> CollectionResult:
            return CollectionResult(reason, len(pages), list(pages), time.monotonic() - started,
                                    detail or STOP_MESSAGES.get(reason, reason))

        def stopped() -> str | None:
            if cancel.is_set():
                return "cancelled"
            if time.monotonic() - started >= max_duration:
                return "max_duration"
            return None

        while True:
            stop = stopped()
            if stop:
                return result(stop)
            try:
                snapshot = self.inspector.refresh()
                stop = stopped()
                if stop:
                    return result(stop)
                if snapshot.text_hash in seen:
                    return result("repeated_page")
                page = self.inspector._export(snapshot, output_dir, f"page-{len(pages) + 1:03d}-{snapshot.text_hash[:8]}")
                seen.add(snapshot.text_hash)
                pages.append(page)
                if on_page is not None:
                    try:
                        on_page(page)
                    except Exception as exc:
                        return result("callback_error", f"页面已保存，但进度回调失败：{exc}")
                stop = stopped()
                if stop:
                    return result(stop)
                if len(pages) >= max_pages:
                    return result("max_pages")
                # Automatic collection only advances using an exact visible-text
                # selector. Resource-only custom actions cannot become navigation.
                selector = self.inspector.profile.actions.get(next_action)
                if not selector:
                    return result("unknown_action")
                if selector.get("text") != "下一页":
                    return result("invalid_profile", "自动分页动作必须使用精确 text: 下一页；可另外指定 resource_id。")
                preview = self.inspector.preview(next_action)
                stop = stopped()
                if stop:
                    return result(stop)
                self.inspector.apply(preview, cancel_event=cancel, deadline=started + max_duration)
            except WorkflowError as exc:
                return result(exc.code, str(exc))
            remaining = max_duration - (time.monotonic() - started)
            if cancel.wait(min(interval, max(0.0, remaining))):
                return result("cancelled")

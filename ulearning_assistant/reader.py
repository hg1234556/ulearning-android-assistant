"""A separately calibrated PDF reader, with verified single-step navigation.

No platform study records are created or edited by this module. Session time is
local and is credited only between successful observations of the same book.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import threading
import time
import uuid

from .core import AdbError
from .ocr import OcrError, WindowsOcr


class ReaderError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.action_sent = False
        super().__init__(message)


@dataclass(frozen=True)
class ReaderConfig:
    package: str = "cn.ulearning.yxy"
    width: int = 1920
    height: int = 1080
    title_region: tuple[int, int, int, int] = (700, 40, 1220, 148)
    page_region: tuple[int, int, int, int] = (760, 210, 1160, 274)
    toolbar_region: tuple[int, int, int, int] = (225, 210, 1695, 274)
    previous_point: tuple[int, int] = (1581, 239)
    next_point: tuple[int, int] = (1651, 239)

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+", self.package):
            raise ReaderError("configuration", "阅读器包名无效。")
        if any(type(v) is not int or not 1 <= v <= 10000 for v in (self.width, self.height)):
            raise ReaderError("configuration", "阅读器尺寸无效。")
        for region in (self.title_region, self.page_region, self.toolbar_region):
            if (len(region) != 4 or any(type(v) is not int for v in region)
                    or not (0 <= region[0] < region[2] <= self.width and 0 <= region[1] < region[3] <= self.height)):
                raise ReaderError("configuration", "阅读器识别区域无效。")
        toolbar = self.toolbar_region
        for point in (self.previous_point, self.next_point):
            if (len(point) != 2 or any(type(v) is not int for v in point)
                    or not (toolbar[0] < point[0] < toolbar[2] and toolbar[1] < point[1] < toolbar[3])):
                raise ReaderError("configuration", "翻页点必须在已校准的 PDF 工具栏内部。")

    @classmethod
    def load(cls, path: str | Path | None = None) -> ReaderConfig:
        if path is None:
            return cls()
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or set(data) - cls.__dataclass_fields__.keys():
                raise ValueError
            return cls(**data)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise ReaderError("configuration", "无法读取阅读器校准配置。") from exc


@dataclass(frozen=True)
class ReaderState:
    title: str
    page: int
    total: int
    width: int
    height: int


@dataclass(frozen=True)
class ReadingResult:
    stop_reason: str
    turns: int
    elapsed_seconds: float
    last_state: ReaderState | None
    log_path: Path | None = None
    resume_count: int = 0
    paused_seconds: float = 0.0


@dataclass(frozen=True)
class PausePrompt:
    title: str
    button_point: tuple[int, int]


def _region_lines(result, region) -> list[str]:
    left, top, right, bottom = region
    lines = []
    for line in result.lines:
        words = []
        for word in line.words:
            x, y, width, height = word.bounds
            if left <= x + width / 2 <= right and top <= y + height / 2 <= bottom:
                words.append(word.text)
        if words:
            lines.append("".join(words).strip())
    return lines


def _check_resolution(result, config):
    if (result.width, result.height) != (config.width, config.height):
        raise ReaderError("layout_changed", "设备分辨率与阅读器校准不一致，已停止；请重新适配布局。")


def _recognize_title(result, config):
    titles = [re.sub(r"\s+", "", title) for title in _region_lines(result, config.title_region) if title.strip()]
    if len(titles) != 1:
        raise ReaderError("title_missing", "无法唯一识别阅读器顶部书名，已停止。")
    title = titles[0]
    if not re.fullmatch(r"[《〈<].{1,100}[》〉>]", title):
        raise ReaderError("title_missing", "阅读器顶部没有可确认的书名，已停止。")
    return title


def recognize_pause(result, config: ReaderConfig) -> PausePrompt | None:
    _check_resolution(result, config)
    normalized = [re.sub(r"\s+", "", line.text) for line in result.lines]
    text = "\n".join(normalized)
    if "计时学习已暂停" not in text or not any(label in text for label in ("走神太久", "计时君停止工作")):
        return None
    title = _recognize_title(result, config)
    candidates = []
    for line, label in zip(result.lines, normalized):
        if label != "继续学习" or not line.words:
            continue
        left = min(word.bounds[0] for word in line.words)
        top = min(word.bounds[1] for word in line.words)
        right = max(word.bounds[0] + word.bounds[2] for word in line.words)
        bottom = max(word.bounds[1] + word.bounds[3] for word in line.words)
        point = (round((left + right) / 2), round((top + bottom) / 2))
        # Only the central, observed pause-dialog button can be a recovery target.
        if 650 < point[0] < 1270 and 650 < point[1] < 900:
            candidates.append(point)
    if len(candidates) != 1:
        raise ReaderError("pause_ambiguous", "检测到走神暂停，但无法唯一确认“继续学习”按钮；已停止。")
    return PausePrompt(title, candidates[0])


def recognize_state(result, config: ReaderConfig) -> ReaderState:
    _check_resolution(result, config)
    if recognize_pause(result, config) is not None:
        raise ReaderError("platform_paused", "平台显示“计时学习已暂停”；请处理走神弹窗后继续。")
    title = _recognize_title(result, config)
    counters = []
    for text in _region_lines(result, config.page_region):
        normalized = re.sub(r"\s+", "", text)
        counters.extend(re.findall(r"(?<!\d)(\d{1,6})[/／](\d{1,6})(?!\d)", normalized))
    if len(counters) != 1:
        raise ReaderError("page_missing", "无法唯一识别 PDF 工具栏页码，已停止。")
    page, total = map(int, counters[0])
    if not 1 <= page <= total <= 100000:
        raise ReaderError("page_missing", "识别到的页码无效，已停止。")
    return ReaderState(title, page, total, result.width, result.height)


class ReadingSession:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.active = False
        self.elapsed_seconds = 0.0
        self.last_state = None
        self._observed_at = None

    def start(self, state: ReaderState) -> None:
        self.active = True
        self.last_state = state
        self._observed_at = self.clock()

    def observe(self, state: ReaderState) -> None:
        now = self.clock()
        if self.last_state and (state.title, state.total) != (self.last_state.title, self.last_state.total):
            self.pause()
            raise ReaderError("book_changed", "书名或总页数已变化，本地计时已暂停。")
        if self.active and self._observed_at is not None:
            self.elapsed_seconds += max(0.0, now - self._observed_at)
        self.last_state = state
        self._observed_at = now

    def pause(self) -> None:
        self.active = False
        self._observed_at = None


class VisualReader:
    def __init__(self, client, config: ReaderConfig | None = None, ocr=None):
        self.client = client
        self.config = config or ReaderConfig.load()
        self.ocr = ocr or WindowsOcr()

    def _foreground(self) -> None:
        try:
            if self.client.foreground_package() != self.config.package:
                raise ReaderError("wrong_app", "前台已不是优学院，已停止阅读操作。")
        except AdbError as exc:
            raise ReaderError("device_error", str(exc)) from exc

    def _capture(self):
        self._foreground()
        try:
            result = self.ocr.recognize(self.client.screenshot())
        except (AdbError, OcrError) as exc:
            raise ReaderError("recognition_failed", str(exc)) from exc
        self._foreground()
        return result

    def inspect(self) -> ReaderState:
        return recognize_state(self._capture(), self.config)

    @staticmethod
    def _check_stop(cancel_event, deadline=None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise ReaderError("cancelled", "已停止阅读操作。")
        if deadline is not None and time.monotonic() >= deadline:
            raise ReaderError("duration_limit", "已到本次阅读时限。")

    def move(self, direction: int, *, cancel_event=None, expected=None, deadline=None) -> ReaderState:
        if type(direction) is not int or direction not in {-1, 1}:
            raise ReaderError("configuration", "翻页方向必须是上一页或下一页。")
        self._check_stop(cancel_event, deadline)
        before = self.inspect()
        self._check_stop(cancel_event, deadline)
        if expected is not None and before != expected:
            raise ReaderError("stale_state", "书名或页码已变化，请重新识别后再翻页。")
        target = before.page + direction
        if target < 1 or target > before.total:
            raise ReaderError("page_boundary", "已到文档首页或末页；不会切换到另一本书。")
        self._foreground()
        self._check_stop(cancel_event, deadline)
        try:
            self.client.tap(*(self.config.next_point if direction > 0 else self.config.previous_point))
        except AdbError as exc:
            raise ReaderError("device_error", str(exc)) from exc
        # A single tap only. Re-read while waiting; never retry an uncertain tap.
        waiting_until = min(time.monotonic() + 10, deadline if deadline is not None else math.inf)
        while time.monotonic() < waiting_until:
            self._check_stop(cancel_event, deadline)
            try:
                after = self.inspect()
            except ReaderError as exc:
                exc.action_sent = True
                raise
            self._check_stop(cancel_event, deadline)
            if (after.title, after.total) != (before.title, before.total):
                raise ReaderError("book_changed", "书名或总页数变化，已停止且不会继续点击。")
            if after.page == target:
                return after
            if after.page != before.page:
                raise ReaderError("unexpected_page", "实际页码变化超出单页范围，已停止。")
            if cancel_event is not None:
                cancel_event.wait(0.25)
            else:
                time.sleep(0.25)
        raise ReaderError("page_timeout", "单次点击后没有确认页码变化；已停止，不会重试点击。")

    def resume_pause(self, *, expected=None, cancel_event=None, deadline=None) -> ReaderState:
        self._check_stop(cancel_event, deadline)
        prompt = recognize_pause(self._capture(), self.config)
        self._check_stop(cancel_event, deadline)
        if prompt is None:
            raise ReaderError("pause_missing", "当前没有可确认的走神暂停弹窗，没有发送点击。")
        if expected is not None and prompt.title != expected.title:
            raise ReaderError("book_changed", "弹窗后的书名已变化，没有发送恢复点击。")
        self._foreground()
        self._check_stop(cancel_event, deadline)
        try:
            self.client.tap(*prompt.button_point)
        except AdbError as exc:
            raise ReaderError("device_error", str(exc)) from exc
        waiting_until = min(time.monotonic() + 10, deadline if deadline is not None else math.inf)
        while time.monotonic() < waiting_until:
            self._check_stop(cancel_event, deadline)
            result = self._capture()
            self._check_stop(cancel_event, deadline)
            if recognize_pause(result, self.config) is None:
                state = recognize_state(result, self.config)
                if state.title != prompt.title:
                    raise ReaderError("book_changed", "恢复后书名变化，已停止。")
                if expected is not None and (state.page, state.total) != (expected.page, expected.total):
                    raise ReaderError("stale_state", "恢复后页码与原记录不同，请重新识别。")
                return state
            if cancel_event is not None:
                cancel_event.wait(0.25)
            else:
                time.sleep(0.25)
        raise ReaderError("resume_timeout", "单次恢复点击后弹窗没有消失，已停止，不会重复点击。")

    def run(self, *, duration_seconds=300, turn_interval=30, max_turns=10, cancel_event=None,
            on_update=None, output_dir=None, auto_resume=True, max_resumes=10, expected_start=None) -> ReadingResult:
        for value, limit, label in ((duration_seconds, 28800, "总时限"), (turn_interval, 3600, "翻页间隔")):
            if type(value) not in {int, float} or not math.isfinite(value) or not 0 < value <= limit:
                raise ReaderError("configuration", f"{label}必须是有效的正数，且不超过 {limit} 秒。")
        if type(max_turns) is not int or not 0 <= max_turns <= 10000:
            raise ReaderError("configuration", "最多翻页次数应为 0–10000，0 表示只做本地计时。")
        if type(auto_resume) is not bool or type(max_resumes) is not int or not 0 <= max_resumes <= 100:
            raise ReaderError("configuration", "自动恢复开关或最大恢复次数无效（允许 0–100 次）。")
        cancel = cancel_event if cancel_event is not None else threading.Event()
        session = ReadingSession()
        turns = 0
        reason = "duration_limit"
        state = None
        moving = False
        resume_count = 0
        paused_seconds = 0.0
        deadline = None
        started_utc = datetime.now(timezone.utc).isoformat()

        def obtain_state(expected):
            nonlocal resume_count, paused_seconds, moving
            try:
                observed = self.inspect()
                self._check_stop(cancel, deadline)
                if expected is not None and observed != expected:
                    raise ReaderError("stale_state", "运行期间书名或页码已变化，请重新识别后再操作。")
                return observed, False
            except ReaderError as exc:
                if exc.code != "platform_paused":
                    raise
            session.pause()
            if not auto_resume:
                raise ReaderError("platform_paused", "已按设置停在走神弹窗，未自动恢复。")
            if resume_count >= max_resumes:
                raise ReaderError("resume_limit", "已达到本次自动恢复次数上限，已停止。")
            pause_started = time.monotonic()
            moving = True
            try:
                resumed = self.resume_pause(expected=expected, cancel_event=cancel, deadline=deadline)
            finally:
                paused_seconds += max(0.0, time.monotonic() - pause_started)
            resume_count += 1
            moving = False
            return resumed, True

        try:
            self._check_stop(cancel)
            state, _ = obtain_state(expected_start)
            self._check_stop(cancel)
            if expected_start is not None and state != expected_start:
                raise ReaderError("stale_state", "开始前书名或页码已变化，请重新识别。")
            session.start(state)
            deadline = time.monotonic() + duration_seconds
            next_turn = time.monotonic() + turn_interval
            while True:
                self._check_stop(cancel, deadline)
                if on_update:
                    try:
                        on_update({"title": state.title, "page": state.page, "total": state.total,
                                   "elapsed_seconds": session.elapsed_seconds, "turns": turns,
                                   "resume_count": resume_count, "paused_seconds": paused_seconds})
                    except Exception as exc:
                        raise ReaderError("callback_error", "阅读状态显示失败，已停止。") from exc
                remaining = deadline - time.monotonic()
                wait = min(1.0, max(0, remaining))
                if max_turns:
                    wait = min(wait, max(0.0, next_turn - time.monotonic()))
                if cancel.wait(wait):
                    self._check_stop(cancel)
                self._check_stop(cancel, deadline)
                if max_turns and time.monotonic() >= next_turn:
                    moving = True
                    try:
                        state = self.move(1, cancel_event=cancel, expected=state, deadline=deadline)
                    except ReaderError as exc:
                        if exc.code != "platform_paused" or exc.action_sent:
                            raise
                        moving = False
                        state, _ = obtain_state(state)
                        session.start(state)
                        next_turn = time.monotonic() + turn_interval
                        continue
                    moving = False
                    session.observe(state)
                    turns += 1
                    if turns >= max_turns:
                        reason = "turn_limit"
                        break
                    next_turn = time.monotonic() + turn_interval
                else:
                    state, recovered = obtain_state(state)
                    if recovered:
                        session.start(state)
                        next_turn = time.monotonic() + turn_interval
                    else:
                        session.observe(state)
        except ReaderError as exc:
            reason = exc.code
            if moving:
                # A cancelled/uncertain in-flight move must not report the old
                # page as the new current page. The UI will require reinspection.
                state = None
        finally:
            session.pause()
        path = None
        if output_dir is not None:
            directory = Path(output_dir).expanduser()
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"reading-{uuid.uuid4().hex[:12]}.json"
            payload = {"source": "local", "platform_confirmed": False, "started_at_utc": started_utc,
                       "stop_reason": reason, "turns": turns, "elapsed_seconds": session.elapsed_seconds,
                       "resume_count": resume_count, "paused_seconds": paused_seconds,
                       "last_state": asdict(state) if state is not None else None}
            with path.open("x", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
        return ReadingResult(reason, turns, session.elapsed_seconds, state, path, resume_count, paused_seconds)

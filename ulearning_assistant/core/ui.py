"""Parse Android accessibility snapshots and export the visible page."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Iterable
import xml.etree.ElementTree as ET


class AdbError(RuntimeError):
    """An actionable device or page error, safe to display in the interface."""


MAX_XML_BYTES = 4 * 1024 * 1024
MAX_UI_NODES = 20_000
_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]\Z")


@dataclass(frozen=True)
class UiNode:
    text: str = ""
    description: str = ""
    resource_id: str = ""
    class_name: str = ""
    package: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    clickable: bool = False
    enabled: bool = True
    password: bool = False

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return ((left + right) // 2, (top + bottom) // 2)


def parse_ui_xml(xml: str | bytes) -> list[UiNode]:
    """Parse one hierarchy; password fields and their children are omitted.

    Only ordinary Android UI XML is accepted. DTD/entity declarations are
    unnecessary for accessibility snapshots and are rejected before parsing.
    """
    if not isinstance(xml, (str, bytes)):
        raise AdbError("页面数据必须是 XML 文本。")
    try:
        encoded = xml.encode("utf-8") if isinstance(xml, str) else xml
    except UnicodeError as exc:
        raise AdbError("页面数据包含无效字符，请重新读取页面。") from exc
    if not encoded or len(encoded) > MAX_XML_BYTES:
        raise AdbError("页面数据为空或超过 4 MB，请重新读取较小的页面。")
    try:
        decoded = encoded.decode("utf-8-sig")
    except UnicodeError as exc:
        raise AdbError("页面数据不是有效的 UTF-8 XML，请重新读取页面。") from exc
    if "<!DOCTYPE" in decoded.upper() or "<!ENTITY" in decoded.upper():
        raise AdbError("页面数据包含不支持的 XML 声明。")
    try:
        root = ET.fromstring(decoded)
    except (ET.ParseError, ValueError) as exc:
        raise AdbError("无法解析页面，请保持优学院页面打开后重新读取。") from exc
    if root.tag != "hierarchy":
        raise AdbError("没有读取到 Android 页面结构，请重新读取页面。")

    result: list[UiNode] = []
    visited = 0
    pending = [(root, False)]
    while pending:
        element, hidden = pending.pop()
        visited += 1
        if visited > MAX_UI_NODES:
            raise AdbError("页面控件过多，请重新读取较小的页面。")
        hidden = hidden or element.get("password", "false").lower() == "true"
        if hidden:
            continue
        pending.extend((child, hidden) for child in reversed(list(element)))
        if element.tag != "node":
            continue
        match = _BOUNDS.fullmatch(element.get("bounds", ""))
        if not match:
            continue
        if any(len(value) > 7 for value in match.groups()):
            continue
        bounds = tuple(int(value) for value in match.groups())
        left, top, right, bottom = bounds
        if left > right or top > bottom:
            continue
        result.append(UiNode(
            text=element.get("text", ""),
            description=element.get("content-desc", ""),
            resource_id=element.get("resource-id", ""),
            class_name=element.get("class", ""),
            package=element.get("package", ""),
            bounds=bounds,
            clickable=element.get("clickable", "false").lower() == "true",
            enabled=element.get("enabled", "true").lower() == "true",
            password=False,
        ))
    return result


def select_unique(
    nodes: Iterable[UiNode], *, text: str | None = None,
    resource_id: str | None = None, description: str | None = None,
) -> UiNode:
    """Find exactly one enabled, non-password node by exact field matching."""
    selectors = {"text": text, "resource_id": resource_id, "description": description}
    active = {name: value for name, value in selectors.items() if value is not None}
    if not active or any(not isinstance(value, str) or not value for value in active.values()):
        raise AdbError("请提供非空的文字、控件 ID 或描述作为匹配条件。")
    matches = [node for node in nodes if not node.password and node.enabled
               and all(getattr(node, name) == value for name, value in active.items())]
    if not matches:
        raise AdbError("当前页面没有唯一匹配的可用控件，请检查文字或控件 ID。")
    if len(matches) != 1:
        raise AdbError(f"匹配到 {len(matches)} 个控件，请增加控件 ID 或描述以精确定位。")
    return matches[0]


def _visible_nodes(nodes: Iterable[UiNode]) -> list[UiNode]:
    return [node for node in nodes if not node.password]


def export_json(nodes: Iterable[UiNode]) -> str:
    """Export the current snapshot; no account state or password nodes included."""
    return json.dumps([asdict(node) for node in _visible_nodes(nodes)],
                      ensure_ascii=False, indent=2) + "\n"


def _markdown_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def export_markdown(nodes: Iterable[UiNode]) -> str:
    """Export displayed text and labels as a small, readable Markdown table."""
    rows = ["# 当前页面资料", "", "| 文字 | 描述 | 控件 ID |", "| --- | --- | --- |"]
    seen: set[tuple[str, str, str]] = set()
    for node in _visible_nodes(nodes):
        if not node.text and not node.description:
            continue
        values = (node.text, node.description, node.resource_id)
        if values in seen:
            continue
        seen.add(values)
        rows.append("| " + " | ".join(_markdown_cell(value) for value in values) + " |")
    if not seen:
        rows.extend(["", "当前页面没有可导出的文字或描述。"])
    return "\n".join(rows) + "\n"

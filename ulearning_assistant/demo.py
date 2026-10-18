"""An entirely local, fictional device for trying the interface without Android."""

from __future__ import annotations

import base64

from .core import Device, UiNode

PACKAGE = "cn.ulearning.yxy"
_PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class DemoClient:
    """Implements the device methods used by the desktop interface and workflows."""

    serial = "demo-local"
    adb_path = "演示模式"

    def __init__(self) -> None:
        self.page = 0
        self.speed_open = False
        self.speed = "1.0倍"
        self.playing = False

    def devices(self) -> list[Device]:
        return [Device(serial=self.serial, state="device", model="本地演示")]

    def select_device(self) -> Device:
        return self.devices()[0]

    def version(self) -> str:
        return "本地演示，不会连接真实设备"

    def device_info(self) -> dict:
        return {"serial": self.serial, "model": "本地演示", "android_version": "演示"}

    def app_info(self, package: str = PACKAGE) -> dict:
        return {"package": package, "installed": True, "version_name": "演示"}

    def launch_app(self, package: str = PACKAGE) -> None:
        if package != PACKAGE:
            raise ValueError("演示模式只支持默认示例包名。")

    def dump_ui(self) -> list[UiNode]:
        titles = ["第一节：学习目标", "第二节：整理笔记", "第三节：复习计划"]
        body = [
            "这是一份虚构的演示资料。先理解概念，再用自己的语言总结。",
            "记录关键概念、例子和不懂的问题；资料导出只包含当前可见内容。",
            "完成阅读后，自行检查理解情况，并制定下一次复习计划。",
        ]

        def node(text: str, rid: str, top: int, *, clickable: bool = False) -> UiNode:
            return UiNode(
                text=text, description=text if clickable else "", resource_id=f"{PACKAGE}:id/{rid}",
                class_name="android.widget.Button" if clickable else "android.widget.TextView",
                package=PACKAGE, bounds=(30, top, 690, top + 80),
                clickable=clickable, enabled=True, password=False,
            )

        nodes = [node(titles[self.page], "title", 60), node(body[self.page], "body", 180)]
        nodes += [
            node("播放中" if self.playing else "播放", "play", 400, clickable=True),
            node("倍速", "speed", 500, clickable=True),
            node(self.speed, "current_speed", 590),
        ]
        if self.page < 2:
            nodes.append(node("下一页", "next", 800, clickable=True))
        if self.speed_open:
            nodes += [node("1.5倍", "speed_1_5", 680, clickable=True), node("2.0倍", "speed_2", 710, clickable=True)]
        return nodes

    def tap(self, x: int, y: int) -> None:
        # Resolve the selected row in the current demo scene, just like a real refresh.
        matches = [n for n in self.dump_ui() if n.center == (x, y) and n.clickable]
        if len(matches) != 1:
            raise ValueError("演示控件未找到。")
        text = matches[0].text
        if text == "下一页":
            self.page = min(self.page + 1, 2)
        elif text == "播放":
            self.playing = True
        elif text == "倍速":
            self.speed_open = True
        elif text in {"1.5倍", "2.0倍"}:
            self.speed = text
            self.speed_open = False

    def screenshot(self) -> bytes:
        return _PIXEL

    def keyevent(self, key: str) -> None:
        if key not in {"back", "home"}:
            raise ValueError("不支持的按键。")
        self.speed_open = False

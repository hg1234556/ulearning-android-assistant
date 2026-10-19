"""Command line and desktop entry points for the same local device services."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import threading

from . import __version__
from .core import AdbClient, AdbError, export_json
from .demo import DemoClient
from .ocr import OcrError
from .reader import ReaderError, VisualReader
from .workflows import Inspector, PageCollector, Profile, WorkflowError


def _options(parser: argparse.ArgumentParser, *, child: bool = False) -> None:
    default = argparse.SUPPRESS if child else None
    parser.add_argument("--adb", default=default, help="雷电自带的 adb.exe 路径")
    parser.add_argument("--serial", default=default, help="目标设备序列号，多设备时必须填写")
    parser.add_argument("--profile", default=default, help="自定义页面配置 JSON 路径")
    parser.add_argument("--demo", action="store_true", default=argparse.SUPPRESS if child else False,
                        help="使用虚构的本地演示设备，不连接模拟器")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="优学院安卓助手：连接、页面操作与资料导出")
    parser.add_argument("--version", action="version", version=__version__)
    _options(parser)
    commands = parser.add_subparsers(dest="command")
    for name, help_text in [
        ("gui", "打开中文桌面界面"), ("devices", "列出设备和在线状态"),
        ("doctor", "检查设备与优学院安装信息"), ("inspect", "读取当前目标页面的控件"),
        ("export", "导出当前页面的可见文字"), ("action", "预览或执行一个配置动作"),
        ("collect", "限定页数连续翻页采集"),
        ("reader", "求是读书：识别 PDF 页码、文档内翻页与本地计时"),
    ]:
        command = commands.add_parser(name, help=help_text)
        _options(command, child=True)
        if name == "export":
            command.add_argument("--output", type=Path, default=Path("exports"))
        elif name == "action":
            command.add_argument("name", help="例如 next_page 或 speed_menu")
            command.add_argument("--execute", action="store_true", help="预览并发送一次点击；默认只预览")
        elif name == "collect":
            command.add_argument("--output", type=Path, default=Path("exports"))
            command.add_argument("--pages", type=int, default=10)
            command.add_argument("--interval", type=float, default=3.0)
            command.add_argument("--timeout", type=float, default=120.0)
        elif name == "reader":
            command.add_argument("action", choices=("status", "next", "previous", "resume", "run"),
                                 help="识别、文档下一页、文档上一页、处理当前走神弹窗、开始计时并翻页")
            command.add_argument("--duration", type=float, default=300.0, help="本地总时长，默认 300 秒")
            command.add_argument("--interval", type=float, default=30.0, help="文档翻页间隔，默认 30 秒")
            command.add_argument("--turns", type=int, default=10, help="最多翻页次数，默认 10 次；0 表示只计时")
            command.add_argument("--output", type=Path, default=Path("exports"), help="本地阅读日志目录")
            command.add_argument("--no-auto-resume", action="store_true", help="运行中发现走神暂停时停止，不自动恢复")
            command.add_argument("--max-resumes", type=int, default=10, help="运行中最多恢复走神暂停的次数，默认 10 次")
    return parser


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    try:
        if options.command in {None, "gui"}:
            from .gui import main as gui_main
            gui_main(demo=options.demo, adb=options.adb, serial=options.serial, profile=options.profile)
            return 0
        if options.command == "reader" and options.demo:
            print("未完成：本地演示不支持视觉阅读器；请连接雷电并打开真实文档。", file=sys.stderr)
            return 2
        client = DemoClient() if options.demo else AdbClient(options.adb, serial=options.serial)
        if options.command == "devices":
            _print({"demo": options.demo, "devices": [asdict(d) for d in client.devices()]})
            return 0
        if options.command == "reader":
            reader = VisualReader(client)
            cancel = threading.Event()
            if options.action == "status":
                _print({"state": asdict(reader.inspect()), "platform_confirmed": False})
            elif options.action == "resume":
                state = reader.resume_pause(cancel_event=cancel)
                _print({"state": asdict(state), "resumed": True, "platform_confirmed": False})
            elif options.action in {"next", "previous"}:
                expected = reader.inspect()
                state = reader.move(1 if options.action == "next" else -1,
                                    cancel_event=cancel, expected=expected)
                _print({"state": asdict(state), "platform_confirmed": False})
            else:
                try:
                    result = reader.run(duration_seconds=options.duration, turn_interval=options.interval,
                                        auto_resume=not options.no_auto_resume, max_resumes=options.max_resumes,
                                        max_turns=options.turns, cancel_event=cancel, output_dir=options.output)
                except KeyboardInterrupt:
                    cancel.set()
                    raise
                _print({"stop_reason": result.stop_reason, "turns": result.turns,
                        "elapsed_seconds": result.elapsed_seconds,
                        "resume_count": result.resume_count, "paused_seconds": result.paused_seconds,
                        "last_state": asdict(result.last_state) if result.last_state else None,
                        "log_path": str(result.log_path), "platform_confirmed": False})
                if result.stop_reason not in {"duration_limit", "turn_limit", "cancelled", "page_boundary"}:
                    return 2
            return 0
        profile = Profile.load(options.profile)
        if options.command == "doctor":
            _print({"demo": options.demo, "adb": client.version(),
                    "device": client.device_info(), "app": client.app_info(profile.package)})
            return 0
        inspector = Inspector(client, profile)
        if options.command == "inspect":
            snapshot = inspector.refresh()
            print(export_json(snapshot.nodes))
        elif options.command == "export":
            result = inspector.collect(options.output)
            _print({"demo": options.demo, "title": result.title, "nodes": result.node_count,
                    "markdown": str(result.markdown_path), "json": str(result.json_path)})
        elif options.command == "action":
            preview = inspector.preview(options.name)
            if options.execute:
                inspector.apply(preview)
            _print({"demo": options.demo, "executed": options.execute, "preview": asdict(preview)})
        elif options.command == "collect":
            cancel = threading.Event()
            result = PageCollector(inspector).collect(options.output, max_pages=options.pages,
                interval=options.interval, max_duration=options.timeout, cancel_event=cancel)
            _print({"demo": options.demo, "count": result.count, "stop_reason": result.stop_reason,
                    "detail": result.detail, "elapsed": result.elapsed,
                    "files": [str(page.markdown_path) for page in result.pages]})
            if result.stop_reason not in {"max_pages", "max_duration", "cancelled", "repeated_page", "next_missing"}:
                return 2
        return 0
    except KeyboardInterrupt:
        print("已停止；已写入的资料文件会保留。", file=sys.stderr)
        return 130
    except ReaderError as exc:
        print(f"未完成：{exc}", file=sys.stderr)
        if exc.code == "platform_paused":
            print("已识别走神暂停，本次未点击；可使用 reader resume 处理当前弹窗。", file=sys.stderr)
        return 2
    except (AdbError, WorkflowError, OcrError, ValueError, OSError) as exc:
        print(f"未完成：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

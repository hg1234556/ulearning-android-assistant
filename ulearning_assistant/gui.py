"""Chinese Tk desktop interface; device work stays off the Tk event thread."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import sys
import threading
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from . import __version__
from .core import AdbClient
from .demo import DemoClient
from .reader import VisualReader
from .workflows import Inspector, PageCollector, Profile


ACTION_NAMES = {
    "next_page": "下一页", "play": "播放", "speed_menu": "打开倍速菜单",
    "speed_1_5": "选择 1.5 倍", "speed_2": "选择 2 倍",
}

READER_STOP_NAMES = {
    "duration_limit": "到达本地时限", "turn_limit": "到达翻页次数上限",
    "cancelled": "已手动停止", "page_boundary": "到达文档首页或末页",
    "wrong_app": "优学院已离开前台", "book_changed": "当前文档已变化",
    "stale_state": "书名或页码已变化", "layout_changed": "画面尺寸与校准不一致",
    "recognition_failed": "本地识别失败", "title_missing": "书名识别不明确",
    "page_missing": "页码识别不明确", "page_timeout": "单次翻页未获确认",
    "unexpected_page": "实际翻页超出单页范围", "device_error": "设备连接出错",
    "callback_error": "阅读状态显示失败", "configuration": "阅读参数或校准配置无效",
    "platform_paused": "平台提示走神，阅读已暂停", "resume_limit": "到达走神恢复次数上限",
    "resume_timeout": "恢复后未确认弹窗关闭", "pause_missing": "未识别到可处理的走神弹窗",
    "pause_ambiguous": "走神弹窗的恢复按钮不唯一或不可确认",
}


def data_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


class AssistantApp:
    def __init__(self, root: tk.Tk, *, demo: bool = False, adb: str | None = None,
                 serial: str | None = None, profile: str | None = None) -> None:
        self.root = root
        self.root.title(f"优学院 · 安卓助手 {__version__}")
        self.root.geometry("1180x800")
        self.root.minsize(960, 680)
        self.root.configure(bg="#eff3f8")
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.cancel_event = threading.Event()
        self.reader_cancel_event = threading.Event()
        self.reader_state = None
        self.client = None
        self.inspector = None
        self.snapshot = None
        self.preview = None
        self.nodes = []
        self.profile = Profile.load(profile)
        self.adb_var = tk.StringVar(value=adb or "")
        self.serial_var = tk.StringVar(value=serial or "")
        self.demo_var = tk.BooleanVar(value=demo)
        self.output_var = tk.StringVar(value=str(data_root() / "exports"))
        self.status_var = tk.StringVar(value="尚未连接 · 启动雷电后检测设备，或使用本地演示")
        self.action_var = tk.StringVar(value="next_page")
        self.preview_var = tk.StringVar(value="先预览定位，再执行操作。页面发生变化后需要重新预览。")
        self.page_limit = tk.StringVar(value="10")
        self.interval_var = tk.StringVar(value="3")
        self.timeout_var = tk.StringVar(value="120")
        self.reader_state_var = tk.StringVar(value="尚未识别 · 在优学院中打开文档后，点击“识别阅读器”")
        self.reader_progress_var = tk.StringVar(value="本地确认计时 0 秒 · 文档翻页 0 次 · 走神恢复 0 次 · 平台阅读学时未确认")
        self.reader_duration_var = tk.StringVar(value="300")
        self.reader_interval_var = tk.StringVar(value="30")
        self.reader_turns_var = tk.StringVar(value="10")
        self.reader_auto_resume_var = tk.BooleanVar(value=True)
        self.reader_max_resumes_var = tk.StringVar(value="10")
        self.task_buttons: list[ttk.Button] = []
        self.config_widgets: list[ttk.Widget] = []
        self._configure_style()
        self._build()
        self.root.after(80, self._drain)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        if demo:
            self.root.after(150, self.connect)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10), background="#eff3f8")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", foreground="#243247", background="#eff3f8")
        style.configure("Muted.TLabel", foreground="#627389")
        style.configure("TButton", padding=(12, 7))
        style.configure("Accent.TButton", foreground="#ffffff", background="#127f78")
        style.map("Accent.TButton", background=[("active", "#106d67"), ("disabled", "#9bacb7")])
        style.configure("Treeview", rowheight=29, background="#ffffff", fieldbackground="#ffffff")
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"), padding=8)
        style.configure("TNotebook.Tab", padding=(18, 9))

    def button(self, parent, text, command, *, accent=False, **pack) -> ttk.Button:
        button = ttk.Button(parent, text=text, command=command,
                            style="Accent.TButton" if accent else "TButton")
        button.pack(**pack)
        self.task_buttons.append(button)
        return button

    def _build(self) -> None:
        header = tk.Frame(self.root, bg="#182c43", padx=24, pady=17)
        header.pack(fill="x")
        tk.Label(header, text="优学院 · 安卓助手", fg="white", bg="#182c43",
                 font=("Microsoft YaHei UI", 21, "bold")).pack(side="left")
        tk.Label(header, text=f"APP 工作台  /  v{__version__}", fg="#a8c4d8", bg="#182c43",
                 font=("Microsoft YaHei UI", 11)).pack(side="right")
        main = ttk.Frame(self.root, padding=(20, 12))
        main.pack(fill="both", expand=True)
        connection = ttk.LabelFrame(main, text="设备连接", padding=10)
        connection.pack(fill="x", pady=(0, 10))
        row = ttk.Frame(connection)
        row.pack(fill="x")
        ttk.Label(row, text="ADB 程序").pack(side="left")
        self.adb_entry = ttk.Entry(row, textvariable=self.adb_var)
        self.adb_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.adb_entry.bind("<KeyRelease>", lambda _: self.invalidate_device())
        self.config_widgets.append(self.adb_entry)
        self.button(row, "选择…", self.browse_adb, side="left", padx=(0, 8))
        self.button(row, "检测设备", self.scan, side="left", padx=(0, 8))
        self.button(row, "连接", self.connect, accent=True, side="left")
        row2 = ttk.Frame(connection)
        row2.pack(fill="x", pady=(9, 0))
        ttk.Label(row2, text="选择设备").pack(side="left")
        self.device_combo = ttk.Combobox(row2, textvariable=self.serial_var, width=38, state="readonly")
        self.device_combo.pack(side="left", padx=8)
        self.config_widgets.append(self.device_combo)
        self.demo_check = ttk.Checkbutton(row2, text="本地演示（无需模拟器）", variable=self.demo_var,
                                         command=self.reset_connection)
        self.demo_check.pack(side="left", padx=10)
        self.config_widgets.append(self.demo_check)
        self.device_combo.bind("<<ComboboxSelected>>", lambda _: self.invalidate_device())
        self.button(row2, "打开优学院", self.launch_app, side="right")
        ttk.Label(main, textvariable=self.status_var, style="Muted.TLabel").pack(anchor="w", pady=(0, 10))

        notebook = ttk.Notebook(main)
        notebook.pack(fill="both", expand=True)
        page_tab = ttk.Frame(notebook, padding=12)
        action_tab = ttk.Frame(notebook, padding=14)
        reader_tab = ttk.Frame(notebook, padding=14)
        guide_tab = ttk.Frame(notebook, padding=18)
        notebook.add(page_tab, text="页面与资料")
        notebook.add(action_tab, text="操作与采集")
        notebook.add(reader_tab, text="求是读书")
        notebook.add(guide_tab, text="使用说明")

        toolbar = ttk.Frame(page_tab)
        toolbar.pack(fill="x", pady=(0, 10))
        self.button(toolbar, "读取当前页面", self.refresh_page, accent=True, side="left", padx=(0, 8))
        self.button(toolbar, "导出当前页", self.export_page, side="left", padx=(0, 8))
        self.button(toolbar, "保存截图", self.save_screenshot, side="left", padx=(0, 8))
        self.button(toolbar, "以选中控件预览操作", self.preview_selected, side="right")
        tree_wrap = ttk.Frame(page_tab)
        tree_wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_wrap, columns=("text", "description", "resource", "clickable"),
                                 show="headings", selectmode="browse")
        for key, title, width in [("text", "可见文字", 300), ("description", "辅助描述", 180),
                                  ("resource", "控件 ID", 300), ("clickable", "可点击", 65)]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=60)
        vscroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        hscroll = ttk.Scrollbar(tree_wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vscroll.grid(row=0, column=1, sticky="ns")
        hscroll.grid(row=1, column=0, sticky="ew")
        tree_wrap.rowconfigure(0, weight=1)
        tree_wrap.columnconfigure(0, weight=1)
        ttk.Label(page_tab, text="资料导出包含当前可见文字；未显示的内容和答案不会自动补全。",
                  style="Muted.TLabel").pack(anchor="w", pady=(8, 0))

        operation = ttk.LabelFrame(action_tab, text="单次页面操作", padding=12)
        operation.pack(fill="x")
        row = ttk.Frame(operation)
        row.pack(fill="x")
        self.action_combo = ttk.Combobox(row, textvariable=self.action_var,
                                        values=list(self.profile.actions), state="readonly", width=23)
        self.action_combo.pack(side="left", padx=(0, 9))
        self.action_combo.bind("<<ComboboxSelected>>", lambda _: self.clear_preview())
        self.config_widgets.append(self.action_combo)
        self.button(row, "预览定位", self.preview_action, side="left", padx=(0, 9))
        self.button(row, "执行预览操作", self.apply_action, accent=True, side="left", padx=(0, 9))
        self.button(row, "载入页面配置…", self.load_profile, side="right")
        ttk.Label(operation, textvariable=self.preview_var, wraplength=960,
                  style="Muted.TLabel").pack(anchor="w", pady=(12, 0))
        labels = "  ·  ".join(f"{key}：{ACTION_NAMES.get(key, key)}" for key in self.profile.actions)
        self.action_help = ttk.Label(operation, text=labels, wraplength=960, style="Muted.TLabel")
        self.action_help.pack(anchor="w", pady=(7, 0))

        collection = ttk.LabelFrame(action_tab, text="连续翻页采集", padding=12)
        collection.pack(fill="x", pady=(16, 0))
        row = ttk.Frame(collection)
        row.pack(fill="x")
        for text, variable in [("最多页数", self.page_limit), ("翻页间隔（秒）", self.interval_var),
                               ("总时限（秒）", self.timeout_var)]:
            ttk.Label(row, text=text).pack(side="left", padx=(0, 6))
            entry = ttk.Entry(row, textvariable=variable, width=7)
            entry.pack(side="left", padx=(0, 16))
            self.config_widgets.append(entry)
        self.button(row, "开始采集", self.start_collection, accent=True, side="left", padx=(0, 8))
        self.stop_button = ttk.Button(row, text="停止", command=self.stop_collection, state="disabled")
        self.stop_button.pack(side="left")
        ttk.Label(collection, text="从你打开的当前页开始。重复页、找不到唯一的下一页按钮或进入登录/测验等页面时停止。",
                  wraplength=970, style="Muted.TLabel").pack(anchor="w", pady=(10, 0))

        reader_status = ttk.LabelFrame(reader_tab, text="当前文档", padding=12)
        reader_status.pack(fill="x")
        ttk.Label(reader_status, textvariable=self.reader_state_var, wraplength=960,
                  font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        reader_row = ttk.Frame(reader_status)
        reader_row.pack(fill="x", pady=(12, 0))
        self.button(reader_row, "识别阅读器", self.inspect_reader, accent=True,
                    side="left", padx=(0, 8))
        self.button(reader_row, "文档上一页", lambda: self.move_reader(-1),
                    side="left", padx=(0, 8))
        self.button(reader_row, "文档下一页", lambda: self.move_reader(1), side="left")
        self.button(reader_row, "处理当前走神弹窗", self.resume_reader, side="left", padx=(8, 0))
        ttk.Label(reader_status,
                  text="使用 Windows 本地 OCR 识别书名与 PDF 页码，只操作文档上方工具栏内的翻页按钮。\n"
                       "已验证布局为 1920 × 1080；设备画面尺寸或页面变化后会停止。底部“下一页”可能切换书籍，请勿作为文档翻页。",
                  wraplength=960, style="Muted.TLabel").pack(anchor="w", pady=(12, 0))

        reader_session = ttk.LabelFrame(reader_tab, text="本地阅读计时与间隔翻页", padding=12)
        reader_session.pack(fill="x", pady=(16, 0))
        reader_row = ttk.Frame(reader_session)
        reader_row.pack(fill="x")
        for text, variable in [("总时长（秒）", self.reader_duration_var),
                               ("翻页间隔（秒）", self.reader_interval_var),
                               ("最多翻页次数", self.reader_turns_var)]:
            ttk.Label(reader_row, text=text).pack(side="left", padx=(0, 6))
            entry = ttk.Entry(reader_row, textvariable=variable, width=7)
            entry.pack(side="left", padx=(0, 14))
            self.config_widgets.append(entry)
        self.button(reader_row, "开始计时并翻页", self.start_reader, accent=True,
                    side="left", padx=(0, 8))
        self.reader_stop_button = ttk.Button(reader_row, text="停止读书", command=self.stop_reader,
                                            state="disabled")
        self.reader_stop_button.pack(side="left")
        pause_row = ttk.Frame(reader_session)
        pause_row.pack(fill="x", pady=(10, 0))
        auto_resume_check = ttk.Checkbutton(pause_row, text="自动处理走神暂停",
                                            variable=self.reader_auto_resume_var)
        auto_resume_check.pack(side="left", padx=(0, 16))
        self.config_widgets.append(auto_resume_check)
        ttk.Label(pause_row, text="最多恢复次数").pack(side="left", padx=(0, 6))
        resume_entry = ttk.Entry(pause_row, textvariable=self.reader_max_resumes_var, width=7)
        resume_entry.pack(side="left")
        self.config_widgets.append(resume_entry)
        ttk.Label(reader_session, textvariable=self.reader_progress_var,
                  wraplength=960).pack(anchor="w", pady=(12, 0))
        ttk.Label(reader_session,
                  text="开始前先识别阅读器，并保持当前文档打开。到达时限、次数上限、文档末页或识别不一致时停止。\n"
                       "最多翻页次数填 0 可只做本地计时。计时只累计成功识别之间的时间，日志不代表平台已确认阅读学时。\n"
                       "自动恢复仅处理识别明确的“走神”提示和唯一“继续学习”按钮，恢复后核对同书；暂停停留时间不计入本地阅读。\n"
                       "运行中请勿手动操作模拟器；需要操作时先点击“停止读书”。本地演示不支持此视觉阅读功能。",
                  wraplength=960, style="Muted.TLabel").pack(anchor="w", pady=(10, 0))

        guide_text = (
            "1. 启动雷电，手动登录优学院并打开想查看的课程页面。\n\n"
            "2. 检测设备 → 选择在线设备 → 连接 → 读取当前页面。\n\n"
            "3. 在“页面与资料”导出可见文字；在“操作与采集”预览按钮并执行，或连续翻页采集。\n\n"
            "4. 求是读书 PDF 请使用独立的“求是读书”标签：先识别书名与页码，再单步验证文档翻页。可按间隔翻页并记录本地阅读时间。\n\n"
            "倍速功能只选择 App 已提供的选项。默认控件名称是示例，真实页面名称不同时请载入自己的 JSON 配置。\n\n"
            "当前版本提供连接、控件操作与可见资料整理。自动答题、自动签到、完整题库、学时写入、互评和 Word/PDF 工具尚未实现。\n\n"
            "没有模拟器时，可以勾选“本地演示”试用；演示里的内容都是虚构示例。"
        )
        guide_links = ttk.Frame(guide_tab)
        guide_links.pack(side="bottom", fill="x", pady=(10, 0))
        self.button(guide_links, "打开 README", self.open_readme, side="left")
        self.button(guide_links, "打开 GitHub 仓库", lambda: webbrowser.open(
            "https://github.com/hg1234556/ulearning-android-assistant"), side="left", padx=12)
        guide_scroll = ttk.Scrollbar(guide_tab, orient="vertical")
        guide_scroll.pack(side="right", fill="y")
        guide_body = tk.Text(guide_tab, wrap="word", font=("Microsoft YaHei UI", 11),
                             bg="#eff3f8", fg="#243247", relief="flat", padx=5, pady=5,
                             yscrollcommand=guide_scroll.set)
        guide_body.pack(fill="both", expand=True)
        guide_body.insert("1.0", guide_text)
        guide_body.configure(state="disabled")
        guide_scroll.configure(command=guide_body.yview)

        output_row = ttk.Frame(main)
        output_row.pack(fill="x", pady=(12, 8))
        ttk.Label(output_row, text="保存位置").pack(side="left")
        out_entry = ttk.Entry(output_row, textvariable=self.output_var)
        out_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.config_widgets.append(out_entry)
        self.button(output_row, "选择…", self.browse_output, side="left", padx=(0, 8))
        self.button(output_row, "打开文件夹", self.open_output, side="left")
        self.log_box = tk.Text(main, height=5, wrap="word", font=("Microsoft YaHei UI", 9),
                               bg="#e4ebf3", fg="#304760", relief="flat", padx=10, pady=8, state="disabled")
        self.log_box.pack(fill="x")
        self.log("已就绪。第一次使用可先试本地演示。")

    def log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{datetime.now():%H:%M:%S}  {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _set_busy(self, value: bool) -> None:
        self.busy = value
        for button in self.task_buttons:
            button.configure(state="disabled" if value else "normal")
        for widget in self.config_widgets:
            widget.configure(state="disabled" if value else ("readonly" if isinstance(widget, ttk.Combobox) else "normal"))

    def run_task(self, label, work, success=None) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self.log(label)

        def worker():
            try:
                self.events.put(("done", (work(), success)))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "error":
                    self._set_busy(False)
                    self.stop_button.configure(state="disabled")
                    self.reader_stop_button.configure(state="disabled")
                    self.clear_reader_state()
                    self.log(f"未完成：{payload}")
                    messagebox.showerror("操作未完成", payload, parent=self.root)
                elif kind == "done":
                    result, callback = payload
                    self._set_busy(False)
                    self.stop_button.configure(state="disabled")
                    self.reader_stop_button.configure(state="disabled")
                    if callback:
                        try:
                            callback(result)
                        except Exception as exc:
                            self.log(f"结果显示失败：{exc}")
                elif kind == "reader_update":
                    self.reader_progress_var.set(
                        f"{payload.get('title', '')} · {payload.get('page', '?')} / {payload.get('total', '?')} 页 · "
                        f"本地确认计时 {payload.get('elapsed_seconds', 0):.1f} 秒 · "
                        f"文档翻页 {payload.get('turns', 0)} 次 · "
                        f"走神恢复 {payload.get('resume_count', 0)} 次 · "
                        f"暂停 {payload.get('paused_seconds', 0):.1f} 秒 · 平台阅读学时未确认")
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def reset_connection(self) -> None:
        self.client = self.inspector = self.snapshot = self.preview = None
        self.nodes = []
        self.clear_reader_state()
        self.tree.delete(*self.tree.get_children())
        self.device_combo.configure(values=[])
        self.serial_var.set("")
        self.status_var.set("演示模式 · 点击连接" if self.demo_var.get() else "尚未连接 · 请重新检测设备")

    def invalidate_device(self) -> None:
        self.client = self.inspector = self.snapshot = self.preview = None
        self.nodes = []
        self.clear_reader_state()
        self.tree.delete(*self.tree.get_children())
        self.status_var.set("设备已变更，请点击连接。")

    def clear_preview(self) -> None:
        self.preview = None
        self.preview_var.set("操作已变更，请重新预览定位。")

    def browse_adb(self) -> None:
        path = filedialog.askopenfilename(title="选择雷电自带的 adb.exe", filetypes=[("ADB", "adb.exe"), ("所有文件", "*")])
        if path:
            self.adb_var.set(path)
            self.client = self.inspector = self.preview = None
            self.clear_reader_state()
            self.status_var.set("ADB 程序已变更，请重新连接。")

    def browse_output(self) -> None:
        path = filedialog.askdirectory(title="选择资料保存文件夹")
        if path:
            self.output_var.set(path)

    def open_output(self) -> None:
        path = Path(self.output_var.get()).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)
        else:
            webbrowser.open(path.as_uri())

    def open_readme(self) -> None:
        path = data_root() / "README.md"
        webbrowser.open(path.as_uri() if path.exists() else "https://github.com/hg1234556/ulearning-android-assistant#readme")

    def scan(self) -> None:
        demo, adb = self.demo_var.get(), self.adb_var.get().strip() or None
        self.invalidate_device()
        def work():
            client = DemoClient() if demo else AdbClient(adb)
            return client, client.devices()
        def done(result):
            client, devices = result
            self.adb_var.set(str(client.adb_path))
            self.device_combo.configure(values=[d.serial for d in devices if d.state == "device"])
            online = [d for d in devices if d.state == "device"]
            self.serial_var.set(online[0].serial if len(online) == 1 else "")
            self.status_var.set("；".join(f"{d.serial} · {d.state} · {d.model}" for d in devices) or "未检测到设备，请启动雷电。")
            self.log(f"检测到 {len(devices)} 台设备，其中 {len(online)} 台在线。")
        self.run_task("检测设备…", work, done)

    def connect(self) -> None:
        demo = self.demo_var.get()
        adb = self.adb_var.get().strip() or None
        serial = self.serial_var.get().strip() or None
        profile = self.profile
        def work():
            client = DemoClient() if demo else AdbClient(adb, serial=serial)
            device = client.select_device()
            return client, device, client.device_info(), client.app_info(profile.package)
        def done(result):
            client, device, info, app = result
            self.client, self.inspector = client, Inspector(client, profile)
            self.preview = self.snapshot = None
            self.clear_reader_state()
            self.adb_var.set(str(client.adb_path))
            self.device_combo.configure(values=[device.serial])
            self.serial_var.set(device.serial)
            label = "本地演示" if demo else f"{device.serial} · Android {info.get('android_version', '?')}"
            app_label = f"优学院 {app.get('version_name', '')}" if app.get("installed") else "未找到配置中的 App"
            self.status_var.set(f"已连接 · {label} · {app_label}")
            self.log("连接成功。" + ("这是虚构演示设备。" if demo else "请手动打开目标课程页后读取页面。"))
            if demo:
                self.refresh_page()
        self.run_task("连接设备并检查应用…", work, done)

    def require_connection(self) -> bool:
        if self.inspector is None:
            messagebox.showinfo("尚未连接", "请先检测并连接设备。", parent=self.root)
            return False
        return True

    def launch_app(self) -> None:
        if self.require_connection():
            client, package = self.client, self.profile.package
            self.run_task("打开优学院…", lambda: client.launch_app(package), lambda _: self.log("已发送打开指令，请在 App 中打开目标页。"))

    def show_snapshot(self, snapshot) -> None:
        self.snapshot = snapshot
        self.preview = None
        self.preview_var.set("页面已读取；执行操作前请重新预览定位。")
        self.nodes = list(snapshot.nodes)
        self.tree.delete(*self.tree.get_children())
        for index, node in enumerate(self.nodes):
            if node.text or node.description or node.clickable:
                self.tree.insert("", "end", iid=str(index), values=(node.text, node.description,
                                 node.resource_id, "是" if node.clickable else ""))
        self.log(f"已读取 {len(self.nodes)} 个目标 App 控件。")

    def refresh_page(self) -> None:
        if self.require_connection():
            inspector = self.inspector
            self.run_task("读取当前页面…", inspector.refresh, self.show_snapshot)

    def export_page(self) -> None:
        if self.require_connection():
            inspector, output = self.inspector, Path(self.output_var.get())
            self.run_task("导出当前可见资料…", lambda: inspector.collect(output),
                          lambda r: self.log(f"导出完成：{r.markdown_path.name} 和 {r.json_path.name}"))

    def save_screenshot(self) -> None:
        if not self.require_connection():
            return
        if self.demo_var.get():
            self.log("演示模式没有真实设备截图；请连接模拟器后使用此功能。")
            return
        client, output = self.client, Path(self.output_var.get()) / "captures"
        def work():
            content = client.screenshot()
            output.mkdir(parents=True, exist_ok=True)
            path = output / f"screen-{datetime.now():%Y%m%d-%H%M%S-%f}.png"
            with path.open("xb") as file:
                file.write(content)
            return path
        self.run_task("保存设备截图…", work, lambda p: self.log(f"截图已保存：{p}"))

    def display_preview(self, preview) -> None:
        self.preview = preview
        node = preview.node
        self.preview_var.set(f"已定位：{node.text or node.description or node.resource_id}  /  区域 {node.bounds}。点击“执行预览操作”发送一次点击。")
        self.log("预览定位成功。")

    def preview_action(self) -> None:
        if self.require_connection():
            inspector, action = self.inspector, self.action_var.get()
            self.run_task("查找操作控件…", lambda: inspector.preview(action), self.display_preview)

    def preview_selected(self) -> None:
        if not self.require_connection():
            return
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("选择控件", "先读取页面，再选中一行控件。", parent=self.root)
            return
        node = self.nodes[int(selected[0])]
        selector = {key: value for key, value in {
            "resource_id": node.resource_id, "text": node.text, "description": node.description,
        }.items() if value}
        if not selector:
            self.log("这个控件没有可精确匹配的标识。")
            return
        inspector = self.inspector
        self.run_task("预览选中控件…", lambda: inspector.preview(selector), self.display_preview)

    def apply_action(self) -> None:
        if not self.require_connection():
            return
        if self.preview is None:
            messagebox.showinfo("需要预览", "请先预览定位；页面变化后请重新预览。", parent=self.root)
            return
        inspector, preview = self.inspector, self.preview
        self.preview = None
        def work():
            inspector.apply(preview)
            return inspector.refresh()
        self.run_task("执行单次操作并重读页面…", work, self.show_snapshot)

    def load_profile(self) -> None:
        path = filedialog.askopenfilename(title="载入 App 页面配置", filetypes=[("JSON 配置", "*.json")])
        if not path:
            return
        try:
            profile = Profile.load(path)
        except Exception as exc:
            messagebox.showerror("配置无法读取", str(exc), parent=self.root)
            return
        self.profile = profile
        self.action_combo.configure(values=list(profile.actions))
        self.action_var.set(next(iter(profile.actions), ""))
        self.action_help.configure(text="  ·  ".join(profile.actions))
        if self.client:
            self.inspector = Inspector(self.client, profile)
        self.preview = self.snapshot = None
        self.clear_reader_state()
        self.nodes = []
        self.tree.delete(*self.tree.get_children())
        self.log(f"已载入配置：{Path(path).name}；目标包名 {profile.package}")

    def start_collection(self) -> None:
        if not self.require_connection():
            return
        try:
            limit = int(self.page_limit.get())
            interval = float(self.interval_var.get())
            timeout = float(self.timeout_var.get())
            if not 1 <= limit <= 200 or not 0.2 <= interval <= 300 or not 1 <= timeout <= 3600:
                raise ValueError
        except ValueError:
            messagebox.showerror("参数有误", "页数应为 1–200；间隔 0.2–300 秒；总时限 1–3600 秒。", parent=self.root)
            return
        inspector, output = self.inspector, Path(self.output_var.get())
        self.cancel_event = threading.Event()
        event = self.cancel_event
        self.preview = None
        def work():
            return PageCollector(inspector).collect(output, max_pages=limit, interval=interval,
                max_duration=timeout, cancel_event=event,
                on_page=lambda page: self.events.put(("log", f"已保存：{page.markdown_path.name}")))
        def done(result):
            self.log(f"采集已停止，保存 {result.count} 页；原因：{result.detail or result.stop_reason}")
        self.run_task("开始采集当前页和后续页面…", work, done)
        self.stop_button.configure(state="normal")

    def stop_collection(self) -> None:
        self.cancel_event.set()
        self.stop_button.configure(state="disabled")
        self.log("已请求停止，当前设备读取结束后退出。")

    def clear_reader_state(self) -> None:
        self.reader_state = None
        self.reader_state_var.set("尚未识别 · 在优学院中打开文档后，点击“识别阅读器”")

    def require_reader(self) -> bool:
        if self.busy or not self.require_connection():
            return False
        if self.demo_var.get():
            self.log("本地演示暂不支持视觉阅读器；请连接雷电并打开真实文档。")
            return False
        return True

    def show_reader_state(self, state) -> None:
        self.reader_state = state
        self.reader_state_var.set(
            f"{state.title} · 第 {state.page} / {state.total} 页 · {state.width} × {state.height}")
        self.preview = None

    def inspect_reader(self) -> None:
        if not self.require_reader():
            return
        client = self.client
        self.clear_reader_state()
        def done(state):
            self.show_reader_state(state)
            self.log(f"已识别文档：{state.title}，第 {state.page} / {state.total} 页。")
        self.run_task("识别文档书名与 PDF 页码…", lambda: VisualReader(client).inspect(), done)

    def move_reader(self, direction: int) -> None:
        if not self.require_reader():
            return
        if self.reader_state is None:
            messagebox.showinfo("先识别阅读器", "请先点击“识别阅读器”，确认当前书名与页码。", parent=self.root)
            return
        client, expected = self.client, self.reader_state
        self.reader_cancel_event = threading.Event()
        event = self.reader_cancel_event
        def done(state):
            self.show_reader_state(state)
            self.log(f"文档翻页已核对：第 {state.page} / {state.total} 页。")
        label = "上一页" if direction < 0 else "下一页"
        self.run_task(f"核对当前文档并翻到文档{label}…",
                      lambda: VisualReader(client).move(direction, cancel_event=event, expected=expected), done)
        self.reader_stop_button.configure(state="normal")

    def resume_reader(self) -> None:
        if not self.require_reader():
            return
        client, expected = self.client, self.reader_state
        self.reader_cancel_event = threading.Event()
        event = self.reader_cancel_event
        def done(state):
            self.show_reader_state(state)
            self.log(f"走神弹窗已处理并核对文档：{state.title}，第 {state.page} / {state.total} 页。")
        self.run_task("识别当前走神弹窗并尝试单次恢复…",
                      lambda: VisualReader(client).resume_pause(expected=expected, cancel_event=event), done)
        self.reader_stop_button.configure(state="normal")

    def start_reader(self) -> None:
        if not self.require_reader():
            return
        if self.reader_state is None:
            messagebox.showinfo("先识别阅读器", "请先点击“识别阅读器”，确认当前书名与页码。", parent=self.root)
            return
        try:
            duration = float(self.reader_duration_var.get())
            interval = float(self.reader_interval_var.get())
            turns = int(self.reader_turns_var.get())
            max_resumes = int(self.reader_max_resumes_var.get())
            if not 1 <= duration <= 3600 or not 1 <= interval <= 300 or not 0 <= turns <= 200:
                raise ValueError
            if not 0 <= max_resumes <= 100:
                raise ValueError
        except ValueError:
            messagebox.showerror("参数有误", "总时长应为 1–3600 秒；间隔 1–300 秒；最多翻页次数 0–200，0 表示只计时；最多恢复次数 0–100。", parent=self.root)
            return
        auto_resume = self.reader_auto_resume_var.get()
        client, expected = self.client, self.reader_state
        output = Path(self.output_var.get())
        self.reader_cancel_event = threading.Event()
        event = self.reader_cancel_event
        self.preview = None
        self.reader_progress_var.set("开始核对当前文档 · 平台阅读学时未确认")
        def work():
            reader = VisualReader(client)
            return reader.run(duration_seconds=duration, turn_interval=interval, max_turns=turns,
                              auto_resume=auto_resume, max_resumes=max_resumes, expected_start=expected,
                              cancel_event=event, output_dir=output,
                              on_update=lambda update: self.events.put(("reader_update", update)))
        def done(result):
            if result.last_state is not None and result.stop_reason in {
                    "duration_limit", "turn_limit", "cancelled", "page_boundary"}:
                self.show_reader_state(result.last_state)
            else:
                self.clear_reader_state()
            self.reader_progress_var.set(
                f"已停止 · 本地确认计时 {result.elapsed_seconds:.1f} 秒 · 文档翻页 {result.turns} 次 · "
                f"走神恢复 {result.resume_count} 次 · 暂停 {result.paused_seconds:.1f} 秒 · 平台阅读学时未确认")
            reason = READER_STOP_NAMES.get(result.stop_reason, result.stop_reason)
            self.log(f"读书已停止：{reason}；日志：{result.log_path}")
        self.run_task("开始本地阅读计时与文档内间隔翻页…", work, done)
        self.reader_stop_button.configure(state="normal")

    def stop_reader(self) -> None:
        self.reader_cancel_event.set()
        self.reader_stop_button.configure(state="disabled")
        self.log("已请求停止读书；当前识别结束后退出，不再发起后续翻页。")

    def _close(self) -> None:
        self.cancel_event.set()
        self.reader_cancel_event.set()
        self.root.destroy()


def main(*, demo: bool = False, adb: str | None = None, serial: str | None = None,
         profile: str | None = None) -> None:
    root = tk.Tk()
    AssistantApp(root, demo=demo, adb=adb, serial=serial, profile=profile)
    root.mainloop()

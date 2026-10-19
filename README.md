# 优学院 Android 助手

面向 Windows 与雷电模拟器的中文桌面工具，通过 ADB 读取优学院当前页面、保存截图、导出可见文字，并按配置执行页面操作。新增独立的“求是读书”PDF 阅读器适配，支持识别书名和页码、文档内单步翻页与本地计时。代码独立实现，Python 侧仅使用标准库；视觉阅读功能调用 Windows 自带的本地 OCR。

**2026-10-19 已在雷电、Android 14、优学院 1.9.85 的真实《资本论》文档中验证 `1 / 376 → 2 / 376 → 1 / 376`。** 这只证明当前布局的 PDF 内部单页翻动成功；全书连续翻页、其他文档与平台阅读学时仍未确认。普通页面操作的默认选择器仍是适配起点，演示和自动化测试不能代替真实页面验证。

## 功能状态

| 功能 | 状态 | 说明 |
| --- | --- | --- |
| 中文桌面界面、离线演示 | 已实现通用框架 | 可在连接模拟器前查看界面和导出格式；演示内容为虚构数据 |
| ADB 查找、设备列表、连接诊断 | 已实现通用框架 | 支持指定雷电自带 `adb.exe` 和设备序列号 |
| 页面控件读取、PNG 截图 | 已实现通用框架 | 读取当前界面的 Android UI 层级；能否读到课程正文取决于 App 页面实现 |
| Markdown、JSON 可见文字导出 | 已实现通用框架 | 保存控件中可读取的文字，不代表已取得整门课程资料或隐藏内容 |
| 精确匹配控件、操作预览、点击 | 已实现通用框架 | 按 `text`、`resource_id` 或 `description` 配置选择器 |
| 限制页数、超时、可取消的连续翻页采集 | 已实现通用框架 | 需要先适配并验证“下一页”按钮 |
| 求是读书书名、PDF 页码识别 | 已完成当前布局实测 | Windows 本地 OCR，已验证《资本论》1 / 376 与 2 / 376 |
| 求是读书文档内上一页、下一页 | 已完成当前布局单步实测 | 校准分辨率 1920 × 1080，只操作 PDF 工具栏；尺寸或书名页码变化时停止 |
| 求是读书本地计时、间隔翻页、日志 | 已通过短时、单次定时翻页和停止实测 | 长时间运行仍需验证；0 次表示只计时；日志明确 `platform_confirmed: false` |
| 求是读书走神暂停恢复 | 已实现，实际验证范围见适配记录 | 支持手动处理当前弹窗，定时运行中默认自动处理；只点击明确识别的唯一“继续学习”按钮，恢复后核对同书 |
| 优学院下一页、播放、倍速菜单 | 待真实课程页面验证 | 默认配置只是适配起点，尚未匹配当前 App |
| 1.5 倍、2 倍播放 | 待真实课程页面验证 | 只能点击 App 实际提供且可识别的选项，不能保证任意倍速 |
| 全自动答题、答案接口、自动签到 | 未实现 | 不包含这些流程 |
| 后台学时写入、互评身份还原 | 未实现 | 不包含这些流程 |
| Word、PDF 资料处理 | 未实现 | 当前导出为 Markdown、JSON 与 PNG |

原网页用户脚本依赖浏览器环境，本项目不直接把它注入 Android App。参考项目与版权说明见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 启动

需要 Windows、带 Tkinter 的 Python 3.11 或更新版本，以及雷电模拟器自带的 ADB。Python 官方 Windows 安装程序通常包含 Tkinter。

“求是读书”视觉识别另需 Windows 10 或更新版本、Windows PowerShell 5.1，以及系统已经安装的可用 OCR 语言（本次使用中文环境）。识别在本机执行，不上传截图。缺少 OCR 时会给出错误，不会自动安装系统组件。OCR 桥接脚本随包提供，路径为 `ulearning_assistant/assets/ocr-image.ps1`。

在仓库目录打开终端：

```powershell
python -m ulearning_assistant gui
```

也可以双击 `start.cmd`。首次适配建议先使用演示模式：

```powershell
python -m ulearning_assistant --demo gui
```

演示模式使用标明“虚构”的内置数据，不表示已连接真实设备或读取你的课程。兼容以下写法：

```powershell
python -m ulearning_assistant gui --demo
```

## 连接雷电

1. 启动雷电模拟器，进入优学院，手动打开要检查的课程页面。
2. 在雷电设置中启用 ADB 本地调试；具体名称随雷电版本变化。
3. 运行连接诊断，确认设备状态为 `device`。

```powershell
python -m ulearning_assistant devices
python -m ulearning_assistant doctor
```

自动查找失败时，指定实际的 ADB 路径。下面的路径只是示例，请按安装位置修改：

```powershell
python -m ulearning_assistant --adb "C:\leidian\LDPlayer14\adb.exe" devices
```

有多台设备时，使用 `devices` 输出中的序列号明确选择目标：

```powershell
python -m ulearning_assistant --serial emulator-5554 inspect
python -m ulearning_assistant --serial emulator-5554 export --output exports
```

`offline` 表示 ADB 尚不能与设备正常通信。先等待模拟器完全启动，再检查本地调试设置及 ADB 路径，重新运行诊断。这个状态下无法验证 App 控件或课程操作。

## 求是读书

1. 手动在优学院中打开“求是读书”的文档，保持 PDF 工具栏和顶部书名可见。
2. 启动或重新启动助手，检测设备并连接，切换到 **“求是读书”** 标签。
3. 点击 **“识别阅读器”**，核对显示的书名与 `当前页 / 总页数`。如果当前已出现走神暂停弹窗，先点 **“处理当前走神弹窗”**，该操作不要求已有识别结果。
4. 先点击一次 **“文档下一页”**，确认 PDF 页码增加 1，再用 **“文档上一页”** 验证返回。
5. 需要本地计时时，设置总时长、间隔和最多翻页次数，点击 **“开始计时并翻页”**。默认 300 秒、每 30 秒一次、最多 10 次；最多次数填 0 可以只计时。**“自动处理走神暂停”默认勾选**，最多恢复次数默认 10；取消勾选后，发现走神暂停就会停止。
6. 需要操作模拟器时先点 **“停止读书”**。到时、次数用完、到达文档边界或识别不一致时也会停止。

**文档上方灰色 PDF 工具栏的箭头才是文档内部翻页。底部“下一页”可能切到下一本书。** 本功能只使用已校准的 PDF 工具栏位置，默认要求设备截图为 1920 × 1080；变更模拟器分辨率后需要重新适配。它不使用 `profiles/default.json` 的通用 `next_page` 选择器，也不会在本书末页切换书籍。

当前阅读页面在 Android UI 层级中只有 WebView，普通“读取当前页面”无法取得 PDF 正文。因此本功能另行对设备截图做本地 OCR，只识别顶部书名和工具栏页码；不代表已经导出书籍正文。

走神恢复会先确认真实暂停提示和唯一的“继续学习”按钮，再发送一次点击；弹窗消失后必须确认仍为同一本书。找不到唯一目标、恢复次数用完或恢复后状态不明确时停止，不会对其他弹窗猜测点击。单独的“识别阅读器”或 `reader status` 只报告当前暂停状态，不会恢复；手动恢复按钮与定时运行才会执行恢复操作。

本地计时只累计同一本书成功识别之间的时间，**走神弹窗的暂停停留时间不计入**，失败或停止前未确认的一段也可能不计入。日志保存在所选保存目录，文件名为 `reading-*.json`，包含翻页次数、恢复次数 `resume_count`、停止原因和 `platform_confirmed: false`。`paused_seconds` 只记录助手本次观察到的恢复过程耗时，不包含启动助手前弹窗已经停留的时间。**这些数字不是平台认可的阅读学时，本次也未验证平台学时增加。** 全书持续运行仍需单独验证。本地演示不支持视觉阅读器，其他演示功能可继续使用。

命令行提供相同服务：

```powershell
python -m ulearning_assistant reader status
python -m ulearning_assistant reader next
python -m ulearning_assistant reader previous
python -m ulearning_assistant reader resume
python -m ulearning_assistant reader run --duration 300 --interval 30 --turns 10 --output exports
python -m ulearning_assistant reader run --duration 60 --turns 0 --output exports
python -m ulearning_assistant reader run --max-resumes 5 --output exports
python -m ulearning_assistant reader run --no-auto-resume --output exports
```

可同时指定 `--adb` 和 `--serial`；`Ctrl+C` 停止命令行任务。`reader resume` 只处理当前一次走神弹窗；`reader run` 默认最多自动恢复 10 次，`--no-auto-resume` 可关闭。识别不明确时不重复发送点击，重新打开文档并识别后再操作。

## 页面操作与导出

在中文界面中检查设备连接，读取当前页面，然后保存截图或导出文字。先通过页面读取结果确认需要的内容确实可见，再配置操作。

`profiles/default.json` 提供 `next_page`、`play`、`speed_menu`、`speed_1_5` 和 `speed_2` 五个操作的初始选择器。它们尚未经过真实优学院页面匹配。按 [适配说明](docs/ADAPTATION.md) 修改配置，在预览确认唯一目标后执行点击。

连续翻页采集需要设置页数和超时；过程中可取消。先以少量页面验证，再增加采集范围。页面结构变化、控件不唯一或下一页不可识别时，应停止并更新配置。

CLI 可单独读取和导出当前页：

```powershell
python -m ulearning_assistant inspect
python -m ulearning_assistant export --output exports
python -m ulearning_assistant --demo export --output exports/demo
```

`--adb`、`--serial`、`--profile` 和 `--demo` 可以放在子命令前或后。完整选项以 `python -m ulearning_assistant --help` 和各子命令 `--help` 为准。

也可以在命令行预览操作或限定页数采集：

```powershell
python -m ulearning_assistant --demo action next_page
python -m ulearning_assistant --demo collect --pages 3 --interval 0.5 --output exports/demo
```

`action` 默认只预览，添加 `--execute` 才会发送一次点击。预览后页面变化会使预览失效；播放器计时不断变化时，也可能需要先暂停播放再定位。自动采集只使用精确文字为“下一页”的导航按钮，可以同时补充控件 ID。

导出默认放在 `exports/`。该目录与截图、日志、本地配置目录已加入 `.gitignore`。截图和页面导出可能包含姓名、课程或题目等个人内容，请保留在本机；提交前用 `git status` 检查实际文件。本项目不需要把账号、密码、Cookie 或令牌写入仓库。

## 开发与验证

运行自动化测试：

```powershell
python -m unittest discover -s tests -v
```

GitHub Actions 在 Ubuntu、Windows 和 Python 3.11、3.12 上运行测试。测试主要验证代码行为；真实设备连接、课程页面选择器、播放器行为仍需在雷电中分别检查。

原有通用界面离线演示已复核。“求是读书”还通过真实书名页码识别、单步往返、单次定时间隔翻页、短时本地计时与停止测试。详细条件和结果见 [阅读器验证记录](docs/READER_VALIDATION.md)。其余课程按钮、播放器、走神恢复与长时间阅读效果以实际记录的范围为准，不能由模拟测试推断平台学时。

可在 GitHub 的 **Actions → Build Windows → Run workflow** 手动构建 Windows 单文件程序。工作流使用 PyInstaller，包含本地 OCR 桥接资源，并上传 `ULearningAssistant.exe` 构建产物；它不会自动发布 Release。新构建首次成功前，不应视为已完成 0.2.1 可执行文件验证。

## 许可

本项目采用仓库所有者建仓时选定的 [MIT License](LICENSE)。第三方项目的源码未并入本仓库。

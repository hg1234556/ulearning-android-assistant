# 优学院 Android 助手

面向 Windows 与雷电模拟器的中文桌面工具，通过 ADB 读取优学院当前页面、保存截图、导出可见文字，并按配置执行页面操作。代码独立实现，运行时仅使用 Python 标准库，不需要安装 pip 依赖。

这是一个初始适配版本。**尚未完成真实优学院课程页面验证。** 初始联调曾检测到 `offline`，提交前未检测到在线设备。仓库中的演示数据、自动化测试和默认选择器，均不能代替实机验证。

## 功能状态

| 功能 | 状态 | 说明 |
| --- | --- | --- |
| 中文桌面界面、离线演示 | 已实现通用框架 | 可在连接模拟器前查看界面和导出格式；演示内容为虚构数据 |
| ADB 查找、设备列表、连接诊断 | 已实现通用框架 | 支持指定雷电自带 `adb.exe` 和设备序列号 |
| 页面控件读取、PNG 截图 | 已实现通用框架 | 读取当前界面的 Android UI 层级；能否读到课程正文取决于 App 页面实现 |
| Markdown、JSON 可见文字导出 | 已实现通用框架 | 保存控件中可读取的文字，不代表已取得整门课程资料或隐藏内容 |
| 精确匹配控件、操作预览、点击 | 已实现通用框架 | 按 `text`、`resource_id` 或 `description` 配置选择器 |
| 限制页数、超时、可取消的连续翻页采集 | 已实现通用框架 | 需要先适配并验证“下一页”按钮 |
| 优学院下一页、播放、倍速菜单 | 待真实课程页面验证 | 默认配置只是适配起点，尚未匹配当前 App |
| 1.5 倍、2 倍播放 | 待真实课程页面验证 | 只能点击 App 实际提供且可识别的选项，不能保证任意倍速 |
| 全自动答题、答案接口、自动签到 | 未实现 | 不包含这些流程 |
| 后台学时写入、互评身份还原 | 未实现 | 不包含这些流程 |
| Word、PDF 资料处理 | 未实现 | 当前导出为 Markdown、JSON 与 PNG |

原网页用户脚本依赖浏览器环境，本项目不直接把它注入 Android App。参考项目与版权说明见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 启动

需要 Windows、带 Tkinter 的 Python 3.11 或更新版本，以及雷电模拟器自带的 ADB。Python 官方 Windows 安装程序通常包含 Tkinter。

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

当前本地验证：45 项自动化测试通过；中文界面在离线演示下通过连接、页面读取、操作预览、播放、资料导出和三页连续采集检查。以上不代表真实课程页验证通过。

可在 GitHub 的 **Actions → Build Windows → Run workflow** 手动构建 Windows 单文件程序。工作流使用 PyInstaller，并上传 `ULearningAssistant.exe` 构建产物；它不会自动发布 Release。工作流首次成功前，不应视为已完成可执行文件验证。

## 许可

本项目采用仓库所有者建仓时选定的 [MIT License](LICENSE)。第三方项目的源码未并入本仓库。

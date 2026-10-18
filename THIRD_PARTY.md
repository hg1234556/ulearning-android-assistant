# 参考项目与第三方说明

本项目独立实现 ADB 调用、Android 页面控件解析、中文桌面界面和资料导出。运行时使用 Python 标准库，没有复制、合并或捆绑以下项目的源码。

| 参考资料 | 用途 |
| --- | --- |
| [Android Debug Bridge 官方文档](https://developer.android.com/tools/adb) | ADB 设备通信与命令说明 |
| [BrocadeHutHost/DGUT-ULearningTakeQuizzesAssistant](https://github.com/BrocadeHutHost/DGUT-ULearningTakeQuizzesAssistant) | 了解原网页用户脚本的目标功能；本项目不是该脚本的 Android 直接移植 |
| [openatx/uiautomator2](https://github.com/openatx/uiautomator2) | Android UI 自动化技术参考；本项目当前不依赖该库 |
| [SuperMonster003/AutoJs6](https://github.com/SuperMonster003/AutoJs6) | Android 端脚本自动化方案参考；本项目当前不依赖该应用 |

以上项目的名称、源码、文档及商标归各自权利人所有，具体使用条件以各项目自身的许可及说明为准。本说明不对第三方授予任何额外权利。

ADB 由用户现有的雷电安装或 Android SDK 提供，本仓库不分发 ADB 二进制文件。优学院、雷电模拟器与本项目没有隶属或官方合作关系。

Windows 构建工作流会安装 [PyInstaller](https://pyinstaller.org/) 作为构建工具；它不是源码运行依赖。打包所需组件及分发条件以 PyInstaller 的官方许可说明为准。生成的构建产物仍需由使用者检查和验证。

本项目的原创源码采用仓库所有者建仓时选定的 [MIT License](LICENSE)。若今后引入第三方代码，应在合并前核对许可、保留要求的声明，并更新本文件。

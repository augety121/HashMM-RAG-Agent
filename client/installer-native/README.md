# HashMM 原生安装器（C++/Qt6 · MinGW）→ 单个 exe

> 脚本（.bat）提示是英文——中文版 Windows 的 cmd 用 GBK 读 .bat，写中文会乱码。每个脚本下面有中文说明。

对标微信：**最终产物是一个 `HashMM-Setup.exe`**，用户下载下来双击就装，不是 zip。

## 为什么需要"自解压外壳"

Qt 程序运行依赖 Qt 的 DLL，所以 Qt 安装器本身没法是单个 exe（一启动就要找 DLL）。
真正的单exe安装器（微信也是这么做的）= 一个**不依赖 Qt 的小外壳程序**，内部塞着
{Qt安装器 + Qt DLL + 程序本体 app}，双击后自动解压到临时目录、再运行真正的安装器。
本项目的 `bootstrap/bootstrap.c` 就是这个外壳（纯 Win32，能编译成单文件）。

---

## ⭐ 一键出单 exe：双击 `build-all.bat`

它会自动完成 6 步，最后产出**一个** `HashMM-Setup.exe`：
```
1 编译 Qt 安装器  2 拷 Qt DLL  3 构建程序本体 app
4 组装 payload    5 编译自解压外壳(gcc)  6 打包成单个 HashMM-Setup.exe
```
- 全程英文进度（`[1/6]`/`[OK]`/`DONE`），慢的是第 3 步构建 app（几分钟），**耐心等到 DONE**。
- 完事后 `installer-native\HashMM-Setup.exe` 就是**发给用户的安装包**——一个 exe，双击即装。
- Qt 默认在 C:\Qt 自动找到；不在的话用记事本改 build-all.bat 顶部 `QT_ROOT`。

> 用户双击这个 exe → 自动解压到 %TEMP%\HashMMSetup\ → 弹出 Qt 安装界面（有最小化/关闭按钮、
> 用 app 里的服务条款/隐私政策）→ 点"立即安装"装到所选位置。

---

## 只想快速看安装界面长啥样（不打整包）

先在 Qt Creator 里点绿色锤子编译，或双击 `run-first.bat`（补 DLL + 直接开窗口）。
这只是预览 UI；要发给用户的单 exe 用 `build-all.bat`。

---

## ⚠️ 实话（必读）

- 自解压外壳 `bootstrap.c` + `pack.ps1` 是**新写的 Win32 代码**，我在 Linux 沙箱**没法编译/测试**
  （没有 Windows）。它用你的 MinGW gcc 编译（build-all.bat 第 5 步），字节格式我反复核对过，
  但**首次构建可能要微调一两处**。有报错把命令行红字发我，我改。
- 单 exe 目前**未压缩**（为可靠，外壳没塞 zip 库），所以 exe 较大（约 540MB，跟程序本体差不多）。
  微信压到 ~200MB；要压缩我后面可以加（外壳里集成 miniz 解压）。你之前说不在乎大小，先求能用。
- 这是把"安装器"原生化 + 单 exe 化；app 本体仍是 Electron。

---

## 文件
- `bootstrap/bootstrap.c` — Win32 自解压外壳（读自身尾部 payload → 解压到 %TEMP% → 运行 Qt 安装器）
- `bootstrap/pack.ps1` — 把 payload 追加进外壳，生成单个 exe
- `install_engine.h/.cpp` — 安装逻辑（对照已测 install-engine.js）
- `InstallerWindow.h/.cpp` — 自绘三态窗口（最小化/关闭按钮 + 圆角 + app 的条款/隐私）
- `main.cpp` — 入口 + --uninstall 卸载
- `CMakeLists.txt` — Qt6 Widgets + Concurrent
- `build-all.bat` — 一键出单 exe（首选）
- `run-first.bat` — 预览 UI（补 DLL + 开窗口）
- `build.bat` — 仅命令行编译 Qt 安装器（不打包）

## 行为对齐（都已在 JS 侧单测过）
已装检测 / 重装识别 / 卸载(开始菜单+控制面板, 保留 HashMM Files + local-backend) / 不改系统环境。

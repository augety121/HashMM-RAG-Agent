# HashMM 桌面端 · Windows 打包指南（诚实版）

> 你在 autodl 的 **Ubuntu 容器**里跑 `npm run dist:win` 撞了两个问题，本文讲清根因
> 和**真正可行**的三条路。先读"为什么"，再选一条照做。

## 你撞到的两个坑（已修第一个）

1. **`configuration has an unknown property '//'`** ✅ 已修
   electron-builder 24 对 `package.json` 的 `build` 段做严格 schema 校验，**任何未知键
   都报错退出**——我之前塞的 `"//"` 注释键正中此坑。现已把打包配置抽到独立的
   `electron-builder.yml`（yaml 能写 `#` 注释，且不受那个 schema 洁癖约束）。

2. **Ubuntu 容器打 Windows 包 + node-pty = 走不通**（平台硬约束，不是命令问题）
   - electron-builder 在 Linux 上打 Windows 包，要靠 **Wine + mono** 跑 Windows 工具链；
   - 但本项目带 **node-pty 原生模块**——它需要一份 **Windows 平台编译的 `.node` 二进制**；
   - Linux 容器里没有 MSVC/Windows SDK，**编不出 Windows 版 node-pty**，Wine 也救不了
     原生模块。你日志里 `rebuilding native dependencies ... platform=linux arch=x64`
     就是证据：它只会编出 **Linux** 版二进制，装到 Windows 上必崩。
   - 结论：**带终端的完整版 Windows exe，必须在 Windows 机器上打**。这不是配置能绕过的。

---

## 路线 A（推荐）：在你的 Windows 笔记本上打完整版

这是唯一能产出"带内嵌终端、能跑 claude/codex"的 Windows exe 的正路。

前置（一次性）：
1. 装 [Node.js LTS](https://nodejs.org)（≥18），安装时**勾选"自动安装必要工具"**
   （会带上 VS Build Tools + Python——node-pty 编译要用）。
2. 把 `desktop/` 整个目录拷到 Windows（U盘/网盘/`scp` 都行）。

打包（PowerShell，在 desktop 目录）：
```powershell
$env:ELECTRON_MIRROR="https://npmmirror.com/mirrors/electron/"                       # 国内加速
$env:ELECTRON_BUILDER_BINARIES_MIRROR="https://npmmirror.com/mirrors/electron-builder-binaries/"
npm install
npm run rebuild            # 在 Windows 上编译 node-pty（关键：产出 Windows .node）
npm run dist:win
#   产物：desktop\dist\HashMM-Setup-1.2.0.exe  ← 拷给任何 Windows 用户双击即装
```
> 若 `npm run rebuild` 报缺 C++ 编译器：管理员开 PowerShell 跑
> `npm install --global windows-build-tools`（旧法）或装 Visual Studio Build Tools
> 勾选"使用 C++ 的桌面开发"。

---

## 路线 B：在 Ubuntu 容器里打"纯瘦客户端"Windows 包（无终端，能成）

如果你暂时拿不到 Windows 机器，又想先要一个**能连后端、能用 RAG-Agent** 的 exe——
去掉 node-pty（终端面板会自动降级提示），就没有原生模块了，**Ubuntu + Wine 能打出
Windows 包**。

容器里装 Wine（Debian/Ubuntu）：
```bash
dpkg --add-architecture i386 && apt-get update
apt-get install -y wine wine32 wine64 mono-complete   # 或 winehq-stable
```
然后用我准备的脚本一键打"无终端版"：
```bash
cd desktop
bash build-win-thin.sh        # 见下方脚本：临时摘掉 node-pty → Wine 打 win 包 → 还原
#   产物：desktop/dist/HashMM-Setup-1.2.0.exe（瘦客户端，无内嵌终端）
```
> 这个 exe 拷到 Windows 能装能用（连后端、对话、检索、设计技能全在），只是没有
> "终端驾驶舱"。等你有 Windows 机器了再用路线 A 打完整版覆盖。

---

## 路线 C：GitHub Actions 云打包（零本地环境，最省心）

不想装任何东西、还要完整版？用 GitHub 的 Windows runner 在云上打：
1. 把 `desktop/` 推到一个 GitHub 仓库；
2. 用我放的 `.github/workflows/build-win.yml`（见 desktop/ci/ 下，拷到仓库根的
   `.github/workflows/`）；
3. push 后在 Actions 页面下载产物 `HashMM-Setup-*.exe`。
   runner 本身就是 Windows，node-pty 在云上正确编译，零本地依赖。

---

## 选哪条？

| 你的情况 | 选 |
|---|---|
| 有 Windows 笔记本 | **A**（完整版，含终端） |
| 只有这台 Ubuntu 容器、先要个能用的 | **B**（瘦客户端，无终端） |
| 有 GitHub、想云上出完整版 | **C** |

终端能力不是必须项——RAG-Agent 的全部功能在瘦客户端里都在。终端只是"额外能在桌面里
直接敲 claude/codex"的驾驶舱，可后补。

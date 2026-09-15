# HashMM Desktop（瘦客户端）

把 HashMM 包成可双击安装的桌面 App（Windows .exe / macOS .dmg / Linux AppImage）。

## 为什么是「瘦客户端」而不是「自包含 exe」

你的后端**绑死了 GPU + torch + FAISS + BGE-M3 模型(约2GB) + CUDA**。这意味着：
- ❌ 做不了「双击即装、在任意笔记本独立跑全部功能」的 exe——别人的笔记本没有 4090，
  BGE-M3 在 CPU 上慢到不可用，自包含包要 4-8GB。
- ✅ 正确做法（也是 hermes-desktop 的做法：「local or remote backend」）：
  **exe 只装 UI 外壳（~80MB），运行时连接一个跑在 GPU 服务器上的 HashMM 后端。**

所以这个安装包：
- 体积小（只有 Electron + 几个文件），任何 Windows/Mac 笔记本都能装；
- 首次打开弹「连接后端」页，填你的后端地址（autodl 服务器 / 客户自己的 GPU 机）；
- 连上后加载远程后端自带的 Web UI，功能 100% 一致；
- 地址存本机，下次自动连。

> 这跟 hermes 的瘦安装器思路一致：hermes 的 .exe 也不含重依赖，首次运行才现场provision。
> 区别是 hermes 调云端 LLM API，所以能在本地 provision；你的是本地 GPU 嵌入，必须放服务器，
> 因此走「连远程后端」而非「本地 provision」。

---

## 打 Windows 安装包（HashMM-Setup-1.0.0.exe）

⚠️ **必须在一台有桌面环境的机器上打包**（你的 Win/Mac 笔记本，或一台带桌面的 CI）。
**autodl 这种无桌面 Linux 服务器打不了、也装不了 Electron（连不上下载源）。**

在你的笔记本上：
```bash
# 1) 装 Node ≥ 18（https://nodejs.org），然后：
cd desktop
# 国内拉 Electron 慢就先设镜像：
#   Windows PowerShell:  $env:ELECTRON_MIRROR="https://npmmirror.com/mirrors/electron/"
#   Mac/Linux:           export ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
npm config set registry https://registry.npmmirror.com
npm install

# 2) 先本地试运行（不打包，验证能连后端）：
npm start
#    弹出连接页 → 填 http://<你的后端地址> → 连接 → 看到 HashMM 界面就 OK

# 3) 打 Windows 安装包：
npm run dist:win
#    产物在 desktop/dist/HashMM-Setup-1.0.0.exe —— 这就是你要的、可拷给别人安装的 exe
```

> 在 Mac 上能打 Win 包吗？electron-builder 可以，但需要额外工具链，**最稳是在 Windows 上打 Win 包、
> 在 Mac 上打 Mac 包**。你要分发给 Windows 用户，就在一台 Windows 机器上跑 `npm run dist:win`。

产物：
- `HashMM-Setup-1.0.0.exe` —— 安装向导（可选安装目录、建桌面快捷方式）。拷给任何 Windows 用户双击即装。

---

## 用户拿到 exe 后的使用流程

1. 双击 `HashMM-Setup-1.0.0.exe` → 安装 → 桌面出现 HashMM 图标。
2. 打开 → 填后端地址（你提供，例如 autodl 外网映射 `http://x.x.x.x:port`）→ 连接。
3. 正常使用（对话、检索、知识图谱…）。地址已保存，下次自动连。

要换后端：界面里调用 `window.hashmmDesktop.reset()`（可在前端加个「切换后端」按钮），
或删掉配置文件（`%APPDATA%/HashMM/hashmm-config.json`）重开。

---

## 后端要可达（部署清单）

瘦客户端能用的前提：后端从用户机器**网络可达**。
- autodl：用「自定义服务」开外网映射，把那个 URL 填进客户端。
- 自有服务器：开放端口 / 反向代理（建议加 HTTPS）。
- 安全：后端 `/api/health` 要能匿名访问（仅探活）；敏感接口建议开 token，
  客户端会把令牌放进 `X-HashMM-Session-Token` 头（main.js 已实现）。

---

## 验证状态（诚实标注）

- ✅ 我已静态验证：`main.js`/`preload.js` 过 `node --check`，`package.json`/`local.html` 合法，
  逻辑（连接页→健康检查→加载远程→存配置→自动重连）人工核对。
- ❌ 我无法实跑/实打包：构建沙箱无桌面、无 Electron。`npm install`/`npm start`/`npm run dist:win`
  必须你在笔记本上做。我不假装打过包。

---

## 安全设计（已内置）
- `contextIsolation:true` + `sandbox:true` + `nodeIntegration:false`：Web UI 拿不到 Node。
- 配置存 `userData` 目录的 JSON（非 localStorage）。
- 仅向所连后端注入令牌头；外部链接走系统浏览器。

## 已知限制 / 后续
- 当前「切换后端」靠 `reset()` IPC，建议前端加个可见入口（设置里）。
- 可加自动更新（electron-updater）——需要一个发布服务器放更新文件。
- 自包含离线版（PyInstaller 冻后端）仅在「目标机有 GPU」时才有意义，一般不做。


---

## v1.1 产品化升级（本次）

- 【修 bug】后端中途挂掉/断网 → 自动回连接页并提示原因（此前白屏死页只能强杀）；
  页面进程崩溃同样兜底。
- 单实例锁：重复双击图标聚焦已开窗口。
- 窗口位置/大小记忆。
- 中文应用菜单：**切换后端 (Ctrl+Shift+B)** / 刷新 / 缩放 / 全屏 / 开发者工具 / 关于
  （"切换后端"从此有可见入口）。
- 多后端：最近连接列表（最多 5 个，连接页一键直连，带在线测速，可移除）。

> 验证状态：`node --check` 通过 main.js/preload.js；local.html 为纯静态页。
> 实跑与打包仍需在有桌面环境的机器上进行（沙箱无 Electron）。

## ⚠️ 服务器上的 desktop/ 目录被污染了

你服务器 `/root/autodl-tmp/desktop/` 里混进了 checkpoints/data/indexes/memory/
output/rag_storage 等运行时目录——这通常是某次以 desktop/ 为工作目录启动了后端导致的。
桌面端只需要 main.js / preload.js / local.html / package.json / assets/，
建议把那些运行时目录挪回项目根（先确认里面有没有最新数据再动）。

---

## v1.2 内嵌终端驾驶舱（适配自 fanbox）

桌面端不再只是"连后端看 RAG-Agent"——菜单「终端驾驶舱」(Ctrl+Shift+T) 打开一个内嵌
终端窗口，可直接在本机指挥 **Claude Code / Codex** 等命令行 coding agent：

- node-pty 起真实 shell（Windows 用 ConPTY + PowerShell，Mac/Linux 用 $SHELL），
  xterm.js 渲染，与系统终端体验一致。
- 顶部一键按钮拉起 `claude` / `codex`；进程状态栏实时显示"● Claude Code 运行中"
  （轮询前台进程名识别）。
- 关窗自动清理所有 PTY，不留僵尸进程。

### node-pty 是原生模块——打包前必须 rebuild

```bash
cd desktop
npm install                 # postinstall 会自动 electron-builder install-app-deps
npm run rebuild             # 针对当前 Electron ABI 编译 node-pty（关键）
npm start                   # 本地验证：打开后 Ctrl+Shift+T 看终端
```

### 打 Windows exe（在 Windows 机器上）

```powershell
cd desktop
$env:ELECTRON_MIRROR="https://npmmirror.com/mirrors/electron/"   # 国内加速（可选）
npm install
npm run rebuild             # Windows 上编译 node-pty（需 VS Build Tools + Python）
npm run dist:win
#   产物：desktop/dist/HashMM-Setup-1.2.0.exe
```

> ⚠️ node-pty 在 Windows 需要 **Visual Studio Build Tools（C++ 工作负载）+ Python 3**
> 才能编译。装 Node 时勾选"自动安装必要工具"最省事。
> 若只想要不含终端的纯瘦客户端，删 package.json 的 node-pty 依赖即可（终端面板会
> 自动降级提示，app 仍可用）。

### 后端侧配套：外部 agent 用量端点

后端新增 `GET /api/agent-usage`（只读，鉴权同 /metrics）——解析本机 ~/.claude 与
~/.codex 会话日志，返回 Claude Code / Codex 的 token 用量（last5h / today / week 窗口
+ Codex 官方配额百分比）。桌面端终端跑 agent 后，可在仪表盘看消耗。未装对应 agent
返回 null。

# HashMM 架构总览（V99）

> 本地优先的企业级 RAG-Agent 工作台。三端一体：Python 后端（检索/Agent/知识图谱）、
> Next.js 前端（静态导出，桌面与 web 同一套代码）、Electron 桌面端（壳层网关 +
> 本机能力 + 本地后端 sidecar）。对标 Hermes/Marvis 的「窄腰」思路：端与端之间
> 只靠少数稳定契约相连，任何一侧可独立演进、独立降级。

```
┌──────────────────────────────── 桌面端 desktop/ ────────────────────────────────┐
│  main.js（装配）─ preload.js（契约桥）─ webui(内置前端) ─ shellserver.js(壳层网关) │
│      │                                                        │   ▲              │
│      │ modules/semantic-serve.js（V98 小模型服务编排）          │   │ /local/embed │
│      │ backendmgr.js（本地 Python sidecar 三级降级）            │   │ (V98)        │
│      ▼                                                        ▼   │              │
│  本机能力：PTY 终端 / 文件浏览·监听 / CU 截屏 / ONNX 小模型      /api 反向代理      │
└───────────────────────────────────────┬─────────────────────────────────────────┘
                                        │ REST + SSE（唯一数据面）
┌───────────────────────────────────────▼─────────────────────────────────────────┐
│                          后端 hashmm/（FastAPI · 平铺模块）                       │
│  api/server.py → agent/ · retrieval_pipeline.py · kg/ · ingestion/ · generation/ │
│  检索主链：dense(faiss)+sparse(BM25/jieba) → KG 扩展 → RRF → 重排(Step6/6.5)      │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## 1. 窄腰契约（跨端只认这些）

| 契约 | 形态 | 说明 |
|---|---|---|
| `/api/*`、`/health`、`/desktop-updates` | HTTP/SSE | 前端↔后端唯一数据面；壳层同源反代，无 CORS |
| preload 桥 `hashmm*` 命名空间 | contextBridge | `hashmmDesktop/Term/Local/Semantic/LLM/CU/Files/Backend/RAG`，前端只 import `lib/desktop.ts` 的类型化 getter |
| `POST /local/embed`（V98） | 壳层本机路由 | 桌面小模型嵌入服务；无 provider/未就绪一律 503 |
| `HASHMM_LOCAL_EMBED_URL`（V98） | 环境变量 | sidecar 启动时由主进程注入；后端 `hashmm/local_semantic.py` 是它唯一消费者 |
| `HASHMM_DESKTOP=1` | 环境变量 | PTY 子进程标记，agent 可感知运行环境 |
| 桌面配置 `config.json` | userData | `url/token/recent/shellPort/localAuto/semanticServe(默认 false)…` |

降级铁律：契约任意一侧缺位 → 另一侧零感知地回退（503/空对象/identity 返回），
绝不抛错进主链。

## 2. 后端 hashmm/（平铺模块，约 5.8 万行）

| 模块 | 职责 |
|---|---|
| `api/server.py` | FastAPI 入口（`hashmm.api.server:app`），路由聚合、鉴权、SSE |
| `retrieval_pipeline.py` | 检索主链 `search()`：Step1-5 召回融合 → **Step6** FlagEmbedding 重排（可选依赖） → **Step6.5 (V98)** 本地语义重排（仅 Step6 缺位且 env 已设时生效） |
| `local_semantic.py`（V98） | `/local/embed` 的 stdlib 客户端：30s 熔断、≤32 文本/次、余弦+名次融合；no-op 返回**同一对象**（identity 判定零成本） |
| `agent/` · `iterative_executor.py` | 工具循环、计划执行、安全护栏（`agent_safety.py`） |
| `kg/` | networkx 知识图谱：抽取、PPR 多跳、检索期扩展（默认关） |
| `ingestion/` · `connectors/` | 文档解析入库、外部源接入 |
| `generation/` · `llm_provider.py` | OpenAI 兼容 LLM 路由（无 GPU 硬依赖） |
| `tests/_mini_runner.py` | 自带 fake-pytest（fixture/parametrize/skip/monkeypatch.setattr），292 项回归 |

硬约束：后端零新增 Python 依赖；`requirements*.txt` 纯 ASCII 无 BOM（GBK pip）。

## 3. 前端 frontend-next/（Next.js 15 静态导出）

| 位置 | 职责 |
|---|---|
| `lib/desktop.ts` | preload 桥的**类型化唯一入口**（`getDesktop/getTerm/getLocal/getSemantic…`），web 端 getter 返回 null → 组件自降级 |
| `lib/store.ts` | zustand 全局态（`desktopView: workbench/files/terminal/usage/backend`） |
| `components/DesktopPanel.tsx` | **装配壳**（V98 从 429 行瘦身到 ~54 行）：标题 + 视图路由 |
| `components/desktop/`（V98） | `util.ts` 共享工具 · `FileBrowser` 可复用浏览列（变更点亮/静默重列） · `FilesView` · `TerminalView`（spawn 复用+回放） · `WorkbenchView`（FanBox 式文件+终端同屏联动） · `UsageView` · `BackendView` + `SemanticCard`（本地语义增强卡） |
| `components/Sidebar.tsx` | 桌面入口分组（工作台/本机文件/终端/用量/后端连接），桥不存在不渲染 |

## 4. 桌面端 desktop/（Electron，对标 Marvis 三层）

| 文件 | 职责 |
|---|---|
| `main.js` | **装配层**：窗口/托盘、IPC 注册、能力实现（PTY/文件监听/ONNX 小模型/CU）。趋势：新能力下沉 `modules/`，main 只 require + 注册 |
| `modules/semantic-serve.js`（V98） | 模块化样板：纯逻辑 + 依赖注入（loadConfig/semLoad/semEmbed/getShell 全部注入），`tests-node/` 直接冒烟，不 require electron |
| `preload.js` | contextBridge 白名单桥（窄腰之一），只透传不实现 |
| `shellserver.js` | 壳层网关（固定端口 17615，origin 稳定 = 登录持久）：内置 webui 托管 + `/api` 反代 + SSE 直通 + **`/local/embed`(V98)** |
| `backendmgr.js` | 本地 Python sidecar：检测→建环境→启动三级降级；`start({env})` 透传注入变量 |
| `semantic.js` | 小模型纯算法（WordPiece/meanPool/L2/余弦/SemanticIndex/deviceCheck），无 IO 可单测 |
| `computeruse.js` | CU 命令风险评估 + 工具 schema |
| `scripts/` | `gen-installer-assets.py`（品牌 BMP 幂等再生）、`installer-harness.nsi`（makensis 真编译验证 installer.nsh） |
| `build/` | `installer.nsh`（V100 微信式单窗：整页一图 installerWelcome.bmp + hit-test，几何**内联**无二级 include；`HM_WELCOME_CLASSIC` 一键回退标准向导）+ 侧栏/页眉 BMP + icon + hm-eula.txt |
| `tests-node/` | 纯 node 冒烟：shellserver 16 / backendmgr 14 / computeruse 7 / semantic_serve 6 / installer_assets 6 / cu_actions 12 / cu_guard 12 / cu_grounding 8 / install_engine 11 / mcp 5 / logger 6 / i18n 5 / workspace 6 / storage 4 / model 4 / services 5 / ocr 7 / imageops 4 / envguard 5 / packaging 4 |

## 5. Agent Runtime：三大工程（对标 Claude Code）

Agent 能力按"谁包着谁"分三层。外层提供受控环境与安全，中层推进迭代，
内层动手操作。这是 agent runtime 的标准分层，不是三个并列模块。

```
┌─ Harness 工程（受控运行时）─────────────────────────────┐
│  工具注册 · 上下文装配 · 权限闸 · 预算 · 事件流          │
│  ┌─ Loop 工程（迭代引擎）──────────────────────────┐    │
│  │  计划→执行→观察→反思→停机 · 无进展熔断 · 子代理并行  │    │
│  │  ┌─ Computer Use 工程（GUI 自动化）─────────┐    │    │
│  │  │  截屏 · 点击/输入/按键/滚动 · 风险闸 · 回放 │    │    │
│  │  └────────────────────────────────────────────┘    │    │
│  │              其它工具：文件 · 终端 · 检索 · LLM      │    │
│  └──────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────────┘
```

### 5.1 Harness 工程 — 受控运行时

后端已有成熟实现（`hashmm/agent/tool_pipeline.py`，165 回归看守）：

| 组件 | 文件 | 职责 |
|---|---|---|
| ToolPipeline | `agent/tool_pipeline.py` | 显式有序守卫链：PermissionGuard → ExecBudgetGuard → SearchBudgetGuard → ConsecutiveDedupGuard，取代散落的 if/elif |
| TurnState | 同上 | 一次 run 的全部预算/去重状态，单一对象贯穿（单一写者，避免漂移） |
| Hooks | 同上 | 空默认扩展点（对标 Claude Code hooks）：pre 可短路拒绝，post 纯观察，异常一律吞 |
| AgentLoop | `agent/loop.py` | 驱动一个用户回合，完整 SSE 事件协议（think/tool_start/tool_done/file/done） |

设计铁律：守卫只读 TurnState 判定（写留在循环里），守卫自身异常绝不拦执行
（harness 故障不放大为任务故障）。桌面侧 Computer Use 动作走同样的守卫纪律
（确认门 + 策略闸，见 5.3）。

### 5.2 Loop 工程 — 迭代引擎

桌面 Computer Use 循环（`frontend-next/lib/`）V99 正规化为可单测状态机：

| 组件 | 文件 | 职责 |
|---|---|---|
| AgentLoopController | `lib/agentLoop.ts` | 状态机：beginRound/recordToolCall/markCompleted/fail；结构化停机原因；**子代理并行** runSubagentsParallel（独立预算/失败隔离/按序归并） |
| 环境隔离守卫 | `desktop/isolation/env-guard.js` | 只写自己目录(安装/userData/临时)，不改 PATH/不动系统 Python/不全局 npm；路径越界兜底 |
| 本地 OCR | `desktop/models/`（onnx-runtime.js + ocr-pipeline.js） | 真集成 onnxruntime-node（沙箱实测 tiny.onnx 跑通）+ DBNet 取框 + CRNN 裁剪缩放(纯JS)+CTC 解码；端到端编排验证跑通；缺模型降级云端 |
| 本地模型管理 | `desktop/models/model-manager.js` | 对标 Marvis models/+device_match：硬件能力分级 + 本地/云端决策 + OCR 槽位 |
| 服务化骨架 | `desktop/services/`（registry.js + model-service.js） | 对标 Marvis 多服务：service locator + 9 服务(storage/logger/model/update/tray/window/terminal/backend/isolation)，installer-native/ 提供 C++/Qt6 原生安装器源码(需用户 Qt 编译，对照已测 install-engine.js)；心跳区分忙(超时)与真下线(拒连)只对真下线弹横幅；main.js 服务注册收口为一行 bootstrapServices；多处调用点已委托（托盘/窗口/更新/终端/后端/下载/截屏） |
| 用户数据/工作区 | `desktop/storage/`（workspace.js 纯逻辑 + index.js 服务） | 默认安装目录内 HashMM Files（Downloads/Documents/Notes），可改路径；文件名净化+防覆盖去重；卸载保留数据夹 |
| 结构化日志 | `desktop/logging/logger.js` | 对标 Marvis logs/：分级/JSON 行/轮转(5MB×5)/密钥脱敏/环形缓冲/子 logger；接入 __logCrash |
| 国际化 i18n | `desktop/i18n/`（i18n.js + zh-CN/en.json） | 对标 Marvis i18n/：点分键/回退链/插值/复数/目录加载 |
| MCP 工具层 | `desktop/mcp/`（protocol/tool-registry/whitelist/hashmm-mcp-server/mcp-host/index） | 对标 Marvis MCP Agent 层：独立进程 MCP Server（stdio JSON-RPC）+ 宿主握手 + 注册表 + 白名单(TTL)；进程分离 |
| 全自绘安装/卸载器 | `desktop/installer/`（ui.html 装 + uninstall.html 卸 无边框窗口 + installer.js/uninstaller.js 主进程 + install-engine 纯逻辑） | portable：双击弹自绘窗口自装；`--uninstall` 弹同款自绘卸载窗口（自删脚本删目录/快捷方式/注册表，保留 %APPDATA% 数据）；与 nsis 并存兜底 |
| CU 视觉定位 | `desktop/modules/cu-grounding.js` | OCR 文本框 + 目标文字 → 分层相似度匹配 → 归一化坐标+置信度；`locate_element` 工具让模型按文字点按钮而非肉眼估坐标；定位点击仍受 cu-actions 禁区/策略约束 |
| 无进展熔断 | 同上 | 同一 (工具名+排序参数) 连续重复 N 次 → 判打转主动停机。过去裸 for 循环会卡在"截屏→看→再截屏"空跑满轮 |
| 预算追踪 | 同上 | maxSteps / maxToolCalls / maxRepeats |
| runComputerUse | `lib/cu.ts` | 消费 controller：超步/无进展/外部中断统一裁决，结果回灌附"你在重复"提示促模型换策略 |

测试 `test_agent_loop.mjs`（14 项）用 tsc 现编译真源码后断言，不维护镜像。

### 5.3 Computer Use 工程 — GUI 自动化

从 V82 一期（只能截屏看）升级到 V99 二期（能动手）。设计对标 Anthropic
Computer Use API：

| 组件 | 文件 | 职责 |
|---|---|---|
| 动作纯逻辑 | `modules/cu-actions.js` | 归一化坐标 0..1000（分辨率无关）· 动作 schema 校验（越界/非法键/超长全挡）· 危险组合键闸（Win+R/Alt+F4 强制确认）· 策略闸（只读模式/高安全全确认）· ActionRecorder 回放审计 |
| 平台执行 | `modules/cu-driver.js` | plan → 真实输入：Windows user32 SendInput（内联 C#，零原生依赖）· mac osascript · Linux xdotool。平台不支持/工具缺失 → 降级不崩 |
| 工具 schema | `computeruse.js` | 单一 `computer` 工具，action 字段分发（left_click/type/key/scroll/drag…），对标 Anthropic CU 单工具设计 |
| 装配 | `main.js cu:exec` | 校验 → 策略闸 → 危险确认（原生对话框）→ 平台执行 → 回放。仅"视觉模式 + 显式开启控制"时注入 computer 工具（默认不给，防误触） |

测试 `test_cu_actions.js`（11 项）覆盖坐标往返、校验失败路径、危险闸、策略、
回放、三平台命令编译、执行层降级。

GUI 动作坐标流：模型在截屏缩略图上看到 0..1000 相对坐标 → `denormalize`
按真实屏幕尺寸映射回像素 → 平台 driver 执行。换屏/换分辨率无需重训。

## 6. 数据流：三条新链路

**① 工作台联动（FanBox 驾驶舱）**
```
浏览目录 ──watchSet──▶ main.js fs.watch ──fs:changed──▶ WorkbenchView
  ▲                                                       │
  └── 终端(共享 PTY, spawn 复用+回放) ◀── agent 写文件 ──┘
点亮文件卡(4s) + 最近变更条 + 静默重列 + 预览自动重读
```

**② 本地语义增强（轻量机型的语义排序）**
```
SemanticCard 开关 → config.semanticServe → semServe.attach()
                                               │ 注册 provider
backend:start → envFor() → HASHMM_LOCAL_EMBED_URL ──▶ sidecar 后端
                                               │
检索 Step6 缺位 → local_semantic.maybe_local_rerank ──POST──▶ 壳层 /local/embed
（失败/未开/未就绪：identity 返回 + 30s 熔断，主链零感知）
```

**③ Computer Use GUI 动作（V99）**
```
模型看截屏 → computer 工具(归一化坐标 0..1000)
   │
cu:exec → validateAction(校验) → applyPolicy(策略闸) → 危险确认(原生框)
   │                                                        │
   └── denormalize(→真实像素) → cu-driver 平台执行 ──────────┘
SendInput / osascript / xdotool · 每步落 ActionRecorder 审计
（非法动作/平台不支持/用户拒绝：降级返回，绝不崩）
```

## 7. 验证矩阵（每轮交付必跑）

| 层 | 命令 | 当前 |
|---|---|---|
| 后端回归 | `python3 tests/_mini_runner.py` | 292 passed, 0 failed |
| 桌面冒烟 | `node desktop/tests-node/test_*.js` ×20 | shell16 · backend14 · cu7 · semantic6 · installer6 · cuActions12 · cuGuard12 · cuGrounding8 · installEngine11 · mcp5 · logger6 · i18n5 · workspace6 · storage4 · model4 · services5 · ocr7 · imageops4 · envguard5 · packaging4 |
| Loop 工程 | `node frontend-next/tests-node/test_agent_loop.mjs` | 14 项（tsc 现编译；含子代理并行） |
| 前端类型 | `cd frontend-next && npx tsc --noEmit` | 零错误 |
| 前端构建 | `cd frontend-next && npm run build` | 静态导出 out/ |
| 图标守卫 | `node scripts/check_icons.mjs`（frontend-next 内） | lucide 引用核对 |
| 高亮守卫 | `node scripts/check_highlight.mjs`（根） | 61 项 |
| 安装器 | `makensis -DBUILD=… installer-harness.nsi`（默认 + `-DHM_WELCOME_CLASSIC`） | 双模式各 0 警告（整页单图内联几何 + hit-test；CLASSIC 回退） |

真机金标准：NSIS 自绘欢迎页视觉、Computer Use GUI 动作运行时行为、Electron
运行时行为、Windows GBK 环境只有真机能终验。

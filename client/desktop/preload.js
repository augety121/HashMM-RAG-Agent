// HashMM Desktop — preload script (contextIsolation ON).
//
// Exposes a tiny, explicit surface:
//  - to connect.html: getConfig / connect (collect & test backend URL)
//  - to the loaded web UI: isDesktop flag, appVersion, reset (re-pick backend)
// The renderer never gets raw Node/IPC — only these whitelisted calls.

const { contextBridge, ipcRenderer } = require("electron");

// V103.90 客户端输入校验/错误归类（纯逻辑模块；渲染层 nodeIntegration:false 不能 require，
// 由 preload 暴露这层安全 API 供连接/配置表单做提交前校验与友好错误提示）。
let _CV = null; try { _CV = require("./client-validate.js"); } catch (_e) {}
// V103.90 Computer Use 动作描述（渲染层不能 require，由 preload 暴露，驱动 in-app 操作记录可视化）。
let _CUD = null; try { _CUD = require("./cu-describe.js"); } catch (_e) {}
// V103.90 本地模型安装向导（纯逻辑，渲染层不能 require，由 preload 暴露）。
let _LLS = null; try { _LLS = require("./localllm-setup.js"); } catch (_e) {}
// V103.90 深度检索结果结构化展示（纯逻辑，渲染层不能 require，由 preload 暴露）。
let _DSF = null; try { _DSF = require("./deepsearch-format.js"); } catch (_e) {}
// V103.90 安全 Markdown 渲染器（聊天消息渲染；渲染层不能 require，由 preload 暴露）。
let _MD = null; try { _MD = require("./chat-markdown.js"); } catch (_e) {}
// V103.90 语义检索状态（纯逻辑，渲染层不能 require，由 preload 暴露）。
let _SS = null; try { _SS = require("./semantic-status.js"); } catch (_e) {}
// V103.90 截图标注几何（纯逻辑，渲染层不能 require，由 preload 暴露）。
let _SA = null; try { _SA = require("./shot-annotate.js"); } catch (_e) {}
// V103.90 配置导入导出（纯逻辑 + 文件 IPC；渲染层不能 require，由 preload 暴露）。
let _CP = null; try { _CP = require("./config-portability.js"); } catch (_e) {}
// V103.90 对话历史持久化（纯逻辑 + 磁盘 IPC）。
let _CS2 = null; try { _CS2 = require("./conversation-store.js"); } catch (_e) {}
// V103.90 命令面板模糊匹配（纯逻辑，渲染层不能 require）。
let _CMD = null; try { _CMD = require("./command-palette.js"); } catch (_e) {}
contextBridge.exposeInMainWorld("hashmmCmd", {
  filter: (commands, query, ctx) => (_CMD ? _CMD.filterCommands(commands, query, ctx) : (commands || [])),
  moveSelection: (cur, delta, len) => (_CMD ? _CMD.moveSelection(cur, delta, len) : 0),
});
// V103.90 多标签会话（纯逻辑，渲染层不能 require）。
let _TAB = null; try { _TAB = require("./tab-manager.js"); } catch (_e) {}
contextBridge.exposeInMainWorld("hashmmTabs", {
  open: (state, tab) => (_TAB ? _TAB.openTab(state, tab) : state),
  close: (state, id) => (_TAB ? _TAB.closeTab(state, id) : state),
  setActive: (state, id) => (_TAB ? _TAB.setActive(state, id) : state),
  rename: (state, id, title) => (_TAB ? _TAB.renameTab(state, id, title) : state),
  getActive: (state) => (_TAB ? _TAB.getActive(state) : null),
  adjacent: (state, dir) => (_TAB ? _TAB.activateAdjacent(state, dir) : state),
});
// V103.90 对话导出（纯逻辑 + 文件 IPC）。
let _CE = null; try { _CE = require("./chat-export.js"); } catch (_e) {}
contextBridge.exposeInMainWorld("hashmmExport", {
  toMarkdown: (conv, opts) => (_CE ? _CE.toMarkdown(conv, opts) : ""),
  toPlainText: (conv, opts) => (_CE ? _CE.toPlainText(conv, opts) : ""),
  filename: (title, ext) => (_CE ? _CE.exportFilename(title, ext) : "对话." + (ext || "md")),
  saveText: (content, suggestName) => ipcRenderer.invoke("export:saveText", { content, suggestName }),
  savePdf: (html, suggestName) => ipcRenderer.invoke("export:savePdf", { html, suggestName }),
});
// V103.90 会话内消息搜索（纯逻辑，渲染层不能 require）。
let _MS = null; try { _MS = require("./message-search.js"); } catch (_e) {}
contextBridge.exposeInMainWorld("hashmmSearch", {
  findMatches: (text, q) => (_MS ? _MS.findMatches(text, q) : []),
  highlightHtml: (text, q, ai) => (_MS ? _MS.highlightHtml(text, q, ai) : String(text || "")),
  stepHit: (cur, delta, total) => (_MS ? _MS.stepHit(cur, delta, total) : -1),
});
// V103.90 单条消息操作（纯逻辑，渲染层不能 require）。
let _MA = null; try { _MA = require("./message-actions.js"); } catch (_e) {}
contextBridge.exposeInMainWorld("hashmmMsgAct", {
  buildQuote: (msg, opts) => (_MA ? _MA.buildQuote(msg, opts) : ""),
  buildShareText: (msg, opts) => (_MA ? _MA.buildShareText(msg, opts) : ""),
  copyText: (msg) => (_MA ? _MA.copyText(msg) : ""),
});
// V103.90 附件管理（纯逻辑，渲染层不能 require）。
let _AT = null; try { _AT = require("./attachment-manager.js"); } catch (_e) {}
contextBridge.exposeInMainWorld("hashmmAttach", {
  classify: (file) => (_AT ? _AT.classifyFile(file) : { kind: "other", canInline: false }),
  validate: (file, opts) => (_AT ? _AT.validateAttachment(file, opts) : { ok: false, reason: "unavailable" }),
  label: (file) => (_AT ? _AT.attachmentLabel(file) : { name: "", short: "", size: "", icon: "📎" }),
  buildTextContext: (files, opts) => (_AT ? _AT.buildTextContext(files, opts) : ""),
});
// V103.90 多语言界面：复用现有 i18n 引擎（desktop/i18n/i18n.js，V100），加载词典并暴露给渲染层。
let _I18N = null;
try {
  const { createI18n } = require("./i18n/i18n.js");
  _I18N = createI18n({ locale: "zh-CN", fallback: "en" });
  _I18N.loadDir(require("path").join(__dirname, "i18n"));   // 加载 zh-CN.json / en.json（随包）
} catch (_e) { _I18N = null; }
contextBridge.exposeInMainWorld("hashmmI18n", {
  t: (key, params) => (_I18N ? _I18N.t(key, params) : key),
  setLocale: (locale) => { if (_I18N) _I18N.setLocale(locale); },
  availableLocales: () => (_I18N ? _I18N.availableLocales() : ["zh-CN", "en"]),
});
contextBridge.exposeInMainWorld("hashmmConv", {
  upsert: (list, conv) => (_CS2 ? _CS2.upsert(list, conv) : list || []),
  remove: (list, id) => (_CS2 ? _CS2.remove(list, id) : list || []),
  rename: (list, id, title) => (_CS2 ? _CS2.rename(list, id, title) : list || []),
  getById: (list, id) => (_CS2 ? _CS2.getById(list, id) : null),
  listSummary: (list) => (_CS2 ? _CS2.listSummary(list) : []),
  deriveTitle: (msgs) => (_CS2 ? _CS2.deriveTitle(msgs) : "新对话"),
  serialize: (list) => (_CS2 ? _CS2.serialize(list) : "{}"),
  deserialize: (s) => (_CS2 ? _CS2.deserialize(s) : []),
  load: () => ipcRenderer.invoke("conv:load"),
  save: (json) => ipcRenderer.invoke("conv:save", { json }),
});
// V103.90 偏好设置（纯逻辑；持久化复用 config）。
let _PR = null; try { _PR = require("./preferences.js"); } catch (_e) {}
contextBridge.exposeInMainWorld("hashmmPrefs", {
  normalize: (p) => (_PR ? _PR.normalize(p) : (p || {})),
  cssVariables: (p, dark) => (_PR ? _PR.cssVariables(p, dark) : { vars: {}, effectiveTheme: "dark" }),
  update: (p, k, v) => (_PR ? _PR.update(p, k, v) : p),
  DEFAULTS: _PR ? _PR.DEFAULTS : {},
});
// V103.90 桌面通知 / 未读角标。
contextBridge.exposeInMainWorld("hashmmNotify", {
  assistantDone: (preview) => ipcRenderer.invoke("notify:assistantDone", { preview }),
  clearUnread: () => ipcRenderer.invoke("notify:clearUnread"),
  setEnabled: (on) => ipcRenderer.invoke("notify:setEnabled", on),
  getEnabled: () => ipcRenderer.invoke("notify:getEnabled"),
});
contextBridge.exposeInMainWorld("hashmmConfigIO", {
  buildExportString: (parts, opts) => (_CP ? _CP.stringifyExport(parts, opts) : "{}"),
  parseImport: (input) => (_CP ? _CP.parseImport(input) : { ok: false, errors: ["unavailable"] }),
  splitForApply: (data) => (_CP ? _CP.splitForApply(data) : { mainPatch: {}, llmCfg: {}, visionCfg: {} }),
  exportToFile: (json, suggestName) => ipcRenderer.invoke("config:exportToFile", { json, suggestName }),
  importFromFile: () => ipcRenderer.invoke("config:importFromFile"),
  applyMainPatch: (patch) => ipcRenderer.invoke("config:applyMainPatch", { patch }),
});
contextBridge.exposeInMainWorld("hashmmShot", {
  normalizeRect: (x0, y0, x1, y1) => (_SA ? _SA.normalizeRect(x0, y0, x1, y1) : { x: Math.min(x0, x1), y: Math.min(y0, y1), w: Math.abs(x1 - x0), h: Math.abs(y1 - y0) }),
  scaleRect: (r, dw, dh, nw, nh) => (_SA ? _SA.scaleRect(r, dw, dh, nw, nh) : r),
  isTinyRect: (r, m) => (_SA ? _SA.isTinyRect(r, m) : false),
  clampPoint: (x, y, w, h) => (_SA ? _SA.clampPoint(x, y, w, h) : { x, y }),
});
contextBridge.exposeInMainWorld("hashmmMD", {
  render: (text) => (_MD ? _MD.renderMarkdown(text) : String(text == null ? "" : text)),
});
contextBridge.exposeInMainWorld("hashmmDeep", {
  format: (data) => (_DSF ? _DSF.buildDisplay(data) : { answer: (data && data.answer) || "", sources: [], citedNumbers: [], meta: {}, subQuestions: [] }),
  metaSummary: (meta) => (_DSF ? _DSF.metaSummary(meta) : ""),
});
contextBridge.exposeInMainWorld("hashmmValidate", {
  backendUrl: (u) => (_CV ? _CV.validateBackendUrl(u) : { ok: true, normalized: u }),
  apiKey: (k, opts) => (_CV ? _CV.validateApiKey(k, opts) : { ok: true, normalized: k }),
  modelName: (m, opts) => (_CV ? _CV.validateModelName(m, opts) : { ok: true, normalized: m }),
  llmConfig: (c, opts) => (_CV ? _CV.validateLlmConfig(c, opts) : { ok: true, errors: {}, normalized: c }),
  classifyConnError: (e) => (_CV ? _CV.classifyConnError(e) : { kind: "unknown", message: "连接失败", hint: "" }),
});

contextBridge.exposeInMainWorld("hashmmDesktop", {
  isDesktop: true,
  platform: process.platform,
  appVersion: () => ipcRenderer.invoke("hashmm:appVersion"),
  installInfo: () => ipcRenderer.invoke("hashmm:installInfo"),   // V97: fresh/update/normal
  checkUpdate: () => ipcRenderer.invoke("hashmm:checkUpdate"),   // V91: 关于页手动检查更新
  // Used by connect.html:
  getConfig: () => ipcRenderer.invoke("hashmm:getConfig"),
  connect: (cfg) => ipcRenderer.invoke("hashmm:connect", cfg),
  // v1.1: 连接页测速 / 最近后端管理
  probe: (url) => ipcRenderer.invoke("hashmm:probe", url),
  // V103.14: 功能档位（基础/推荐/全开）。BackendView 经 getDesktop() 调用，故必须挂在本对象上。
  getPreset: () => ipcRenderer.invoke("feature:getPreset"),
  setPreset: (p) => ipcRenderer.invoke("feature:setPreset", p),
  forgetRecent: (url) => ipcRenderer.invoke("hashmm:forgetRecent", url),
  // Lets the UI offer a "switch backend" action.
  reset: () => ipcRenderer.invoke("hashmm:reset"),
  // v1.3: 统一壳导航（菜单 → 页签）与内置默认后端
  openTerminal: () => ipcRenderer.invoke("hashmm:openTerminal"),
  getDefaultBackend: () => ipcRenderer.invoke("hashmm:getDefaultBackend"),
  setOverlay: (dark) => ipcRenderer.invoke("hashmm:setOverlay", { dark }),
  goLocal: () => ipcRenderer.invoke("hashmm:goLocal"),
  // V103: 按需能力包（拆出的本地 Python 运行时等），用户需要时再下载
  packStatus: (id) => ipcRenderer.invoke("pack:status", id),
  packInstall: (id) => ipcRenderer.invoke("pack:install", id),
  onPackProgress: (cb) => { const h = (e, m) => cb(m); ipcRenderer.on("pack:progress", h);
                            return () => ipcRenderer.removeListener("pack:progress", h); },
  onNav: (cb) => { const h = (e, view) => cb(view); ipcRenderer.on("nav", h);
                   return () => ipcRenderer.removeListener("nav", h); },
  // V103.90 关闭确认：主进程拦截 X → 通知前端弹 app 风格弹窗；用户选择回传。
  onConfirmClose: (cb) => { const h = () => cb(); ipcRenderer.on("app:confirmClose", h);
                            return () => ipcRenderer.removeListener("app:confirmClose", h); },
  closeChoice: (choice, remember) => ipcRenderer.send("app:closeChoice", { choice, remember }),
});

// v1.2: 内嵌终端（node-pty）——指挥 claude / codex 等 coding agent（适配自 fanbox）
contextBridge.exposeInMainWorld("hashmmTerm", {
  available: true,
  spawn: (opts) => ipcRenderer.invoke("term:spawn", opts),
  input: (id, data) => ipcRenderer.send("term:input", { id, data }),
  resize: (id, cols, rows) => ipcRenderer.send("term:resize", { id, cols, rows }),
  kill: (id) => ipcRenderer.send("term:kill", { id }),
  proc: (id) => ipcRenderer.invoke("term:proc", { id }),
  runAgent: (id, agent) => ipcRenderer.invoke("term:runAgent", { id, agent }),
  onData: (cb) => { const h = (e, m) => cb(m); ipcRenderer.on("term:data", h);
                    return () => ipcRenderer.removeListener("term:data", h); },
  onExit: (cb) => { const h = (e, m) => cb(m); ipcRenderer.on("term:exit", h);
                    return () => ipcRenderer.removeListener("term:exit", h); },
  // v1.2: agent 改文件 → 前端刷新（适配自 fanbox）
  watchSet: (dirs) => ipcRenderer.invoke("fs:watchSet", { dirs }),
  onFsChanged: (cb) => { const h = (e, m) => cb(m); ipcRenderer.on("fs:changed", h);
                         return () => ipcRenderer.removeListener("fs:changed", h); },
  openExternal: (url) => ipcRenderer.invoke("shell:openExternal", { url }),
});

// V73: 本地 RAG（本地知识库 + BM25 检索，不依赖远程后端）
contextBridge.exposeInMainWorld("hashmmRAG", {
  pickAndIngest: () => ipcRenderer.invoke("localrag:pickAndIngest"),
  search: (q, topK) => ipcRenderer.invoke("localrag:search", { q, topK }),
  stat: () => ipcRenderer.invoke("localrag:stat"),
  clear: () => ipcRenderer.invoke("localrag:clear"),
  // V103.90 知识库管理：分组/移除/增量同步
  folderList: () => ipcRenderer.invoke("localrag:folderList"),
  fileList: (folder) => ipcRenderer.invoke("localrag:fileList", { folder }),
  removeFolder: (folder) => ipcRenderer.invoke("localrag:removeFolder", { folder }),
  removeFile: (file) => ipcRenderer.invoke("localrag:removeFile", { file }),
  sync: (folder) => ipcRenderer.invoke("localrag:sync", { folder }),
  onProgress: (cb) => { const h = (e, m) => cb(m); ipcRenderer.on("localrag:progress", h);
                        return () => ipcRenderer.removeListener("localrag:progress", h); },
});

// V77: 语义检索（ONNX，可选；装不上自动 BM25 降级）
contextBridge.exposeInMainWorld("hashmmSemantic", {
  deviceCheck: () => ipcRenderer.invoke("semantic:deviceCheck"),
  downloadModel: () => ipcRenderer.invoke("semantic:downloadModel"),
  enable: () => ipcRenderer.invoke("semantic:enable"),
  disable: () => ipcRenderer.invoke("semantic:disable"),
  reindex: () => ipcRenderer.invoke("semantic:reindex"),
  getServe: () => ipcRenderer.invoke("semantic:getServe"),          // V98: 本地嵌入服务开关
  setServe: (on) => ipcRenderer.invoke("semantic:setServe", on),    // V98
  // V103.90 语义检索状态步骤 + 覆盖率（纯逻辑）
  steps: (status) => (_SS ? _SS.buildSemanticSteps(status) : []),
  overall: (status) => (_SS ? _SS.overallSemantic(status) : { summary: "" }),
  onProgress: (cb) => { const h = (e, m) => cb(m); ipcRenderer.on("semantic:progress", h);
                        return () => ipcRenderer.removeListener("semantic:progress", h); },
});

// V71: 直连 LLM（离线对话，用户自己的 API Key，主进程代理无 CORS）
contextBridge.exposeInMainWorld("hashmmLLM", {
  chat: (opts) => ipcRenderer.invoke("llm:chat", opts),
  abort: () => ipcRenderer.send("llm:abort"),
  chatTools: (opts) => ipcRenderer.invoke("llm:chatTools", opts),
  testConnection: (opts) => ipcRenderer.invoke("llm:testConnection", opts || {}),  // V103.90 模型连通性预检
  setupSteps: (status) => (_LLS ? _LLS.buildSetupSteps(status) : []),               // V103.90 本地模型安装向导
  setupOverall: (status) => (_LLS ? _LLS.overallState(status) : { ready: false, summary: "" }),
  // V103.90 方案3：本地 LLM 推理（数据全程不出端）。装了 node-llama-cpp + 放了 GGUF 才可用。
  localStatus: () => ipcRenderer.invoke("llm:localStatus"),
  localGenerate: (opts) => ipcRenderer.invoke("llm:localGenerate", opts || {}),
  localUnload: () => ipcRenderer.invoke("llm:localUnload"),
});

// V82: Computer Use（shell 型 agent，危险操作主进程确认门）
contextBridge.exposeInMainWorld("hashmmCU", {
  tools: (vision, control) => ipcRenderer.invoke("cu:tools", { vision, control }),
  meta: () => ipcRenderer.invoke("cu:meta"),
  capture: (opts) => ipcRenderer.invoke("cu:capture", opts || {}),
  precapture: () => ipcRenderer.invoke("cu:precapture"),   // V103.16: 开菜单即预热抓帧，截屏瞬间出冻结层
  exec: (name, args) => ipcRenderer.invoke("cu:exec", { name, args }),
  replay: (n) => ipcRenderer.invoke("cu:replay", { n }),            // V99 动作回放审计
  replayClear: () => ipcRenderer.invoke("cu:replayClear"),
  getSafety: () => ipcRenderer.invoke("cu:getSafety"),              // V99 安全级别
  setSafety: (level) => ipcRenderer.invoke("cu:setSafety", level),
  getFileDir: () => ipcRenderer.invoke("cu:getFileDir"),            // V103.90 默认文件保存目录
  setFileDir: (dir) => ipcRenderer.invoke("cu:setFileDir", dir),
  pickFileDir: () => ipcRenderer.invoke("cu:pickFileDir"),
  // V103.90 动作可视化（纯逻辑，本地计算不走 IPC）
  describe: (ev) => (_CUD ? _CUD.describeEvent(ev) : { icon: "•", text: "", risk: "write", riskLabel: "", ok: true }),
  summarize: (events) => (_CUD ? _CUD.summarizeSession(events) : { total: 0 }),
  onDelta: (cb) => { const h = (e, m) => cb(m); ipcRenderer.on("llm:delta", h);
                     return () => ipcRenderer.removeListener("llm:delta", h); },
});

// V96: 文件保存（微信式下载位置）
contextBridge.exposeInMainWorld("hashmmFiles", {
  getSaveConfig: () => ipcRenderer.invoke("files:getSaveConfig"),
  chooseSaveDir: () => ipcRenderer.invoke("files:chooseSaveDir"),
  setSaveMode: (mode) => ipcRenderer.invoke("files:setSaveMode", mode),
  save: (filename, content, isDataUrl) => ipcRenderer.invoke("files:save", { filename, content, isDataUrl: !!isDataUrl }),
  revealInFolder: (p) => ipcRenderer.invoke("files:revealInFolder", p),
  openPath: (p) => ipcRenderer.invoke("files:openPath", p),
});

// V96: 本地后端 sidecar 控制桥（Marvis 的 MarvisNode/KnowledgeBase 对应物）
contextBridge.exposeInMainWorld("hashmmBackend", {
  detect: (custom) => ipcRenderer.invoke("backend:detect", custom || ""),
  setup: (o) => ipcRenderer.invoke("backend:setup", o || {}),
  start: (o) => ipcRenderer.invoke("backend:start", o || {}),
  stop: () => ipcRenderer.invoke("backend:stop"),
  resetEnv: () => ipcRenderer.invoke("backend:resetEnv"),
  status: () => ipcRenderer.invoke("backend:status"),
  logs: () => ipcRenderer.invoke("backend:logs"),
  getDataDir: () => ipcRenderer.invoke("backend:getDataDir"),
  chooseDataDir: () => ipcRenderer.invoke("backend:chooseDataDir"),
  resetDataDir: () => ipcRenderer.invoke("backend:resetDataDir"),
  setAuto: (on) => ipcRenderer.invoke("backend:setAuto", !!on),
  getCfg: () => ipcRenderer.invoke("backend:getLocalCfg"),
  deepsearch: (o) => ipcRenderer.invoke("backend:deepsearch", o || {}),
  shellInfo: () => ipcRenderer.invoke("shell:info"),
  onLog: (cb) => { const h = (_e, line) => cb(line); ipcRenderer.on("backend:log", h);
                   return () => ipcRenderer.removeListener("backend:log", h); },
});

// v1.3: 本机能力（fanbox 本体功能适配）——文件工作台 + agent 用量，离线可用
contextBridge.exposeInMainWorld("hashmmLocal", {
  roots: () => ipcRenderer.invoke("local:roots"),
  list: (dir) => ipcRenderer.invoke("local:list", { dir }),
  read: (file) => ipcRenderer.invoke("local:read", { file }),
  write: (file, text) => ipcRenderer.invoke("local:write", { file, text }),
  gitLog: (cwd, limit) => ipcRenderer.invoke("git:log", { cwd, limit }),
  agentUsage: () => ipcRenderer.invoke("local:agentUsage"),
  search: (dir, q) => ipcRenderer.invoke("local:search", { dir, q }),
  grep: (dir, q) => ipcRenderer.invoke("local:grep", { dir, q }),
  recent: (dir) => ipcRenderer.invoke("local:recent", { dir }),
});

// V103.15: 远程传输（远程桌面）宿主控制桥——零依赖 WS 服务端（main.js 接 desktopCapturer + cu-driver）
// V103.16: 加 WebRTC P2P（投屏窗）+ 跨网络手动信令 + ICE 配置
contextBridge.exposeInMainWorld("hashmmRemote", {
  start: (opts) => ipcRenderer.invoke("remote:start", opts || {}),   // 启动服务+投屏窗+签发配对码，返回 {port,code,addrs}
  stop: () => ipcRenderer.invoke("remote:stop"),
  status: () => ipcRenderer.invoke("remote:status"),                 // {running,port,clients,paired,hostConnected,webrtcActive,addrs}
  issueCode: () => ipcRenderer.invoke("remote:issueCode"),           // 重新签发 6 位配对码
  currentCode: () => ipcRenderer.invoke("remote:currentCode"),       // 当前码 + 剩余有效期
  setIce: (iceServers) => ipcRenderer.invoke("remote:setIce", iceServers),   // 设 STUN/TURN（存配置）
  getIce: () => ipcRenderer.invoke("remote:getIce"),
  manualOffer: () => ipcRenderer.invoke("remote:manualOffer"),       // 跨网络：生成邀请码（含 ICE 候选）
  manualAnswer: (code) => ipcRenderer.invoke("remote:manualAnswer", code),   // 跨网络：应用对方应答码
  viewerHtml: () => ipcRenderer.invoke("remote:viewerHtml"),         // 取查看端网页（跨网络时存盘发对方）
  startAccountHost: (token) => ipcRenderer.invoke("remote:startAccountHost", token),  // 允许本机被同账号设备远程
  updateAccountToken: (token) => ipcRenderer.send("remote:updateAccountToken", token),  // V105 推送刷新后的令牌，保活在线
  stopAccountHost: () => ipcRenderer.invoke("remote:stopAccountHost"),
  accountHostStatus: () => ipcRenderer.invoke("remote:accountHostStatus"),
  openAccountViewer: (opts) => ipcRenderer.invoke("remote:openAccountViewer", opts || {}),  // 打开查看端去控制其它同账号设备
  // V103.51 对标 UU 远程的扩展能力：
  wake: (mac, opts) => ipcRenderer.invoke("remote:wake", { mac, ...(opts || {}) }),          // 远程开机（WOL 魔术包）
  listWolTargets: () => ipcRenderer.invoke("remote:listWolTargets"),                         // 已保存的开机目标（名称+MAC）
  saveWolTarget: (t) => ipcRenderer.invoke("remote:saveWolTarget", t || {}),                 // 保存/更新一个开机目标
  removeWolTarget: (id) => ipcRenderer.invoke("remote:removeWolTarget", id),
  listMonitors: () => ipcRenderer.invoke("remote:listMonitors"),                             // 多屏：枚举本机显示器
  setRemoteQuality: (q) => ipcRenderer.invoke("remote:setQuality", q || {}),                 // 画质：fps / jpeg 质量
  setPrivacy: (on) => ipcRenderer.invoke("remote:setPrivacy", !!on),                         // 隐私防护：被控端黑屏/锁输入
});

// V104 接力：接收主进程「手机交接来的对话」事件，前端据此切到该会话。
contextBridge.exposeInMainWorld("hashmmHandoff", {
  onOpen: (cb) => { ipcRenderer.on("hashmm-open-conversation", (_e, data) => { try { cb(data); } catch (_) { /* */ } }); },
});

/**
 * desktop/remote-protocol.js — 远控 DataChannel 控制协议（V103.90，对标 UU GameViewer 的 gv_pb）。
 *
 * 现状：控制消息是临时 JSON（如 {type:"input", ...}），没有版本、没有结构校验、不好扩展。
 * UU 用的是带命名空间的结构化 Protobuf 消息族（gv_pb.InputEventH/ClipboardChangeH/
 * FileTransferBlockH/CaptureConfigH...），并能平滑迭代（文件传输 v1~v4、剪贴板 v1~v3）。
 *
 * 本模块把控制协议正式化（不引第三方依赖、不上 Protobuf，保持 JSON-over-DataChannel 的轻量，
 * 但补上 UU 那套**结构 + 版本 + 校验 + 可扩展**）：
 *   - 统一信封：{ v:版本, t:类型, d:数据, seq?:序号 }
 *   - 消息族覆盖 输入/光标/剪贴板/文件传输/采集配置/编码协商/设备控制/心跳（对齐 gv_pb 分类）
 *   - 输入坐标统一归一到 0..1000（与 Computer Use 驱动 validateAction 的既有约定一致）
 *   - **向后兼容**：decode 同时认旧的无版本 {type:"input"} 报文并自动升级，灰度期对端不受影响
 *
 * 纯逻辑、不抛异常、可单测（tests-node/test_remote-protocol.js）。
 */
"use strict";

const PROTO_VERSION = 1;

// 消息类型（对齐 gv_pb 分类；值用短字符串省带宽）
const T = {
  INPUT: "in",              // InputEventH/TouchEventH/ActionKeyToggle
  CURSOR: "cur",            // CursorShapeH 光标形状同步
  CLIP_CHANGE: "clpc",      // ClipboardChangeH 剪贴板变更通知
  CLIP_REQUEST: "clpq",     // ClipboardRequestH 请求剪贴板内容
  CLIP_DATA: "clpd",        // ClipboardResponseH/DataBlockH 剪贴板数据
  FILE_OFFER: "fto",        // FileTransferAskH
  FILE_CONFIRM: "ftc",      // FileTransferConfirmH
  FILE_BLOCK: "ftb",        // FileTransferBlockH 分块
  FILE_DONE: "ftd",         // FileTransferCompleteH
  CAPTURE_CONFIG: "cap",    // CaptureConfigH 帧率/分辨率/码率
  CODEC_NEGO: "cdc",        // CodecNegotiationH 编解码协商
  DEVICE_ACTION: "dev",     // SimpleActionH 锁屏/重启/关机/任务管理器
  SYS_STATE: "sys",         // QuerySystemStateH/SystemStateChangeH
  PING: "pi",
  PONG: "po",
};
const _TYPES = new Set(Object.values(T));

// 输入动作子类型（对齐 CU 驱动既有动作名，便于直接转 validateAction）
const INPUT_ACTIONS = new Set([
  "left_click", "right_click", "middle_click", "double_click",
  "mouse_move", "left_click_drag", "type", "key", "scroll",
  "left_mouse_down", "left_mouse_up", "cursor_position",
]);

// 设备控制动作（对齐 SimpleActionH）
const DEVICE_ACTIONS = new Set(["lock", "restart", "shutdown", "taskmgr", "logoff", "ctrl_alt_del"]);

// ── 信封编解码 ──

/** 编码一条控制消息为字符串（DataChannel.send 用）。type 非法时返回 null。 */
function encode(type, data, seq) {
  if (!_TYPES.has(type)) return null;
  const env = { v: PROTO_VERSION, t: type, d: data || {} };
  if (typeof seq === "number") env.seq = seq;
  try {
    return JSON.stringify(env);
  } catch (_e) {
    return null;
  }
}

/**
 * 解码一条收到的控制消息。返回 { ok, v, t, d, seq } 或 { ok:false, error }。
 * 向后兼容：旧报文 {type:"input", x,y,...}（无 v 字段）自动升级为 INPUT 信封。
 */
function decode(raw) {
  let obj;
  if (typeof raw === "string") {
    try { obj = JSON.parse(raw); } catch (_e) { return { ok: false, error: "bad_json" }; }
  } else if (raw && typeof raw === "object") {
    obj = raw;
  } else {
    return { ok: false, error: "bad_type" };
  }

  // 旧格式兼容：{type:"input", ...} / {type:"fileOffer"...} 等无版本报文
  if (obj.v === undefined && typeof obj.type === "string") {
    return _upgradeLegacy(obj);
  }

  if (obj.v !== PROTO_VERSION) return { ok: false, error: "version", v: obj.v };
  if (!_TYPES.has(obj.t)) return { ok: false, error: "unknown_type", t: obj.t };
  return { ok: true, v: obj.v, t: obj.t, d: obj.d || {}, seq: obj.seq };
}

// 旧 {type:...} → 新信封（只覆盖现网在用的 input；其余原样透传，标记 legacy）
const _LEGACY_MAP = {
  input: T.INPUT, cursor: T.CURSOR,
  clipboard: T.CLIP_CHANGE, fileOffer: T.FILE_OFFER, fileBlock: T.FILE_BLOCK,
};
function _upgradeLegacy(obj) {
  const t = _LEGACY_MAP[obj.type];
  if (!t) return { ok: true, v: 0, t: obj.type, d: obj, seq: undefined, legacy: true };
  const d = Object.assign({}, obj);
  delete d.type;
  return { ok: true, v: 0, t, d, seq: undefined, legacy: true };
}

// ── 输入消息：构造 + 校验 + 转 CU 驱动动作 ──

function _num(v, dflt) { const n = Number(v); return Number.isFinite(n) ? n : dflt; }
function _clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

/** 构造一条输入消息（坐标归一到 0..1000）。action 非法→null。 */
function makeInput(action, fields) {
  if (!INPUT_ACTIONS.has(action)) return null;
  const f = fields || {};
  const d = { action };
  if (f.x !== undefined) d.x = _clamp(Math.round(_num(f.x, 0)), 0, 1000);
  if (f.y !== undefined) d.y = _clamp(Math.round(_num(f.y, 0)), 0, 1000);
  if (f.x2 !== undefined) d.x2 = _clamp(Math.round(_num(f.x2, 0)), 0, 1000);
  if (f.y2 !== undefined) d.y2 = _clamp(Math.round(_num(f.y2, 0)), 0, 1000);
  if (f.text !== undefined) d.text = String(f.text).slice(0, 4096);
  if (f.keys !== undefined) d.keys = Array.isArray(f.keys) ? f.keys.slice(0, 8).map(String) : String(f.keys);
  if (f.scroll_direction !== undefined) d.scroll_direction = String(f.scroll_direction);
  if (f.scroll_amount !== undefined) d.scroll_amount = _clamp(Math.round(_num(f.scroll_amount, 3)), 1, 100);
  if (f.ms !== undefined) d.ms = _clamp(Math.round(_num(f.ms, 0)), 0, 60000);
  return d;
}

/** 校验一条输入数据是否合法可执行。返回 { ok, reason }。 */
function validateInput(d) {
  if (!d || typeof d !== "object") return { ok: false, reason: "no_data" };
  // V257: 设备控制（重启/关机等）——App 系统面板此前发出即被拒（"装饰按钮"根因）。
  // 白名单 cmd + 独立形状校验，通过后由 main.js 的 device 分支执行（不进 CU 驱动）。
  if (d.action === "device") {
    if (!DEVICE_ACTIONS.has(String(d.cmd || ""))) return { ok: false, reason: "bad_device_cmd" };
    return { ok: true, data: { action: "device", cmd: String(d.cmd) } };
  }
  if (!INPUT_ACTIONS.has(d.action)) return { ok: false, reason: "bad_action" };
  const needXY = ["left_click", "right_click", "middle_click", "double_click",
    "mouse_move", "left_click_drag", "left_mouse_down", "left_mouse_up"];
  if (needXY.includes(d.action)) {
    if (!_inRange(d.x) || !_inRange(d.y)) return { ok: false, reason: "xy_out_of_range" };
  }
  if (d.action === "left_click_drag" && (!_inRange(d.x2) || !_inRange(d.y2))) {
    return { ok: false, reason: "drag_target_out_of_range" };
  }
  if (d.action === "type" && !d.text) return { ok: false, reason: "type_needs_text" };
  if (d.action === "key" && !d.keys) return { ok: false, reason: "key_needs_keys" };
  return { ok: true };
}
function _inRange(v) { return Number.isFinite(v) && v >= 0 && v <= 1000; }

/** 把输入数据转成 CU 驱动 validateAction 期望的形状（字段直通，集中一处便于演进）。 */
function toDriverAction(d) {
  return {
    type: d.action, x: d.x, y: d.y, x2: d.x2, y2: d.y2,
    text: d.text, keys: d.keys,
    scroll_direction: d.scroll_direction, scroll_amount: d.scroll_amount, ms: d.ms,
  };
}

// ── 采集配置 / 设备控制 构造器（带范围裁剪）──

/** CaptureConfigH：帧率/分辨率/码率。 */
function makeCaptureConfig(cfg) {
  const c = cfg || {};
  return {
    fps: _clamp(Math.round(_num(c.fps, 30)), 1, 144),
    width: _clamp(Math.round(_num(c.width, 1920)), 320, 7680),
    height: _clamp(Math.round(_num(c.height, 1080)), 240, 4320),
    bitrateKbps: _clamp(Math.round(_num(c.bitrateKbps, 4000)), 200, 80000),
  };
}

/** SimpleActionH：设备控制。action 非法→null。 */
function makeDeviceAction(action) {
  if (!DEVICE_ACTIONS.has(action)) return null;
  return { action };
}

module.exports = {
  PROTO_VERSION, T, INPUT_ACTIONS, DEVICE_ACTIONS,
  encode, decode,
  makeInput, validateInput, toDriverAction,
  makeCaptureConfig, makeDeviceAction,
};

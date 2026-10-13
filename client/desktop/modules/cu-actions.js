/**
 * desktop/modules/cu-actions.js — Computer Use 动作工程 · 纯逻辑核心（V99）。
 *
 * 把桌面 Computer Use 从"只能截屏看"（V82 一期）升级到"能动手"（二期：
 * 点击/输入/按键/滚动/移动/拖拽）。对标 Anthropic Computer Use API 的设计：
 *
 *   1. 归一化坐标（0..1000）——分辨率无关。模型在缩略图上看到的坐标，
 *      经 denormalize() 映射回真实屏幕像素，换屏/换分辨率不用重训。
 *   2. 动作 schema 严格校验——非法动作在进系统调用前就被挡（坐标越界、
 *      按键白名单、文本长度上限），绝不把脏参数喂给 PowerShell/osascript。
 *   3. 风险分级——只读（move/screenshot）零确认；写入（click/type/key）
 *      按目标分级；破坏性组合（如 Win+R 跑命令）强制确认。
 *   4. 回放审计——每个动作落一条结构化记录（时间/类型/坐标/结果），
 *      可导出供事后审计（大厂合规要求）。
 *
 * 本文件是**纯逻辑**：不 require electron、不碰真实输入设备，platform 执行
 * 由 cu-driver.js 注入。tests-node/test_cu_actions.js 在沙箱直接冒烟。
 */
"use strict";

// ── 动作类型表（对标 Anthropic CU action 枚举，裁剪到桌面可靠子集） ──
const ACTIONS = {
  screenshot: { write: false, needsXY: false },
  cursor_position: { write: false, needsXY: false },
  mouse_move: { write: false, needsXY: true },
  left_click: { write: true, needsXY: true },
  right_click: { write: true, needsXY: true },
  middle_click: { write: true, needsXY: true },
  double_click: { write: true, needsXY: true },
  left_click_drag: { write: true, needsXY: true, needsXY2: true },
  type: { write: true, needsXY: false, needsText: true },
  key: { write: true, needsXY: false, needsKeys: true },
  scroll: { write: true, needsXY: true, needsScroll: true },
  open_url: { write: true, needsUrl: true },   // 浏览器导航：用系统默认浏览器打开 URL（让 CU 直接跳转，不用在地址栏一个个点字）
  wait: { write: false, needsXY: false },
};

// 归一化坐标空间上限（模型始终在 0..N 的网格里思考，与真实像素解耦）
const NORM = 1000;
const MAX_TEXT = 4000;          // 单次 type 文本上限
const MAX_KEY_CHORD = 5;        // 组合键最多 5 键（Ctrl+Shift+Alt+X 够用）
const SCROLL_CLAMP = 50;        // 滚动格数上限（防失控滚动）

// 允许的按键名（小写规范化后白名单）——挡住注入怪异序列
const KEY_NAMES = new Set([
  "enter", "return", "tab", "escape", "esc", "space", "backspace", "delete", "del",
  "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
  "ctrl", "control", "alt", "shift", "win", "cmd", "meta", "super",
  "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m",
  "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
  "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
  "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
  "minus", "plus", "equal", "comma", "period", "slash", "semicolon",
]);

// 破坏性组合键：命中 → 强制确认（即便 type/key 本身按目标可能放行）
// 这些是"能跑任意命令/抹掉东西"的入口，必须 human-in-the-loop。
const DANGEROUS_CHORDS = [
  ["win", "r"],            // 运行对话框 → 任意命令
  ["ctrl", "alt", "delete"],
  ["alt", "f4"],           // 关闭窗口（可能丢未保存）
];

function _clampInt(v, lo, hi) {
  v = Math.round(Number(v));
  if (!Number.isFinite(v)) return lo;
  return v < lo ? lo : v > hi ? hi : v;
}

/**
 * 归一化坐标 → 真实屏幕像素。
 * @param {number} nx 0..1000  @param {number} ny 0..1000
 * @param {{width:number,height:number}} screen 真实屏幕尺寸
 * @returns {{x:number,y:number}} 像素坐标（已 clamp 到屏幕内）
 */
function denormalize(nx, ny, screen) {
  const w = Math.max(1, (screen && screen.width) || 1);
  const h = Math.max(1, (screen && screen.height) || 1);
  const x = _clampInt((Number(nx) / NORM) * w, 0, w - 1);
  const y = _clampInt((Number(ny) / NORM) * h, 0, h - 1);
  return { x, y };
}

/** 真实像素 → 归一化（截屏标注/回报模型时用）。 */
function normalize(px, py, screen) {
  const w = Math.max(1, (screen && screen.width) || 1);
  const h = Math.max(1, (screen && screen.height) || 1);
  return {
    nx: _clampInt((Number(px) / w) * NORM, 0, NORM),
    ny: _clampInt((Number(py) / h) * NORM, 0, NORM),
  };
}

/** 规范化组合键：拆分、小写、去空白、别名归并。返回 string[]（非法返回 null）。 */
function normalizeKeys(keys) {
  let parts;
  if (Array.isArray(keys)) parts = keys.slice();
  else parts = String(keys || "").split(/[+\-\s]+/);
  parts = parts.map((k) => String(k || "").trim().toLowerCase()).filter(Boolean);
  if (!parts.length || parts.length > MAX_KEY_CHORD) return null;
  const alias = { control: "ctrl", esc: "escape", return: "enter", del: "delete",
                  cmd: "win", meta: "win", super: "win" };
  parts = parts.map((k) => alias[k] || k);
  for (const k of parts) if (!KEY_NAMES.has(k)) return null;
  return parts;
}

function _chordHit(parts, chord) {
  if (parts.length !== chord.length) return false;
  const a = parts.slice().sort().join("+");
  const b = chord.slice().sort().join("+");
  return a === b;
}

/**
 * 校验 + 规范化一个动作。把模型给的原始 action dict 变成"可安全执行的计划"，
 * 或返回错误。纯函数，不执行任何东西。
 *
 * @param {object} action  {type, x?, y?, x2?, y2?, text?, keys?, scroll_direction?, scroll_amount?, ms?}
 * @param {{width:number,height:number}} screen
 * @returns {{ok:boolean, plan?:object, error?:string}}
 *   plan: {type, write, px?, py?, px2?, py2?, text?, keys?[], scrollDir?, scrollAmt?, ms?, risk, needsConfirm, reason}
 */
function validateAction(action, screen) {
  action = action || {};
  const type = String(action.type || "").trim();
  const spec = ACTIONS[type];
  if (!spec) return { ok: false, error: `未知动作类型: ${type || "(空)"}` };

  const plan = { type, write: spec.write, risk: spec.write ? "write" : "read",
                 needsConfirm: false, reason: "" };

  // 坐标
  if (spec.needsXY) {
    if (action.x == null || action.y == null) return { ok: false, error: `${type} 缺少坐标 x/y` };
    if (Number(action.x) < 0 || Number(action.x) > NORM || Number(action.y) < 0 || Number(action.y) > NORM)
      return { ok: false, error: `坐标越界（应在 0..${NORM}）: x=${action.x} y=${action.y}` };
    const p = denormalize(action.x, action.y, screen);
    plan.px = p.x; plan.py = p.y; plan.nx = _clampInt(action.x, 0, NORM); plan.ny = _clampInt(action.y, 0, NORM);
  }
  if (spec.needsXY2) {
    if (action.x2 == null || action.y2 == null) return { ok: false, error: `${type} 缺少终点坐标 x2/y2` };
    const p2 = denormalize(action.x2, action.y2, screen);
    plan.px2 = p2.x; plan.py2 = p2.y;
  }

  // 文本
  if (spec.needsText) {
    const text = String(action.text == null ? "" : action.text);
    if (!text) return { ok: false, error: "type 动作缺少 text" };
    if (text.length > MAX_TEXT) return { ok: false, error: `文本过长（>${MAX_TEXT} 字）` };
    plan.text = text;
  }

  // 按键
  if (spec.needsKeys) {
    const parts = normalizeKeys(action.keys);
    if (!parts) return { ok: false, error: `非法按键序列: ${JSON.stringify(action.keys)}` };
    plan.keys = parts;
    for (const chord of DANGEROUS_CHORDS) {
      if (_chordHit(parts, chord)) { plan.needsConfirm = true; plan.reason = `危险组合键 ${parts.join("+")}`; }
    }
  }

  // 滚动
  if (spec.needsScroll) {
    const dir = String(action.scroll_direction || "down").toLowerCase();
    if (!["up", "down", "left", "right"].includes(dir)) return { ok: false, error: `非法滚动方向: ${dir}` };
    plan.scrollDir = dir;
    plan.scrollAmt = _clampInt(action.scroll_amount == null ? 3 : action.scroll_amount, 1, SCROLL_CLAMP);
  }

  // 浏览器导航 URL
  if (spec.needsUrl) {
    let url = String(action.url == null ? "" : action.url).trim();
    if (!url) return { ok: false, error: "open_url 动作缺少 url" };
    // 明确挡掉危险协议（file/javascript/data 等），只放行网页
    if (/^\s*(file|javascript|data|vbscript|about|chrome|ftp|blob):/i.test(url))
      return { ok: false, error: "只允许 http/https 网址" };
    if (!/^https?:\/\//i.test(url)) url = "https://" + url;   // 没协议 → 补 https
    if (url.length > 2048) return { ok: false, error: "url 过长" };
    if (!/^https?:\/\/[^\s]+$/i.test(url)) return { ok: false, error: `非法 url: ${url.slice(0, 60)}` };
    plan.url = url;
  }

  // 等待
  if (type === "wait") plan.ms = _clampInt(action.ms == null ? 500 : action.ms, 0, 10000);

  // 风险/确认：写动作默认不弹（GUI 操作高频，逐次确认体验崩）；
  // 仅破坏性组合键强制确认。需要"全部写操作都确认"的高安全模式由 policy 控制（见下）。
  return { ok: true, plan };
}

// 默认敏感区（归一化 0..1000）：命中点击类动作 → 强制确认。
// 防 agent 误点系统关键控件。坐标 {x1,y1,x2,y2} 为矩形左上/右下。
const DEFAULT_NO_CLICK_ZONES = [
  // 屏幕最底部 24px 高度带（Windows 任务栏区域，约 >976/1000）——右键删除/关闭等高危
  { name: "任务栏", x1: 0, y1: 976, x2: 1000, y2: 1000 },
];

function _pointInZone(nx, ny, z) {
  return nx >= z.x1 && nx <= z.x2 && ny >= z.y1 && ny <= z.y2;
}

/**
 * 安全策略闸：在 validateAction 之上叠加全局策略。
 * @param {object} plan  validateAction 产出的 plan
 * @param {object} policy  {confirmAllWrites?:bool, blockWrites?:bool, noClickZones?:Array, disableZones?:bool}
 * @returns {{allow:boolean, needsConfirm:boolean, reason:string}}
 */
function applyPolicy(plan, policy) {
  policy = policy || {};
  if (policy.blockWrites && plan.write)
    return { allow: false, needsConfirm: false, reason: "只读模式：已禁止所有写操作" };
  let needsConfirm = plan.needsConfirm;
  let reason = plan.reason;
  // 坐标禁区：点击/拖拽类动作落入敏感区 → 强制确认
  const isClick = ["left_click", "right_click", "middle_click", "double_click", "left_click_drag"].includes(plan.type);
  if (isClick && !policy.disableZones && plan.nx != null) {
    const zones = policy.noClickZones || DEFAULT_NO_CLICK_ZONES;
    for (const z of zones) {
      if (_pointInZone(plan.nx, plan.ny, z)) { needsConfirm = true; reason = `点击进入敏感区（${z.name}）`; break; }
    }
  }
  if (policy.confirmAllWrites && plan.write && !needsConfirm) {
    needsConfirm = true; reason = "高安全模式：写操作需确认";
  }
  return { allow: true, needsConfirm, reason };
}

/** 人类可读的动作摘要（确认弹窗/审计日志用）。 */
function describeAction(plan) {
  switch (plan.type) {
    case "left_click": return `左键点击 (${plan.nx}, ${plan.ny})`;
    case "right_click": return `右键点击 (${plan.nx}, ${plan.ny})`;
    case "middle_click": return `中键点击 (${plan.nx}, ${plan.ny})`;
    case "double_click": return `双击 (${plan.nx}, ${plan.ny})`;
    case "mouse_move": return `移动光标到 (${plan.nx}, ${plan.ny})`;
    case "left_click_drag": return `拖拽到终点像素 (${plan.px2}, ${plan.py2})`;
    case "type": return `输入文本「${plan.text.length > 30 ? plan.text.slice(0, 30) + "…" : plan.text}」`;
    case "key": return `按键 ${plan.keys.join("+")}`;
    case "scroll": return `滚动 ${plan.scrollDir} ×${plan.scrollAmt}`;
    case "screenshot": return "截取屏幕";
    case "cursor_position": return "查询光标位置";
    case "wait": return `等待 ${plan.ms}ms`;
    default: return plan.type;
  }
}

// ── 回放审计：结构化记录每个动作的执行（可导出供合规审计） ──
class ActionRecorder {
  constructor(cap = 500) { this.cap = cap; this.events = []; }
  record(plan, result) {
    this.events.push({
      ts: Date.now(),
      type: plan.type,
      summary: describeAction(plan),
      px: plan.px, py: plan.py, nx: plan.nx, ny: plan.ny,
      ok: !!(result && result.ok),
      error: (result && result.error) || "",
    });
    if (this.events.length > this.cap) this.events.splice(0, this.events.length - this.cap);
  }
  recent(n = 50) { return this.events.slice(-n); }
  clear() { this.events = []; }
  /** 导出为可读文本（审计/排障）。 */
  toText() {
    return this.events.map((e) => {
      const t = new Date(e.ts).toISOString();
      return `[${t}] ${e.summary} → ${e.ok ? "ok" : "FAIL: " + e.error}`;
    }).join("\n");
  }
}

module.exports = {
  ACTIONS, NORM, MAX_TEXT, KEY_NAMES, DANGEROUS_CHORDS, DEFAULT_NO_CLICK_ZONES,
  denormalize, normalize, normalizeKeys,
  validateAction, applyPolicy, describeAction,
  ActionRecorder,
};

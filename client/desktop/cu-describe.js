/**
 * desktop/cu-describe.js — Computer Use 动作可视化纯逻辑（V103.90）。
 *
 * CU 后端已经很完整（validateAction 出带 risk/confirm/reason 的 plan、guard 链 allow/confirm/deny、
 * ActionRecorder 记录每步动作与结果）。缺的是**让用户看见 agent 在干什么**——把机器味的
 * {type:"left_click", px:320, py:450} 翻成人话「左键点击 (320,450)」并配风险徽章，
 * 驱动一个 in-app 的"操作记录/动作流"面板。
 *
 * 纯函数、不抛异常、字段名容错（px/x、scrollDir/scroll_direction 都认），便于沙箱单测。
 */
"use strict";

// 风险等级 → 中文标签 + 颜色键（UI 自行映射颜色）
const RISK = {
  read:   { label: "只读", tone: "muted" },
  write:  { label: "写入", tone: "accent" },
  danger: { label: "危险", tone: "danger" },
};

function _xy(a) {
  const x = a.px != null ? a.px : a.x;
  const y = a.py != null ? a.py : a.y;
  return (x != null && y != null) ? `(${x}, ${y})` : "";
}
function _xy2(a) {
  const x = a.px2 != null ? a.px2 : a.x2;
  const y = a.py2 != null ? a.py2 : a.y2;
  return (x != null && y != null) ? `(${x}, ${y})` : "";
}
function _keys(a) {
  const k = a.keys;
  if (Array.isArray(k)) return k.join("+");
  return String(k || "");
}
function _clip(s, n) { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n) + "…" : s; }

/**
 * 把一个动作/计划描述成人话。入参兼容 validateAction 的 plan 与原始 action。
 * @returns { icon, text, risk:"read"|"write"|"danger", riskLabel, tone }
 */
function describeAction(a) {
  a = a || {};
  const type = String(a.type || "").trim();
  let icon = "•", text = type || "(空动作)";
  // 风险：优先用 plan.risk；shell/命令默认按 danger 判定函数另算
  let risk = a.risk === "danger" ? "danger" : (a.write || a.risk === "write") ? "write" : "read";

  switch (type) {
    case "screenshot": icon = "VIEW"; text = "截屏（观察当前画面）"; risk = "read"; break;
    case "cursor_position": icon = "POINT"; text = "读取光标位置"; risk = "read"; break;
    case "mouse_move": icon = "MOUSE"; text = `移动鼠标到 ${_xy(a)}`; risk = "read"; break;
    case "left_click": icon = "MOUSE"; text = `左键点击 ${_xy(a)}`; break;
    case "right_click": icon = "MOUSE"; text = `右键点击 ${_xy(a)}`; break;
    case "middle_click": icon = "MOUSE"; text = `中键点击 ${_xy(a)}`; break;
    case "double_click": icon = "MOUSE"; text = `双击 ${_xy(a)}`; break;
    case "left_click_drag": icon = "MOUSE"; text = `拖拽 ${_xy(a)} → ${_xy2(a)}`; break;
    case "type": icon = "KEY"; text = `输入文本「${_clip(a.text, 40)}」`; break;
    case "key": icon = "KEY"; text = `按键 ${_keys(a)}`; break;
    case "scroll": {
      const dir = a.scrollDir || a.scroll_direction || "";
      const amt = a.scrollAmt != null ? a.scrollAmt : a.scroll_amount;
      const dirCn = { up: "上", down: "下", left: "左", right: "右" }[dir] || dir;
      icon = "MOUSE"; text = `滚动${dirCn ? "（" + dirCn + (amt ? " " + amt : "") + "）" : ""} ${_xy(a)}`.trim();
      break;
    }
    case "wait": icon = "WAIT"; text = `等待${a.ms ? " " + a.ms + "ms" : ""}`; risk = "read"; break;
    case "open_url": icon = "WEB"; text = `打开网址「${_clip(a.url, 50)}」`; break;
    case "run_command": case "shell": case "bash": {
      const cmd = a.command || a.cmd || a.text || "";
      icon = "⌘"; text = `执行命令：${_clip(cmd, 60)}`;
      risk = a.danger || a.risk === "danger" ? "danger" : "write";
      break;
    }
    case "write_file": case "edit_file": {
      icon = "FILE"; text = `写入文件${a.path ? "：" + _clip(a.path, 50) : ""}`;
      risk = a.risk === "danger" ? "danger" : "write";
      break;
    }
    default:
      if (a.text) text = `${type}：${_clip(a.text, 40)}`;
  }

  const meta = RISK[risk] || RISK.write;
  return { icon, text, risk, riskLabel: meta.label, tone: meta.tone };
}

/**
 * 描述一条记录器事件（兼容 {plan, result, ts} 或扁平 {type, ok, error, ts}）。
 * @returns { icon, text, risk, riskLabel, tone, ok, error, ts, confirm }
 */
function describeEvent(ev) {
  ev = ev || {};
  const action = ev.plan || ev.action || ev;
  const d = describeAction(action);
  const result = ev.result || ev;
  const ok = result.ok !== undefined ? !!result.ok : (ev.ok !== undefined ? !!ev.ok : true);
  const error = result.error || ev.error || "";
  return Object.assign(d, {
    ok, error,
    ts: ev.ts || ev.time || 0,
    confirm: !!(action.needsConfirm || ev.needsConfirm),
    reason: action.reason || ev.reason || "",
  });
}

/** 汇总一段会话的动作（按风险/结果计数），供面板头部展示。 */
function summarizeSession(events) {
  const out = { total: 0, read: 0, write: 0, danger: 0, ok: 0, failed: 0 };
  for (const ev of (events || [])) {
    const d = describeEvent(ev);
    out.total += 1;
    out[d.risk] = (out[d.risk] || 0) + 1;
    if (d.ok) out.ok += 1; else out.failed += 1;
  }
  return out;
}

module.exports = { RISK, describeAction, describeEvent, summarizeSession };

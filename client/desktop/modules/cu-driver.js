/**
 * desktop/modules/cu-driver.js — Computer Use 动作工程 · 平台执行层（V99）。
 *
 * 把 cu-actions.js 校验过的 plan 翻译成真实的鼠标/键盘输入。设计目标：
 * **零额外原生依赖**（robotjs/@nut-tree 要编译，跨平台打包痛苦），改用各平台
 * 自带的脚本能力：
 *   - Windows：PowerShell + Win32 user32.dll SendInput（Add-Type 内联 C#）
 *   - macOS：osascript（System Events，模拟点击/按键）
 *   - Linux：xdotool（若安装；否则该平台动作层优雅缺位）
 *
 * 每个执行函数返回 {ok, error?}，绝不抛。平台不支持/工具缺失 → {ok:false}，
 * 上层据此降级（Computer Use 退回"只能截屏看"，不崩）。
 *
 * 注入式：runShell(cmd, args) 由 main.js 注入（复用其 execFile + UTF-8 解码），
 * 本模块不直接 require child_process —— 便于 tests-node 用假 runner 验证
 * "plan → 正确的命令字符串"而不真的动鼠标。
 */
"use strict";

const CU = require("./cu-actions");

// ── Windows：内联 C# 调 user32 SendInput ──
// PowerShell Add-Type 把一段 C# 编译进会话，提供 Move/Click/Wheel/Key/Type。
// 注：坐标用 SetCursorPos（绝对像素），点击用 mouse_event，按键用 keybd_event。
const PS_WIN32 = `
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class HMInput {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, IntPtr e);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint f, IntPtr e);
  public const uint MOVE=0x0001, LDOWN=0x0002, LUP=0x0004, RDOWN=0x0008, RUP=0x0010, MDOWN=0x0020, MUP=0x0040, WHEEL=0x0800, HWHEEL=0x01000;
  public static void Move(int x,int y){ SetCursorPos(x,y); }
  public static void LClick(int x,int y){ SetCursorPos(x,y); mouse_event(LDOWN,0,0,0,IntPtr.Zero); mouse_event(LUP,0,0,0,IntPtr.Zero); }
  public static void RClick(int x,int y){ SetCursorPos(x,y); mouse_event(RDOWN,0,0,0,IntPtr.Zero); mouse_event(RUP,0,0,0,IntPtr.Zero); }
  public static void MClick(int x,int y){ SetCursorPos(x,y); mouse_event(MDOWN,0,0,0,IntPtr.Zero); mouse_event(MUP,0,0,0,IntPtr.Zero); }
  public static void DClick(int x,int y){ LClick(x,y); System.Threading.Thread.Sleep(60); LClick(x,y); }
  public static void Drag(int x1,int y1,int x2,int y2){ SetCursorPos(x1,y1); mouse_event(LDOWN,0,0,0,IntPtr.Zero); System.Threading.Thread.Sleep(40); SetCursorPos(x2,y2); System.Threading.Thread.Sleep(40); mouse_event(LUP,0,0,0,IntPtr.Zero); }
  public static void Wheel(int amt){ mouse_event(WHEEL,0,0,(uint)amt,IntPtr.Zero); }
  public static void HWheel(int amt){ mouse_event(HWHEEL,0,0,(uint)amt,IntPtr.Zero); }
}
"@
`;

// 规范化键名 → Win32 虚拟键码（VK）
const VK = {
  enter: 0x0D, return: 0x0D, tab: 0x09, escape: 0x1B, space: 0x20, backspace: 0x08, delete: 0x2E,
  up: 0x26, down: 0x28, left: 0x25, right: 0x27, home: 0x24, end: 0x23, pageup: 0x21, pagedown: 0x22,
  ctrl: 0x11, alt: 0x12, shift: 0x10, win: 0x5B,
  f1: 0x70, f2: 0x71, f3: 0x72, f4: 0x73, f5: 0x74, f6: 0x75, f7: 0x76, f8: 0x77,
  f9: 0x78, f10: 0x79, f11: 0x7A, f12: 0x7B,
};
function _vkFor(k) {
  if (VK[k] != null) return VK[k];
  if (/^[a-z]$/.test(k)) return k.toUpperCase().charCodeAt(0);     // A-Z
  if (/^[0-9]$/.test(k)) return k.charCodeAt(0);                   // 0-9
  return null;
}

// PS 字符串转义（单引号串）
function _psStr(s) { return "'" + String(s).replace(/'/g, "''") + "'"; }

/**
 * 把 plan 编译成该平台的 shell 命令 [shell, argsArray]。
 * 纯字符串拼装——可被测试断言而不执行。platform 可注入以便测试各分支。
 * @returns {{shell:string, args:string[]} | null}  null = 该平台无法执行此动作
 */
function compileCommand(plan, platform) {
  platform = platform || process.platform;
  if (platform === "win32") return _compileWin(plan);
  if (platform === "darwin") return _compileMac(plan);
  if (platform === "linux") return _compileLinux(plan);
  return null;
}

function _compileWin(plan) {
  let body;
  switch (plan.type) {
    case "mouse_move": body = `[HMInput]::Move(${plan.px},${plan.py})`; break;
    case "left_click": body = `[HMInput]::LClick(${plan.px},${plan.py})`; break;
    case "right_click": body = `[HMInput]::RClick(${plan.px},${plan.py})`; break;
    case "middle_click": body = `[HMInput]::MClick(${plan.px},${plan.py})`; break;
    case "double_click": body = `[HMInput]::DClick(${plan.px},${plan.py})`; break;
    case "left_click_drag": body = `[HMInput]::Drag(${plan.px},${plan.py},${plan.px2},${plan.py2})`; break;
    case "scroll": {
      const unit = 120 * plan.scrollAmt;
      if (plan.scrollDir === "up") body = `[HMInput]::Wheel(${unit})`;
      else if (plan.scrollDir === "down") body = `[HMInput]::Wheel(${-unit})`;
      else if (plan.scrollDir === "right") body = `[HMInput]::HWheel(${unit})`;
      else body = `[HMInput]::HWheel(${-unit})`;
      break;
    }
    case "type": {
      // SendKeys 处理 Unicode 文本最省事，但特殊字符需转义；这里用逐字符 SendWait
      // 经 [System.Windows.Forms.SendKeys]，对 + ^ % ~ ( ) { } [ ] 转义。
      body = `Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait(${_psStr(_sendKeysEscape(plan.text))})`;
      return { shell: "powershell.exe", args: ["-NoProfile", "-NonInteractive", "-Command", body] };
    }
    case "key": {
      // 组合键：修饰键 keydown → 主键 down/up → 修饰键 keyup（逆序）
      const mods = plan.keys.filter((k) => ["ctrl", "alt", "shift", "win"].includes(k));
      const main = plan.keys.filter((k) => !["ctrl", "alt", "shift", "win"].includes(k));
      const lines = [];
      for (const m of mods) lines.push(`[HMInput]::keybd_event(${_vkFor(m)},0,0,[IntPtr]::Zero)`);
      for (const k of main) {
        const vk = _vkFor(k);
        if (vk == null) return null;
        lines.push(`[HMInput]::keybd_event(${vk},0,0,[IntPtr]::Zero)`);
        lines.push(`[HMInput]::keybd_event(${vk},0,2,[IntPtr]::Zero)`);   // KEYUP=0x02
      }
      for (const m of mods.slice().reverse()) lines.push(`[HMInput]::keybd_event(${_vkFor(m)},0,2,[IntPtr]::Zero)`);
      body = lines.join("; ");
      break;
    }
    case "wait": return { shell: "powershell.exe", args: ["-NoProfile", "-Command", `Start-Sleep -Milliseconds ${plan.ms}`] };
    case "open_url": return { shell: "powershell.exe", args: ["-NoProfile", "-NonInteractive", "-Command", "Start-Process " + _psStr(plan.url)] };
    default: return null;
  }
  return { shell: "powershell.exe", args: ["-NoProfile", "-NonInteractive", "-Command", PS_WIN32 + "\n" + body] };
}

// SendKeys 元字符转义
function _sendKeysEscape(text) {
  return String(text).replace(/[+^%~(){}\[\]]/g, (c) => "{" + c + "}");
}

function _compileMac(plan) {
  // osascript via System Events。坐标点击用 cliclick 风格不可用 → 退而用
  // AppleScript 的 click at（需辅助功能权限）。这里给最常用子集。
  const osa = (script) => ({ shell: "osascript", args: ["-e", script] });
  switch (plan.type) {
    case "left_click": return osa(`tell application "System Events" to click at {${plan.px}, ${plan.py}}`);
    case "mouse_move": return osa(`tell application "System Events" to set the position of the mouse to {${plan.px}, ${plan.py}}`);
    case "type": return osa(`tell application "System Events" to keystroke ${_osaStr(plan.text)}`);
    case "key": {
      const mods = plan.keys.filter((k) => ["ctrl", "alt", "shift", "win"].includes(k))
        .map((k) => ({ ctrl: "control down", alt: "option down", shift: "shift down", win: "command down" }[k]));
      const main = plan.keys.filter((k) => !["ctrl", "alt", "shift", "win"].includes(k));
      const keyName = main[0] || "";
      const using = mods.length ? ` using {${mods.join(", ")}}` : "";
      const special = { enter: "return", escape: "escape", tab: "tab", space: "space",
                        delete: "delete", up: "up arrow", down: "down arrow",
                        left: "left arrow", right: "right arrow" }[keyName];
      if (special) return osa(`tell application "System Events" to key code (key code of "${special}")${using}`);
      return osa(`tell application "System Events" to keystroke ${_osaStr(keyName)}${using}`);
    }
    case "wait": return { shell: "sleep", args: [String((plan.ms || 0) / 1000)] };
    case "open_url": return { shell: "open", args: [plan.url] };
    default: return null;
  }
}
function _osaStr(s) { return '"' + String(s).replace(/["\\]/g, "\\$&") + '"'; }

function _compileLinux(plan) {
  // xdotool（若装）。坐标用 mousemove + click。
  const xd = (args) => ({ shell: "xdotool", args });
  switch (plan.type) {
    case "mouse_move": return xd(["mousemove", String(plan.px), String(plan.py)]);
    case "left_click": return xd(["mousemove", String(plan.px), String(plan.py), "click", "1"]);
    case "right_click": return xd(["mousemove", String(plan.px), String(plan.py), "click", "3"]);
    case "middle_click": return xd(["mousemove", String(plan.px), String(plan.py), "click", "2"]);
    case "double_click": return xd(["mousemove", String(plan.px), String(plan.py), "click", "--repeat", "2", "1"]);
    case "type": return xd(["type", "--", plan.text]);
    case "key": return xd(["key", plan.keys.map((k) => ({ ctrl: "ctrl", alt: "alt", shift: "shift", win: "super",
                            enter: "Return", escape: "Escape", pageup: "Prior", pagedown: "Next" }[k] || k)).join("+")]);
    case "scroll": {
      const btn = plan.scrollDir === "up" ? "4" : plan.scrollDir === "down" ? "5" : plan.scrollDir === "left" ? "6" : "7";
      return xd(["mousemove", String(plan.px), String(plan.py), "click", "--repeat", String(plan.scrollAmt), btn]);
    }
    case "wait": return { shell: "sleep", args: [String((plan.ms || 0) / 1000)] };
    case "open_url": return { shell: "xdg-open", args: [plan.url] };
    default: return null;
  }
}

/**
 * 执行一个已校验的 plan。runShell 注入（(shell, args) => Promise<{ok,output?,error?}>）。
 * @returns {Promise<{ok:boolean, error?:string}>}
 */
async function executePlan(plan, runShell, platform) {
  const cmd = compileCommand(plan, platform);
  if (!cmd) return { ok: false, error: `当前平台不支持动作 ${plan.type}（或缺少 xdotool/辅助功能权限）` };
  try {
    const r = await runShell(cmd.shell, cmd.args);
    if (r && r.ok) return { ok: true };
    return { ok: false, error: (r && r.error) || "执行失败" };
  } catch (e) {
    return { ok: false, error: (e && e.message) || String(e) };
  }
}

module.exports = { compileCommand, executePlan, _vkFor, _sendKeysEscape };

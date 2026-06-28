/**
 * computeruse.js — 桌面端 Computer Use 一期（V82，shell 型）。
 *
 * 演进路线：prompt 工程 → harness 工程 → loop 工程 → **computer use**。
 * 一期是 shell 型（Claude Code 本质：LLM + 终端 + 文件 = 操控电脑），
 * 视觉型（截图+点击，需视觉 LLM）列为二期。
 *
 * 本模块只放**纯逻辑**（危险命令检测、工具 schema）——可单测；
 * 真正的执行（child_process / fs）在 main.js，受确认门控制。
 */
"use strict";

// 危险命令模式：命中 → 必须用户确认才执行（不是禁止，是 human-in-the-loop）
const DANGER_PATTERNS = [
  /\brm\s+-[a-z]*r/i,            // rm -rf
  /\brmdir\b/i,
  /\bdel\s+\/[a-z]/i,           // del /s /q
  // V95: format 必须带磁盘/分区上下文才算危险——否则 PowerShell 的
  // Format-Table / Format-List / -Format 全被误杀（真机实锤把无害命令拦下弹确认）。
  /\bformat\s+[a-z]:/i,         // format C:
  /\bformat\b.*\/(fs|q|x)\b/i,  // format ... /fs:ntfs /q
  /\bFormat-Volume\b/i,         // PowerShell 真·格式化卷
  /\bmkfs\b/i,
  /\bdd\s+if=/i,
  /\b(shutdown|reboot|halt|poweroff)\b/i,
  /\bkill(all)?\s+-9/i,
  /:\(\)\s*\{.*\}\s*;/,         // fork bomb
  />\s*\/dev\/sd/i,
  /\bchmod\s+-R\b/i,
  /\bgit\s+push\s+.*--force/i,
  /\bnpm\s+publish\b/i,
  /\bcurl\b.*\|\s*(sudo\s+)?(ba)?sh/i,   // curl | sh
  /\bwget\b.*\|\s*(sudo\s+)?(ba)?sh/i,
  /\bRemove-Item\b.*-Recurse\b.*-Force\b/i,   // PS 强制递归删除
];

/** 判断命令是否危险（需确认）。返回 {danger, reason}。纯函数。 */
function assessCommand(cmd) {
  const s = String(cmd || "");
  for (const re of DANGER_PATTERNS) {
    if (re.test(s)) return { danger: true, reason: `命中危险模式: ${re.source.slice(0, 40)}` };
  }
  // sudo 单列：提权操作一律确认
  if (/\bsudo\b/.test(s)) return { danger: true, reason: "提权操作 (sudo)" };
  return { danger: false, reason: "" };
}

/** Computer Use 工具的 OpenAI function-calling schema（注入 LLM）。 */
const TOOLS = [
  {
    type: "function",
    function: {
      name: "run_shell",
      description: "在用户电脑上执行一条 shell 命令并返回输出（Windows=PowerShell，Linux/Mac=bash）。" +
        "危险命令会先请用户确认。Windows 要点：取环境变量用 $env:USERPROFILE（不是 %USERPROFILE%）；" +
        "要查'屏幕开着什么/在跑什么程序/用户目录'请优先用 get_system_info 工具而非自己拼命令。" +
        "命令输出已统一为 UTF-8。",
      parameters: {
        type: "object",
        properties: {
          command: { type: "string", description: "要执行的命令（单条）" },
          cwd: { type: "string", description: "工作目录（可选，默认用户主目录）" },
        },
        required: ["command"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "read_file",
      description: "读取用户电脑上一个文本文件的内容。",
      parameters: {
        type: "object",
        properties: { path: { type: "string", description: "文件绝对路径" } },
        required: ["path"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "write_file",
      description: "把内容写入用户电脑上的文件（覆盖）。写入前会请用户确认。",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "文件绝对路径" },
          content: { type: "string", description: "要写入的完整内容" },
        },
        required: ["path", "content"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "list_dir",
      description: "列出用户电脑上一个目录的文件和子目录。",
      parameters: {
        type: "object",
        properties: { path: { type: "string", description: "目录绝对路径" } },
        required: ["path"],
      },
    },
  },
];

// 视觉型工具（截屏）——仅在配置了视觉模型时注入。Marvis 同款：截屏理解屏幕，不做远程控制。
const VISION_TOOLS = [
  {
    type: "function",
    function: {
      name: "capture_screen",
      description: "截取当前屏幕画面并查看，用于理解屏幕上正在显示什么（如某个窗口的内容、报错信息、UI 状态）。截屏仅用于理解，不做任何远程控制。",
      parameters: {
        type: "object",
        properties: {
          reason: { type: "string", description: "为什么需要看屏幕（如'查看当前报错弹窗'）" },
        },
      },
    },
  },
  // V100: 视觉元素定位（OCR grounding）。给一段按钮/控件上的文字，主进程对当前
  // 屏幕做 OCR，再用 cu-grounding 的文本相似度匹配到具体元素，返回归一化坐标
  // （0..1000）+ 置信度。让模型"点登录"时拿到精确坐标，而非肉眼估（估的常点空）。
  // 只读：仅识别不操作；返回的坐标交给 computer 工具点击时仍走安全策略/禁区。
  {
    type: "function",
    function: {
      name: "locate_element",
      description:
        "在当前屏幕上按文字定位一个可点击元素（按钮/链接/菜单项/输入框标签）。" +
        "给出元素上显示的文字（如\"登录\"\"保存\"\"取消\"），返回该元素中心的归一化坐标 " +
        "(x,y) 0..1000、匹配置信度，以及若干备选。" +
        "用法：要点某个按钮时，先用本工具拿到精确坐标，再用 computer 的 left_click 点该坐标，" +
        "不要凭截图肉眼猜坐标。若返回 found=false 或 ambiguous=true，请改用截图判断或换更精确的文字。",
      parameters: {
        type: "object",
        properties: {
          text: { type: "string", description: "目标元素上显示的文字（尽量用完整、唯一的可见文字）" },
        },
        required: ["text"],
      },
    },
  },
];

// V95: 系统信息工具——"屏幕开着什么/在跑什么程序"是高频意图，
// 让模型现编 PowerShell 极易栽在引号转义+编码上（真机实锤 6 连败）。
// 这里直接结构化返回，主进程用稳定实现取数。
const SYSTEM_TOOLS = [
  {
    type: "function",
    function: {
      name: "get_system_info",
      description: "获取当前系统状态的结构化信息：打开的可见窗口列表（标题+进程）、" +
        "操作系统、用户主目录、当前时间。需要了解'屏幕上开着什么''在运行什么程序'" +
        "'我的用户目录是什么'时，优先用本工具，不要自己写命令去查。",
      parameters: {
        type: "object",
        properties: {
          what: { type: "string", enum: ["windows", "all"],
                  description: "windows=只要可见窗口列表；all=窗口+系统概况（默认 all）" },
        },
      },
    },
  },
];

const VISION_TOOLS_ALL = [...VISION_TOOLS];

// V99 Computer Use 二期：GUI 动作工具（单一 computer 工具，action 字段分发，
// 对标 Anthropic Computer Use API）。坐标用归一化 0..1000（分辨率无关）——
// 模型在截屏缩略图上看到的坐标直接给，主进程映射回真实像素。
// 仅在"视觉模式 + 已显式启用 GUI 控制"时注入（默认不给，避免误触）。
const CONTROL_TOOLS = [
  {
    type: "function",
    function: {
      name: "computer",
      description:
        "控制鼠标和键盘操作当前屏幕（点击、输入、按键、滚动、拖拽）。坐标系统为归一化 " +
        "0..1000：屏幕左上角是 (0,0)，右下角是 (1000,1000)，与实际分辨率无关——" +
        "直接给你在截屏里看到的相对位置即可。每次操作后建议配合 capture_screen 确认结果。" +
        "破坏性操作（如 Win+R 运行命令）会请求用户确认。",
      parameters: {
        type: "object",
        properties: {
          action: {
            type: "string",
            enum: ["left_click", "right_click", "middle_click", "double_click",
                   "mouse_move", "left_click_drag", "type", "key", "scroll",
                   "cursor_position", "wait"],
            description: "动作类型",
          },
          x: { type: "number", description: "横坐标 0..1000（点击/移动/滚动/拖拽起点需要）" },
          y: { type: "number", description: "纵坐标 0..1000" },
          x2: { type: "number", description: "拖拽终点横坐标 0..1000（left_click_drag）" },
          y2: { type: "number", description: "拖拽终点纵坐标 0..1000" },
          text: { type: "string", description: "要输入的文本（type 动作）" },
          keys: { type: "string", description: "组合键，如 'ctrl+s'、'enter'、'alt+tab'（key 动作）" },
          scroll_direction: { type: "string", enum: ["up", "down", "left", "right"], description: "滚动方向（scroll）" },
          scroll_amount: { type: "number", description: "滚动格数 1..50（scroll，默认 3）" },
          ms: { type: "number", description: "等待毫秒数 0..10000（wait）" },
        },
        required: ["action"],
      },
    },
  },
];

const TOOL_META = {
  run_shell:      { label: "执行命令", readonly: false },
  read_file:      { label: "读取文件", readonly: true },
  write_file:     { label: "写入文件", readonly: false },
  list_dir:       { label: "列出目录", readonly: true },
  capture_screen: { label: "截取屏幕", readonly: true },
  get_system_info: { label: "系统信息", readonly: true },
  locate_element: { label: "定位元素", readonly: true },
  computer:       { label: "屏幕操作", readonly: false },
};

// V100: 视觉定位执行的格式化层（纯函数，可测）。主进程对屏幕做 OCR 得到
// elements（[{text,x1,y1,x2,y2}]）后，调本函数算定位并产出给模型的结构化文本。
// OCR 引擎本身由主进程在真机提供（系统 OCR / 视觉服务），此处只做匹配+措辞。
const grounding = require("./modules/cu-grounding");

function runLocate(elements, query, screen, opts) {
  const loc = grounding.locateElement(elements, query, screen, opts);
  if (!loc.found) {
    const alt = (loc.alternatives || []).slice(0, 3)
      .map((a) => `「${a.text}」(${a.score})`).join("、");
    return {
      ok: false,
      found: false,
      output: `未能定位「${query}」。${loc.reason || ""}` + (alt ? ` 屏幕上较接近的文字：${alt}。请换更精确的文字，或用 capture_screen 看屏判断。` : ""),
    };
  }
  const amb = loc.ambiguous
    ? "（注意：屏幕上有多个相近文字，若点错请用更唯一的文字重新定位）" : "";
  return {
    ok: true,
    found: true,
    x: loc.nx, y: loc.ny, confidence: loc.score,
    output: `已定位「${loc.text}」→ 坐标 (${loc.nx}, ${loc.ny})，置信度 ${loc.score}${amb}。可用 computer left_click 点击该坐标。`,
  };
}

/** 哪些工具调用需要用户确认（写操作 + 危险 shell）。纯函数。 */
function needsConfirm(toolName, args) {
  if (toolName === "write_file") return { confirm: true, reason: "写入文件: " + (args && args.path || "") };
  if (toolName === "run_shell") {
    const a = assessCommand(args && args.command);
    return { confirm: a.danger, reason: a.reason };
  }
  return { confirm: false, reason: "" };   // read_file / list_dir 只读，免确认
}

module.exports = { assessCommand, needsConfirm, TOOLS, VISION_TOOLS, SYSTEM_TOOLS, CONTROL_TOOLS, TOOL_META, DANGER_PATTERNS, runLocate, grounding };

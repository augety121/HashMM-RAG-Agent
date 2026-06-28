/** desktop/agent-loop.js — 真·Agent 循环内核（harness → loop → computer use，V103）。
 *
 * 背景：此前多步循环散在前端——前端调 llm:chatTools 拿 message → 自己解析 tool_calls →
 * 逐个 cu:exec → 把结果拼回 messages → 再调。逻辑碎、没有统一的步数/错误/安全/事件管理，
 * 正是"表面工程"的部分。本模块把这套**多步编排**收进一个内聚、可测、带安全闸与事件流的
 * 内核，对标 Claude Code / Codex 的 agent loop。
 *
 * 复用项目已有件（不另造轮子）：
 *   - 工具 schema：computeruse.js 的 TOOLS / SYSTEM_TOOLS / VISION_TOOLS / CONTROL_TOOLS
 *   - 安全闸：computeruse.needsConfirm(name,args) → {confirm,reason}（write_file 必确认、
 *            run_shell 命中危险模式才确认、只读工具免确认）——单一事实来源
 *   - 真正执行：注入的 execTool（生产里 = main.js 的 cu:exec 守卫链 + 实际 fs/shell）
 *   - 模型调用：注入的 callModel（生产里 = main.js 的 llm:chatTools，非流式 function calling）
 *
 * 设计：纯编排、零 electron 依赖；callModel/execTool/confirm/onEvent 全可注入 → 沙箱里
 * 用假模型 + 假执行器完整冒烟（见 tests-node/test_agent-loop.js，已实测通过）。
 */
"use strict";
const CU = require("./computeruse");

class AgentLoop {
  /**
   * @param {object} deps
   *   callModel(messages, tools) -> Promise<{ok, message?, error?}>
   *       message = OpenAI 助手消息对象（可能含 tool_calls）
   *   execTool({name, args}) -> Promise<{ok, output?, error?, vision?, image?}>
   *   confirm({name, args, reason}) -> Promise<boolean>   // 危险工具的 human-in-the-loop
   *       不注入时默认放行（生产里建议接 UI/dialog；execTool=cu:exec 本身也有守卫闸做二次兜底）
   *   onEvent(ev) -> void          // 步骤事件流（给 cockpit 实时显示每一步）
   *   maxSteps (默认 16)           // 防失控
   *   needsConfirm (默认 CU.needsConfirm)
   */
  constructor(deps = {}) {
    this.callModel = deps.callModel || (async () => ({ ok: false, error: "未注入 callModel" }));
    this.execTool = deps.execTool || (async () => ({ ok: false, error: "未注入 execTool" }));
    this.confirm = deps.confirm || (async () => true);
    this.onEvent = deps.onEvent || (() => {});
    this.maxSteps = deps.maxSteps || 16;
    this.needsConfirm = deps.needsConfirm || CU.needsConfirm;
    this._abort = false;
  }

  /** 外部可随时中断（如用户点"停止"）。当前步执行完即停。 */
  abort() { this._abort = true; }

  _emit(type, data) { try { this.onEvent(Object.assign({ type }, data || {})); } catch (_e) { /* */ } }

  /**
   * 跑一个目标到完成（无 tool_calls 的助手回复 = 终态）或到达上限/被中断。
   * @param {object} o
   *   goal    string   用户目标（追加为 user 消息）
   *   system  string   系统提示（可选）
   *   tools   array    注入 LLM 的工具数组（默认 CU.TOOLS + CU.SYSTEM_TOOLS）
   *   history array    既有对话（可选，多轮续接）
   * @returns {Promise<{ok, finalText?, steps, messages, error?, stopped?}>}
   */
  async run(o = {}) {
    const tools = o.tools || [].concat(CU.TOOLS, CU.SYSTEM_TOOLS);
    const messages = [];
    if (o.system) messages.push({ role: "system", content: o.system });
    if (Array.isArray(o.history)) messages.push.apply(messages, o.history);
    if (o.goal) messages.push({ role: "user", content: o.goal });

    this._emit("start", { goal: o.goal || "", maxSteps: this.maxSteps });

    let steps = 0;
    while (steps < this.maxSteps) {
      if (this._abort) { this._emit("stopped", { reason: "aborted", steps }); return { ok: true, stopped: true, steps, messages }; }
      steps++;

      // 1) 调模型
      const mr = await this.callModel(messages, tools);
      if (!mr || !mr.ok || !mr.message) {
        const err = (mr && mr.error) || "模型调用失败";
        this._emit("error", { error: err, step: steps });
        return { ok: false, error: err, steps, messages };
      }
      const msg = mr.message;
      messages.push(msg);
      const calls = Array.isArray(msg.tool_calls) ? msg.tool_calls : [];
      this._emit("assistant", { step: steps, content: msg.content || "", toolCalls: calls.length });

      // 2) 没有工具调用 = 模型给出终态回答，循环结束
      if (!calls.length) {
        this._emit("done", { step: steps, finalText: msg.content || "" });
        return { ok: true, finalText: msg.content || "", steps, messages };
      }

      // 3) 逐个执行工具，结果按 OpenAI tool 消息格式塞回 messages
      for (let i = 0; i < calls.length; i++) {
        const call = calls[i];
        if (this._abort) break;
        const name = call.function && call.function.name;
        let args = {};
        try { args = call.function && call.function.arguments ? JSON.parse(call.function.arguments) : {}; }
        catch (_e) { args = {}; }

        this._emit("tool_call", { step: steps, id: call.id, name, args });

        // 3a) 安全闸（复用 computeruse 的统一判定）
        let gate = { confirm: false, reason: "" };
        try { gate = this.needsConfirm(name, args) || gate; } catch (_e) { /* */ }
        if (gate.confirm) {
          this._emit("confirm_needed", { step: steps, id: call.id, name, args, reason: gate.reason });
          let okGo = false;
          try { okGo = await this.confirm({ name, args, reason: gate.reason }); } catch (_e) { okGo = false; }
          if (!okGo) {
            messages.push({ role: "tool", tool_call_id: call.id, content: "用户拒绝了此操作。请改用其它方式或向用户说明。" });
            this._emit("tool_result", { step: steps, id: call.id, name, ok: false, denied: true });
            continue;
          }
        }

        // 3b) 执行
        let res;
        try { res = await this.execTool({ name, args }); }
        catch (e) { res = { ok: false, error: (e && e.message) ? e.message : String(e) }; }
        res = res || { ok: false, error: "执行无返回" };

        // 3c) 结果回填。视觉工具（截屏）的图片另以 user 视觉消息补充
        //     （多数 API 不接受 tool 角色消息里带图）。
        const text = res.ok ? (res.output || "(无输出)") : ("错误: " + (res.error || "未知错误"));
        messages.push({ role: "tool", tool_call_id: call.id, content: String(text).slice(0, 30000) });
        if (res.vision && res.image) {
          messages.push({ role: "user", content: [
            { type: "text", text: "（上一步截屏画面）" },
            { type: "image_url", image_url: { url: res.image } },
          ] });
        }
        this._emit("tool_result", { step: steps, id: call.id, name, ok: !!res.ok, output: String(text).slice(0, 400) });
      }
    }

    this._emit("max_steps", { steps });
    return { ok: true, stopped: true, steps, messages, error: "达到最大步数 " + this.maxSteps };
  }
}

module.exports = { AgentLoop };

/**
 * desktop/modules/cu-guard.js — 桌面 Computer Use 工具守卫链（V99，Harness 工程）。
 *
 * 对标后端 hashmm/agent/tool_pipeline.py 的 harness 设计：每个工具调用在真正
 * 执行前，流经一条**显式有序、各自独立可测**的守卫链，而不是散落在 cu:exec 里
 * 的 if/else。把"shell 危险确认 + 文件写确认 + GUI 动作策略闸 + 坐标禁区"收敛成
 * 统一裁决：
 *
 *   工具调用 → [ReadOnlyGuard] → [ShellDangerGuard] → [FileWriteGuard]
 *            → [GuiPolicyGuard] → 裁决（allow / confirm / deny）
 *
 * 设计原则（V306 修订 —— 安全守卫 fail-closed）：
 * - 守卫只【读】请求做判定，不执行副作用；
 * - 守卫顺序即语义（先 deny 类，后 confirm 类）；
 * - 守卫异常的裁决按工具风险分级（对标后端 tool_pipeline 的失效策略）：
 *     · 写类工具（run_shell / write_file / computer 的写动作）→ 异常即【拒绝】。
 *       对拥有 Shell、文件写入、鼠标键盘能力的 Agent，策略层故障时放行等于无门禁
 *       （fail-open），这是 V305 审计判定的 P0（DESK-P0-01），本版改为 fail-closed。
 *     · 只读工具（read_file / screenshot / mouse_move 等）→ 异常交由后续守卫/执行层，
 *       harness 故障不拦无副作用的读操作（保持旧行为，避免故障放大为全面瘫痪）。
 * - 纯逻辑、依赖注入（assessCommand / validateAction / applyPolicy 注入）→
 *   tests-node 直接冒烟，不依赖 electron。
 *
 * 返回统一裁决对象 {decision: "allow"|"confirm"|"deny", reason, plan?, title?, detail?}：
 * - allow   直接执行
 * - confirm 弹原生确认框，用户允许才执行
 * - deny    直接拒绝（如只读模式下的写操作）
 */
"use strict";

/**
 * @param {object} deps
 *   assessCommand (cmd) => {danger, reason}            shell 危险判定（computeruse.js）
 *   validateAction (action, screen) => {ok, plan, error}  GUI 动作校验（cu-actions.js）
 *   applyPolicy (plan, policy) => {allow, needsConfirm, reason}  GUI 策略闸
 *   describeAction (plan) => string                    动作摘要
 */
function createGuardChain(deps) {
  const { assessCommand, validateAction, applyPolicy, describeAction } = deps;

  // 写类工具集合：守卫异常时必须 fail-closed 的对象。
  // computer 的只读动作（plan.write === false，如 screenshot / mouse_move）不算写类；
  // computer 无 plan（无法证明只读）按写类从严处理。
  const WRITE_CLASS = { run_shell: 1, write_file: 1, computer: 1 };
  function isWriteClass(ctx) {
    if (!WRITE_CLASS[ctx.name]) return false;
    if (ctx.name === "computer" && ctx.plan && ctx.plan.write === false) return false;
    return true;
  }

  // 各守卫返回 null=放行（交下一个），或裁决对象=终止链。
  // 异常：写类工具 → 立即 deny（fail-closed）；只读工具 → 当放行交下一个守卫。
  const guards = [
    // 1. 只读模式：任何写工具直接拒绝（最高优先级，先于一切 confirm）
    function readOnlyGuard(ctx) {
      if (!ctx.policy || !ctx.policy.cuReadOnly) return null;
      const writeTools = { run_shell: 1, write_file: 1, computer: 1 };
      if (writeTools[ctx.name]) {
        // computer 的只读动作（move/screenshot）豁免
        if (ctx.name === "computer" && ctx.plan && !ctx.plan.write) return null;
        return { decision: "deny", reason: "只读模式：已禁止所有写操作" };
      }
      return null;
    },

    // 2. shell 危险命令 → 确认
    function shellDangerGuard(ctx) {
      if (ctx.name !== "run_shell") return null;
      const a = assessCommand(ctx.args && ctx.args.command);
      if (a && a.danger) {
        return { decision: "confirm", reason: a.reason,
                 title: "Computer Use 需要确认",
                 detail: `命令：${ctx.args.command}\n原因：${a.reason}` };
      }
      return null;
    },

    // 3. 文件写入 → 确认
    function fileWriteGuard(ctx) {
      if (ctx.name !== "write_file") return null;
      const p = (ctx.args && ctx.args.path) || "";
      const len = ((ctx.args && ctx.args.content) || "").length;
      return { decision: "confirm", reason: "写入文件",
               title: "Computer Use 需要确认",
               detail: `写入：${p}（${len} 字）` };
    },

    // 4. GUI 动作 → 校验 + 策略闸（危险组合键/坐标禁区/全确认）
    function guiPolicyGuard(ctx) {
      if (ctx.name !== "computer" || !ctx.plan) return null;
      const pol = applyPolicy(ctx.plan, {
        confirmAllWrites: !!(ctx.policy && ctx.policy.cuConfirmAllWrites),
        blockWrites: false,   // 只读已由 readOnlyGuard 处理
      });
      if (!pol.allow) return { decision: "deny", reason: pol.reason };
      if (pol.needsConfirm) {
        return { decision: "confirm", reason: pol.reason,
                 title: "屏幕操作需要确认",
                 detail: `Agent 想执行：${describeAction(ctx.plan)}\n${pol.reason || "此操作可能影响系统，请确认。"}` };
      }
      return null;
    },
  ];

  /**
   * 裁决一次工具调用。
   * @param {object} req  {name, args, plan?, policy?}
   *   plan 仅 computer 工具需要（调用方先 validateAction 得到）；
   *   policy = {cuReadOnly?, cuConfirmAllWrites?}
   * @returns {{decision, reason, plan?, title?, detail?}}
   */
  function decide(req) {
    const ctx = { name: req.name, args: req.args || {}, plan: req.plan || null, policy: req.policy || {} };
    for (const g of guards) {
      let r = null;
      try {
        r = g(ctx);
      } catch (e) {
        // fail-closed：写类工具在守卫异常时拒绝——策略层故障不能成为放行理由。
        if (isWriteClass(ctx)) {
          return {
            decision: "deny",
            failClosed: true,
            reason: `安全守卫异常，已按 fail-closed 拒绝写类操作（${g.name || "guard"}: ${(e && e.message) || e}）`,
          };
        }
        r = null;   // 只读工具：守卫故障不拦无副作用操作，交下一个守卫
      }
      if (r) return r;
    }
    return { decision: "allow", reason: "" };
  }

  return { decide, _guards: guards };
}

module.exports = { createGuardChain };

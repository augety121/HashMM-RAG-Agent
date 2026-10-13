/** cu.ts — 主界面「电脑操作」循环引擎（V99：Loop 工程正规化）。
 *  ① 视觉：capture_screen 工具静默全屏抓图 → 多模态注入下一轮（模型不吃图自动降级）；
 *  ② GUI 控制：启用 computer 工具（点击/输入/按键/滚动），动作经主进程校验+确认+回放；
 *  ③ Loop 工程：AgentLoopController 统一停机判定——超步/无进展熔断/外部中断，
 *     结构化停机原因，不再是裸 for 循环空跑满轮；
 *  ④ 步骤回调 onStep → 接入既有 liveTimeline 卡片（与后端 Agent 同一套呈现）；
 *  ⑤ 危险操作确认在主进程原生对话框（cu:exec 内置，拒绝即回传"用户拒绝"）。
 *  LLM 决策走后端 /api/llm/tools（登录即用），工具在本机执行（hashmmCU 桥）。 */
import { getCU, getLocal } from "@/lib/desktop";
import { authHeaders } from "@/lib/api";
import { AgentLoopController, budgetForQuery } from "@/lib/agentLoop";

const SYS = "你是能操控用户电脑的助手。可用工具：run_shell/read_file/write_file/list_dir 完成操作，" +
  "get_system_info 获取系统状态（打开的窗口、OS、用户目录——查'屏幕开着什么/在跑什么/用户目录'时优先用它，别自己拼命令），" +
  "capture_screen 截取并查看当前屏幕画面（理解报错弹窗、UI 状态），" +
  "locate_element 视觉定位：给它一段按钮/控件上的文字（如\"登录\"\"保存\"），它用 OCR 在当前屏幕上找到该元素并返回归一化坐标，比你肉眼估坐标准得多，" +
  "computer 直接操作屏幕（点击/输入/按键/滚动/拖拽，坐标用 0..1000 归一化，即你在截图里看到的相对位置）。" +
  "重要：① 需要了解屏幕/窗口/系统信息时，先用 get_system_info，不要现写 PowerShell；" +
  "② 用 computer 点击某个按钮/控件前，优先用 locate_element 拿到它的精确坐标再点，不要凭截图肉眼猜坐标（猜的坐标常点空）；定位不到或有歧义时再退回截图判断；" +
  "③ 操作 GUI 前先 capture_screen 看清当前画面，操作后再截一次确认结果；" +
  "④ Windows 上 run_shell 是 PowerShell，取环境变量用 $env:USERPROFILE，输出已是 UTF-8；" +
  "⑤ 每步先用一句话说明意图再调用工具；路径未指明则在用户主目录下操作。任务完成后给出清晰总结。";

type Msg = { role: string; content: string | unknown; tool_call_id?: string; tool_calls?: unknown[] };
export type CuStep = { node: string; detail: string; status: "running" | "done" | "error" };

async function callLLM(msgs: Msg[], tools: unknown[]): Promise<Msg & { tool_calls?: { id: string; function: { name: string; arguments: string } }[] }> {
  const res = await fetch("/api/llm/tools", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ messages: msgs, tools }),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok || !j.ok) throw new Error(j?.detail || j?.error || res.statusText || "模型调用失败");
  return j.message;
}

export async function runComputerUse(query: string, opts: {
  history?: { role: string; content: string }[];
  onText?: (t: string) => void;
  onStep?: (s: CuStep) => void;
  shouldStop?: () => boolean;
  enableControl?: boolean;     // V99: 是否放开 GUI 控制（点击/输入）。默认开。
  taskId?: string;             // 固定到发起 Chat；切换界面不能改变后台任务归属。
}): Promise<string> {
  const cu = getCU();
  if (!cu) throw new Error("当前环境不支持电脑操作（需在桌面版使用）");
  const local = getLocal();
  const lease = local?.gitWorkspaceLeaseAcquire ? await local.gitWorkspaceLeaseAcquire() : null;
  if (lease && !lease.ok) throw new Error(lease.error || "当前工作区正被另一个任务使用");
  try {
  const control = opts.enableControl !== false;
  const tools = await cu.tools(true, control);     // V99: vision + （可选）GUI 控制
  // V99: 读当前安全级别，注入系统提示让模型知道权限边界（避免反复试被拒的操作）
  let safetyNote = "";
  try {
    const s = cu.getSafety ? await cu.getSafety() : null;
    if (s && s.level === "readonly") safetyNote = "\n【当前为只读安全模式】你只能查看和读取，任何点击/输入/写文件/运行命令都会被拒绝。请只做观察与分析，不要尝试修改类操作。";
    else if (s && s.level === "strict") safetyNote = "\n【当前为高安全模式】你的每一次写入/点击/输入都会弹出确认框等待用户批准，请减少不必要的操作步骤。";
  } catch { /* 读不到就用默认 */ }
  const msgs: Msg[] = [
    { role: "system", content: SYS + safetyNote },
    ...(opts.history || []).filter(m => m.content && (m.role === "user" || m.role === "assistant")).slice(-8),
    { role: "user", content: query },
  ];
  let acc = "";
  let visionDegraded = false;
  const emit = (t: string) => { acc += t; opts.onText?.(t); };
  const loop = new AgentLoopController(budgetForQuery(query));   // V99 Loop 工程：按任务复杂度自适应预算

  while (!loop.stopped) {
    if (!loop.beginRound(!!opts.shouldStop?.())) {
      emit(`\n\n[${loop.summary()}]`);
      break;
    }
    let m: Awaited<ReturnType<typeof callLLM>>;
    try {
      m = await callLLM(msgs, tools);
    } catch (e) {
      // 多模态降级——末轮注入了截图但后端模型不支持图片 → 摘掉图片重试一次
      const last = msgs[msgs.length - 1];
      const hasImg = Array.isArray(last?.content) &&
        (last.content as { type?: string }[]).some(p => p?.type === "image_url");
      if (hasImg && !visionDegraded) {
        visionDegraded = true;
        const textPart = (last.content as { type?: string; text?: string }[])
          .filter(p => p?.type === "text").map(p => p.text || "").join("\n");
        msgs[msgs.length - 1] = { role: "user", content: textPart + "\n（注：当前模型不支持图片，截图内容无法注入）" };
        opts.onStep?.({ node: "vision", detail: "当前默认模型不支持图片理解，已降级为纯文本继续", status: "error" });
        emit("\n（当前默认模型不支持图片理解，截图已跳过）");
        loop.state.step -= 1;   // 这轮不计步（纯降级重试）
        continue;
      }
      loop.fail(String((e as Error)?.message || e));
      emit(`\n\n[${loop.summary()}]`);
      throw e;
    }
    msgs.push(m);
    if (typeof m.content === "string" && m.content) emit((acc ? "\n\n" : "") + m.content);
    const calls = m.tool_calls || [];
    if (!calls.length) { loop.markCompleted(); break; }

    for (const c of calls) {
      if (opts.shouldStop?.()) break;
      const name = c.function?.name || "?";
      let args: Record<string, unknown> = {};
      try { args = JSON.parse(c.function?.arguments || "{}"); } catch { /* 保持空 */ }

      // V99 Loop：登记调用 + 无进展熔断
      const prog = loop.recordToolCall(name, args);
      const brief = JSON.stringify(args);
      const stepDetail = `${name} ${brief.length > 100 ? brief.slice(0, 100) + "…" : brief}`;
      opts.onStep?.({ node: name, detail: stepDetail, status: "running" });
      emit(`\n\n→ 执行 ${stepDetail}`);

      let out: { ok?: boolean; output?: string; error?: string; image?: string } | string;
      try { out = await cu.exec(name, args, opts.taskId); }
      catch (e) { out = { ok: false, error: String((e as Error)?.message || e) }; }

      const o = typeof out === "string" ? { ok: true, output: out } : (out || { ok: false, error: "无返回" });
      let outStr = o.output || o.error || JSON.stringify(o);
      if (outStr.length > 1500) outStr = outStr.slice(0, 1500) + `…[截断，共 ${outStr.length} 字符]`;
      emit(`\n　· ${o.ok ? "结果" : "失败"} ${outStr.length > 220 ? outStr.slice(0, 220) + "…" : outStr}`);
      opts.onStep?.({ node: name, detail: stepDetail, status: o.ok ? "done" : "error" });

      // 无进展：给模型显式信号，促其换策略（结果回灌里附提示）
      let toolContent = outStr;
      if (prog.repeated) toolContent += "\n[系统提示：你刚重复了上一步相同的操作。若未取得进展，请换一种方法或给出最终结论。]";
      msgs.push({ role: "tool", tool_call_id: c.id, content: toolContent });

      // 截屏视觉注入——抓图成功 → 以多模态 user 消息进入下一轮（模型"看见"屏幕）
      if (o.image && !visionDegraded) {
        msgs.push({
          role: "user",
          content: [
            { type: "text", text: "（这是刚截取的当前屏幕画面，请基于它继续）" },
            { type: "image_url", image_url: { url: o.image } },
          ],
        });
        opts.onStep?.({ node: "vision", detail: "屏幕画面已注入视觉理解", status: "done" });
      }

      // V103.7 evaluator-acts（借鉴 Orange Book：评判要"动手看结果"，不能只信模型自述）：
      // GUI 状态变更动作（点击/输入/按键/滚动/拖拽等）执行后，自动补一张截图注入下一轮，
      // 让"操作后核对结果"成为**结构性步骤**，而非依赖模型记得调 capture_screen。
      // 仅对 computer 工具的变更类动作触发、且本步未自带截图、视觉未降级时；截图失败不影响主流程。
      const guiChanged = name === "computer" && o.ok && !o.image && !visionDegraded
        && /\b(click|type|key|press|scroll|drag|move|double|right|left|hotkey|input)\b/i.test(brief);
      if (guiChanged && !opts.shouldStop?.()) {
        try {
          const shot = await cu.exec("capture_screen", {}, opts.taskId);
          const so = (shot && typeof shot === "object") ? (shot as { image?: string }) : null;
          if (so && so.image) {
            msgs.push({
              role: "user",
              content: [
                { type: "text", text: "（这是上一步操作后自动截取的屏幕：请核对该操作是否达成预期。若未达成，请换方法或修正，不要假设已成功。）" },
                { type: "image_url", image_url: { url: so.image } },
              ],
            });
            opts.onStep?.({ node: "verify", detail: "操作后自动截图核对结果", status: "done" });
          }
        } catch (_e) { /* 自动核对截图失败：忽略，不打断主流程 */ }
      }

      if (prog.noProgress) { emit(`\n\n[${loop.summary()}]`); break; }
    }
  }
  return acc;
  } finally {
    if (lease?.token && local?.gitWorkspaceLeaseRelease) {
      await local.gitWorkspaceLeaseRelease(lease.token).catch(() => null);
    }
  }
}

export async function cuSave(
  convId: string,
  userContent: string,
  assistantContent: string,
  workMethod?: { retrieval: string; effort: string; run_mode: string },
) {
  await fetch("/api/llm/cu_save", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      conv_id: convId,
      user_content: userContent,
      assistant_content: assistantContent,
      work_method: workMethod,
    }),
  });
}

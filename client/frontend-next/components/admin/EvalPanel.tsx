"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import * as api from "@/lib/api";
import { Play, GitCompare, TrendingUp, TrendingDown, Minus, Award, Download, Inbox, CheckCircle2, XCircle } from "lucide-react";
import { EvalCaseList, EvalCategoryBars, EvalFailBuckets, EvalTrend, EvalDiffPairs, QualityDashboard } from "./EvalViews";
import type { ModelConfig } from "@/lib/types";

interface RunMeta { run_id: string; tag: string; saved_at: number; avg_score: number; pass_rate: number; }

export function EvalPanel() {
  const [runs, setRuns] = useState<RunMeta[]>([]);
  const [running, setRunning] = useState(false);
  const [runMsg, setRunMsg] = useState("");   // V103.90 评测进度/完成提示（应对网关超时）
  const [tag, setTag] = useState("");
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [judgeModel, setJudgeModel] = useState("");   // 评审模型 id（空=默认模型）
  const [evalMode, setEvalMode] = useState<"comprehensive" | "agentic" | "standard">("comprehensive");
  const [lastReport, setLastReport] = useState<Record<string, unknown> | null>(null);
  const [baseline, setBaseline] = useState("");
  const [candidate, setCandidate] = useState("");
  const [diff, setDiff] = useState<Record<string, unknown> | null>(null);
  const [quality, setQuality] = useState<Record<string, unknown> | null>(null);
  const [feedbackCases, setFeedbackCases] = useState<api.FeedbackCase[]>([]);
  const [feedbackPolicy, setFeedbackPolicy] = useState("");
  const [references, setReferences] = useState<Record<string, string>>({});
  const [reviewBusy, setReviewBusy] = useState("");
  const [reviewError, setReviewError] = useState("");
  const pollRef = useRef<number | null>(null);
  const settledRef = useRef(false);
  useEffect(() => () => { if (pollRef.current) clearTimeout(pollRef.current); }, []);

  const loadRuns = useCallback(async () => {
    try { const r = await api.listEvalRuns(); setRuns(r.runs || []); } catch { /* */ }
    try { setQuality(await api.getQualityDashboard(7)); } catch { /* */ }
    try { setModels(await api.listModels()); } catch { /* */ }
    try {
      const feedback = await api.listFeedbackCandidates("pending", 50);
      setFeedbackCases(feedback.cases || []); setFeedbackPolicy(feedback.policy || "");
    } catch { /* non-admin/offline keeps the section empty */ }
  }, []);
  useEffect(() => { loadRuns(); }, [loadRuns]);

  // 导出本次报告为 Markdown（方便贴给 Claude / 存档）
  function exportReport() {
    const s = (lastReport?.summary as Record<string, unknown>) || {};
    const results = (lastReport?.results as Record<string, unknown>[]) || [];
    const pct = (v: unknown) => v == null ? "—" : `${Math.round(Number(v) * 100)}%`;
    const lines: string[] = [];
    lines.push(`# HashMM 质量评测报告`);
    lines.push(`生成时间：${new Date().toLocaleString("zh-CN")}`);
    lines.push("");
    lines.push(`## 总览`);
    lines.push(`- 模式：${s.comprehensive ? "全面体检（合并全部内置用例集）" : "标准"}`);
    lines.push(`- 用例数：${s.n_cases_run ?? s.total ?? "—"}`);
    lines.push(`- 通过率：${pct(s.pass_rate)}`);
    lines.push(`- 平均分：${s.avg_score ?? "—"}`);
    lines.push(`- 评审分：${s.avg_judge_score ?? "—"}（评审模型：${s.judge_model ?? "—"}）`);
    if (s.retrieval && Object.keys(s.retrieval as object).length) {
      lines.push(`- 检索指标：${Object.entries(s.retrieval as Record<string, number>).map(([k, v]) => `${k}=${v}`).join(", ")}`);
    }
    if (s.layered) {
      const L = s.layered as Record<string, { score: number | null; n_cases: number }>;
      lines.push(`- 分层：检索 ${pct(L.retrieval?.score)} · 生成 ${pct(L.generation?.score)} · 端到端 ${pct(L.end_to_end?.score)}`);
    }
    if (s.by_category) {
      lines.push("");
      lines.push(`## 按类别（哪类能力弱一目了然）`);
      for (const [cat, c] of Object.entries(s.by_category as Record<string, { total: number; passed: number; pass_rate: number; avg_score: number }>)) {
        lines.push(`- ${cat}：${c.passed}/${c.total} 通过（${pct(c.pass_rate)}），均分 ${c.avg_score}`);
      }
    }
    const failed = results.filter(r => r.passed === false);
    if (failed.length) {
      lines.push("");
      lines.push(`## 失败用例（共 ${failed.length} 条）— 按类别归类，便于一次性修复`);
      const byCat: Record<string, Record<string, unknown>[]> = {};
      for (const r of failed) { const c = String(r.category || "其他"); (byCat[c] ||= []).push(r); }
      for (const [cat, items] of Object.entries(byCat)) {
        lines.push("");
        lines.push(`### 【${cat}】${items.length} 条`);
        items.slice(0, 30).forEach((r, i) => {
          lines.push(`${i + 1}. ${String(r.query || r.case_id || r.id || "")}`);
          const reason = r.fail_reason || r.judge_reason || r.judge_rationale;
          if (reason) lines.push(`   - 失败原因：${String(reason)}`);
          if (r.answer) lines.push(`   - 实际答案：${String(r.answer).slice(0, 300)}`);
          if (r.reference_answer) lines.push(`   - 参考答案：${String(r.reference_answer).slice(0, 200)}`);
        });
      }
    }
    const md = lines.join("\n");
    try {
      const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `eval-report-${new Date().toISOString().slice(0, 10)}.md`;
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    } catch { /* */ }
  }

  async function run() {
    if (running) return;
    setRunning(true); setLastReport(null); setRunMsg("正在启动评测…");
    settledRef.current = false;
    const runTag = tag || `run-${Date.now()}`;
    const startedAt = Date.now();

    // 先记下已有的 run_id，用于识别本次新产生的那条
    let beforeIds = new Set<string>();
    try { const r0 = await api.listEvalRuns(); beforeIds = new Set<string>((r0.runs || []).map((x: RunMeta) => x.run_id)); } catch { /* */ }

    const stopPoll = () => { if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; } };
    const finishWithReport = (rep: Record<string, unknown>) => {
      if (settledRef.current) return; settledRef.current = true;
      stopPoll(); setLastReport(rep); setRunMsg(""); setRunning(false); loadRuns();
    };
    const finishWithMeta = (m: RunMeta) => {
      if (settledRef.current) return; settledRef.current = true;
      stopPoll(); setRunning(false);
      setRunMsg(`评测完成：通过率 ${Math.round((m.pass_rate ?? 0) * 100)}% · 平均分 ${m.avg_score ?? "—"}。结果已存入下方「运行记录」，可用「对比两次运行」看明细。`);
      loadRuns();
    };

    // 发起评测。100 用例约十几分钟，客户端到服务器的网关（如反代/AutoDL）可能在请求跑完前
    // 就返回 502/超时 —— 但后端会把结果持久化进「运行记录」，所以这里改为轮询记录来判断完成，
    // 不再依赖那条可能被网关掐断的响应。若后端比网关快（小用例集），直连响应也会被采用。
    api.runEvalEnhanced(runTag, true, judgeModel, evalMode === "comprehensive", evalMode === "agentic" ? "agentic" : "")
      .then(rep => finishWithReport(rep as Record<string, unknown>))
      .catch(() => { /* 长任务下大概率是网关超时，忽略：靠轮询拿到已保存的 run */ });

    const MAX_WAIT = 30 * 60 * 1000;   // 30 分钟兜底
    const fmt = (s: number) => s >= 60 ? `${Math.floor(s / 60)}分${s % 60}秒` : `${s}秒`;
    const tick = async () => {
      if (settledRef.current) return;
      const elapsed = Math.round((Date.now() - startedAt) / 1000);
      setRunMsg(`评测进行中…已 ${fmt(elapsed)}（跑 golden 用例 + LLM 评审，100 例通常十几分钟）。可关掉此窗口，完成后「运行记录」会出现本次结果。`);
      if (Date.now() - startedAt > MAX_WAIT) {
        settledRef.current = true; setRunning(false);
        setRunMsg("评测超过 30 分钟仍未返回结果，请查看后端日志，或稍后点「刷新」看「运行记录」是否已出现本次结果。");
        return;
      }
      try {
        const r = await api.listEvalRuns();
        const list: RunMeta[] = r.runs || [];
        setRuns(list);
        const fresh = list.filter(x => !beforeIds.has(x.run_id));
        const found = fresh.find(x => x.tag === runTag) || fresh[0];
        if (found) { finishWithMeta(found); return; }
      } catch { /* 网络抖动，继续轮询 */ }
      pollRef.current = window.setTimeout(tick, 12000);
    };
    pollRef.current = window.setTimeout(tick, 12000);
  }

  async function compare() {
    if (!baseline || !candidate) { alert("请选择两个运行"); return; }
    try { setDiff(await api.compareEvalRuns(baseline, candidate)); }
    catch (e) { alert("对比失败：" + String(e)); }
  }

  async function reviewCandidate(item: api.FeedbackCase, decision: "approve" | "dismiss") {
    const reference = (references[item.id] || "").trim();
    if (decision === "approve" && reference.length < 5) {
      setReviewError("加入回归集前必须填写可信参考答案，不能把失败回答直接当作标准答案。");
      return;
    }
    setReviewBusy(item.id); setReviewError("");
    try {
      await api.reviewFeedbackCandidate(item.id, decision, reference);
      setFeedbackCases(current => current.filter(x => x.id !== item.id));
      setReferences(current => { const next = { ...current }; delete next[item.id]; return next; });
    } catch (error) {
      setReviewError(error instanceof Error ? error.message : "复核失败");
    } finally {
      setReviewBusy("");
    }
  }

  const card = { background: "var(--bg-secondary)", border: "1px solid var(--border)" };
  const sel = "px-2.5 py-1.5 rounded-lg text-[12px]";
  const selStyle = { background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" };

  const summary = lastReport?.summary as Record<string, unknown> | undefined;

  return (
    <div className="mt-6">
      <div className="mb-3">
        <h4 className="text-sm font-semibold flex items-center gap-1.5" style={{ color: "var(--text-primary)" }}>
          <Award size={14} /> RAG 质量评测
        </h4>
        <p className="text-[11px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
          跑 golden 用例，算检索指标 + LLM 评审分；改动前后对比，防止改退步
        </p>
      </div>

      {/* Run */}
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <input value={tag} onChange={e => setTag(e.target.value)} placeholder="本次运行标签 (如 改prompt前)"
          className={sel} style={{ ...selStyle, flex: 1, minWidth: 140 }} />
        <select value={judgeModel} onChange={e => setJudgeModel(e.target.value)} className={sel} style={selStyle}
          title="评审模型：选你的 Claude 模型可用 Claude 当评审（更强）；不选=用默认模型">
          <option value="">评审：默认模型</option>
          {models.map(m => <option key={m.id} value={m.id}>评审：{m.name || m.model_name}{m.provider === "anthropic" ? " (Claude)" : ""}</option>)}
        </select>
        <select value={evalMode} onChange={e => setEvalMode(e.target.value as "comprehensive" | "agentic" | "standard")}
          className="text-[12px] rounded-lg px-2 py-1.5"
          style={{ background: "var(--surface)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
          title="选择测评范围。困难智能体集=只跑长思考/长任务/解决问题/代码等高难度题（约136例，带参考答案+评分标准），比全面体检快。">
          <option value="comprehensive">全面体检（全部内置集，约283例）</option>
          <option value="agentic">困难智能体集（约136例·长思考/长任务/代码）</option>
          <option value="standard">标准集（约100例）</option>
        </select>
        <button onClick={run} disabled={running}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] text-white"
          style={{ background: "var(--accent)" }}>
          <Play size={13} /> {running ? "评测中…" : "运行评测"}
        </button>
      </div>
      {runMsg && (
        <div className="text-[11.5px] mb-4 px-3 py-2.5 rounded-lg leading-relaxed"
          style={{ background: "var(--bg-tertiary)", color: runMsg.startsWith("评测完成") ? "var(--success)" : "var(--text-secondary)" }}>
          {running && <span className="inline-block w-2 h-2 rounded-full mr-1.5 animate-pulse" style={{ background: "var(--accent)" }} />}
          {runMsg}
        </div>
      )}

      {/* v16 Phase 14: online quality dashboard (real traffic) */}
      <QualityDashboard data={quality} />

      <div className="rounded-xl p-4 mb-4" style={card}>
        <div className="flex items-start justify-between gap-3 mb-3">
          <div>
            <div className="text-[12px] font-semibold flex items-center gap-1.5" style={{ color: "var(--text-primary)" }}>
              <Inbox size={13} /> 真实失败回灌
              <span className="px-1.5 py-0.5 rounded-md text-[10px]" style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>
                待复核 {feedbackCases.length}
              </span>
            </div>
            <div className="text-[10.5px] mt-1" style={{ color: "var(--text-tertiary)" }}>
              {feedbackPolicy || "Chat 负反馈会携带真实运行证据进入这里，人工确认后才进入回归集。"}
            </div>
          </div>
        </div>
        {reviewError && <div className="text-[11px] mb-2" style={{ color: "var(--error)" }}>{reviewError}</div>}
        {feedbackCases.length === 0 ? (
          <div className="rounded-lg px-3 py-4 text-center text-[11px]" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>
            当前没有待复核的真实失败。这里不会用演示数据填充空状态。
          </div>
        ) : feedbackCases.map(item => (
          <div key={item.id} className="rounded-xl p-3 mb-2 last:mb-0" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
            <div className="flex items-center justify-between gap-2">
              <div className="text-[11px] font-semibold" style={{ color: "var(--text-primary)" }}>{item.reason_label || item.reason_code}</div>
              <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{new Date(item.updated_at * 1000).toLocaleString("zh-CN")}</div>
            </div>
            <div className="text-[11px] mt-1.5 leading-relaxed" style={{ color: "var(--text-secondary)" }}>
              <span style={{ color: "var(--text-tertiary)" }}>用户问题：</span>{item.query || "（未找到对应用户轮次）"}
            </div>
            <details className="mt-1.5 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
              <summary className="cursor-pointer">查看失败回答与用户补充</summary>
              <div className="mt-1.5 p-2 rounded-lg whitespace-pre-wrap max-h-40 overflow-auto" style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>
                {item.answer || "（空回答）"}{item.comment ? `\n\n用户补充：${item.comment}` : ""}
              </div>
            </details>
            <textarea value={references[item.id] || ""}
              onChange={e => setReferences(current => ({ ...current, [item.id]: e.target.value.slice(0, 12000) }))}
              placeholder="人工填写可信参考答案；审批后进入 held-out 回归集"
              className="w-full mt-2 rounded-lg px-2.5 py-2 text-[11px] min-h-[62px] resize-y outline-none"
              style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            <div className="flex justify-end gap-2 mt-2">
              <button onClick={() => reviewCandidate(item, "dismiss")} disabled={reviewBusy === item.id}
                className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[11px] disabled:opacity-40"
                style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                <XCircle size={12} /> 忽略
              </button>
              <button onClick={() => reviewCandidate(item, "approve")} disabled={reviewBusy === item.id || !(references[item.id] || "").trim()}
                className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[11px] text-white disabled:opacity-40"
                style={{ background: "var(--accent)" }}>
                <CheckCircle2 size={12} /> {reviewBusy === item.id ? "处理中…" : "加入回归集"}
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Last report */}
      {summary && (
        <div className="rounded-xl p-4 mb-4" style={card}>
          <div className="flex items-center justify-between mb-2">
            <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>本次结果{summary.comprehensive ? "（全面体检）" : ""}{summary.n_cases_run ? ` · ${summary.n_cases_run} 例` : ""}{summary.judge_model ? ` · 评审：${String(summary.judge_model)}` : ""}</div>
            <div className="flex items-center gap-1.5">
              <button onClick={exportReport} className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px]"
                style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
                <Download size={12} /> 导出报告(MD)
              </button>
              <button
                onClick={async () => {
                  const id = lastReport?.run_id as string | undefined;
                  if (!id) { alert("没有可下载的运行（请先跑一次评测）"); return; }
                  try { await api.downloadEvalReport(id); } catch (e) { alert("下载失败：" + String(e)); }
                }}
                title="生成含分层定位 + 可执行改进建议 + 每条失败用例明细的详细报告，并落盘到服务器 data/eval_runs/{id}.report.md"
                className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] text-white"
                style={{ background: "var(--accent)" }}>
                <Download size={12} /> 详细报告(存服务器)
              </button>
            </div>
          </div>
          <div className="grid grid-cols-4 gap-3 text-center">
            {[
              { k: "通过率", v: `${Math.round(Number(summary.pass_rate) * 100)}%` },
              { k: "平均分", v: String(summary.avg_score ?? "—") },
              { k: "评审分", v: summary.avg_judge_score != null ? String(summary.avg_judge_score) : "—" },
              { k: "用例数", v: String(summary.total ?? "—") },
            ].map(m => (
              <div key={m.k}>
                <div className="text-lg font-bold" style={{ color: "var(--accent)" }}>{m.v}</div>
                <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{m.k}</div>
              </div>
            ))}
          </div>
          {summary.layered != null && (
            <div className="mt-3 pt-3 grid grid-cols-3 gap-2" style={{ borderTop: "1px solid var(--border)" }}>
              {([
                ["检索层", "retrieval", "找得到吗"],
                ["生成层", "generation", "答得准吗"],
                ["端到端", "end_to_end", "整体通过"],
              ] as const).map(([label, key, hint]) => {
                const L = (summary.layered as Record<string, { score: number | null; metric: string; n_cases: number }>)[key];
                if (!L) return null;
                const pct = L.score == null ? "—" : `${Math.round(Number(L.score) * 100)}%`;
                return (
                  <div key={key} className="text-center p-2 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
                    <div className="text-[18px] font-bold" style={{ color: "var(--accent)" }}>{pct}</div>
                    <div className="text-[11px] font-medium" style={{ color: "var(--text-primary)" }}>{label}</div>
                    <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{hint} · {L.n_cases}例</div>
                  </div>
                );
              })}
            </div>
          )}
          {summary.retrieval && Object.keys(summary.retrieval as object).length > 0 && (
            <div className="mt-3 pt-3 text-[11px] font-mono" style={{ borderTop: "1px solid var(--border)", color: "var(--text-secondary)" }}>
              检索指标：{Object.entries(summary.retrieval as Record<string, number>).map(([k, v]) => `${k}=${v}`).join("  ")}
            </div>
          )}
          {/* v16: explain why judge / retrieval metrics may be empty */}
          {summary.avg_judge_score == null && (
            <div className="mt-2 text-[11px]" style={{ color: "#d97706" }}>
              评审分为空：{Number(summary.cases_with_reference) === 0
                ? "用例未配置参考答案（reference_answer）。在「质量评测」用例里补上参考答案即可启用 LLM 评审。"
                : !summary.judge_enabled ? "本次未启用评审。" : "部分用例缺参考答案。"}
            </div>
          )}
          {(!summary.retrieval || Object.keys(summary.retrieval as object).length === 0) && Number(summary.cases_with_relevant_docs) === 0 && (
            <div className="mt-1 text-[11px]" style={{ color: "#d97706" }}>
              检索指标为空：用例未标注相关文档（relevant_docs）。标注后可算 Recall@k / MRR / NDCG。
            </div>
          )}
        </div>
      )}

      {/* v16 Phase 13: deep visualizations */}
      {summary?.by_category != null && (
        <EvalCategoryBars byCategory={summary.by_category as Record<string, { total: number; passed: number; pass_rate: number; avg_score: number }>} />
      )}
      {summary?.fail_buckets != null && Object.keys(summary.fail_buckets as object).length > 0 && (
        <EvalFailBuckets buckets={summary.fail_buckets as Record<string, number>} />
      )}
      <EvalTrend runs={runs} />
      {lastReport?.results != null && (
        <EvalCaseList results={lastReport.results as Parameters<typeof EvalCaseList>[0]["results"]} />
      )}

      {/* Compare */}
      <div className="rounded-xl p-4" style={card}>
        <div className="text-[12px] font-semibold mb-2 flex items-center gap-1.5" style={{ color: "var(--text-primary)" }}>
          <GitCompare size={13} /> 对比两次运行（检测回归）
        </div>
        <div className="flex items-center gap-2 mb-3">
          <select value={baseline} onChange={e => setBaseline(e.target.value)} className={sel} style={{ ...selStyle, flex: 1 }}>
            <option value="">基准运行…</option>
            {runs.map(r => <option key={r.run_id} value={r.run_id}>{r.tag || r.run_id} (分 {r.avg_score})</option>)}
          </select>
          <span style={{ color: "var(--text-tertiary)" }}>→</span>
          <select value={candidate} onChange={e => setCandidate(e.target.value)} className={sel} style={{ ...selStyle, flex: 1 }}>
            <option value="">对比运行…</option>
            {runs.map(r => <option key={r.run_id} value={r.run_id}>{r.tag || r.run_id} (分 {r.avg_score})</option>)}
          </select>
          <button onClick={compare} className="px-3 py-1.5 rounded-lg text-[12px]"
            style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)" }}>对比</button>
        </div>

        {diff && !diff.error && (
          <div>
            <div className="flex items-center gap-3 mb-2">
              <span className="px-2 py-1 rounded text-[12px] font-bold text-white"
                style={{ background: diff.verdict === "REGRESSION" ? "#ef4444" : "#22c55e" }}>
                {diff.verdict === "REGRESSION" ? "检测到回归" : "无回归"}
              </span>
              <span className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
                平均分变化 {Number(diff.avg_score_delta) >= 0 ? "+" : ""}{String(diff.avg_score_delta)}
              </span>
            </div>
            {(diff.regressed as unknown[])?.length > 0 && (
              <div className="text-[11px] mt-2">
                <div className="font-semibold mb-1 flex items-center gap-1" style={{ color: "#ef4444" }}>
                  <TrendingDown size={12} /> 退步的用例（点开看答案对比）：
                </div>
                <EvalDiffPairs pairs={diff.regressed as Record<string, unknown>[]} kind="regressed" />
              </div>
            )}
            {(diff.improved as unknown[])?.length > 0 && (
              <div className="text-[11px] mt-2">
                <div className="font-semibold mb-1 flex items-center gap-1" style={{ color: "#22c55e" }}>
                  <TrendingUp size={12} /> 改进的用例（点开看答案对比）：
                </div>
                <EvalDiffPairs pairs={diff.improved as Record<string, unknown>[]} kind="improved" />
              </div>
            )}
            <div className="text-[11px] mt-2 flex items-center gap-1" style={{ color: "var(--text-tertiary)" }}>
              <Minus size={11} /> {String(diff.unchanged_count)} 个用例无变化
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

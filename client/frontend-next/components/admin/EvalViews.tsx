"use client";
import { useState } from "react";
import { ChevronDown, ChevronRight, FileText } from "lucide-react";

const cardStyle = { background: "var(--bg-secondary)", border: "1px solid var(--border)" };

interface CaseResult {
  case_id: string; query: string; passed: boolean; score: number;
  category?: string; answer?: string; fail_reason?: string;
  judge_score?: number | null; judge_reason?: string;
  retrieval_metrics?: Record<string, number>;
  checks?: { name: string; passed: boolean; detail?: string }[];
  sources?: { filename: string; score: number; snippet?: string }[];
}

// ── Per-case drill-down list ──
export function EvalCaseList({ results }: { results: CaseResult[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!results?.length) return null;
  return (
    <div className="rounded-xl overflow-hidden mb-4" style={cardStyle}>
      <div className="px-4 py-2.5 text-[12px] font-semibold" style={{ color: "var(--text-primary)", borderBottom: "1px solid var(--border)" }}>
        逐用例结果（点开看详情）
      </div>
      {results.map(r => {
        const isOpen = open === r.case_id;
        return (
          <div key={r.case_id} style={{ borderBottom: "1px solid var(--border-light)" }}>
            <button onClick={() => setOpen(isOpen ? null : r.case_id)}
              className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-[var(--bg-tertiary)]">
              {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              <span className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                style={{ background: r.passed ? "#22c55e" : "#ef4444" }} />
              <span className="text-[12px] font-mono flex-shrink-0" style={{ color: "var(--text-tertiary)" }}>{r.case_id}</span>
              <span className="text-[12px] flex-1 truncate" style={{ color: "var(--text-primary)" }}>{r.query}</span>
              {r.category && <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>{r.category}</span>}
              {!r.passed && r.fail_reason && (
                <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "#fef2f2", color: "#ef4444" }}>{r.fail_reason}</span>
              )}
              <span className="text-[12px] font-bold flex-shrink-0" style={{ color: r.passed ? "#22c55e" : "#ef4444" }}>{r.score}</span>
            </button>
            {isOpen && (
              <div className="px-4 pb-3 pl-11 text-[11px] space-y-2" style={{ color: "var(--text-secondary)" }}>
                {r.answer && (
                  <div>
                    <div className="font-semibold mb-0.5" style={{ color: "var(--text-tertiary)" }}>答案</div>
                    <div className="p-2 rounded-lg max-h-32 overflow-y-auto" style={{ background: "var(--bg-tertiary)" }}>{r.answer}</div>
                  </div>
                )}
                {(r.checks?.length ?? 0) > 0 && (
                  <div>
                    <div className="font-semibold mb-0.5" style={{ color: "var(--text-tertiary)" }}>检查项</div>
                    <div className="flex flex-wrap gap-1.5">
                      {r.checks!.map((c, i) => (
                        <span key={i} className="px-1.5 py-0.5 rounded text-[10px]"
                          style={{ background: c.passed ? "#f0fdf4" : "#fef2f2", color: c.passed ? "#16a34a" : "#ef4444" }}>
                          {c.passed ? "✓" : "✗"} {c.name}{c.detail ? `（${c.detail}）` : ""}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {r.retrieval_metrics && Object.keys(r.retrieval_metrics).length > 0 && (
                  <div className="font-mono text-[10px]" style={{ color: "var(--text-tertiary)" }}>
                    检索：{Object.entries(r.retrieval_metrics).map(([k, v]) => `${k}=${v}`).join("  ")}
                  </div>
                )}
                {r.judge_score != null && (
                  <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>评审分 {r.judge_score}{r.judge_reason ? ` · ${r.judge_reason}` : ""}</div>
                )}
                {(r.sources?.length ?? 0) > 0 && (
                  <div>
                    <div className="font-semibold mb-0.5" style={{ color: "var(--text-tertiary)" }}>检索到的源</div>
                    {r.sources!.map((s, i) => (
                      <div key={i} className="flex items-start gap-1.5 py-0.5">
                        <FileText size={11} className="mt-0.5 flex-shrink-0" />
                        <span className="font-mono">{s.filename || "?"}</span>
                        <span style={{ color: "var(--text-tertiary)" }}>({s.score})</span>
                        {s.snippet && <span className="truncate" style={{ color: "var(--text-tertiary)" }}>— {s.snippet}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Per-category bar chart ──
export function EvalCategoryBars({ byCategory }: { byCategory: Record<string, { total: number; passed: number; pass_rate: number; avg_score: number }> }) {
  const cats = Object.entries(byCategory || {});
  if (!cats.length) return null;
  return (
    <div className="rounded-xl p-4 mb-4" style={cardStyle}>
      <div className="text-[12px] font-semibold mb-3" style={{ color: "var(--text-primary)" }}>按类别表现</div>
      <div className="space-y-2">
        {cats.map(([cat, s]) => (
          <div key={cat} className="flex items-center gap-3">
            <span className="text-[11px] w-20 flex-shrink-0" style={{ color: "var(--text-secondary)" }}>{cat}</span>
            <div className="flex-1 h-4 rounded-full overflow-hidden" style={{ background: "var(--bg-tertiary)" }}>
              <div className="h-full rounded-full" style={{
                width: `${Math.round(s.pass_rate * 100)}%`,
                background: s.pass_rate >= 0.8 ? "#22c55e" : s.pass_rate >= 0.5 ? "#d97706" : "#ef4444",
              }} />
            </div>
            <span className="text-[11px] font-mono w-28 text-right flex-shrink-0" style={{ color: "var(--text-tertiary)" }}>
              {s.passed}/{s.total} · 分{s.avg_score}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Failure buckets ──
export function EvalFailBuckets({ buckets }: { buckets: Record<string, number> }) {
  const items = Object.entries(buckets || {});
  if (!items.length) return null;
  const tips: Record<string, string> = {
    "检索未召回": "检索没找到相关文档 → 检查切分/索引，或开启 HyDE/多查询",
    "召回未引用": "找到了但答案没引用 → 检查生成 prompt 的引用要求",
    "答案偏题": "引用了但内容不对 → 可能检索到错文档，或 LLM 跑偏",
    "评审低分": "检查项过了但语义质量低 → 看参考答案对齐",
  };
  return (
    <div className="rounded-xl p-4 mb-4" style={cardStyle}>
      <div className="text-[12px] font-semibold mb-2" style={{ color: "var(--text-primary)" }}>失败原因分布（该修哪块）</div>
      <div className="space-y-1.5">
        {items.sort((a, b) => b[1] - a[1]).map(([reason, n]) => (
          <div key={reason} className="flex items-center gap-2 text-[11px]">
            <span className="px-2 py-0.5 rounded font-semibold" style={{ background: "#fef2f2", color: "#ef4444" }}>{n}</span>
            <span style={{ color: "var(--text-primary)" }}>{reason}</span>
            <span style={{ color: "var(--text-tertiary)" }}>— {tips[reason] || ""}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Trend line over runs (SVG, no chart lib) ──
export function EvalTrend({ runs }: { runs: { run_id: string; tag: string; saved_at: number; avg_score: number; pass_rate: number; avg_judge_score?: number }[] }) {
  const data = [...(runs || [])].filter(r => r.avg_score != null).reverse(); // oldest→newest
  if (data.length < 2) return null;
  const W = 600, H = 120, pad = 24;
  const xs = (i: number) => pad + (i * (W - 2 * pad)) / (data.length - 1);
  const ys = (v: number) => H - pad - v * (H - 2 * pad);
  const line = (key: "avg_score" | "pass_rate") =>
    data.map((d, i) => `${i === 0 ? "M" : "L"}${xs(i).toFixed(1)},${ys(Number(d[key]) || 0).toFixed(1)}`).join(" ");
  return (
    <div className="rounded-xl p-4 mb-4" style={cardStyle}>
      <div className="flex items-center justify-between mb-2">
        <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>质量趋势（历次运行）</div>
        <div className="flex gap-3 text-[10px]">
          <span style={{ color: "var(--accent)" }}>● 平均分</span>
          <span style={{ color: "#22c55e" }}>● 通过率</span>
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 140 }}>
        <line x1={pad} y1={H - pad} x2={W - pad} y2={H - pad} stroke="var(--border)" strokeWidth="1" />
        <path d={line("avg_score")} fill="none" stroke="var(--accent)" strokeWidth="2" />
        <path d={line("pass_rate")} fill="none" stroke="#22c55e" strokeWidth="2" />
        {data.map((d, i) => (
          <g key={d.run_id}>
            <circle cx={xs(i)} cy={ys(Number(d.avg_score) || 0)} r="3" fill="var(--accent)" />
            <circle cx={xs(i)} cy={ys(Number(d.pass_rate) || 0)} r="3" fill="#22c55e" />
          </g>
        ))}
      </svg>
      <div className="flex justify-between text-[9px] mt-1" style={{ color: "var(--text-tertiary)" }}>
        {data.map(d => <span key={d.run_id} className="truncate max-w-[60px]">{d.tag || d.run_id.slice(-4)}</span>)}
      </div>
    </div>
  );
}

// ── Online quality dashboard (real traffic) ──
export function QualityDashboard({ data }: { data: Record<string, unknown> | null }) {
  if (!data || !data.samples) return null;
  const gr = Number(data.avg_grounded_ratio);
  const metrics = [
    { k: "采样数", v: String(data.samples) },
    { k: "引用依据率", v: `${Math.round(gr * 100)}%`, warn: gr < 0.7 },
    { k: "无来源回答", v: String(data.answers_without_sources), warn: Number(data.answers_without_sources) > 0 },
    { k: "弱依据率", v: `${Math.round(Number(data.weak_rate) * 100)}%`, warn: Number(data.weak_rate) > 0.2 },
    { k: "平均源数", v: String(data.avg_sources) },
    { k: "平均延迟", v: `${data.avg_latency_ms}ms` },
  ];
  return (
    <div className="rounded-xl p-4 mb-4" style={cardStyle}>
      <div className="text-[12px] font-semibold mb-1" style={{ color: "var(--text-primary)" }}>线上质量大盘（真实流量抽样）</div>
      <div className="text-[10px] mb-3" style={{ color: "var(--text-tertiary)" }}>
        每 {String(data.sample_rate)} 轮对话抽样 1 次，过去 {String(data.days)} 天
      </div>
      <div className="grid grid-cols-3 gap-3 text-center">
        {metrics.map(m => (
          <div key={m.k}>
            <div className="text-base font-bold" style={{ color: m.warn ? "#ef4444" : "var(--accent)" }}>{m.v}</div>
            <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{m.k}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
export function EvalDiffPairs({ pairs, kind }: { pairs: Record<string, unknown>[]; kind: "regressed" | "improved" }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!pairs?.length) return null;
  const color = kind === "regressed" ? "#ef4444" : "#22c55e";
  return (
    <div className="mt-2">
      {pairs.map((p) => {
        const cid = String(p.case_id);
        const isOpen = open === cid;
        return (
          <div key={cid}>
            <button onClick={() => setOpen(isOpen ? null : cid)}
              className="w-full flex items-center gap-2 py-0.5 text-[11px] text-left" style={{ color: "var(--text-secondary)" }}>
              {isOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
              <span className="font-mono">{cid}</span>
              <span>{String(p.baseline)} → {String(p.candidate)} ({Number(p.delta) >= 0 ? "+" : ""}{String(p.delta)})</span>
            </button>
            {isOpen && (
              <div className="grid grid-cols-2 gap-2 pl-5 pb-2">
                <div className="p-2 rounded-lg text-[10px]" style={{ background: "var(--bg-tertiary)" }}>
                  <div className="font-semibold mb-1" style={{ color: "var(--text-tertiary)" }}>基准（分 {String(p.baseline)}）</div>
                  <div className="max-h-28 overflow-y-auto" style={{ color: "var(--text-secondary)" }}>{String(p.baseline_answer || "—")}</div>
                </div>
                <div className="p-2 rounded-lg text-[10px]" style={{ background: "var(--bg-tertiary)", border: `1px solid ${color}33` }}>
                  <div className="font-semibold mb-1" style={{ color }}>候选（分 {String(p.candidate)}）</div>
                  <div className="max-h-28 overflow-y-auto" style={{ color: "var(--text-secondary)" }}>{String(p.candidate_answer || "—")}</div>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

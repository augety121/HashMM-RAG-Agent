/**
 *  BenchmarkCards — 「外部基准对标」结果卡片（V306·诚实性修正版）。
 *
 *  修掉了"什么都显示 100%、什么都'已超过顶尖 agent'"的假象。分三级渲染：
 *    · smoke（管线自检）   ：**不画分数条、不显示对标** —— 只证明 agent 能被该基准驱动；
 *    · builtin（内置最小集）：画分数条，但**明确标注"非官方口径，不可与 leaderboard 比较"**；
 *    · official（官方数据集）：才画 leaderboard 参照刻度 + 对标位置。
 *  另提供「导出对标报告(.md)」一键存档。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Download, Upload, Play, Loader2, BarChart3, Trash2, Trophy, AlertTriangle, ClipboardList, Container } from "lucide-react";
import { benchList, benchTrend, benchReport, benchPurge, benchVsFrontier, benchChartSvg, benchChartCsv, benchImport, benchComparableCandidates, benchComparableRun, selftestJob, type ComparableCandidate, type BenchInfo, type BenchTrendSummary, type BenchVsRow, type BenchParityRow, type SelfTestResult } from "@/lib/api";

const BENCH_ID_MAP: Record<string, string> = {
  bench_humaneval: "humaneval",
  bench_tool_calling: "tool_calling",
  bench_tau2: "tau2_bench",
  bench_gaia: "gaia",
  bench_kotlin: "kotlin_bench",
  bench_webvoyager: "webvoyager",
  bench_swebench: "swebench_verified",
  bench_agentbench: "agentbench_os",
  bench_terminal: "terminal_bench",
  bench_webarena: "webarena",
};
const GROUP = "外部基准对标";

type Kind = "smoke" | "builtin" | "official";

/** 从后端 detail 文案判定这次跑分的性质（后端已在 detail 里明确写出）。 */
function kindOf(detail: string): Kind {
  if (detail.includes("适配管线自检")) return "smoke";
  if (detail.includes("内置最小集")) return "builtin";
  if (detail.includes("官方数据集")) return "official";
  return "smoke";
}

function parseScore(detail: string): number | null {
  const m = (detail || "").match(/([\d.]+)\s*%/);
  return m ? parseFloat(m[1]) : null;
}

function ScoreBar({ score, refs, metric }: { score: number; refs: [string, number][]; metric: string }) {
  const sorted = [...refs].sort((a, b) => b[1] - a[1]);
  return (
    <div className="mt-2">
      <div className="flex items-center justify-between text-[10.5px] mb-1" style={{ color: "var(--text-tertiary)" }}>
        <span>{metric || "分数"}</span>
        <span>你的分：<b style={{ color: "var(--text-primary)" }}>{score}%</b></span>
      </div>
      <div className="relative h-6 rounded-md" style={{ background: "var(--bg-tertiary)" }}>
        <div className="absolute inset-y-0 left-0 rounded-md transition-all duration-500"
          style={{ width: `${Math.min(100, score)}%`, background: "color-mix(in srgb, var(--accent) 60%, transparent)" }} />
        {sorted.map(([name, val], i) => (
          <div key={name} className="absolute inset-y-0" style={{ left: `${Math.min(100, val)}%` }}>
            <div className="absolute inset-y-0 w-[2px]" style={{ background: "var(--text-secondary)", opacity: 0.65 }} />
            <div className="absolute -translate-x-1/2 whitespace-nowrap text-[9px]"
              style={{ color: "var(--text-tertiary)", top: i % 2 ? -14 : -26 }} title={`${name}: ${val}%`}>
              {name.length > 8 ? name.slice(0, 8) + "…" : name} {val}%
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ComparableRunControl({ onComplete }: { onComplete: () => Promise<void> }) {
  const [candidates, setCandidates] = useState<ComparableCandidate[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sample, setSample] = useState<"standard" | "full">("standard");
  const [parallel, setParallel] = useState(2);
  const [paired, setPaired] = useState(true);
  const [job, setJob] = useState<{ running: boolean; done: number; total: number; detail: string }>({ running: false, done: 0, total: 0, detail: "" });

  useEffect(() => {
    let alive = true;
    benchComparableCandidates().then(r => {
      if (!alive) return;
      const list = r.candidates || [];
      setCandidates(list);
      setSelected(new Set(list.filter(c => c.runnable).map(c => c.id)));
    }).catch(e => {
      if (alive) setJob(p => ({ ...p, detail: `就绪探测失败：${e instanceof Error ? e.message : String(e)}` }));
    });
    return () => { alive = false; };
  }, []);

  const toggle = (id: string) => setSelected(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const run = async () => {
    const ids = candidates.filter(c => c.runnable && selected.has(c.id)).map(c => c.id);
    if (!ids.length || job.running) {
      if (!ids.length) setJob(p => ({ ...p, detail: "当前没有选中可运行的官方基准；先按下方提示补齐数据集。" }));
      return;
    }
    setJob({ running: true, done: 0, total: ids.length * (paired ? 2 : 1), detail: paired ? "先跑裸模型基线，再跑 Agent；两轮严格使用相同题集与档位。" : "正在运行 Agent 正式轮。" });
    try {
      const started = await benchComparableRun(ids, sample, parallel, paired);
      if (!started.ok || !started.job_id) throw new Error(started.detail || "后端未创建跑测任务");
      const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
      // 每次请求都很短，长任务在后端继续运行；页面不会被反向代理长连接掐断。
      // eslint-disable-next-line no-constant-condition
      while (true) {
        await sleep(1500);
        const status = await selftestJob(started.job_id);
        setJob(p => ({ ...p, done: status.done, total: status.total }));
        if (status.status === "error") throw new Error(status.error || "跑测任务异常");
        if (status.status === "done") {
          const sm = status.summary as unknown as { ran?: number; comparable?: number; baselines?: number } | null;
          setJob({ running: false, done: status.total, total: status.total,
            detail: paired
              ? `成对跑测完成：Agent ${sm?.ran ?? 0} 项、裸模型基线 ${sm?.baselines ?? 0} 项；差值已进入下方对比表。`
              : `正式轮完成：${sm?.comparable ?? 0} 项达到可比口径。` });
          await onComplete();
          break;
        }
      }
    } catch (e) {
      setJob(p => ({ ...p, running: false, detail: `跑测失败：${e instanceof Error ? e.message : String(e)}` }));
    }
  };

  const runnable = candidates.filter(c => c.runnable);
  return (
    <div className="mb-4 rounded-xl p-3.5" style={{ border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
      <div className="flex items-start gap-3 flex-wrap">
        <div className="min-w-[220px] flex-1">
          <div className="text-[12.5px] font-semibold" style={{ color: "var(--text-primary)" }}>可归因跑测</div>
          <div className="text-[10.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
            同题、同模型、同档位成对运行，避免把底座模型能力误算成 Agent 能力。
          </div>
        </div>
        <label className="flex items-center gap-1.5 text-[11px]" style={{ color: "var(--text-secondary)" }} title="推荐开启；会增加约一倍模型调用成本">
          <input type="checkbox" checked={paired} onChange={e => setPaired(e.target.checked)} disabled={job.running} className="accent-[var(--accent)]" />
          同跑裸模型基线（推荐）
        </label>
        <label className="text-[11px]" style={{ color: "var(--text-secondary)" }}>题量
          <select value={sample} onChange={e => setSample(e.target.value as "standard" | "full")} disabled={job.running}
            className="ml-1 rounded-md px-2 py-1" style={{ border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
            <option value="standard">标准（≥50题）</option><option value="full">官方全集</option>
          </select>
        </label>
        <label className="text-[11px]" style={{ color: "var(--text-secondary)" }} title="基准级并发；总模型压力还会乘以逐题并发配置">并发
          <select value={parallel} onChange={e => setParallel(Number(e.target.value))} disabled={job.running}
            className="ml-1 rounded-md px-2 py-1" style={{ border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
            {[1, 2, 3, 4].map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <button onClick={run} disabled={job.running || runnable.length === 0 || selected.size === 0}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11.5px] text-white disabled:opacity-50"
          style={{ background: "var(--accent)" }}>
          {job.running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          {job.running ? `${job.done}/${job.total}` : "开始正式跑测"}
        </button>
      </div>
      <div className="flex flex-wrap gap-1.5 mt-3">
        {candidates.map(c => (
          <label key={c.id} className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10.5px]"
            style={{ border: "1px solid var(--border)", color: c.runnable ? "var(--text-secondary)" : "var(--text-tertiary)", opacity: c.runnable ? 1 : 0.65 }}
            title={c.hint}>
            <input type="checkbox" checked={selected.has(c.id)} onChange={() => toggle(c.id)} disabled={!c.runnable || job.running} className="accent-[var(--accent)]" />
            {c.name.replace(/（.*$/, "")}{!c.runnable ? " · 未就绪" : ""}
          </label>
        ))}
        {!candidates.length && <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>正在探测官方基准…</span>}
      </div>
      {job.total > 0 && (
        <div className="mt-2 h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg-tertiary)" }}>
          <div className="h-full transition-all" style={{ width: `${Math.min(100, 100 * job.done / job.total)}%`, background: "var(--accent)" }} />
        </div>
      )}
      {job.detail && <div className="mt-2 text-[10.5px]" style={{ color: job.detail.startsWith("跑测失败") ? "#b42318" : "var(--text-tertiary)" }}>{job.detail}</div>}
      {candidates.some(c => !c.runnable) && (
        <div className="mt-1 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>
          未就绪项不会被偷偷降级成玩具题；悬停可查看缺少的数据集或运行环境。
        </div>
      )}
    </div>
  );
}

export function BenchmarkCards({ results }: { results: SelfTestResult[] | null }) {
  const [infoById, setInfoById] = useState<Record<string, BenchInfo>>({});
  const [summary, setSummary] = useState<Record<string, BenchTrendSummary>>({});
  const [mode, setMode] = useState<string>("");
  // V317 大厂对比：comparable/趋势区/口径对齐
  const [vsData, setVsData] = useState<{ comparable: BenchVsRow[]; trend_only: BenchVsRow[]; model: string } | null>(null);
  const [parity, setParity] = useState<BenchParityRow[]>([]);
  const [showVs, setShowVs] = useState(false);
  const [vsHeadline, setVsHeadline] = useState<string>("");
  // V327 内网闭环：导入 CI Artifact 的结果 JSON
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [importHint, setImportHint] = useState<string>("");

  async function importCiFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    try {
      const items: unknown[] = [];
      for (const f of Array.from(files)) {
        const parsed = JSON.parse(await f.text());
        if (Array.isArray(parsed)) items.push(...parsed); else items.push(parsed);
      }
      const r = await benchImport(items);
      const bad = (r.results || []).filter(x => !x.ok);
      const hasSkipped = bad.some(x => (x.detail || "").includes("跳过"));
      setImportHint(`${r.detail}${bad.length ? `；未导入：${bad.map(x => `${x.bench_id || "?"}(${(x.detail || "").slice(0, 40)})`).join("、")}` : ""}${hasSkipped ? "。被跳过表示 CI 前置未就绪：去对应 Actions 运行页下载 install-logs Artifact 检查安装步骤" : ""}`);
      // 刷新对比表（清空触发下次展开重取；已展开则立即重取）
      const rv = await benchVsFrontier();
      if (rv?.comparison) setVsData({ comparable: rv.comparison.comparable, trend_only: rv.comparison.trend_only, model: rv.comparison.model });
    } catch (e) {
      setImportHint(`导入失败：${e instanceof Error ? e.message : String(e)}（要选 Actions Artifact 里的 bench_results/*.json）`);
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  const benchResults = useMemo(() => (results || []).filter(r => r.group === GROUP), [results]);

  useEffect(() => {
    if (benchResults.length === 0) return;
    let alive = true;
    (async () => {
      try {
        const [list, tr] = await Promise.all([benchList(), benchTrend()]);
        if (!alive) return;
        if (list?.ok) {
          const m: Record<string, BenchInfo> = {};
          for (const b of list.benchmarks) m[b.id] = b;
          setInfoById(m);
          setMode(list.mode || "");
        }
        if (tr?.ok) setSummary(tr.summary || {});
      } catch { /* 安静降级 */ }
    })();
    return () => { alive = false; };
  }, [benchResults.length]);

  const purgeHistory = async () => {
    try {
      const r = await benchPurge();
      if (r?.ok) {
        const tr = await benchTrend();
        if (tr?.ok) setSummary(tr.summary || {});
      }
    } catch { /* ignore */ }
  };

  const toggleVsFrontier = async () => {
    if (!showVs && !vsData) {
      try {
        const r = await benchVsFrontier();
        if (r?.ok) {
          setVsData({ comparable: r.comparison.comparable, trend_only: r.comparison.trend_only, model: r.comparison.model });
          setParity(r.parity || []);
          setVsHeadline(r.summary?.headline || "");
        }
      } catch { /* 安静降级 */ }
    }
    setShowVs(v => !v);
  };

  const exportReport = async () => {
    try {
      const r = await benchReport();
      if (!r?.ok || !r.report_md) return;
      const blob = new Blob([r.report_md], { type: "text/markdown;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `benchmark-report-${new Date().toISOString().slice(0, 10)}.md`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch { /* ignore */ }
  };

  // V320：一键导出对比图（SVG）/ 数据表（CSV）——与 vs-frontier 同一可比性门禁
  const downloadBlob = (content: string, mime: string, filename: string) => {
    const blob = new Blob([content], { type: mime });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const exportChartSvg = async () => {
    try {
      const svg = await benchChartSvg();
      downloadBlob(svg, "image/svg+xml;charset=utf-8", `hashmm-vs-frontier-${new Date().toISOString().slice(0, 10)}.svg`);
    } catch { /* ignore */ }
  };
  const exportChartCsv = async () => {
    try {
      const csv = await benchChartCsv();
      downloadBlob(csv, "text/csv;charset=utf-8", `hashmm-vs-frontier-${new Date().toISOString().slice(0, 10)}.csv`);
    } catch { /* ignore */ }
  };

  const refreshAfterComparableRun = async () => {
    const [rv, tr] = await Promise.all([benchVsFrontier(), benchTrend()]);
    if (rv?.comparison) {
      setVsData({ comparable: rv.comparison.comparable, trend_only: rv.comparison.trend_only, model: rv.comparison.model });
      setParity(rv.parity || []);
      setVsHeadline(rv.summary?.headline || "");
      setShowVs(true);
    }
    if (tr?.ok) setSummary(tr.summary || {});
  };

  return (
    <div className="mt-5">
      <ComparableRunControl onComplete={refreshAfterComparableRun} />
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[12.5px] font-semibold inline-flex items-center gap-1.5" style={{ color: "var(--text-primary)" }}><BarChart3 size={13} /> 外部基准对标</span>
        {mode && (
          <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>
            {mode === "full" ? "真集模式(full)" : "smoke 模式：只自检管线，不出分数"}
          </span>
        )}
        <button onClick={purgeHistory}
          className="ml-auto inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] hover:bg-[var(--bg-tertiary)]"
          style={{ border: "1px solid var(--border)", color: "var(--text-tertiary)" }}
          title="清掉早期 smoke 管线自检误入库的假分数（那些莫名的 100%）">
          <Trash2 size={11} /> 清理历史假分
        </button>
        <button onClick={toggleVsFrontier}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] hover:bg-[var(--bg-tertiary)]"
          style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}
          title="你 vs 2026 大厂横向对比（跑够 50 题即默认可比）">
          <Trophy size={11} /> 和大厂对比
        </button>
        <button onClick={exportReport}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] hover:bg-[var(--bg-tertiary)]"
          style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          <Download size={11} /> 导出对标报告(.md)
        </button>
        <button onClick={exportChartSvg}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] hover:bg-[var(--bg-tertiary)]"
          style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}
          title="一张自包含对比图：正式区实心条+95%CI 误差线，趋势区灰色虚边并标注不可比。浏览器直接打开，可插 PPT">
          <Download size={11} /> 导出对比图(.svg)
        </button>
        <button onClick={exportChartCsv}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] hover:bg-[var(--bg-tertiary)]"
          style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}
          title="Excel 双击即开（UTF-8 BOM），含 CI/样本量/可比性，可自行二次绘图">
          <Download size={11} /> 导出数据(.csv)
        </button>
        <button onClick={() => fileRef.current?.click()}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] hover:bg-[var(--bg-tertiary)]"
          style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}
          title="服务器在内网收不到 CI 回传？去 GitHub Actions 运行页底部 Artifacts 下载 bench-results，选里面的 *.json 一键入库（支持多选，也识别基线文件）">
          <Upload size={11} /> 导入CI结果(.json)
        </button>
        <input ref={fileRef} type="file" accept=".json,application/json" multiple className="hidden"
          onChange={e => importCiFiles(e.target.files)} />
      </div>
      {importHint && (
        <div className="text-[10.5px] mt-1" style={{ color: "var(--text-tertiary)" }}>{importHint}</div>
      )}

      {showVs && vsData && (
        <div className="mb-4 p-3 rounded-lg" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
          <div className="text-[11.5px] font-semibold mb-2" style={{ color: "var(--text-primary)" }}>
            <Trophy size={12} className="inline mr-1" />HashMM（{vsData.model || "本机模型"}）vs 2026 大厂
          </div>
          {vsHeadline && (
            <div className="mb-2 px-2 py-1 rounded text-[11px] font-medium" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>
              {vsHeadline}
            </div>
          )}
          {vsData.comparable.length > 0 ? (
            <div className="space-y-3">
              {vsData.comparable.map(row => (
                <div key={row.id}>
                  <div className="text-[10.5px] font-medium mb-1" style={{ color: "var(--text-secondary)" }}>{row.name}</div>
                  {[{ name: "HashMM（你）", val: row.your_score, mine: true },
                    ...row.anchors.map(a => ({ name: a[0], val: a[1], mine: false }))]
                    .sort((a, b) => b.val - a.val).map(bar => (
                    <div key={bar.name} className="flex items-center gap-2 mb-0.5">
                      <span className="text-[9.5px] w-28 truncate" style={{ color: bar.mine ? "var(--accent)" : "var(--text-tertiary)" }}>
                        {bar.name}
                      </span>
                      <div className="flex-1 h-3 rounded" style={{ background: "var(--bg-tertiary)" }}>
                        <div className="h-3 rounded" style={{ width: `${Math.min(100, bar.val)}%`, background: bar.mine ? "var(--accent)" : "var(--text-secondary)", opacity: bar.mine ? 1 : 0.5 }} />
                      </div>
                      <span className="text-[9.5px] w-10 text-right" style={{ color: "var(--text-tertiary)" }}>{bar.val}%</span>
                    </div>
                  ))}
                  <div className="text-[9px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
                    {row.passed}/{row.n} 题 · 95%CI [{row.ci_low}%, {row.ci_high}%]{row.beats.length > 0 ? ` · 已超过：${row.beats.join("、")}` : ""}{row.elapsed_ms && row.elapsed_ms > 1000 ? ` · 用时 ${Math.round(row.elapsed_ms / 60000)} 分` : ""}{row.remote ? ` · 远程 CI（${row.source || "ci"}）跑出` : ""}
                  </div>
                  {(row.sut || row.baseline_score != null) && (
                    <div className="text-[9px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
                      {row.sut ? <span title="本基准被测的系统；项目 Agent 基准会测 AgentLoop 与工具，其余按业界标准口径测底座模型">被测：{row.sut}</span> : null}
                      {row.baseline_score != null ? (
                        <span style={{ color: row.your_score - (row.baseline_score ?? 0) > 0 ? "var(--accent)" : "var(--text-tertiary)" }}
                          title="消融对照：同一基准，裸模型直答（未用你的 agent）的分；差值表示 agent 脚手架贡献">
                          {row.sut ? " · " : ""}裸模型基线 {row.baseline_score}% → 你的 Agent {row.your_score}%（{row.your_score - (row.baseline_score ?? 0) >= 0 ? "+" : ""}{Math.round((row.your_score - (row.baseline_score ?? 0)) * 10) / 10}）
                        </span>
                      ) : null}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
              暂无样本量达标的基准。现在<strong>默认就跑 50 题（可比）</strong>——若这里为空或分数不足，通常是你的启动脚本里设了 <code>HASHMM_TAU2_LIMIT</code>/<code>HASHMM_GAIA_LIMIT</code> 等把题数压到了 50 以下，删掉那些行重启即可；或数据集还没装（见下方各基准提示）。
            </div>
          )}
          {vsData.trend_only.length > 0 && (
            <div className="mt-2 pt-2 text-[9.5px]" style={{ borderTop: "1px solid var(--border)", color: "var(--text-tertiary)" }}>
              <AlertTriangle size={11} className="inline mr-1" />样本不足（不能和大厂比）：{vsData.trend_only.map(r => `${r.name} ${r.your_score}%(${r.passed}/${r.n})`).join(" · ")}
            </div>
          )}
          {parity.length > 0 && (
            <div className="mt-2 pt-2 text-[9.5px]" style={{ borderTop: "1px solid var(--border)", color: "var(--text-tertiary)" }}>
              <ClipboardList size={11} className="inline mr-1" />口径对齐：{parity.filter(p => p.verdict.includes("一致")).length}/{parity.length} 个基准判分口径与大厂官方一致
            </div>
          )}
          <div className="mt-1.5 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>
            <Container size={11} className="inline mr-1" />SWE-bench / WebArena / OSWorld / Terminal 需要 Docker——本机跑不了可用 CI（GitHub Actions）运行，
            结果自动回传进本表。见源码 <code>hashmm/evaluation/benchmarks/README_CI.md</code>。
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {benchResults.map(r => {
          const benchId = BENCH_ID_MAP[r.id] || r.id;
          const info = infoById[benchId];
          const kind = kindOf(r.detail || "");
          const score = parseScore(r.detail || "");
          const refs = info?.leaderboard?.refs || [];
          const sum = summary[benchId];
          const ranking = (r.detail || "").split("对标：")[1]?.split("｜")[0] || "";

          return (
            <div key={r.id} className="rounded-xl p-3.5" style={{ border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[12.5px] font-medium" style={{ color: "var(--text-primary)" }}>
                  {r.name.replace(/（.*$/, "")}
                </span>
                {r.skip ? (
                  <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>未跑·前置不满足</span>
                ) : kind === "official" ? (
                  <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "color-mix(in srgb, #16a34a 12%, transparent)", color: "#15803d" }}>官方数据集·可对标</span>
                ) : kind === "builtin" ? (
                  <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "color-mix(in srgb, #d97706 14%, transparent)", color: "#b45309" }}>内置最小集·不可对标</span>
                ) : (
                  <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>管线自检·无分数</span>
                )}
              </div>

              {kind === "smoke" && !r.skip && (
                <div className="mt-2 text-[11.5px]" style={{ color: r.ok ? "var(--text-secondary)" : "#b42318" }}>
                  {r.ok ? "适配管线通过" : "适配管线失败"}
                  <span style={{ color: "var(--text-tertiary)" }}>——这不是基准分数，只证明 agent 能被该基准驱动。</span>
                </div>
              )}

              {kind === "official" && score != null && !r.skip && (
                <>
                  <ScoreBar score={score} refs={refs} metric={info?.leaderboard?.metric || ""} />
                  {ranking && (
                    <div className="mt-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
                      位置：<b style={{ color: "var(--text-primary)" }}>{ranking}</b>
                    </div>
                  )}
                </>
              )}

              {kind === "builtin" && score != null && !r.skip && (
                <>
                  <div className="mt-2">
                    <div className="flex items-center justify-between text-[10.5px] mb-1" style={{ color: "var(--text-tertiary)" }}>
                      <span>内置集得分（非官方口径）</span>
                      <span><b style={{ color: "var(--text-primary)" }}>{score}%</b></span>
                    </div>
                    <div className="relative h-6 rounded-md" style={{ background: "var(--bg-tertiary)" }}>
                      <div className="absolute inset-y-0 left-0 rounded-md"
                        style={{ width: `${Math.min(100, score)}%`, background: "color-mix(in srgb, #d97706 45%, transparent)" }} />
                    </div>
                  </div>
                  <div className="mt-2 text-[10.5px] leading-snug" style={{ color: "#b45309" }}>
                    自造的小用例集，不可与 Claude Code 等公开分比较。装官方 BFCL 数据后本项会自动改跑真实基准。
                  </div>
                </>
              )}

              {r.skip && <div className="mt-2 text-[11px]" style={{ color: "var(--text-tertiary)" }}>{r.detail}</div>}

              {sum && (
                <div className="flex items-center gap-2 mt-1.5 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
                  <span>跑测 {sum.runs} 次</span>
                  <span>最高 <b style={{ color: "var(--text-secondary)" }}>{sum.best}%</b></span>
                  {sum.delta != null && (
                    <span style={{ color: sum.delta > 0 ? "#16a34a" : sum.delta < 0 ? "#dc2626" : "var(--text-tertiary)" }}>
                      {sum.trend} 较上次 {sum.delta > 0 ? "+" : ""}{sum.delta}%
                    </span>
                  )}
                </div>
              )}
              {info?.desc && (
                <div className="mt-1.5 text-[10px] leading-snug" style={{ color: "var(--text-tertiary)" }}>{info.desc}</div>
              )}
              {info?.leaderboard?.source_url && (
                <a href={info.leaderboard.source_url} target="_blank" rel="noopener noreferrer"
                  className="inline-block mt-1 text-[9.5px] hover:underline" style={{ color: "var(--accent)" }}>
                  查看官方榜单来源（新窗口）
                </a>
              )}
            </div>
          );
        })}
      </div>

      <div className="mt-2 text-[10px] leading-snug" style={{ color: "var(--text-tertiary)" }}>
        标为项目 Agent 的基准<b>不需要 Docker</b>，在你的服务器上直接能跑出真实可对标分数（BFCL / τ² / GAIA / Kotlin）。
        只有 <b>官方数据集</b> 的分数才拿去和顶尖 agent 对标。<b>内置最小集</b>（自造用例）与 <b>smoke</b>（管线自检）都不代表真实基准水平。
        SWE-bench / Terminal-bench 的真集需要 Docker；工具调用基准（BFCL）不需要 Docker，装了官方数据即可出真实可对标分数。
      </div>
    </div>
  );
}

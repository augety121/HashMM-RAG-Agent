"use client";
/** SelfTestView — 测试中枢（V272）。
 *  你点名的"按钮式自测"：分组勾选要测的功能 → 一键运行 → 逐项 PASS/FAIL/SKIP
 *  与耗时、可行动的失败详情。后端每个套件都是真调用（/api/selftest）。
 *  评测·慢 组会跑嵌入/LLM 批，仅管理员可执行、默认不勾。 */
import { useEffect, useMemo, useState } from "react";
import { selftestSuites, selftestRunAsync, selftestJob, probeBackend, type SelfTestSuite, type SelfTestResult, type SelfTestCase } from "@/lib/api";
import { BenchmarkCards } from "./BenchmarkCards";
import { Play, CheckCircle2, XCircle, MinusCircle, Loader2, ClipboardCopy, Download, ChevronDown, ChevronRight } from "lucide-react";
import { showToast } from "@/lib/toast";
import { useStore } from "@/lib/store";

export function SelfTestView() {
  const [suites, setSuites] = useState<SelfTestSuite[]>([]);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [err, setErr] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // V295 后台化：运行态（进度/结果/汇总/报告）全部挂在全局 store 并持久化——退出测试中枢再回来、
  // 甚至整页刷新，都能看到上次/正在进行的测试进度与结果，不再一片空白。诉求："刚测完退出再打开
  // 测试中枢，之前的任务没了"。
  const stRun = useStore(s => s.selftest);
  const setSelftest = useStore(s => s.setSelftest);
  const running = !!stRun?.running;
  const results = (stRun?.results as unknown as SelfTestResult[] | undefined) ?? null;
  const summary = stRun?.summary ?? null;
  const progress = stRun?.progress ?? { done: 0, total: 0, cur: "" };
  const reportMd = stRun?.reportMd ?? "";
  const reportPath = stRun?.reportPath ?? "";

  useEffect(() => {
    selftestSuites()
      .then(r => {
        const ss = r.suites || [];
        setSuites(ss);
        // V296 桌面体验：优先恢复**上次运行的勾选**（配合 V295 的运行态持久化——退出重开测试中枢，
        // 勾选、进度、结果全都接着上次），上次勾选里已下线的套件自动剔除；没有历史才落默认非慢项。
        const lastIds = (useStore.getState().selftest?.ids || []).filter(id => ss.some(s => s.id === id));
        setSel(lastIds.length ? new Set(lastIds) : new Set(ss.filter(s => !s.slow).map(s => s.id)));
      })
      .catch(e => setErr((e as Error).message || "加载套件失败（后端需 V272+）"));
  }, []);

  const groups = useMemo(() => {
    const g = new Map<string, SelfTestSuite[]>();
    for (const s of suites) { if (!g.has(s.group)) g.set(s.group, []); g.get(s.group)!.push(s); }
    return [...g.entries()];
  }, [suites]);

  const toggle = (id: string) => setSel(p => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const toggleGroup = (items: SelfTestSuite[]) => setSel(p => {
    const n = new Set(p);
    const all = items.every(i => n.has(i.id));
    items.forEach(i => { if (all) n.delete(i.id); else n.add(i.id); });
    return n;
  });
  const selectAll = () => setSel(new Set(suites.map(s => s.id)));
  const toggleExpand = (id: string) => setExpanded(p => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  // V282→V295→V298：逐套件运行驱动真实进度条；运行态写进全局 store（并持久化），退出/切页/刷新
  // 后重进都能看到进度与逐项结果。V298 抽出 runIds(ids) 内核，供"只重跑失败项"复用。
  const runIds = async (ids: string[]) => {
    if (ids.length === 0) { showToast("没有可运行的项", "warning"); return; }
    if (running) { showToast("已有测试在运行中（后台进行，可切走再回来看进度）", "info"); return; }
    setErr("");
    setSelftest({ running: true, results: [], summary: null, reportMd: "", reportPath: "",
      ids, startedAt: Date.now(), progress: { done: 0, total: ids.length, cur: "" } });
    // V300 异步任务式：一次 /run_async 拿 job_id（server 后台跑），随后轮询 /job/{id}——**每次 HTTP
    // 都很短**，超重 LLM 套件（可跑 7 分钟+）也不会占住 AutoDL 反代长连接被掐断误判"后端未连接"。
    // 轮询自带容错：单次抖动重探后端后继续，绝不因一次网络抖动就中止整轮。
    try {
      const { job_id } = await selftestRunAsync(ids, true);   // save=true：服务器落盘 data/selftest_reports
      let miss = 0;
      const sleep = (ms: number) => new Promise(res => setTimeout(res, ms));
      // 首轮稍等，之后每 1.5s 拉一次
      // eslint-disable-next-line no-constant-condition
      while (true) {
        await sleep(1500);
        let job: Awaited<ReturnType<typeof selftestJob>>;
        try {
          job = await selftestJob(job_id);
          miss = 0;
        } catch (e) {
          miss++;
          if (miss >= 8) { throw new Error(`轮询任务连续失败：${(e as Error)?.message || e}`); }
          try { await probeBackend(); } catch { /* 继续轮询 */ }
          continue;
        }
        const cur = job.results.length < job.total
          ? (suites.find(s => s.id === ids[job.done])?.name || "运行中…")
          : "";
        setSelftest({
          results: (job.results || []) as never[],
          progress: { done: job.done, total: job.total, cur },
        });
        if (job.status === "done" || job.status === "error") {
          const sm = job.summary || { pass: 0, fail: 0, skip: 0, total: job.results.length };
          // 服务器已生成完整报告并落盘；本地兜底用客户端渲染保证"下载"始终可用
          const md = job.report_md || buildReportMd((job.results || []) as unknown as SelfTestResult[], sm);
          setSelftest({
            progress: { done: job.total, total: job.total, cur: "" },
            summary: sm, reportMd: md, reportPath: job.report_path || "", running: false,
          });
          if (job.status === "error") showToast(`任务异常：${job.error || "未知"}`, "warning");
          else if (job.results.length !== job.total) showToast(`已执行 ${job.results.length}/${job.total} 项`, "warning");
          break;
        }
      }
    } catch (e) {
      setErr((e as Error).message || "执行失败");
      setSelftest({ running: false });
    }
  };

  const run = async () => {
    if (sel.size === 0) { showToast("先勾选至少一个测试项", "warning"); return; }
    await runIds(suites.filter(s => sel.has(s.id)).map(s => s.id));   // 按展示顺序
  };

  // V298 桌面提效：大跑一轮后，只针对失败项快速迭代——不必全选重跑。
  const rerunFailures = async () => {
    const failIds = (results || []).filter(r => !r.ok && !r.skip).map(r => r.id);
    if (failIds.length === 0) { showToast("没有失败项可重跑", "info"); return; }
    setSel(new Set(failIds));   // 勾选聚焦到失败集
    await runIds(failIds);
  };

  const copyFails = () => {
    const bad = (results || []).filter(r => !r.ok && !r.skip);
    const text = bad.map(r => `[FAIL] ${r.group}/${r.name} (${r.ms}ms): ${r.detail}`).join("\n") || "无失败项";
    navigator.clipboard?.writeText(text).then(() => showToast("失败项已复制，可直接贴给我修", "success")).catch(() => {});
  };
  // 客户端直接生成完整报告 md（含深度逐条用例+逐次留证）——保证"保存/下载"永远可用，不依赖服务器往返
  const buildReportMd = (rs: SelfTestResult[], _sm: { pass: number; fail: number; skip: number; total: number }): string => {
    const L: string[] = [];
    // 计数一律从结果本身算，保证与下方逐项一致（修 summary 曾显示 0/0/0 的问题）
    const pass = rs.filter(r => r.ok && !r.skip).length;
    const fail = rs.filter(r => !r.ok && !r.skip).length;
    const skip = rs.filter(r => r.skip).length;
    const totalMs = rs.reduce((a, r) => a + (r.ms || 0), 0);
    L.push("# HashMM 自测报告");
    L.push(`- 时间：${new Date().toLocaleString()}`);
    L.push(`- 结果：**通过 ${pass} / 失败 ${fail} / 跳过 ${skip}**（共 ${rs.length} 项）· 总耗时 ${(totalMs / 1000).toFixed(1)}s`);
    L.push("");
    // 分组汇总（快速定位哪一块出问题）
    const groups = Array.from(new Set(rs.map(r => r.group)));
    if (groups.length > 1) {
      L.push("## 分组汇总");
      L.push("| 分组 | 通过 | 失败 | 跳过 | 耗时 |");
      L.push("| --- | --- | --- | --- | --- |");
      for (const g of groups) {
        const gr = rs.filter(r => r.group === g);
        const gm = gr.reduce((a, r) => a + (r.ms || 0), 0);
        L.push(`| ${g} | ${gr.filter(r => r.ok && !r.skip).length} | ${gr.filter(r => !r.ok && !r.skip).length} | ${gr.filter(r => r.skip).length} | ${(gm / 1000).toFixed(1)}s |`);
      }
      L.push("");
    }
    // V294/V295 困难压测 + 持久化后台速览：并发/性能/分层记忆/后台执行/历史日期的关键硬指标提到最前。
    const hard = rs.filter(r => r.group === "困难压测" || r.group === "持久化后台");
    if (hard.length) {
      L.push("## 困难压测 · 持久化速览（并发 / 性能 / 分层记忆 / 后台执行 / 历史日期）");
      for (const r of hard) {
        const mark = r.skip ? "跳过" : (r.ok ? "通过" : "失败");
        const hl: string[] = [];
        for (const c of (r.cases || [])) {
          const tr = (c.trace || {}) as Record<string, unknown>;
          for (const key of ["加速比", "吞吐", "有效并行度", "估算省下 token", "轻任务墙钟", "净增", "写入耗时", "丢失条数", "回读条数"]) {
            if (key in tr) hl.push(`${key}=${tr[key]}`);
          }
        }
        const extra = hl.length ? " · " + hl.slice(0, 4).join("；") : "";
        L.push(`- ${mark} **${r.name}**：${(r.detail || "").slice(0, 120)}${extra}`);
      }
      L.push("");
    }
    // 最慢套件 TOP（性能分析——大厂日志必看的一项）
    const slow = [...rs].filter(r => (r.ms || 0) > 0).sort((a, b) => (b.ms || 0) - (a.ms || 0)).slice(0, 5);
    if (slow.length) {
      L.push(`## 最慢套件 TOP${slow.length}`);
      for (const r of slow) L.push(`- ${((r.ms || 0) / 1000).toFixed(1)}s — ${r.group} / ${r.name}`);
      L.push("");
    }
    // 失败模式汇总（跨深度套件聚合，最能看出系统性问题）
    const fmAgg: Record<string, number> = {};
    for (const r of rs) {
      const fm = (r.metrics as Record<string, unknown> | undefined)?.["failure_modes"];
      if (fm && typeof fm === "object") for (const [k, v] of Object.entries(fm as Record<string, unknown>)) fmAgg[k] = (fmAgg[k] || 0) + (Number(v) || 0);
    }
    const fmKeys = Object.keys(fmAgg);
    if (fmKeys.length) {
      L.push("## 失败模式汇总（跨深度套件）");
      for (const k of fmKeys.sort((a, b) => fmAgg[b] - fmAgg[a])) L.push(`- ${k}：${fmAgg[k]} 次`);
      L.push("");
    }
    const fails = rs.filter(r => !r.ok && !r.skip);
    if (fails.length) { L.push("## 失败项"); for (const r of fails) L.push(`- **${r.group} / ${r.name}**（${r.ms}ms）：${r.detail}`); L.push(""); }
    const skips = rs.filter(r => r.skip);
    if (skips.length) { L.push("## 跳过项"); for (const r of skips) L.push(`- ${r.group} / ${r.name}：${r.detail}`); L.push(""); }
    L.push("## 通过项");
    for (const r of rs.filter(r => r.ok && !r.skip)) L.push(`- ${r.group} / ${r.name}（${r.ms}ms）：${r.detail}`);
    L.push("");
    const deep = rs.filter(r => (r.cases?.length ?? 0) > 0);
    if (deep.length) {
      L.push("## 深度评测逐条用例（问题 · 思考过程 · 最终答案 · 问题定位）");
      for (const r of deep) {
        L.push("");
        L.push(`### ${r.group} / ${r.name}（通过率 ${r.metrics?.pass_rate ?? "?"}、均分 ${r.metrics?.avg_score ?? "?"}）`);
        const mm = (r.metrics || {}) as Record<string, unknown>;
        const kpis = Object.entries(mm).filter(([k, v]) => !["pass_rate", "avg_score", "passed", "failed", "skipped", "failure_modes"].includes(k) && (typeof v === "number" || typeof v === "string"));
        if (kpis.length) L.push(`- 关键指标：${kpis.map(([k, v]) => `${k}=${v}`).join(" · ")}`);
        for (const c of (r.cases as SelfTestCase[])) {
          const mark = c.skipped ? "跳过" : c.passed ? "通过" : "失败";
          L.push("");
          L.push(`#### ${mark} ${c.name} · ${c.score}分${c.runs ? ` · ${c.passes ?? 0}/${c.runs} 跑通` : ""}`);
          const tr = c.trace || {};
          for (const [k, v] of Object.entries(tr)) {
            // V295 日志清晰化：列表型字段（执行日志/steps/timeline 等）逐行渲染成子列表——用户明确
            // 要"极其详细、逐步记录"的日志，一行行看比挤成一坨"；"分隔清楚得多。
            if (Array.isArray(v) && v.length) {
              L.push(`- **${k}**（${v.length} 步）：`);
              for (const line of v as unknown[]) L.push(`    - ${String(line)}`);
            } else {
              const t = (typeof v === "object" && v !== null) ? JSON.stringify(v) : String(v);
              L.push(`- **${k}**：${t}`);
            }
          }
          if (c.runs_detail && c.runs_detail.length > 1) {
            L.push(`- 逐次运行留证（${c.runs_detail.length} 次）：`);
            for (const rd of c.runs_detail) L.push(`    - 第${rd.run}次 ${rd.passed ? "通过" : "失败"} ${rd.score}分${rd.failure_mode ? ` [${rd.failure_mode}]` : ""} · ${rd.gist || ""}`);
          }
        }
      }
    }
    L.push("");
    L.push("## 结论");
    L.push(fail === 0 ? "本轮全部通过；建议发版前全量重跑并归档。" : `本轮有 ${fail} 项失败，见上方"失败项"、"失败模式汇总"与逐条用例的"问题定位"。`);
    return L.join("\n");
  };

  const downloadReport = () => {
    const md = reportMd || (results.length ? buildReportMd(results, summary || { pass: 0, fail: 0, skip: 0, total: results.length }) : "");
    if (!md) { showToast("先运行一次自测", "warning"); return; }
    const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `hashmm-selftest-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "")}.md`;
    a.click(); URL.revokeObjectURL(a.href);
  };

  const Icon = ({ r }: { r: SelfTestResult }) =>
    r.skip ? <MinusCircle size={14} style={{ color: "var(--text-tertiary)" }} />
      : r.ok ? <CheckCircle2 size={14} style={{ color: "#16a34a" }} />
        : <XCircle size={14} style={{ color: "#dc2626" }} />;

  return (
    <div className="flex-1 overflow-y-auto px-6 py-5 max-w-[980px] mx-auto w-full">
      <div className="text-[12.5px] mb-4 leading-relaxed" style={{ color: "var(--text-secondary)" }}>
        每个套件都<b>真实调用</b>对应功能入口（LLM 往返、嵌入、重排/多查询、审计写读闭环、
        Agent 门控接线…），不是假绿灯。FAIL 的详情可一键复制发给我，SKIP=前置条件不满足
        （如未配模型/语料为空），如实标注、不算失败。
      </div>
      {err && <div className="text-[12px] mb-3 px-3 py-2 rounded-lg" style={{ background: "color-mix(in srgb, #dc2626 8%, transparent)", color: "#b42318" }}>{err}</div>}

      {/* 勾选区 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
        {groups.map(([g, items]) => (
          <div key={g} className="rounded-xl p-3.5" style={{ border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
            <div className="flex items-center mb-2">
              <button onClick={() => toggleGroup(items)} className="text-[12.5px] font-semibold hover:opacity-75" style={{ color: "var(--text-primary)" }}>
                {g}{g.includes("慢") ? "（管理员）" : ""}
              </button>
              <span className="ml-auto text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
                {items.filter(i => sel.has(i.id)).length}/{items.length}
              </span>
            </div>
            {items.map(s => (
              <label key={s.id} className="flex items-center gap-2 py-1 cursor-pointer text-[12px]" style={{ color: "var(--text-secondary)" }}>
                <input type="checkbox" checked={sel.has(s.id)} onChange={() => toggle(s.id)} className="accent-[var(--accent)]" />
                {s.name}
              </label>
            ))}
          </div>
        ))}
      </div>

      <div className="flex items-center gap-3 mb-3 flex-wrap">
        <button onClick={run} disabled={running}
          className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-[12.5px] font-semibold text-white disabled:opacity-60 hover:opacity-90"
          style={{ background: "var(--accent)" }}>
          {running ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
          {running ? "自测运行中…" : `运行自测（已选 ${sel.size} 项）`}
        </button>
        {/* V296 上次运行摘要：重开测试中枢一眼看到上次何时跑的、结果如何（配合运行态持久化） */}
        {!running && stRun?.summary && (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px]"
            style={{ border: "1px solid var(--border)", color: "var(--text-tertiary)" }}
            title={`上次运行开始于 ${new Date(stRun.startedAt).toLocaleString()}`}>
            上次 {new Date(stRun.startedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}
            <span style={{ color: "var(--success, #22a06b)" }}>通过 {stRun.summary.pass}</span>
            {stRun.summary.fail > 0 && <span style={{ color: "#b42318" }}>失败 {stRun.summary.fail}</span>}
            {stRun.summary.skip > 0 && <span>跳过 {stRun.summary.skip}</span>}
          </span>
        )}
        <button onClick={selectAll} disabled={running}
          className="px-3 py-2 rounded-lg text-[12px] font-medium hover:bg-[var(--bg-tertiary)] disabled:opacity-60"
          style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          全选（含深度评测 {suites.length} 项）
        </button>
        {summary && (
          <span className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
            通过 <b style={{ color: "#16a34a" }}>{summary.pass}</b> · 失败 <b style={{ color: summary.fail ? "#dc2626" : "var(--text-secondary)" }}>{summary.fail}</b> · 跳过 {summary.skip} / 共 {summary.total}
          </span>
        )}
        {results && results.some(r => !r.ok && !r.skip) && !running && (
          <>
            <button onClick={rerunFailures} className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[11.5px] font-semibold hover:opacity-90" style={{ color: "#dc2626", border: "1px solid #dc2626" }} title="只把失败的套件再跑一遍，快速验证修复">
              <Play size={12} /> 只重跑失败项（{results.filter(r => !r.ok && !r.skip).length}）
            </button>
            <button onClick={copyFails} className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[11.5px] hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
              <ClipboardCopy size={12} /> 复制失败项
            </button>
          </>
        )}
        {(reportMd || (results && results.length > 0)) && !running && (
          <button onClick={downloadReport} className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[11.5px] font-semibold text-white hover:opacity-90" style={{ background: "var(--accent)" }}>
            <Download size={12} /> 保存报告到本地(.md)
          </button>
        )}
      </div>

      {/* V282 进度条 */}
      {running && progress.total > 0 && (
        <div className="mb-5">
          <div className="flex items-center justify-between text-[11.5px] mb-1.5" style={{ color: "var(--text-tertiary)" }}>
            <span>正在测试：{progress.cur || "汇总报告…"}</span>
            <span>{progress.done}/{progress.total}（{Math.round((progress.done / progress.total) * 100)}%）</span>
          </div>
          <div className="h-2 rounded-full overflow-hidden" style={{ background: "var(--bg-tertiary)" }}>
            <div className="h-full rounded-full transition-all duration-300"
              style={{ width: `${Math.round((progress.done / progress.total) * 100)}%`, background: "var(--accent)" }} />
          </div>
        </div>
      )}
      {reportPath && !running && (
        <div className="text-[11px] mb-4 px-3 py-2 rounded-lg" style={{ background: "var(--bg-secondary)", color: "var(--text-tertiary)" }}>
          完整报告已存到服务器：<code style={{ color: "var(--text-secondary)" }}>{reportPath}</code>
          <br />同目录下 <code style={{ color: "var(--text-secondary)" }}>selftest-latest.md</code> 永远是最新的一份（服务器日志启动时也会打印该目录）。点上方按钮可另存到本地。
        </div>
      )}

      {/* 结果表 */}
      {results && (
        <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--border)" }}>
          {results.map(r => {
            const cases = r.cases;
            const canExpand = !!(cases && cases.length);
            const isOpen = expanded.has(r.id);
            return (
              <div key={r.id} style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                <div className="flex items-start gap-2.5 px-4 py-2.5">
                  <span className="mt-0.5"><Icon r={r} /></span>
                  <span className="flex-shrink-0 w-[76px] text-[11px] pt-0.5" style={{ color: "var(--text-tertiary)" }}>{r.group}</span>
                  <span className="flex-shrink-0 w-[150px] text-[12.5px] font-medium flex items-center gap-1" style={{ color: "var(--text-primary)" }}>
                    {canExpand && (
                      <button onClick={() => toggleExpand(r.id)} className="hover:opacity-70">
                        {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                      </button>
                    )}
                    {r.name}
                  </span>
                  <span className="flex-1 min-w-0 text-[12px] leading-relaxed break-words" style={{ color: r.skip ? "var(--text-tertiary)" : r.ok ? "var(--text-secondary)" : "#b42318" }}>
                    {r.skip ? `跳过：${r.detail}` : r.detail || (r.ok ? "通过" : "失败")}
                  </span>
                  <span className="flex-shrink-0 text-[11px] pt-0.5" style={{ color: "var(--text-tertiary)" }}>{r.ms}ms</span>
                </div>
                {canExpand && isOpen && (
                  <div className="px-4 pb-3 pl-[92px] space-y-2.5">
                    {cases!.map((c, i) => {
                      const trace = c.trace && Object.keys(c.trace).length ? c.trace : null;
                      const stable = (c.runs ? ` · ${c.passes ?? 0}/${c.runs} 跑通` : "");
                      return (
                        <div key={i} className="rounded-lg px-3 py-2 text-[11.5px]" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                          <div className="flex items-center gap-2 mb-1">
                            <span>{c.skipped ? <MinusCircle size={12} style={{ color: "var(--text-tertiary)" }} /> : c.passed ? <CheckCircle2 size={12} style={{ color: "#16a34a" }} /> : <XCircle size={12} style={{ color: "#dc2626" }} />}</span>
                            <span className="font-semibold" style={{ color: "var(--text-primary)" }}>{c.name}</span>
                            <span style={{ color: "var(--text-tertiary)" }}>{c.score}分{stable}</span>
                          </div>
                          {trace ? (
                            <div className="space-y-0.5 pl-1">
                              {Object.entries(trace).map(([k, v]) => {
                                const isProblem = k.includes("问题定位") || k.includes("判定");
                                const text = Array.isArray(v) ? (v as unknown[]).map(x => String(x)).join("；") : (typeof v === "object" ? JSON.stringify(v) : String(v));
                                return (
                                  <div key={k} className="flex gap-1.5 break-words">
                                    <span className="flex-shrink-0 font-medium" style={{ color: "var(--text-secondary)" }}>{k}：</span>
                                    <span className="min-w-0" style={{ color: isProblem && !c.passed ? "#b42318" : "var(--text-primary)", whiteSpace: "pre-wrap" }}>{text}</span>
                                  </div>
                                );
                              })}
                            </div>
                          ) : (
                            <div className="break-words pl-1" style={{ color: c.skipped ? "var(--text-tertiary)" : c.passed ? "var(--text-secondary)" : "#b42318" }}>{c.detail}</div>
                          )}
                          {c.runs_detail && c.runs_detail.length > 1 && (
                            <div className="mt-2 pt-2" style={{ borderTop: "1px dashed var(--border)" }}>
                              <div className="font-medium mb-1" style={{ color: "var(--text-secondary)" }}>逐次运行留证（{c.runs_detail.length} 次 · LLM 非确定，每次都留）：</div>
                              <div className="space-y-0.5 pl-1">
                                {c.runs_detail.map((rd, j) => (
                                  <div key={j} className="flex gap-1.5 break-words items-start">
                                    <span className="flex-shrink-0" style={{ color: "var(--text-tertiary)" }}>#{rd.run}</span>
                                    <span className="flex-shrink-0">{rd.passed ? <CheckCircle2 size={11} style={{ color: "#16a34a" }} /> : <XCircle size={11} style={{ color: "#dc2626" }} />}</span>
                                    <span className="flex-shrink-0" style={{ color: "var(--text-tertiary)" }}>{rd.score}分</span>
                                    {rd.failure_mode && <span className="flex-shrink-0" style={{ color: "#b42318" }}>[{rd.failure_mode}]</span>}
                                    {rd.gist && <span className="min-w-0" style={{ color: "var(--text-primary)", whiteSpace: "pre-wrap" }}>{rd.gist}</span>}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* V306 外部基准对标结果卡片：分数条 + leaderboard 对标位置 + 趋势 */}
      <BenchmarkCards results={results} />
    </div>
  );
}

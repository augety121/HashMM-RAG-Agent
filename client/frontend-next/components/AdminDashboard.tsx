"use client";
import { useState, useEffect, useCallback } from "react";
import { BarChart3, Users, MessageSquare, Zap, Clock, Database, Cpu, Network,
         RefreshCw, TrendingUp, FileText, Search, CheckCircle2, XCircle, AlertTriangle, Info } from "lucide-react";

interface DashboardData {
  messages: { total: number; today: number };
  conversations: { total: number };
  tokens_today: { input: number; output: number; cost_cny: number };
  task_distribution: { detail: string; count: number }[];
}

interface MetricsData {
  runtime: {
    total_queries: number; cache_hits: number; cache_hit_rate: string;
    avg_latency_ms: number; p95_latency_ms: number;
    total_llm_calls: number; intents: Record<string, number>; skills: Record<string, number>;
  };
  retrieval: {
    total_vectors: number; bm25_docs: number; reranker_available: boolean;
    documents: { filename: string; doc_id: string; num_chunks: number }[];
  };
  knowledge_graph: { entities: number; relations: number };
  database: { total_conversations: number; total_messages: number; total_users: number; user_memories: number };
  embedding_cache?: { size: number; max_size: number; hits: number; misses: number; hit_rate: string };
}

function StatCard({ icon: Icon, label, value, sub, color }: {
  icon: React.ElementType; label: string; value: string | number | null; sub?: string; color: string;
}) {
  return (
    <div className="p-4 rounded-xl" style={{ border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-2 mb-2">
        <div className="p-1.5 rounded-lg" style={{ background: `${color}12` }}>
          <Icon size={15} style={{ color }} />
        </div>
        <span className="text-[11px] font-medium" style={{ color: "var(--text-tertiary)" }}>{label}</span>
      </div>
      <div className="text-[24px] font-bold tabular-nums" style={{ color: "var(--text-primary)" }}>
        {value == null ? "—" : typeof value === "number" ? value.toLocaleString() : value}
      </div>
      {sub && <div className="text-[11px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{sub}</div>}
    </div>
  );
}

type SourceResult<T> = { data: T | null; issue: string | null };

async function readSource<T>(url: string, headers: Record<string, string>): Promise<SourceResult<T>> {
  try {
    const response = await fetch(url, { headers });
    if (!response.ok) {
      const reason = response.status === 401 ? "登录已失效"
        : response.status === 403 ? "当前账号无权限"
        : `服务返回 ${response.status}`;
      return { data: null, issue: reason };
    }
    return { data: await response.json() as T, issue: null };
  } catch {
    return { data: null, issue: "网络连接失败" };
  }
}

function BarChart({ data, color }: { data: { label: string; value: number }[]; color: string }) {
  const max = Math.max(...data.map(d => d.value), 1);
  return (
    <div className="space-y-2">
      {data.map(d => (
        <div key={d.label} className="flex items-center gap-2">
          <span className="w-[80px] text-[11px] text-right truncate" style={{ color: "var(--text-secondary)" }}>{d.label}</span>
          <div className="flex-1 h-5 rounded-md overflow-hidden" style={{ background: "var(--bg-tertiary)" }}>
            <div className="h-full rounded-md transition-all flex items-center pl-2"
                 style={{ width: `${(d.value / max) * 100}%`, background: color, minWidth: d.value > 0 ? "20px" : "0" }}>
              {(d.value / max) > 0.2 && <span className="text-[10px] text-white font-medium">{d.value}</span>}
            </div>
          </div>
          <span className="w-[35px] text-[11px] font-mono tabular-nums text-right" style={{ color: "var(--text-tertiary)" }}>{d.value}</span>
        </div>
      ))}
    </div>
  );
}

export function AdminDashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [metrics, setMetrics] = useState<MetricsData | null>(null);
  const [analytics, setAnalytics] = useState<Record<string, any> | null>(null);
  const [loading, setLoading] = useState(true);
  const [evalRunning, setEvalRunning] = useState(false);
  const [evalResult, setEvalResult] = useState<Record<string, any> | null>(null);
  const [evalError, setEvalError] = useState("");
  const [sourceIssues, setSourceIssues] = useState<string[]>([]);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);

  const headers = useCallback(() => {
    const h: Record<string, string> = {};
    const t = localStorage.getItem("hmm_token");
    if (t) h["Authorization"] = `Bearer ${t}`;
    return h;
  }, []);

  const [agentUsage, setAgentUsage] = useState<any>(null);
  const load = useCallback(async () => {
    setLoading(true);
    const [d, m, a, au] = await Promise.all([
      readSource<DashboardData>("/api/admin/dashboard", headers()),
      readSource<MetricsData>("/api/admin/metrics", headers()),
      readSource<Record<string, any>>("/api/admin/retrieval-analytics?hours=24", headers()),
      // V68: 服务器侧外部 coding agent 用量（V59 端点；未装 agent 时各项为 null）
      readSource<any>("/api/agent-usage", headers()),
    ]);
    if (d.data) setData(d.data);
    if (m.data) setMetrics(m.data);
    if (a.data) setAnalytics(a.data);
    if (au.data) setAgentUsage(au.data);
    const issues = [
      d.issue && `业务概览：${d.issue}`,
      m.issue && `运行指标：${m.issue}`,
      a.issue && `检索分析：${a.issue}`,
      au.issue && `Agent 用量：${au.issue}`,
    ].filter((item): item is string => Boolean(item));
    setSourceIssues(issues);
    if (issues.length < 4) setLastUpdatedAt(Date.now());
    setLoading(false);
  }, [headers]);

  useEffect(() => { load(); }, [load]);

  if (loading && !data && !metrics && !analytics && !agentUsage) {
    return <div className="p-8 text-center" style={{ color: "var(--text-tertiary)" }}>正在核对运行数据…</div>;
  }

  const rt = metrics?.runtime;
  const ret = metrics?.retrieval;
  const kg = metrics?.knowledge_graph;
  const db = metrics?.database;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>系统监控</h3>
          <div className="text-[10.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
            {sourceIssues.length === 0 ? "4/4 个数据源本次已验证" : `${4 - sourceIssues.length}/4 个数据源本次已验证`}
            {lastUpdatedAt ? ` · ${new Date(lastUpdatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}
          </div>
        </div>
        <button onClick={load} disabled={loading} className="admin-btn">
          <RefreshCw size={13} className={loading ? "animate-spin" : ""} /> {loading ? "核对中" : "刷新"}
        </button>
      </div>

      {sourceIssues.length > 0 && (
        <div className="rounded-xl px-3.5 py-3 flex items-start gap-2.5" style={{ border: "1px solid #d9770633", background: "#d9770608" }}>
          <AlertTriangle size={15} style={{ color: "#d97706", marginTop: 1, flexShrink: 0 }} />
          <div className="min-w-0">
            <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>部分数据暂时无法验证</div>
            <div className="text-[11px] mt-0.5 leading-5" style={{ color: "var(--text-secondary)" }}>
              {sourceIssues.join("；")}。数值不会用 0 代替读取失败；已有内容为最近一次成功结果。
            </div>
          </div>
        </div>
      )}

      {/* Top stats */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        <StatCard icon={MessageSquare} label="总对话" value={db?.total_conversations ?? data?.conversations?.total ?? null}
                  sub={data ? `今日 ${data.messages?.today ?? 0} 条消息` : "业务概览未读取"} color="#2563eb" />
        <StatCard icon={Zap} label="LLM 调用" value={rt?.total_llm_calls ?? null}
                  sub={rt ? `缓存命中 ${rt.cache_hit_rate}` : "运行指标未读取"} color="#7c3aed" />
        <StatCard icon={Clock} label="平均延迟" value={rt ? `${rt.avg_latency_ms}ms` : null}
                  sub={rt ? `P95: ${rt.p95_latency_ms}ms` : "运行指标未读取"} color="#d97706" />
        <StatCard icon={Database} label="知识库" value={ret?.total_vectors ?? null}
                  sub={ret ? `${ret.documents?.length ?? 0} 文档 · Reranker ${ret.reranker_available ? "可用" : "不可用"}` : "检索指标未读取"} color="#059669" />
      </div>

      {/* Second row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        <StatCard icon={Users} label="用户" value={db?.total_users ?? null}
                  sub={db ? `${db.user_memories ?? 0} 条记忆` : "数据库指标未读取"} color="#4f46e5" />
        <StatCard icon={Network} label="知识图谱" value={kg?.entities ?? null}
                  sub={kg ? `${kg.relations ?? 0} 关系` : "图谱指标未读取"} color="#0891b2" />
        <StatCard icon={TrendingUp} label="今日 Token" value={data?.tokens_today ? `${Math.round((data.tokens_today.input + data.tokens_today.output) / 1000)}K` : null}
                  sub={data?.tokens_today ? `≈ ¥${data.tokens_today.cost_cny.toFixed(2)}` : "业务概览未读取"} color="#ea580c" />
        <StatCard icon={Cpu} label="Embedding 缓存" value={metrics?.embedding_cache?.hit_rate ?? null}
                  sub={metrics?.embedding_cache ? `${metrics.embedding_cache.size}/${metrics.embedding_cache.max_size} 条目` : "缓存指标未读取"} color="#16a34a" />
      </div>

      {/* V68: 服务器侧 Coding Agent 用量（Claude Code / Codex，读自 ~/.claude ~/.codex） */}
      {agentUsage && (agentUsage.claude_code || agentUsage.codex) && (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          {agentUsage.claude_code && (<>
            <StatCard icon={Zap} label="Claude Code · 今天"
              value={`${Math.round((agentUsage.claude_code.today?.total || 0) / 1000)}K`}
              sub={`${agentUsage.claude_code.today?.msgs || 0} msgs`} color="#7C5CFC" />
            <StatCard icon={Zap} label="Claude Code · 近7天"
              value={`${Math.round((agentUsage.claude_code.week?.total || 0) / 1000)}K`}
              sub={`${agentUsage.claude_code.week?.msgs || 0} msgs`} color="#7C5CFC" />
          </>)}
          {agentUsage.codex && (<>
            <StatCard icon={Cpu} label="Codex · 累计 tokens"
              value={`${Math.round((agentUsage.codex.tokens?.total || 0) / 1000)}K`}
              sub="最新会话快照" color="#16A34A" />
            {agentUsage.codex.rate_limits?.primary?.used_percent != null && (
              <StatCard icon={Clock} label="Codex · 5h 窗口"
                value={`${agentUsage.codex.rate_limits.primary.used_percent}%`}
                sub="官方配额已用" color="#16A34A" />
            )}
          </>)}
        </div>
      )}

      {/* Charts row */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {/* Task distribution */}
        {data?.task_distribution && data.task_distribution.length > 0 && (
          <div className="rounded-xl p-4" style={{ border: "1px solid var(--border)" }}>
            <h4 className="text-[12px] font-semibold mb-3" style={{ color: "var(--text-tertiary)" }}>
              <FileText size={12} className="inline mr-1.5" />任务分布（7天）
            </h4>
            <BarChart data={data.task_distribution.map(t => ({ label: t.detail, value: t.count }))} color="#4f46e5" />
          </div>
        )}

        {/* Intent distribution */}
        {rt?.intents && Object.keys(rt.intents).length > 0 && (
          <div className="rounded-xl p-4" style={{ border: "1px solid var(--border)" }}>
            <h4 className="text-[12px] font-semibold mb-3" style={{ color: "var(--text-tertiary)" }}>
              <Search size={12} className="inline mr-1.5" />意图分布
            </h4>
            <BarChart data={Object.entries(rt.intents).map(([k, v]) => ({ label: k, value: v as number }))} color="#059669" />
          </div>
        )}
      </div>

      {/* Documents table */}
      {ret?.documents && ret.documents.length > 0 && (
        <div className="rounded-xl p-4" style={{ border: "1px solid var(--border)" }}>
          <h4 className="text-[12px] font-semibold mb-3" style={{ color: "var(--text-tertiary)" }}>
            <Database size={12} className="inline mr-1.5" />已索引文档
          </h4>
          <div className="space-y-1.5">
            {ret.documents.map((d, i) => (
              <div key={i} className="flex items-center justify-between px-2 py-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]">
                <span className="text-[12px] truncate flex-1" style={{ color: "var(--text-primary)" }}>{d.filename}</span>
                <span className="text-[11px] font-mono tabular-nums ml-3" style={{ color: "var(--accent)" }}>{d.num_chunks} chunks</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* v9.0: Retrieval Analytics */}
      {analytics && analytics.total > 0 && (
        <div className="rounded-xl p-4" style={{ border: "1px solid var(--border)" }}>
          <h4 className="text-[12px] font-semibold mb-3" style={{ color: "var(--text-tertiary)" }}>
            <Search size={12} className="inline mr-1.5" />检索质量（24h）
          </h4>
          <div className="grid grid-cols-4 gap-2 mb-3">
            {[
              ["平均 Score", analytics.avg_score],
              ["零结果率", analytics.zero_result_rate],
              ["KG 命中率", analytics.kg_hit_rate],
              ["平均延迟", `${analytics.avg_latency_ms}ms`],
            ].map(([label, value]) => (
              <div key={String(label)} className="p-2 rounded-lg text-center" style={{ background: "var(--bg-tertiary)" }}>
                <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{label}</div>
                <div className="text-[14px] font-bold" style={{ color: "var(--text-primary)" }}>{String(value)}</div>
              </div>
            ))}
          </div>
          {analytics.strategy_distribution && (
            <div className="flex gap-2 mb-2">
              {Object.entries(analytics.strategy_distribution as Record<string, number>).map(([k, v]) => (
                <span key={k} className="px-2 py-0.5 rounded text-[10px] font-medium"
                  style={{ background: k === "grounded" ? "#05966915" : k === "augmented" ? "#d9770615" : "#64748b15",
                           color: k === "grounded" ? "#059669" : k === "augmented" ? "#d97706" : "#64748b" }}>
                  {k}: {v}
                </span>
              ))}
            </div>
          )}
          {analytics.zero_result_queries?.length > 0 && (
            <div className="mt-2">
              <div className="text-[10px] font-semibold mb-1" style={{ color: "var(--text-tertiary)" }}>零结果查询</div>
              {analytics.zero_result_queries.slice(0, 3).map((q: { query: string }, i: number) => (
                <div key={i} className="text-[11px] py-0.5 truncate" style={{ color: "var(--text-secondary)" }}>• {q.query}</div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* v10.0: Quality Evaluation + Evolution Stats + Benchmark */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {/* Evolution Engine Stats */}
        <EvolutionStats headers={headers} />

        {/* Performance Benchmark */}
        <BenchmarkPanel headers={headers} />
      </div>

      <div className="rounded-xl p-4" style={{ border: "1px solid var(--border)" }}>
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-[12px] font-semibold" style={{ color: "var(--text-tertiary)" }}>
            <BarChart3 size={12} className="inline mr-1.5" />回答质量评估
          </h4>
          <button onClick={async () => {
            setEvalRunning(true);
            setEvalError("");
            try {
              const res = await fetch("/api/admin/eval/run", { method: "POST", headers: headers() });
              if (res.ok) setEvalResult(await res.json());
              else setEvalError(res.status === 403 ? "当前账号没有运行评测的权限" : `评测服务返回 ${res.status}`);
            } catch { setEvalError("评测服务暂时无法连接"); } finally { setEvalRunning(false); }
          }} disabled={evalRunning}
            className="admin-btn" style={{ fontSize: 11 }}>
            {evalRunning ? "运行中..." : "运行测试"}
          </button>
        </div>
        {evalError ? (
          <div className="text-[12px] text-center py-3" style={{ color: "#dc2626" }}>{evalError}</div>
        ) : evalResult ? (
          <div>
            <div className="text-[20px] font-bold mb-2" style={{ color: evalResult.pass_rate >= 80 ? "#059669" : "#ef4444" }}>
              {evalResult.summary}
            </div>
            <div className="space-y-1">
              {evalResult.results?.slice(0, 8).map((r: { case_id: string; passed: boolean; query: string; score: number }, i: number) => (
                <div key={i} className="flex items-center gap-2 text-[11px]">
                  {r.passed ? <CheckCircle2 size={12} style={{ color: "#16a34a", flexShrink: 0 }} /> : <XCircle size={12} style={{ color: "#ef4444", flexShrink: 0 }} />}
                  <span className="truncate flex-1" style={{ color: "var(--text-secondary)" }}>{r.query}</span>
                  <span className="font-mono" style={{ color: "var(--text-tertiary)" }}>{Math.round(r.score * 100)}%</span>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="text-[12px] text-center py-3" style={{ color: "var(--text-tertiary)" }}>
            点击"运行测试"对 Golden Test 用例执行自动评估
          </div>
        )}
      </div>
    </div>
  );
}

function EvolutionStats({ headers }: { headers: () => Record<string, string> }) {
  const [data, setData] = useState<{
    skills: number; profiles: number; active_7d: number;
    prompt_satisfaction: Record<string, { satisfaction: number; total: number }>;
  } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      fetch("/api/evolution/skills", { headers: headers() }).then(r => r.ok ? r.json() : null).catch(() => null),
      fetch("/api/evolution/profile/stats", { headers: headers() }).then(r => r.ok ? r.json() : null).catch(() => null),
      fetch("/api/evolution/prompt/analysis", { headers: headers() }).then(r => r.ok ? r.json() : null).catch(() => null),
    ]).then(([sk, pr, pa]) => {
      if (!sk && !pr && !pa) {
        setError("进化统计暂时无法读取");
        return;
      }
      setError("");
      setData({
        skills: sk?.skills?.length || 0,
        profiles: pr?.total_profiles || 0,
        active_7d: pr?.active_7d || 0,
        prompt_satisfaction: pa?.analysis || {},
      });
    });
  }, [headers]);

  if (error) return (
    <div className="rounded-xl p-4" style={{ border: "1px solid var(--border)" }}>
      <h4 className="text-[12px] font-semibold mb-2" style={{ color: "var(--text-tertiary)" }}>自我进化引擎</h4>
      <div className="text-[11.5px]" style={{ color: "var(--text-secondary)" }}>{error}，不会显示伪造的零值。</div>
    </div>
  );
  if (!data) return <div className="rounded-xl p-4 text-[11.5px]" style={{ border: "1px solid var(--border)", color: "var(--text-tertiary)" }}>正在读取进化统计…</div>;
  return (
    <div className="rounded-xl p-4" style={{ border: "1px solid var(--border)" }}>
      <h4 className="text-[12px] font-semibold mb-3" style={{ color: "var(--text-tertiary)" }}>
        自我进化引擎
      </h4>
      <div className="grid grid-cols-3 gap-2 mb-3">
        {[
          ["自动技能", data.skills],
          ["用户画像", data.profiles],
          ["7日活跃", data.active_7d],
        ].map(([label, value]) => (
          <div key={String(label)} className="p-2 rounded-lg text-center" style={{ background: "var(--bg-tertiary)" }}>
            <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{label}</div>
            <div className="text-[16px] font-bold" style={{ color: "var(--text-primary)" }}>{String(value)}</div>
          </div>
        ))}
      </div>
      {Object.keys(data.prompt_satisfaction).length > 0 && (
        <div>
          <div className="text-[10px] font-semibold mb-1" style={{ color: "var(--text-tertiary)" }}>Prompt 满意度</div>
          <div className="space-y-1">
            {Object.entries(data.prompt_satisfaction).map(([task, stats]) => (
              <div key={task} className="flex items-center gap-2 text-[11px]">
                <span className="w-20 truncate" style={{ color: "var(--text-secondary)" }}>{task}</span>
                <div className="flex-1 h-2 rounded-full overflow-hidden" style={{ background: "var(--bg-tertiary)" }}>
                  <div className="h-full rounded-full" style={{
                    width: `${(stats.satisfaction || 0) * 100}%`,
                    background: stats.satisfaction >= 0.7 ? "#22c55e" : stats.satisfaction >= 0.4 ? "#f59e0b" : "#ef4444",
                  }} />
                </div>
                <span className="font-mono w-10 text-right" style={{ color: "var(--text-tertiary)" }}>
                  {Math.round((stats.satisfaction || 0) * 100)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function BenchmarkPanel({ headers }: { headers: () => Record<string, string> }) {
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<Record<string, { latency_ms?: number; status: string; meets_target?: boolean }> | null>(null);
  const [error, setError] = useState("");

  async function run() {
    setRunning(true);
    setError("");
    try {
      const r = await fetch("/api/benchmark", { headers: headers() });
      if (r.ok) {
        const data = await r.json();
        setResults(data.benchmark);
      } else {
        setError(r.status === 403 ? "当前账号没有运行基准测试的权限" : `基准服务返回 ${r.status}`);
      }
    } catch { setError("基准服务暂时无法连接"); } finally { setRunning(false); }
  }

  return (
    <div className="rounded-xl p-4" style={{ border: "1px solid var(--border)" }}>
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-[12px] font-semibold" style={{ color: "var(--text-tertiary)" }}>性能基准</h4>
        <button onClick={run} disabled={running} className="admin-btn" style={{ fontSize: 11 }}>
          {running ? "测试中..." : "运行基准测试"}
        </button>
      </div>
      {error ? (
        <div className="text-[12px] text-center py-3" style={{ color: "#dc2626" }}>{error}</div>
      ) : results ? (
        <div className="space-y-2">
          {Object.entries(results).map(([key, val]) => (
            <div key={key} className="flex items-center gap-2 text-[11px]">
              {val.meets_target === true ? <CheckCircle2 size={12} style={{ color: "#16a34a", flexShrink: 0 }} /> : val.meets_target === false ? <AlertTriangle size={12} style={{ color: "#d97706", flexShrink: 0 }} /> : <Info size={12} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />}
              <span className="w-32 truncate" style={{ color: "var(--text-secondary)" }}>{key.replace(/_/g, " ")}</span>
              <span className="font-mono font-bold" style={{ color: "var(--text-primary)" }}>
                {val.latency_ms != null ? `${val.latency_ms}ms` : val.status}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-[12px] text-center py-3" style={{ color: "var(--text-tertiary)" }}>
          测试 Embedding、检索、LLM 延迟，对比目标值
        </div>
      )}
    </div>
  );
}

"use client";
import { useState, useEffect, useCallback } from "react";
import { RefreshCw, Download, Network, BarChart3, Users2, Loader2, Zap } from "lucide-react";

interface KGStats {
  entities: number;
  relations: number;
  density: number;
  communities: number;
  avg_degree?: number;
  connected_components?: number;
  largest_component?: number;
  isolated_entities?: number;
  type_distribution: Record<string, number>;
  top_entities: Array<{ name: string; degree: number; type: string }>;
}

const TYPE_COLORS: Record<string, string> = {
  // English types (from LightweightKG)
  "ORG": "#4f46e5", "PERSON": "#dc2626", "MONEY": "#059669",
  "DATE": "#d97706", "PRODUCT": "#0891b2", "METRIC": "#ea580c",
  "PERCENT": "#8b5cf6", "CONCEPT": "#71717a",
  // Chinese types (from full KG extractor)
  "方法": "#2563eb", "算法": "#7c3aed", "模型": "#059669",
  "数据集": "#d97706", "概念": "#0891b2", "人物": "#dc2626",
  "组织": "#4f46e5", "工具": "#0d9488", "论文": "#6366f1",
  "指标": "#ea580c", "任务": "#8b5cf6", "框架": "#06b6d4",
};

const TYPE_LABELS: Record<string, string> = {
  "ORG": "组织机构", "PERSON": "人物", "MONEY": "金额",
  "DATE": "日期", "PRODUCT": "产品", "METRIC": "指标",
  "PERCENT": "百分比", "CONCEPT": "概念",
};

export function KGTab() {
  const [stats, setStats] = useState<KGStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [building, setBuilding] = useState(false);
  const [showGraph, setShowGraph] = useState(false);
  const [msg, setMsg] = useState("");

  const headers = useCallback(() => {
    const h: Record<string, string> = {};
    const t = localStorage.getItem("hmm_token");
    if (t) h["Authorization"] = `Bearer ${t}`;
    return h;
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/kg/stats", { headers: headers() });
      setStats(await res.json());
    } catch {}
    setLoading(false);
  }, [headers]);

  useEffect(() => { load(); }, [load]);

  async function buildFromChunks() {
    setBuilding(true); setMsg("");
    try {
      const res = await fetch("/api/kg/build-from-chunks", {
        method: "POST", headers: { ...headers(), "Content-Type": "application/json" },
      });
      const data = await res.json();
      if (data.ok) {
        setMsg(`KG 构建完成: ${data.stats?.entities || 0} 实体, ${data.stats?.relations || 0} 关系`);
        load();
      } else {
        setMsg(`${data.error || "构建失败"}`);
      }
    } catch (e) { setMsg(`出错：${(e as Error).message}`); }
    setBuilding(false);
  }

  async function exportGraph() {
    const res = await fetch("/api/kg/export?format=json", { headers: headers() });
    const data = await res.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `kg-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
  }

  if (loading) return <div className="py-12 text-center text-[13px]" style={{ color: "var(--text-tertiary)" }}>加载中...</div>;

  const empty = !stats || stats.entities === 0;

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <div>
          <h3 className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>知识图谱</h3>
          <p className="text-[11px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
            {empty ? "从已索引的文档切片中提取实体和关系" : `${stats!.entities} 实体 · ${stats!.relations} 关系`}
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={load} className="admin-btn"><RefreshCw size={13}/> 刷新</button>
          <button onClick={buildFromChunks} disabled={building} className="admin-btn-primary">
            {building ? <Loader2 size={13} className="animate-spin"/> : <Zap size={13}/>}
            {building ? "构建中..." : empty ? "一键构建" : "重新构建"}
          </button>
          {!empty && (
            <>
              <button onClick={() => setShowGraph(true)} className="admin-btn">
                <Network size={13}/> 可视化
              </button>
              <button onClick={exportGraph} className="admin-btn"><Download size={13}/> 导出</button>
            </>
          )}
        </div>
      </div>

      {msg && (
        <div className="mb-4 px-4 py-2.5 rounded-lg text-[12px]"
          style={{ background: msg.includes("❌") ? "#fef2f2" : "#f0fdf4",
                   color: msg.includes("❌") ? "#ef4444" : "#059669" }}>
          {msg}
        </div>
      )}

      {empty ? (
        <div className="text-center py-16">
          <Network size={44} className="mx-auto mb-3" style={{ color: "var(--text-tertiary)", opacity: 0.4 }} />
          <div className="text-[14px] font-medium" style={{ color: "var(--text-primary)" }}>知识图谱为空</div>
          <div className="text-[12px] mt-2 max-w-[420px] mx-auto leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
            点击上方「一键构建」从已索引的文档切片中自动提取实体和关系。
            <br/>不需要重新解析 PDF，不需要 LLM，通常 3 秒内完成。
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          {/* Stats cards */}
          <div className="grid grid-cols-4 gap-3">
            {[
              { icon: BarChart3, label: "实体", value: stats!.entities, color: "#4f46e5" },
              { icon: Network, label: "关系", value: stats!.relations, color: "#7c3aed" },
              { icon: Users2, label: "社区", value: stats!.communities, color: "#059669" },
              { icon: BarChart3, label: "平均连接度", value: (stats!.avg_degree ?? 0).toFixed(1), color: "#d97706" },
            ].map(card => (
              <div key={card.label} className="p-3.5 rounded-xl" style={{ border: "1px solid var(--border)" }}>
                <div className="flex items-center gap-1.5 mb-1">
                  <card.icon size={13} style={{ color: card.color }}/>
                  <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>{card.label}</span>
                </div>
                <div className="text-[22px] font-bold tabular-nums" style={{ color: "var(--text-primary)" }}>
                  {typeof card.value === "number" ? card.value.toLocaleString() : card.value}
                </div>
              </div>
            ))}
          </div>

          {/* Type distribution */}
          {Object.keys(stats!.type_distribution).length > 0 && (
            <div className="rounded-xl p-4" style={{ border: "1px solid var(--border)" }}>
              <h4 className="text-[12px] font-semibold mb-3" style={{ color: "var(--text-tertiary)" }}>实体类型分布</h4>
              <div className="space-y-2.5">
                {Object.entries(stats!.type_distribution)
                  .sort((a, b) => Number(b[1]) - Number(a[1]))
                  .map(([type, count]) => {
                    const pct = Math.round((Number(count) / stats!.entities) * 100);
                    const label = TYPE_LABELS[type] || type;
                    return (
                      <div key={type} className="flex items-center gap-3">
                        <span className="w-[70px] text-[12px] text-right font-medium" style={{ color: "var(--text-secondary)" }}>
                          {label}
                        </span>
                        <div className="flex-1 h-6 rounded-lg overflow-hidden" style={{ background: "var(--bg-tertiary)" }}>
                          <div className="h-full rounded-lg transition-all flex items-center pl-2"
                               style={{ width: `${Math.max(pct, 3)}%`, background: TYPE_COLORS[type] || "#71717a" }}>
                            {pct > 15 && <span className="text-[10px] text-white font-medium">{pct}%</span>}
                          </div>
                        </div>
                        <span className="w-[50px] text-[12px] font-mono tabular-nums" style={{ color: "var(--text-tertiary)" }}>
                          {count}
                        </span>
                      </div>
                    );
                  })}
              </div>
            </div>
          )}

          {/* Top entities */}
          {stats!.top_entities.length > 0 && (
            <div className="rounded-xl p-4" style={{ border: "1px solid var(--border)" }}>
              <h4 className="text-[12px] font-semibold mb-3" style={{ color: "var(--text-tertiary)" }}>高连接度实体 Top 15</h4>
              <div className="space-y-1">
                {stats!.top_entities.slice(0, 15).map((e, i) => (
                  <div key={i} className="flex items-center gap-2.5 px-2 py-1.5 rounded-lg hover:bg-[var(--bg-tertiary)] transition-colors">
                    <span className="text-[11px] font-mono w-7 text-center py-0.5 rounded"
                          style={{ background: TYPE_COLORS[e.type] || "#71717a", color: "white", opacity: 0.85 }}>
                      {e.degree}
                    </span>
                    <span className="text-[12px] font-medium flex-1" style={{ color: "var(--text-primary)" }}>{e.name}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded"
                          style={{ color: TYPE_COLORS[e.type] || "#71717a", background: "var(--bg-tertiary)" }}>
                      {TYPE_LABELS[e.type] || e.type}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {showGraph && <KGGraphModal onClose={() => setShowGraph(false)} />}
    </div>
  );
}

function KGGraphModal({ onClose }: { onClose: () => void }) {
  const [KGVis, setKGVis] = useState<React.ComponentType<{ onClose: () => void }> | null>(null);
  useEffect(() => {
    import("../KGVisualization").then(mod => setKGVis(() => mod.KGVisualization));
  }, []);

  if (!KGVis) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.5)" }}>
        <Loader2 size={32} className="animate-spin" style={{ color: "var(--accent)" }} />
      </div>
    );
  }
  return <KGVis onClose={onClose} />;
}

"use client";
import { useState } from "react";
import { Bug, ChevronDown, ChevronRight, Zap, Database, Network, Search } from "lucide-react";

interface DebugData {
  mode: string;
  top_k: number;
  result_count: number;
  elapsed_ms: number;
  reranked?: boolean;
  local_count?: number;
  global_count?: number;
  query_analysis?: {
    entities: string[];
    query_type: string;
    needs_multi_hop: boolean;
    explanation: string;
  };
  sources?: Array<{
    method: string;
    chunk_id: string;
    score: number;
    source: string;
  }>;
}

interface Props {
  debug: DebugData;
}

const MODE_LABELS: Record<string, string> = {
  naive: "向量检索",
  local: "KG 局部",
  global: "KG 全局",
  hybrid: "局部+全局",
  mix: "混合融合",
};

const METHOD_ICONS: Record<string, { icon: React.ElementType; color: string; label: string }> = {
  hash: { icon: Zap, color: "#2563eb", label: "哈希向量" },
  kg_local: { icon: Network, color: "#7c3aed", label: "KG 局部" },
  kg_global: { icon: Database, color: "#059669", label: "KG 全局" },
  bm25: { icon: Search, color: "#d97706", label: "关键词" },
  mix: { icon: Zap, color: "#6366f1", label: "融合" },
};

export function RetrievalDebugPanel({ debug }: Props) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="retrieval-debug mt-2">
      <button onClick={() => setExpanded(!expanded)} className="retrieval-debug-toggle">
        <Bug size={12} />
        <span>检索调试</span>
        <span className="retrieval-debug-badge">{MODE_LABELS[debug.mode] || debug.mode}</span>
        <span className="retrieval-debug-meta">{debug.result_count} 条 · {debug.elapsed_ms}ms</span>
        {debug.reranked && <span className="retrieval-debug-tag">Reranked</span>}
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </button>

      {expanded && (
        <div className="retrieval-debug-content">
          {/* Query Analysis */}
          {debug.query_analysis && (
            <div className="retrieval-debug-section">
              <div className="retrieval-debug-section-title">Query 分析</div>
              <div className="retrieval-debug-row">
                <span>类型</span>
                <span className="font-medium">{debug.query_analysis.query_type}</span>
              </div>
              {debug.query_analysis.entities.length > 0 && (
                <div className="retrieval-debug-row">
                  <span>检测实体</span>
                  <span>{debug.query_analysis.entities.join(", ")}</span>
                </div>
              )}
              <div className="retrieval-debug-row">
                <span>Multi-hop</span>
                <span>{debug.query_analysis.needs_multi_hop ? "是" : "否"}</span>
              </div>
            </div>
          )}

          {/* Source breakdown */}
          {debug.sources && debug.sources.length > 0 && (
            <div className="retrieval-debug-section">
              <div className="retrieval-debug-section-title">各路检索结果</div>
              {debug.sources.map((s, i) => {
                const info = METHOD_ICONS[s.method] || METHOD_ICONS["mix"];
                const Icon = info.icon;
                return (
                  <div key={i} className="retrieval-debug-source">
                    <Icon size={12} style={{ color: info.color }} />
                    <span className="retrieval-debug-source-label" style={{ color: info.color }}>{info.label}</span>
                    <span className="retrieval-debug-source-file">{s.source || s.chunk_id}</span>
                    <span className="retrieval-debug-source-score">{s.score.toFixed(3)}</span>
                  </div>
                );
              })}
            </div>
          )}

          {/* Mode details */}
          <div className="retrieval-debug-section">
            <div className="retrieval-debug-section-title">检索配置</div>
            <div className="retrieval-debug-row"><span>模式</span><span>{debug.mode}</span></div>
            <div className="retrieval-debug-row"><span>Top-K</span><span>{debug.top_k}</span></div>
            {debug.local_count !== undefined && (
              <div className="retrieval-debug-row"><span>Local 结果</span><span>{debug.local_count}</span></div>
            )}
            {debug.global_count !== undefined && (
              <div className="retrieval-debug-row"><span>Global 结果</span><span>{debug.global_count}</span></div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

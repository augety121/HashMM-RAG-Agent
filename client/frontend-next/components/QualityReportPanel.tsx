"use client";
import { AlertCircle, CheckCircle, AlertTriangle, Info } from "lucide-react";

interface QualityReport {
  overall_score: number;
  extractable_ratio: number;
  noise_ratio: number;
  structure_score: number;
  encoding_issues: number;
  empty_pages: number[];
  issues: string[];
  suggestions: string[];
}

interface Props {
  report: QualityReport;
  filename: string;
}

export function QualityReportPanel({ report, filename }: Props) {
  const score = report.overall_score;
  const scoreColor = score >= 0.8 ? "#059669" : score >= 0.6 ? "#d97706" : "#ef4444";
  const ScoreIcon = score >= 0.8 ? CheckCircle : score >= 0.6 ? AlertTriangle : AlertCircle;

  return (
    <div className="quality-report">
      {/* Header with overall score */}
      <div className="quality-header">
        <ScoreIcon size={18} style={{ color: scoreColor }} />
        <div className="flex-1">
          <div className="quality-filename">{filename}</div>
          <div className="quality-subtitle">文档质量评估</div>
        </div>
        <div className="quality-score" style={{ color: scoreColor }}>
          {(score * 100).toFixed(0)}分
        </div>
      </div>

      {/* Metrics */}
      <div className="quality-metrics">
        <MetricBar label="提取完整度" value={report.extractable_ratio} />
        <MetricBar label="结构清晰度" value={report.structure_score} />
        <MetricBar label="噪声比例" value={1 - report.noise_ratio} invert />
      </div>

      {/* Issues */}
      {report.issues.length > 0 && (
        <div className="quality-section">
          <div className="quality-section-title">
            <AlertTriangle size={12} /> 发现的问题
          </div>
          {report.issues.map((issue, i) => (
            <div key={i} className="quality-issue">{issue}</div>
          ))}
        </div>
      )}

      {/* Suggestions */}
      {report.suggestions.length > 0 && (
        <div className="quality-section">
          <div className="quality-section-title" style={{ color: "#2563eb" }}>
            <Info size={12} /> 建议
          </div>
          {report.suggestions.map((s, i) => (
            <div key={i} className="quality-suggestion">{s}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function MetricBar({ label, value, invert }: { label: string; value: number; invert?: boolean }) {
  const pct = Math.round(value * 100);
  const color = invert
    ? (value >= 0.8 ? "#059669" : value >= 0.5 ? "#d97706" : "#ef4444")
    : (value >= 0.8 ? "#059669" : value >= 0.6 ? "#d97706" : "#ef4444");

  return (
    <div className="quality-metric">
      <span className="quality-metric-label">{label}</span>
      <div className="quality-metric-bar">
        <div className="quality-metric-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="quality-metric-value">{pct}%</span>
    </div>
  );
}

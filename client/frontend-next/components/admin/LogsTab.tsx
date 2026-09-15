"use client";
import { useState, useEffect, useCallback } from "react";
import type { AuditLog } from "@/lib/types";
import * as api from "@/lib/api";
import { RefreshCw, ChevronLeft, ChevronRight, Download } from "lucide-react";
import { Badge } from "./shared";

export function LogsTab() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [fUser, setFUser] = useState("");
  const [fAction, setFAction] = useState("");
  const PER = 20;

  const load = useCallback(async () => {
    try {
      if (fUser || fAction) {
        const r = await api.queryAuditLogs({ username: fUser, action: fAction, limit: PER, offset: page * PER });
        setLogs(r.logs); setTotal(r.total);
      } else {
        const r = await api.getAuditLogs(PER, page * PER);
        setLogs(r.logs); setTotal(r.total);
      }
    } catch (_e) { /* empty */ }
  }, [page, fUser, fAction]);
  useEffect(() => { load(); }, [load]);

  function exportCsv() {
    const url = api.auditExportUrl({ username: fUser, action: fAction });
    const token = (typeof window !== "undefined") ? window.localStorage.getItem("token") : "";
    // open with token as query param (export endpoint accepts header or ?token=)
    window.open(url + (token ? `&token=${encodeURIComponent(token)}` : ""), "_blank");
  }

  const pages = Math.ceil(total / PER);
  const labels: Record<string, { label: string; color: string }> = {
    login: { label: "登录", color: "#2563eb" }, register: { label: "注册", color: "#059669" },
    chat: { label: "对话", color: "#7c3aed" }, upload: { label: "上传", color: "#d97706" },
    create_user: { label: "创建用户", color: "#059669" }, delete_user: { label: "删除用户", color: "#ef4444" },
    create_model: { label: "添加模型", color: "#059669" }, delete_model: { label: "删除模型", color: "#ef4444" },
    set_default_model: { label: "切换模型", color: "#d97706" }, change_password: { label: "改密码", color: "#7c3aed" },
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h4 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>操作日志 <span className="font-normal text-[11px]" style={{ color: "var(--text-tertiary)" }}>共 {total} 条</span></h4>
        <div className="flex items-center gap-2">
          <input value={fUser} onChange={e => { setPage(0); setFUser(e.target.value); }}
            placeholder="按用户名筛选" className="px-2.5 py-1.5 rounded-lg text-[12px]"
            style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          <input value={fAction} onChange={e => { setPage(0); setFAction(e.target.value); }}
            placeholder="按动作筛选 (如 login)" className="px-2.5 py-1.5 rounded-lg text-[12px]"
            style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          <button onClick={exportCsv} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] text-white"
            style={{ background: "var(--accent)" }}><Download size={13} /> 导出 CSV</button>
          <button onClick={load} className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-tertiary)" }}><RefreshCw size={15} /></button>
        </div>
      </div>
      <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--border)" }}>
        <table className="w-full text-[12px]">
          <thead><tr style={{ background: "var(--bg-secondary)" }}>
            {["时间", "用户", "操作", "详情"].map(h => (<th key={h} className="text-left px-4 py-2.5 font-semibold" style={{ color: "var(--text-secondary)" }}>{h}</th>))}
          </tr></thead>
          <tbody>
            {logs.map(log => {
              const a = labels[log.action] || { label: log.action, color: "#71717a" };
              return (
                <tr key={log.id} className="transition-colors hover:bg-[var(--bg-secondary)]" style={{ borderTop: "1px solid var(--border-light)" }}>
                  <td className="px-4 py-2 font-mono whitespace-nowrap" style={{ color: "var(--text-tertiary)" }}>{new Date(log.ts * 1000).toLocaleString()}</td>
                  <td className="px-4 py-2" style={{ color: "var(--text-primary)" }}>{log.username || "—"}</td>
                  <td className="px-4 py-2"><Badge color={a.color}>{a.label}</Badge></td>
                  <td className="px-4 py-2 max-w-[200px] truncate" style={{ color: "var(--text-tertiary)" }}>{log.detail || "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {logs.length === 0 && <div className="text-center py-12 text-sm" style={{ color: "var(--text-tertiary)" }}>暂无日志</div>}
      </div>
      {pages > 1 && (
        <div className="flex items-center justify-center gap-2 mt-4">
          <button onClick={() => setPage(Math.max(0, page - 1))} disabled={page === 0} className="p-1.5 rounded-lg disabled:opacity-30" style={{ color: "var(--text-secondary)" }}><ChevronLeft size={16} /></button>
          <span className="text-[12px]" style={{ color: "var(--text-tertiary)" }}>{page + 1} / {pages}</span>
          <button onClick={() => setPage(Math.min(pages - 1, page + 1))} disabled={page >= pages - 1} className="p-1.5 rounded-lg disabled:opacity-30" style={{ color: "var(--text-secondary)" }}><ChevronRight size={16} /></button>
        </div>
      )}
    </div>
  );
}

"use client";
/** components/admin/ValidityTab.tsx — 失效区 / 文档时效（消费后端 /api/kb/validity/*）。
 *  按状态筛选（失效/归档/生效中/全部）、设有效期、归档/恢复、扫描过期。变更后端写审计+历史。 */
import React, { useState, useEffect, useCallback } from "react";
import { RefreshCw, Loader2, Archive, RotateCcw, CalendarClock, RotateCw, Save, X } from "lucide-react";
import { useToast } from "../Toast";

interface DocVal {
  filename: string; doc_id?: string; status?: string;
  effective_date?: number | null; expiry_date?: number | null; note?: string;
}

const FILTERS: { key: string; label: string }[] = [
  { key: "expired", label: "已失效" }, { key: "archived", label: "已归档" },
  { key: "active", label: "生效中" }, { key: "", label: "全部" },
];

function toDateInput(epochSec?: number | null): string {
  if (!epochSec) return "";
  const d = new Date(epochSec * 1000);
  if (isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function statusMeta(s?: string): [string, string] {
  switch (s) {
    case "expired": return ["已失效", "#ef4444"];
    case "archived": return ["已归档", "#8a8a8e"];
    case "pending": return ["待生效", "#d97706"];
    default: return ["生效中", "#059669"];
  }
}

function daysLeft(expiry?: number | null): string {
  if (!expiry) return "到期 不限";
  const days = Math.round((expiry - Date.now() / 1000) / 86400);
  if (days < 0) return `已过期 ${-days} 天`;
  return `剩 ${days} 天`;
}

export function ValidityTab() {
  const [items, setItems] = useState<DocVal[]>([]);
  const [filter, setFilter] = useState("expired");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [eff, setEff] = useState("");
  const [exp, setExp] = useState("");
  const [note, setNote] = useState("");
  const { toast } = useToast();

  const hdr = useCallback(() => {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    const t = localStorage.getItem("hmm_token");
    if (t) h["Authorization"] = `Bearer ${t}`;
    return h;
  }, []);

  const load = useCallback(async (f = filter) => {
    setLoading(true);
    try {
      const q = f ? `?status=${f}` : "";
      const r = await (await fetch(`/api/kb/validity/list${q}`, { headers: hdr() })).json();
      setItems(r.items || []);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [filter, hdr]);

  useEffect(() => { load("expired"); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const switchFilter = (f: string) => { setFilter(f); setEditing(null); load(f); };

  const openEditor = (d: DocVal) => {
    setEditing(d.filename);
    setEff(toDateInput(d.effective_date));
    setExp(toDateInput(d.expiry_date));
    setNote(d.note || "");
  };

  const post = async (path: string, body: Record<string, unknown>) => {
    const r = await (await fetch(path, { method: "POST", headers: hdr(), body: JSON.stringify(body) })).json();
    return r;
  };

  const saveValidity = async (filename: string) => {
    if (busy) return;
    setBusy(true);
    try {
      const body: Record<string, unknown> = { filename };
      if (eff) body.effective_date = eff;
      if (exp) body.expiry_date = exp;
      if (note) body.note = note;
      const r = await post("/api/kb/validity/set", body);
      if (r.error) toast(r.error, "error");
      else { toast(`已设置：${r.status || "active"}`, "success"); setEditing(null); load(); }
    } catch { toast("保存失败（需管理员或检查网络）", "error"); }
    finally { setBusy(false); }
  };

  const doAction = async (path: string, filename: string, okMsg: string) => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await post(path, { filename });
      if (r.ok) { toast(okMsg, "success"); load(); }
      else toast("操作失败（需管理员）", "error");
    } catch { toast("操作失败", "error"); }
    finally { setBusy(false); }
  };

  const sweep = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await post("/api/kb/validity/sweep", {});
      toast(`扫描完成，新增失效 ${r.count ?? 0} 篇`, "success");
      load();
    } catch { toast("扫描失败", "error"); }
    finally { setBusy(false); }
  };

  return (
    <div>
      {/* 工具条 */}
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div className="flex items-center gap-1.5">
          {FILTERS.map(f => {
            const active = filter === f.key;
            return (
              <button key={f.key} onClick={() => switchFilter(f.key)}
                className="px-3 py-1.5 rounded-lg text-[12.5px] transition-colors"
                style={active
                  ? { background: "var(--accent-light)", color: "var(--accent)", fontWeight: 600 }
                  : { background: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>
                {f.label}
              </button>
            );
          })}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => load()} disabled={loading}
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[12.5px] hover:bg-[var(--bg-tertiary)]"
            style={{ color: "var(--text-secondary)" }}>
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />刷新
          </button>
          <button onClick={sweep} disabled={busy}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12.5px] font-medium"
            style={{ background: "var(--accent-light)", color: "var(--accent)" }}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : <RotateCw size={14} />}扫描过期
          </button>
        </div>
      </div>

      {/* 列表 */}
      {loading ? (
        <div className="flex items-center justify-center py-16" style={{ color: "var(--text-tertiary)" }}>
          <Loader2 className="animate-spin" size={20} />
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-16">
          <CalendarClock size={28} style={{ color: "var(--text-tertiary)", margin: "0 auto 10px" }} />
          <div className="text-[14px] font-medium" style={{ color: "var(--text-primary)" }}>这里什么都没有</div>
          <div className="text-[12px] mt-1.5" style={{ color: "var(--text-tertiary)" }}>
            在「文档管理」给文档设到期日，过期后会自动出现在这里，也可点「扫描过期」。
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map(d => {
            const [label, color] = statusMeta(d.status);
            const isEditing = editing === d.filename;
            return (
              <div key={d.filename} className="rounded-xl p-3.5"
                style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                <div className="flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="text-[13.5px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{d.filename}</div>
                    <div className="text-[11.5px] mt-1" style={{ color: "var(--text-tertiary)" }}>
                      生效 {toDateInput(d.effective_date) || "—"} · 到期 {toDateInput(d.expiry_date) || "不限"} · {daysLeft(d.expiry_date)}
                      {d.note ? ` · ${d.note}` : ""}
                    </div>
                  </div>
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium flex-shrink-0"
                    style={{ color, background: `${color}18` }}>
                    <span className="w-[6px] h-[6px] rounded-full" style={{ background: color }} />{label}
                  </span>
                  <div className="flex items-center gap-1.5 flex-shrink-0">
                    <button onClick={() => (isEditing ? setEditing(null) : openEditor(d))} title="设有效期"
                      className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-secondary)" }}>
                      <CalendarClock size={15} />
                    </button>
                    {d.status === "archived" ? (
                      <button onClick={() => doAction("/api/kb/validity/restore", d.filename, `已恢复「${d.filename}」`)} title="恢复"
                        className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-secondary)" }}>
                        <RotateCcw size={15} />
                      </button>
                    ) : (
                      <button onClick={() => doAction("/api/kb/validity/archive", d.filename, `已归档「${d.filename}」`)} title="归档"
                        className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-secondary)" }}>
                        <Archive size={15} />
                      </button>
                    )}
                  </div>
                </div>

                {isEditing && (
                  <div className="mt-3 pt-3 flex items-end gap-2 flex-wrap" style={{ borderTop: "1px solid var(--border)" }}>
                    <label className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                      生效日期
                      <input type="date" value={eff} onChange={e => setEff(e.target.value)}
                        className="block mt-1 px-2 py-1.5 rounded-lg text-[12.5px] outline-none"
                        style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)" }} />
                    </label>
                    <label className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                      到期日期（空=不限）
                      <input type="date" value={exp} onChange={e => setExp(e.target.value)}
                        className="block mt-1 px-2 py-1.5 rounded-lg text-[12.5px] outline-none"
                        style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)" }} />
                    </label>
                    <input value={note} onChange={e => setNote(e.target.value)} placeholder="备注（可空）"
                      className="flex-1 min-w-[120px] px-2.5 py-1.5 rounded-lg text-[12.5px] outline-none"
                      style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)" }} />
                    <button onClick={() => saveValidity(d.filename)} disabled={busy}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12.5px] font-medium"
                      style={{ background: "var(--accent)", color: "#fff" }}>
                      {busy ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}保存
                    </button>
                    <button onClick={() => setEditing(null)} className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-tertiary)" }}>
                      <X size={15} />
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

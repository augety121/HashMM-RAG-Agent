"use client";
/** V231 站内通知铃铛（全局，与隐形拖拽条同级挂载）：
 *  30s 轮询未读；红点角标；下拉最近 10 条 + 全部已读。未登录/老后端静默隐身。 */
import { useEffect, useRef, useState } from "react";
import { Bell } from "lucide-react";
import { listNotifications, readAllNotifications, withToken } from "@/lib/api";

type Item = { id: string; type: string; text: string; by: string; ts: number; read: boolean; share_id?: string };

export function NotificationBell({ inline = false }: { inline?: boolean } = {}) {
  const [items, setItems] = useState<Item[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [ok, setOk] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let dead = false;
    const pull = () => listNotifications()
      .then(r => { if (dead) return; setItems(r.items || []); setUnread(r.unread || 0); setOk(true); })
      .catch(() => { if (!dead) setOk(false); });
    pull();
    const t = setInterval(pull, 30000);
    return () => { dead = true; clearInterval(t); };
  }, []);

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);

  if (!ok) return null;   // 未登录/老后端：整体隐身，不打扰
  const ago = (ts: number) => { const s = Math.max(0, Date.now() / 1000 - ts); return s < 60 ? "刚刚" : s < 3600 ? `${Math.floor(s / 60)}分钟前` : s < 86400 ? `${Math.floor(s / 3600)}小时前` : `${Math.floor(s / 86400)}天前`; };

  return (
    <div ref={boxRef}
      style={inline
        ? ({ position: "relative", WebkitAppRegion: "no-drag" } as any)
        : ({ position: "fixed", top: 10,
            right: "calc(max(146px, calc(100vw - env(titlebar-area-x, 0px) - env(titlebar-area-width, 100vw))) + 8px)",
            zIndex: 9998, WebkitAppRegion: "no-drag" } as any)}>
      <button onClick={() => setOpen(v => !v)} aria-label="通知"
        className="relative p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]"
        style={{ color: unread > 0 ? "var(--accent)" : "var(--text-tertiary)" }}
        title={unread > 0 ? `${unread} 条新通知（@提及 / 画布评论）` : "通知"}>
        <Bell size={15} />
        {unread > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[14px] h-[14px] px-0.5 rounded-full text-[9px] font-bold flex items-center justify-center"
            style={{ background: "var(--error, #ef4444)", color: "#fff" }}>{unread > 9 ? "9+" : unread}</span>
        )}
      </button>
      {open && (
        <div className="absolute top-full right-0 mt-1.5 w-[300px] max-h-[380px] overflow-auto rounded-xl py-1 z-20"
          style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
          <div className="px-3 py-1.5 flex items-center">
            <span className="text-[11px] font-semibold flex-1" style={{ color: "var(--text-secondary)" }}>通知</span>
            {unread > 0 && (
              <button onClick={() => { readAllNotifications().catch(() => { /* */ }); setUnread(0); setItems(x => x.map(i => ({ ...i, read: true }))); }}
                className="text-[10px] hover:underline" style={{ color: "var(--accent)" }}>全部已读</button>
            )}
          </div>
          {items.length === 0 ? (
            <div className="px-3 py-4 text-[11px]" style={{ color: "var(--text-tertiary)" }}>还没有通知——被 @ 或画布有新评论时会出现在这里</div>
          ) : items.slice(0, 10).map(n => (
            <div key={n.id}
              onClick={() => { if (n.share_id) { try { window.open(withToken(`/canvas/${n.share_id}`), "_blank"); } catch { /* */ } setOpen(false); } }}
              className={"px-3 py-2 border-t" + (n.share_id ? " cursor-pointer hover:bg-[var(--bg-secondary)] transition-colors" : "")}
              style={{ borderColor: "var(--border)", opacity: n.read ? 0.62 : 1 }}
              title={n.share_id ? "点击打开对应画布 Viewer" : undefined}>
              <div className="text-[11.5px] leading-snug" style={{ color: "var(--text-primary)" }}>{n.text}</div>
              <div className="text-[9.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
                {n.type === "mention" ? "@提及" : n.type === "reply" ? "回复了你" : "画布评论"} · {ago(n.ts)}{n.share_id ? " · 点击直达 ›" : ""}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

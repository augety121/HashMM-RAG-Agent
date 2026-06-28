"use client";
import { useEffect, useRef, useState } from "react";
import type { Message } from "@/lib/types";
import { useStore } from "@/lib/store";
import { openArtifact } from "@/lib/artifact";
import { TodoCard } from "./TodoCard";
import { withToken } from "@/lib/api";
import { renderMsg, doKatex, relativeTime } from "@/lib/render";
import { MessageRenderer } from "./MessageRenderer";
import { SubAgentPanel } from "./SubAgentPanel";
import { ThinkingPanel } from "./ThinkingPanel";
import { Copy, Check, ChevronDown, FileText, Image, FileCode, File as FileIcon,
         RefreshCw, ThumbsUp, ThumbsDown, Pencil, Download, Eye, X, AlertTriangle } from "lucide-react";
import { AgentLog } from "./AgentLog";
import { QualityBadgesRow } from "./QualityBadges";
import { FilePreview } from "./FilePreview";
import { FileCard } from "./FileCard";

function fileIconFor(name: string) {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["pdf"].includes(ext)) return { Icon: FileText, color: "#ef4444" };
  if (["png","jpg","jpeg","gif","webp","svg"].includes(ext)) return { Icon: Image, color: "#8b5cf6" };
  if (["py","js","ts","java","cpp","c","h","go","rs","rb"].includes(ext)) return { Icon: FileCode, color: "#059669" };
  if (["pptx","ppt"].includes(ext)) return { Icon: FileText, color: "#f97316" };
  if (["docx","doc"].includes(ext)) return { Icon: FileText, color: "#2563eb" };
  if (["xlsx","xls","csv"].includes(ext)) return { Icon: FileText, color: "#16a34a" };
  if (["md","txt"].includes(ext)) return { Icon: FileText, color: "#6b7280" };
  return { Icon: FileIcon, color: "#71717a" };
}

// V103.27: 去掉回复里的 [[ASK]]...[[/ASK]] 澄清块（澄清以可点 chips 形式单独渲染，正文不显示标记）
function stripClarify(s: string): string {
  return (s || "").replace(/\[\[ASK\]\][\s\S]*?\[\[\/ASK\]\]/g, "").trimEnd();
}

function UserBubble({ msg, onEdit }: { msg: Message; onEdit?: () => void }) {
  const lines = msg.content.split("\n");
  const textLines: string[] = [], fileLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith("📎 ")) fileLines.push(line.slice(2).trim());   // 旧消息兼容（V86 起改为结构化 files，不再写进正文）
    else textLines.push(line);
  }
  const text = textLines.join("\n").trim();
  // V86: 结构化附件——截屏/图片直接显示缩略图（刷新后仍在），非图片显示文件 chip
  const isImgName = (n: string) => ["png","jpg","jpeg","gif","webp","bmp","svg"].includes((n.split(".").pop() || "").toLowerCase());
  const atts = msg.files || [];
  const imgAtts = atts.filter(f => isImgName(f.filename || ""));
  const chipNames = [...atts.filter(f => !isImgName(f.filename || "")).map(f => f.filename), ...fileLines];
  return (
    <div className="flex justify-end mb-5 anim-fade-up group">
      <div className="max-w-[80%]">
        <div className="relative">
          {text && (
            <div className="px-4 py-2.5 rounded-2xl rounded-br-md text-[14px] leading-relaxed text-white whitespace-pre-wrap" style={{ background: "var(--accent)" }}>{text}</div>
          )}
          {onEdit && text && (
            <button onClick={onEdit}
              className="absolute -left-8 top-1/2 -translate-y-1/2 p-1 rounded-md opacity-0 group-hover:opacity-100 transition-opacity hover:bg-[var(--bg-tertiary)]"
              title="编辑消息">
              <Pencil size={13} style={{ color: "var(--text-tertiary)" }} />
            </button>
          )}
        </div>
        {imgAtts.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-1.5 justify-end">
            {imgAtts.map((f, i) => (
              <img key={i} src={withToken(f.download_url)} alt={f.filename} title={f.filename}
                className="rounded-xl"
                style={{ maxHeight: 180, maxWidth: 260, objectFit: "contain",
                         border: "1px solid var(--border)", background: "var(--bg-secondary)" }} />
            ))}
          </div>
        )}
        {chipNames.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-1.5 justify-end">
            {chipNames.map((name, i) => {
              const { Icon, color } = fileIconFor(name);
              return <div key={i} className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px]" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                <Icon size={13} style={{ color }} /><span className="truncate max-w-[150px] font-medium" style={{ color: "var(--text-primary)" }}>{name}</span>
              </div>;
            })}
          </div>
        )}
      </div>
    </div>
  );
}

interface Props { msg: Message; index?: number; convId?: string; showRegenerate?: boolean; onRegenerate?: () => void; onEdit?: () => void; onSuggestion?: (text: string) => void; }

export function MsgBubble({ msg, index, convId, showRegenerate, onRegenerate, onEdit, onSuggestion }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState(msg.content);
  const [srcOpen, setSrcOpen] = useState<number>(-1);
  const [showLog, setShowLog] = useState(false);
  const [previewFile, setPreviewFile] = useState<{ filename: string; download_url: string } | null>(null);
  const sbOpen = useStore(s => s.sbOpen);
  const sid = useStore(s => s.sid);
  const updateMsg = useStore(s => s.updateMsg);

  // Citation hover tooltip state
  const [citeTip, setCiteTip] = useState<{x: number; y: number; src: any} | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const run = () => {
      if (!ref.current) return;
      doKatex(ref.current);
      if (msg.sources?.length) {
        ref.current.querySelectorAll(".cite-num").forEach(el => {
          const n = parseInt(el.getAttribute("data-n") || "0");
          const src = msg.sources?.[n - 1];
          if (!src) return;
          // Rich hover tooltip
          const htmlEl = el as HTMLElement;
          htmlEl.style.cursor = "pointer";
          htmlEl.onmouseenter = (e) => {
            const rect = htmlEl.getBoundingClientRect();
            setCiteTip({ x: rect.left, y: rect.bottom + 4, src: { ...src, idx: n } });
          };
          htmlEl.onmouseleave = () => {
            setTimeout(() => setCiteTip(prev => prev?.src?.idx === n ? null : prev), 200);
          };
        });
      }
    };
    run(); const t1 = setTimeout(run, 500); const t2 = setTimeout(run, 2000);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [msg.content, sbOpen]);

  // v23: Stable code block enhancement via data attributes
  useEffect(() => {
    if (msg.role !== "assistant" || !ref.current) return;
    const blocks = ref.current.querySelectorAll('.cb-wrap[data-enhanced="false"]') as NodeListOf<HTMLElement>;
    blocks.forEach((block) => {
      block.setAttribute('data-enhanced', 'true');
      const head = block.querySelector('.cb-head') as HTMLElement | null;
      const codeEl = block.querySelector('code') as HTMLElement | null;
      if (!head || !codeEl) return;

      const lang = block.getAttribute('data-lang') || '';
      const fname = block.getAttribute('data-filename') || '';
      const isPython = lang === 'python' || fname.endsWith('.py');

      // Extract convId from file URLs in this message
      const fileUrl = msg.files?.[0]?.download_url || '';
      const cidMatch = fileUrl.match(/\/conversations\/([^/]+)/);
      const cid = cidMatch?.[1] || '';

      if (isPython && cid) {
        const btn = document.createElement('button');
        btn.className = 'cb-btn';
        btn.title = '运行代码';
        btn.innerHTML = '<span style="color:#22c55e;font-size:13px">▶</span>';
        btn.onclick = async () => {
          btn.innerHTML = '<span style="color:#f59e0b">…</span>';
          try {
            const r = await fetch('/api/conversations/' + cid + '/execute', {
              method: 'POST', headers: {'Content-Type':'application/json'},
              body: JSON.stringify({code: codeEl.textContent || ''})
            });
            const d = await r.json();
            let out = block.querySelector('.code-output-content') as HTMLElement | null;
            if (!out) {
              const wrap = document.createElement('div');
              wrap.innerHTML = '<div class="code-output-header">▶ 输出</div><pre class="code-output-content"></pre>';
              block.appendChild(wrap.firstElementChild as HTMLElement);
              block.appendChild(wrap.lastElementChild as HTMLElement);
              out = block.querySelector('.code-output-content') as HTMLElement;
            }
            if (out) out.textContent = d.output || d.error || '(无输出)';
            btn.innerHTML = '<span style="color:#22c55e">▶</span>';
          } catch (_e) { btn.innerHTML = '<span style="color:#ef4444">✗</span>'; }
        };
        head.appendChild(btn);
      }
    });
  }, [msg.content]);

  // v25: Render artifact placeholders (HTML → iframe)
  useEffect(() => {
    if (!ref.current) return;
    const placeholders = ref.current.querySelectorAll('.artifact-placeholder[data-type="html"]') as NodeListOf<HTMLElement>;
    placeholders.forEach(ph => {
      if (ph.getAttribute('data-rendered')) return;
      ph.setAttribute('data-rendered', 'true');
      const encoded = ph.getAttribute('data-code') || '';
      try {
        const html = decodeURIComponent(escape(atob(encoded)));
        const iframe = document.createElement('iframe');
        iframe.sandbox.add('allow-scripts');
        iframe.style.cssText = 'width:100%;height:300px;border:none;background:white;border-radius:0 0 8px 8px;';
        // Remove loading text
        const loadDiv = ph.querySelector('div:last-child');
        if (loadDiv) loadDiv.remove();
        ph.appendChild(iframe);
        const doc = iframe.contentDocument;
        if (doc) { doc.open(); doc.write(html); doc.close(); }
      } catch (_e) {}
    });
  }, [msg.content]);

  function copyContent() {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(msg.content).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); });
    } else {
      const ta = document.createElement("textarea"); ta.value = msg.content;
      ta.style.position = "fixed"; ta.style.left = "-9999px";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); setCopied(true); setTimeout(() => setCopied(false), 2000); } catch {} document.body.removeChild(ta);
    }
  }

  function setFeedback(fb: "up" | "down") {
    if (sid && index !== undefined) {
      const current = msg.feedback === fb ? null : fb;
      updateMsg(sid, index, { feedback: current });
      // v10.0: Send feedback to server (triggers skill creation + user model update)
      if (current && convId) {
        const session = useStore.getState().sessions.find(s => s.id === convId);
        const userMsgs = session?.messages.filter(m => m.role === "user") || [];
        const lastQuery = userMsgs[userMsgs.length - 1]?.content || "";
        const token = useStore.getState().token;
        const headers: Record<string, string> = { "Content-Type": "application/json" };
        if (token) headers["Authorization"] = `Bearer ${token}`;
        fetch(`/api/conversations/${convId}/messages/${index}/feedback`, {
          method: "POST", headers,
          body: JSON.stringify({ feedback: current, message_content: msg.content?.slice(0, 500) || "", query: lastQuery.slice(0, 200) }),
        }).catch(() => {});
      }
    }
  }

  if (msg.role === "user") return <UserBubble msg={msg} onEdit={onEdit} />;

  const hasSources = msg.sources && msg.sources.length > 0;

  return (
    <div className="mb-6 anim-fade-up flex gap-3 group/msg">
      {/* Assistant avatar */}
      <div className="w-5 h-5 rounded-md flex items-center justify-center text-white text-[9px] font-bold flex-shrink-0 mt-1"
        style={{ background: "linear-gradient(135deg, #2563eb, #7c3aed)" }}>H</div>

      <div className="flex-1 min-w-0 pl-3" style={{ borderLeft: "2px solid var(--accent-light, rgba(37,99,235,0.15))" }}>
        {/* v29: Thinking panel */}
        {msg.thinking && (
          <ThinkingPanel content={msg.thinking} />
        )}

        {/* V50: 任务清单（最终状态随消息持久化） */}
        {msg.todo && msg.todo.length > 0 && <TodoCard items={msg.todo} />}

        {/* v12: Unified Agent Execution Log — 优先用有序 timeline（思考/工具交错，对标 Claude），
            旧消息无 timeline 时回退到 trace+steps 拼接 */}
        {(() => {
          const tl = (msg as { timeline?: Array<{ kind: string; node: string; detail: string; tool?: string; status: string; elapsed_ms?: number; id: string }> }).timeline;
          const hasTimeline = Array.isArray(tl) && tl.length > 0;
          const hasLegacy = (msg.steps && msg.steps.length > 0) || (msg.trace && msg.trace.length > 0);
          if (!hasTimeline && !hasLegacy) return null;
          const steps = hasTimeline
            ? tl!.map((e, i) => ({ id: e.id || `tl-${i}`, node: e.node, detail: e.detail, tool: e.tool,
                                   status: (e.status as "done" | "running" | "error") || "done", elapsed_ms: e.elapsed_ms }))
            : [
                ...(msg.trace || []).map((t: { node: string; detail: string }, i: number) => ({
                  id: `trace-${i}`, node: t.node, detail: t.detail, status: "done" as const,
                })),
                ...(msg.steps || []).map((s: { tool: string; detail?: string; status?: string; duration_ms?: number }, i: number) => ({
                  id: `step-${i}`, node: "tool", tool: s.tool, detail: s.detail || "",
                  status: (s.status as "done" | "running" | "error") || "done", elapsed_ms: s.duration_ms,
                })),
              ];
          return (
            <>
              <AgentLog steps={steps} totalElapsed={msg.elapsed_ms} visible={showLog} onToggle={() => setShowLog(v => !v)} />
              {/* V86: 质量徽章常显在折叠条外——dod/citation/verify 等信号后端一直在发，
                  此前实现埋在从未挂载的 AgentRunTimeline 里，用户根本看不见。点徽章展开完整轨迹。 */}
              <QualityBadgesRow steps={steps} onBadgeClick={() => setShowLog(true)} />
            </>
          );
        })()}

        {/* v10: Message status banner */}
        {msg.content?.includes("*回答生成中断") && (
          <div className="mb-2 px-3 py-2 rounded-lg text-[12px] flex items-center gap-2" style={{ background: "var(--warning-light)", color: "var(--warning)", border: "1px solid var(--warning)" }}>
            <AlertTriangle size={13} style={{ flexShrink: 0 }} /> 此回答可能不完整（生成过程中断）
          </div>
        )}

        {msg.orchestration && <SubAgentPanel orch={msg.orchestration} />}
        <div ref={ref} className="text-[14px] leading-[1.85] msg-content" style={{ color: "var(--text-primary)" }}>
          <MessageRenderer content={stripClarify(msg.content)}
            convId={convId || msg.files?.[0]?.download_url?.match(/conversations\/([^/]+)/)?.[1] || undefined} />
        </div>

        {/* v12: Inline chart images extracted from code execution output */}
        {msg.content && (() => {
          const chartUrls = msg.content.match(/CHART_FILE:(\/api\/files\/download\/[^\s\n]+)/g);
          if (!chartUrls || chartUrls.length === 0) return null;
          return (
            <div className="mt-2 space-y-2">
              {chartUrls.map((match, i) => {
                const url = match.replace("CHART_FILE:", "");
                return (
                  <div key={i} className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--border)" }}>
                    <img src={url} alt={`chart-${i}`} className="w-full" style={{ maxHeight: 400, objectFit: "contain" }} />
                    <div className="flex items-center justify-between px-3 py-1.5" style={{ background: "var(--bg-secondary)" }}>
                      <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>代码生成图表</span>
                      <a href={url} download className="text-[10px] font-medium" style={{ color: "var(--accent)" }}>下载</a>
                    </div>
                  </div>
                );
              })}
            </div>
          );
        })()}

        {hasSources && (
          <div className="mt-3">
            {/* v10.0: Compact source pills (Claude-style) */}
            <div className="flex flex-wrap gap-1.5">
              {msg.sources!.map((s, i) => {
                const label = s.filename || s.modality || "文档";
                const page = s.page && s.page > 0 ? ` p.${s.page}` : "";
                return (
                  <button key={i}
                    onClick={() => setSrcOpen(srcOpen === i ? -1 : i)}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] transition-all hover:shadow-sm"
                    style={{
                      background: srcOpen === i ? "var(--accent-light)" : "var(--bg-secondary)",
                      border: `1px solid ${srcOpen === i ? "var(--accent)" : "var(--border)"}`,
                      color: srcOpen === i ? "var(--accent)" : "var(--text-secondary)",
                    }}>
                    <FileText size={11} />
                    <span className="truncate max-w-[140px] font-medium">{label}{page}</span>
                    {typeof s.score === "number" && s.score > 0 && (
                      <span className="font-mono text-[9px] opacity-60">{s.score < 1 ? s.score.toFixed(2) : s.score.toFixed(1)}</span>
                    )}
                  </button>
                );
              })}
            </div>
            {/* Expanded source detail */}
            {srcOpen >= 0 && srcOpen < (msg.sources?.length || 0) && (() => {
              const s = msg.sources![srcOpen];
              return (
                <div className="mt-2 p-3 rounded-lg text-[11px] leading-relaxed anim-fade-up"
                  style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="font-semibold text-[12px]" style={{ color: "var(--text-primary)" }}>
                      [{s.id ?? srcOpen + 1}] {s.filename || "文档"}{s.page && s.page > 0 ? ` · 第${s.page}页` : ""}
                    </span>
                    <button onClick={() => setSrcOpen(-1)} className="p-0.5 rounded hover:bg-[var(--bg-tertiary)]">
                      <X size={12} style={{ color: "var(--text-tertiary)" }} />
                    </button>
                  </div>
                  <p style={{ color: "var(--text-secondary)" }}>{s.text?.slice(0, 350)}</p>
                </div>
              );
            })()}
          </div>
        )}

        {/* Citation hover tooltip */}
        {citeTip && (
          <div className="fixed z-50 max-w-sm rounded-xl shadow-lg p-3 text-[12px]"
               style={{
                 left: Math.min(citeTip.x, window.innerWidth - 360),
                 top: citeTip.y,
                 background: "var(--bg-primary)",
                 border: "1px solid var(--border)",
                 boxShadow: "0 8px 30px rgba(0,0,0,0.12)",
               }}
               onMouseEnter={() => {/* keep open */}}
               onMouseLeave={() => setCiteTip(null)}>
            <div className="flex items-center justify-between mb-1.5">
              <span className="font-semibold" style={{ color: "var(--accent)" }}>
                [{citeTip.src.idx}] {citeTip.src.filename || "来源"}
              </span>
              {citeTip.src.page > 0 && (
                <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>
                  p.{citeTip.src.page}
                </span>
              )}
            </div>
            <p className="leading-relaxed" style={{ color: "var(--text-secondary)" }}>
              {citeTip.src.text?.slice(0, 200) || ""}
            </p>
            {typeof citeTip.src.score === "number" && (
              <div className="mt-1.5 text-[10px]" style={{ color: "var(--text-tertiary)" }}>
                相关度 {citeTip.src.score > 1 ? citeTip.src.score.toFixed(1) : citeTip.src.score.toFixed(3)}
              </div>
            )}
          </div>
        )}

        {/* File downloads + inline images */}
        {msg.files && msg.files.length > 0 && (
          <div className="mt-3 space-y-2">
            {msg.files.map((f, i) => {
              const ext = (f.filename || "").split(".").pop()?.toLowerCase() || "";
              const isImage = ["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext);
              const previewable = !["zip","gz","tar","pptx","docx","xlsx","pdf"].includes(ext) && !isImage;
              const { Icon, color } = fileIconFor(ext);

              if (isImage) {
                return (
                  <div key={i} className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--border)" }}>
                    <img src={withToken(f.download_url)} alt={f.filename} className="w-full" style={{ maxHeight: 400, objectFit: "contain", background: "var(--bg-secondary)" }} />
                    <div className="flex items-center justify-between px-3 py-2" style={{ background: "var(--bg-secondary)", borderTop: "1px solid var(--border)" }}>
                      <span className="text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>{f.filename}</span>
                      <a href={withToken(f.download_url)} download className="text-[11px] font-medium flex items-center gap-1" style={{ color: "var(--accent)" }}>
                        <Download size={11} /> 下载
                      </a>
                    </div>
                  </div>
                );
              }

              return (
                <div key={i} className="flex items-center gap-3 px-4 py-3 rounded-xl transition-all hover:shadow-sm"
                  style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                  <div className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
                    style={{ background: color + "15" }}>
                    <Icon size={18} style={{ color }} />
                  </div>
                  <div className="flex-1 min-w-0 cursor-pointer"
                    onClick={() => openArtifact(useStore.getState().sid, f)}>
                    <div className="text-[13px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{f.filename}</div>
                    <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
                      {ext.toUpperCase()} 文件
                      {f.mtime && ` · ${new Date(f.mtime * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}`}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {previewable && (
                      <button onClick={() => openArtifact(useStore.getState().sid, f)}
                        className="p-2 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" title="在右侧查看">
                        <Eye size={15} style={{ color: "var(--text-tertiary)" }} />
                      </button>
                    )}
                    <a href={withToken(f.download_url)} download
                      className="p-2 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" title="下载">
                      <Download size={15} style={{ color: "var(--accent)" }} />
                    </a>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* File Preview Modal */}
        {previewFile && (
          <FilePreview
            filename={previewFile.filename}
            downloadUrl={previewFile.download_url}
            onClose={() => setPreviewFile(null)}
          />
        )}

        {/* V103.27: 主动澄清 — 可点选项（点击即作为回答发送，复用 onSuggestion 发送管线） */}
        {msg.clarify && msg.clarify.options && msg.clarify.options.length > 0 && onSuggestion && (
          <div className="mt-3 rounded-xl p-3" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
            <div className="text-[12.5px] mb-2 font-medium" style={{ color: "var(--text-primary)" }}>{msg.clarify.question}</div>
            <div className="flex flex-wrap gap-1.5">
              {msg.clarify.options.map((o, i) => (
                <button key={i} onClick={() => onSuggestion(o)}
                  className="suggestion-btn px-3 py-1.5 rounded-full text-[11px]"
                  style={{ background: "var(--bg-primary)", border: "1px solid var(--accent)", color: "var(--accent)" }}>
                  {o}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Follow-up suggestions */}
        {msg.suggestions && msg.suggestions.length > 0 && onSuggestion && (
          <div className="flex flex-wrap gap-1.5 mt-3">
            {msg.suggestions.map((s, i) => (
              <button key={i} onClick={() => onSuggestion(s)}
                className="suggestion-btn px-3 py-1.5 rounded-full text-[11px]"
                style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                {s}
              </button>
            ))}
          </div>
        )}

        {/* Footer: time + quality + copy + feedback — shows on hover */}
        <div className="flex items-center gap-1.5 mt-2 opacity-0 group-hover/msg:opacity-100 transition-opacity duration-200">
          <span className="text-[10px] mr-1" style={{ color: "var(--text-tertiary)" }}>{relativeTime(msg.ts)}</span>
          {msg.elapsed_ms != null && msg.elapsed_ms > 0 && (
            <span className="text-[10px] font-mono mr-1" style={{ color: "var(--text-tertiary)" }}>
              {msg.elapsed_ms < 1000 ? `${msg.elapsed_ms}ms` : `${(msg.elapsed_ms / 1000).toFixed(1)}s`}
            </span>
          )}
          {msg.tokens && <span className="text-[10px] mr-1 font-mono" style={{ color: "var(--text-tertiary)" }}>{msg.tokens.input}→{msg.tokens.output}t</span>}
          {msg.sources && msg.sources.length > 0 && (
            <span className="inline-flex items-center gap-0.5 text-[10px] mr-1" style={{ color: "var(--text-tertiary)" }}><FileText size={10} />{msg.sources.length}源</span>
          )}
          <button onClick={copyContent} className="flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] transition-all hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-tertiary)" }}>
            {copied ? <><Check size={11} className="text-green-500" /> 已复制</> : <><Copy size={11} /> 复制</>}
          </button>
          {/* Feedback */}
          <button onClick={() => setFeedback("up")} className={`fb-btn ${msg.feedback === "up" ? "active-up" : ""}`} title="有帮助">
            <ThumbsUp size={13} />
          </button>
          <button onClick={() => setFeedback("down")} className={`fb-btn ${msg.feedback === "down" ? "active-down" : ""}`} title="没帮助">
            <ThumbsDown size={13} />
          </button>
          {showRegenerate && onRegenerate && (
            <>
              <button onClick={onRegenerate} className="flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] transition-all hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-tertiary)" }}>
                <RefreshCw size={11} /> 重新生成
              </button>
              <button onClick={() => {
                const cid = window.location.pathname.match(/\/chat\/([^/]+)/)?.[1] || "";
                if (cid && msg.id) {
                  fetch(`/api/conversations/${cid}/messages/${msg.id}/regenerate`, { method: "POST" })
                    .then(() => window.location.reload());
                }
              }} className="flex items-center gap-1 text-[11px] transition-colors" style={{ color: "var(--text-tertiary)" }} title="从此重新生成">
                ↻
              </button>
              <button onClick={() => {
                const cid = window.location.pathname.match(/\/chat\/([^/]+)/)?.[1];
                if (cid && msg.id) {
                  fetch(`/api/conversations/${cid}/fork`, {
                    method: "POST", headers: {"Content-Type":"application/json"},
                    body: JSON.stringify({message_id: msg.id})
                  }).then(r => r.json()).then(d => {
                    if (d.ok) window.location.href = `/chat/${d.conv_id}`;
                  });
                }
              }} className="flex items-center gap-1 text-[11px] transition-colors" style={{ color: "var(--text-tertiary)" }} title="从此分支">
                ⑂
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

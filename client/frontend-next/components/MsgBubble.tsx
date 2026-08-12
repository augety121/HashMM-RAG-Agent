"use client";
import { useEffect, useRef, useState, memo } from "react";
import type { Message, ToolApprovalRequest } from "@/lib/types";
import { useStore } from "@/lib/store";
import { openArtifact } from "@/lib/artifact";
import { TodoCard } from "./TodoCard";
import { cancelOcrJob, decideToolApproval, executeConversationCode, submitMessageFeedback, withToken, ocrJobs, retryOcrJob } from "@/lib/api";
import type { OcrJob } from "@/lib/api";
import type { FeedbackReason } from "@/lib/api";
import { renderMsg, doKatex, relativeTime } from "@/lib/render";
import { MessageRenderer } from "./MessageRenderer";
import { SubAgentPanel } from "./SubAgentPanel";
import { Copy, Check, ChevronDown, FileText, Image, FileCode, File as FileIcon,
         RefreshCw, ThumbsUp, ThumbsDown, Pencil, Download, Eye, X, AlertTriangle,
         CircleHelp, CheckCircle2 } from "lucide-react";
import { AgentLog } from "./AgentLog";
import { QualityBadgesRow } from "./QualityBadges";
import { FilePreview } from "./FilePreview";
import { FileCard } from "./FileCard";
import HashMascot from "./HashMascot";
import { GroundingAudit } from "./GroundingAudit";
import { toPublicAgentTimeline } from "@/lib/publicAgentTimeline";

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

function OcrQueueBadge({ filename, convId }: { filename: string; convId?: string }) {
  const [job, setJob] = useState<OcrJob | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    const refresh = () => void ocrJobs(convId).then(feed => {
      if (!alive) return;
      const found = feed.items.find(item => item.filename === filename);
      if (found) setJob(found);
    }).catch(() => undefined);
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => { alive = false; clearInterval(timer); };
  }, [filename, convId]);
  if (!job) return null;
  const label = job.status === "succeeded"
    ? (job.result_state === "partial" ? "OCR 部分可读" : "OCR 已完成")
    : job.status === "running"
      ? `OCR ${Math.round((job.progress || 0) * 100)}%`
      : job.status === "failed" ? "OCR 失败"
      : job.status === "cancelled" ? "OCR 已取消"
      : "OCR 排队中";
  const color = job.status === "succeeded"
    ? (job.result_state === "partial" ? "var(--warning)" : "var(--success)")
    : job.status === "failed" ? "var(--danger)" : "var(--warning)";
  return <span className="inline-flex items-center gap-1 text-[9px]" style={{ color }} title={job.error || job.error_code || label}>
    {label}
    {job.status === "failed" || job.status === "cancelled" ? <button className="underline" disabled={busy} onClick={() => { setBusy(true); void retryOcrJob(job.id).then(setJob).finally(() => setBusy(false)); }}>重试</button> : null}
    {job.status === "queued" || job.status === "running" ? <button className="underline" disabled={busy} onClick={() => { setBusy(true); void cancelOcrJob(job.id).then(setJob).finally(() => setBusy(false)); }}>取消</button> : null}
  </span>;
}

function UserBubble({ msg, onEdit, convId }: { msg: Message; onEdit?: () => void; convId?: string }) {
  const lines = msg.content.split("\n");
  const textLines: string[] = [], fileLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith("\u{1F4CE} ")) fileLines.push(line.slice(2).trim());   // 旧消息兼容（V86 起改为结构化 files，不再写进正文）
    else textLines.push(line);
  }
  const text = textLines.join("\n").trim();
  // V86: 结构化附件——截屏/图片直接显示缩略图（刷新后仍在），非图片显示文件 chip
  const isImgName = (n: string) => ["png","jpg","jpeg","gif","webp","bmp","svg"].includes((n.split(".").pop() || "").toLowerCase());
  const atts = msg.files || [];
  const imgAtts = atts.filter(f => isImgName(f.filename || ""));
  const fileChips = [
    ...atts.filter(f => !isImgName(f.filename || "")).map(f => ({ name: f.filename, file: f })),
    ...fileLines.map(name => ({ name, file: undefined })),
  ];
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
        {fileChips.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-1.5 justify-end">
            {fileChips.map(({ name, file }, i) => {
              const { Icon, color } = fileIconFor(name);
              const state = file?.parse_state;
              const pages = Number(file?.page_count || 0);
              const readable = Number(file?.readable_pages || 0);
              const failed = Array.isArray(file?.failed_pages) ? file.failed_pages.length : 0;
              const stateText = state === "ready"
                ? (pages ? `${readable}/${pages} 页可读` : "可读取")
                : state === "partial" ? `${readable}/${pages || readable + failed} 页可读${failed ? `，${failed} 页失败` : ""}`
                : state === "needs_ocr" ? "需要 OCR"
                : state === "encrypted" ? "文件已加密"
                : state === "corrupted" ? "文件已损坏"
                : state ? `不可读：${state}` : "";
              return <div key={i} className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px]" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }} title={file?.warnings?.join("；") || stateText}>
                <Icon size={13} style={{ color }} />
                <span className="truncate max-w-[150px] font-medium" style={{ color: "var(--text-primary)" }}>{name}</span>
                {stateText && <span className="text-[9px] whitespace-nowrap" style={{ color: state === "ready" ? "var(--success)" : "var(--warning)" }}>{stateText}</span>}
                {state === "needs_ocr" || state === "partial" ? <OcrQueueBadge filename={name} convId={convId} /> : null}
              </div>;
            })}
          </div>
        )}
      </div>
    </div>
  );
}

interface Props { msg: Message; index?: number; convId?: string; showRegenerate?: boolean; onRegenerate?: () => void; onEdit?: () => void; onSuggestion?: (text: string) => void; }

function MsgBubbleImpl({ msg, index, convId, showRegenerate, onRegenerate, onEdit, onSuggestion }: Props) {
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
  const waitingForInput = msg.status === "waiting_input";
  const inputResolved = msg.status === "resolved";
  // Legacy in-memory clarify messages had no status. A persisted complete
  // message must never be resurrected as pending after the user has replied.
  const inputPending = waitingForInput || (msg.status == null && !!msg.clarify);
  const inputOptions = msg.clarify?.options?.length ? msg.clarify.options : (msg.suggestions || []);
  const inputQuestion = msg.clarify?.question || msg.content;
  const renderedContent = stripClarify(msg.content).trim();
  const approval = msg.run_manifest?.approval_request;
  const [approvalStatus, setApprovalStatus] = useState<ToolApprovalRequest["status"] | null>(approval?.status || null);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [approvalError, setApprovalError] = useState("");
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackReason, setFeedbackReason] = useState<FeedbackReason | "">("");
  const [feedbackComment, setFeedbackComment] = useState("");
  const [feedbackBusy, setFeedbackBusy] = useState(false);
  const [feedbackError, setFeedbackError] = useState("");

  useEffect(() => {
    setApprovalStatus(approval?.status || null);
    setApprovalError("");
  }, [approval?.request_id, approval?.status]);

  async function actOnApproval(decision: "approve" | "decline") {
    const targetConv = convId || sid;
    if (!approval || !targetConv || approvalBusy) return;
    setApprovalBusy(true); setApprovalError("");
    try {
      const response = await decideToolApproval(targetConv, approval.request_id, decision);
      const next = response.approval_request;
      setApprovalStatus(next.status);
      if (typeof index === "number" && sid) {
        updateMsg(sid, index, {
          status: next.status === "declined" ? "resolved" : "waiting_approval",
          run_manifest: { ...msg.run_manifest!, approval_request: next },
        });
      }
      if (decision === "approve" && next.status === "approved" && onSuggestion) {
        onSuggestion("继续执行已批准的操作。只执行刚才批准的原始工具和参数。")
      }
    } catch (error) {
      setApprovalError(error instanceof Error ? error.message : "审批请求失败");
    } finally {
      setApprovalBusy(false);
    }
  }

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
        btn.textContent = '运行';
        btn.onclick = async () => {
          btn.innerHTML = '<span style="color:#f59e0b">…</span>';
          try {
            const d = await executeConversationCode(cid, codeEl.textContent || '');
            let out = block.querySelector('.code-output-content') as HTMLElement | null;
            if (!out) {
              const wrap = document.createElement('div');
              wrap.innerHTML = '<div class="code-output-header">输出</div><pre class="code-output-content"></pre>';
              block.appendChild(wrap.firstElementChild as HTMLElement);
              block.appendChild(wrap.lastElementChild as HTMLElement);
              out = block.querySelector('.code-output-content') as HTMLElement;
            }
            if (out) out.textContent = d.output || d.error || '(无输出)';
            btn.textContent = '运行';
          } catch (_e) { btn.textContent = '失败'; }
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

  async function persistFeedback(rating: "up" | "down" | "clear", reason: FeedbackReason | "" = "", comment = "") {
    const targetConv = convId || sid;
    if (!targetConv || !msg.id || feedbackBusy || typeof index !== "number" || !sid) return;
    setFeedbackBusy(true); setFeedbackError("");
    try {
      const response = await submitMessageFeedback(targetConv, msg.id, rating, reason, comment);
      updateMsg(sid, index, { feedback: response.feedback_case.rating || null });
      setFeedbackOpen(false); setFeedbackReason(""); setFeedbackComment("");
    } catch (error) {
      setFeedbackError(error instanceof Error ? error.message : "反馈提交失败");
    } finally {
      setFeedbackBusy(false);
    }
  }

  function setFeedback(fb: "up" | "down") {
    if (msg.feedback === fb) {
      persistFeedback("clear");
    } else if (fb === "up") {
      persistFeedback("up");
    } else {
      setFeedbackError("");
      setFeedbackOpen(true);
    }
  }

  if (msg.role === "user") return <UserBubble msg={msg} onEdit={onEdit} convId={convId} />;

  const displaySources = (msg.sources || []).reduce<Array<{ source: NonNullable<typeof msg.sources>[number]; index: number }>>(
    (rows, source, index) => {
      const duplicate = rows.some(row =>
        (row.source.filename || row.source.doc_id || row.source.chunk_id)
          === (source.filename || source.doc_id || source.chunk_id)
        && row.source.page === source.page
      );
      if (!duplicate) rows.push({ source, index });
      return rows;
    },
    [],
  );
  const hasSources = displaySources.length > 0;

  return (
    <div className="mb-6 anim-fade-up flex gap-3 group/msg">
      {/* Assistant avatar — V254: 修复"回答图标"问题。此前是紫蓝渐变"H"方块，
          与全站品牌吉祥物「小哈」（深色头+红天线，见侧栏/首页/登录页）完全脱节，
          在浅色背景上还显得像个错位的空框。统一换小哈，尺寸对齐首行文字。 */}
      <div className="w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5 overflow-hidden"
        style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)" }}>
        <HashMascot size={20} />
      </div>

      <div className="flex-1 min-w-0 pl-3" style={{ borderLeft: "2px solid var(--accent-light, rgba(37,99,235,0.15))" }}>
        {/* V50: 任务清单（最终状态随消息持久化） */}
        {(() => {
          const persistedTodo = msg.run_manifest?.process?.todo || [];
          const todo = msg.todo?.length ? msg.todo : persistedTodo;
          return todo.length > 0 ? <TodoCard items={todo} /> : null;
        })()}

        {/* v12: Unified Agent Execution Log — 优先用有序 timeline（思考/工具交错，对标 Claude），
            旧消息无 timeline 时回退到 trace+steps 拼接 */}
        {(() => {
          const transient = (msg as { timeline?: Array<{ kind: string; node: string; detail: string; tool?: string; status: string; elapsed_ms?: number; id: string }> }).timeline;
          const persisted = msg.run_manifest?.process?.timeline || [];
           const tl = toPublicAgentTimeline(transient?.length ? transient : persisted);
           const hasTimeline = Array.isArray(tl) && tl.length > 0;
          const hasLegacy = (msg.steps && msg.steps.length > 0) || (msg.trace && msg.trace.length > 0);
          if (!hasTimeline && !hasLegacy) return null;
          const legacyTimeline = hasLegacy
            ? toPublicAgentTimeline([
                ...(msg.trace || []).map((t: { node: string; detail: string }, i: number) => ({
                  id: `trace-${i}`, kind: "node", node: t.node, detail: t.detail, status: "done",
                })),
                ...(msg.steps || []).map((s: { tool: string; detail?: string; status?: string; duration_ms?: number; hooks?: import("@/lib/types").HookRun[] }, i: number) => ({
                  id: `step-${i}`, kind: "tool", node: "tool", tool: s.tool, detail: s.detail || "",
                  status: s.status || "done", elapsed_ms: s.duration_ms, hooks: s.hooks,
                })),
              ])
            : [];
          const steps = (hasTimeline ? tl! : legacyTimeline).map((e, i) => ({
            id: e.id || `tl-${i}`,
            node: e.node,
            detail: e.detail,
            tool: e.tool,
            status: (e.status as "done" | "running" | "error") || "done",
            elapsed_ms: e.elapsed_ms,
            hooks: (e as { hooks?: import("@/lib/types").HookRun[] }).hooks,
          }));
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

        {msg.stop_reason === "delivery_incomplete" && (
          <div
            className="mt-3 rounded-xl px-3.5 py-3 flex items-start justify-between gap-4"
            style={{
              background: "color-mix(in srgb, var(--warning) 8%, var(--bg-primary))",
              border: "1px solid color-mix(in srgb, var(--warning) 35%, var(--border))",
            }}
          >
            <div className="flex items-start gap-2.5 min-w-0">
              <AlertTriangle size={15} className="mt-0.5 flex-shrink-0" style={{ color: "var(--warning)" }} />
              <div className="min-w-0">
                <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>
                  交付尚未通过完整性检查
                </div>
                <div className="mt-0.5 text-[11px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                  已生成的文件和任务进度已保留。HashMM 不会把缺文件、正文重复或时间与来源未通过校验的结果标成完成。
                </div>
              </div>
            </div>
            {onSuggestion && (
              <button
                type="button"
                className="h-8 px-3 rounded-lg text-[11px] font-medium flex-shrink-0 transition-colors"
                style={{ color: "var(--warning)", border: "1px solid color-mix(in srgb, var(--warning) 45%, var(--border))" }}
                onClick={() => onSuggestion(
                  "继续完成上一轮未通过的交付。先读取上一轮任务清单和已生成文件，只补齐缺失或未通过校验的交付；不要重新搜索已核验来源，不要重复正文。完成后再次运行确定性交付检查。"
                )}
              >
                继续补齐交付
              </button>
            )}
          </div>
        )}

        {/* Claim-level evidence ledger: this is deliberately separate from the
            source pills because "a source exists" is not the same as "this claim
            is supported by that source". */}
        <GroundingAudit ledger={msg.groundings} />

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
              {displaySources.map(({ source: s, index: sourceIndex }) => {
                const label = s.filename || s.modality || "文档";
                const page = s.page && s.page > 0 ? ` p.${s.page}` : "";
                return (
                  <button key={`${s.chunk_id || s.doc_id || label}-${s.page || 0}-${sourceIndex}`}
                    onClick={() => setSrcOpen(srcOpen === sourceIndex ? -1 : sourceIndex)}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] transition-all hover:shadow-sm"
                    style={{
                      background: srcOpen === sourceIndex ? "var(--accent-light)" : "var(--bg-secondary)",
                      border: `1px solid ${srcOpen === sourceIndex ? "var(--accent)" : "var(--border)"}`,
                      color: srcOpen === sourceIndex ? "var(--accent)" : "var(--text-secondary)",
                    }}>
                    <FileText size={11} />
                    <span className="truncate max-w-[140px] font-medium">{label}{page}</span>
                    {typeof s.score === "number" && s.score > 0 && (
                      <span className="font-mono text-[10px] opacity-60">{s.score < 1 ? s.score.toFixed(2) : s.score.toFixed(1)}</span>
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

        {/* Durable input request: survives reload and can be answered from either client. */}
        {(inputPending || inputResolved) && (
          <div className="mt-3 rounded-xl p-3" style={{
            background: inputPending ? "var(--accent-light)" : "var(--bg-secondary)",
            border: `1px solid ${inputPending ? "var(--accent)" : "var(--border)"}`,
          }}>
            <div className="flex items-center gap-2">
              {inputResolved ? <CheckCircle2 size={14} style={{ color: "#15803d" }} /> : <CircleHelp size={14} style={{ color: "var(--accent)" }} />}
              <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>
                {inputResolved ? "已收到你的补充" : "需要你的输入"}
              </div>
              <span className="ml-auto text-[10px]" style={{ color: "var(--text-tertiary)" }}>
                {inputResolved ? "等待已解除" : "任务已安全暂停"}
              </span>
            </div>
            {inputQuestion.trim() !== renderedContent && (
              <div className="text-[12px] mt-2 leading-relaxed" style={{ color: "var(--text-secondary)" }}>{inputQuestion}</div>
            )}
            {inputPending && inputOptions.length > 0 && onSuggestion ? (
              <div className="flex flex-wrap gap-1.5 mt-2.5">
                {inputOptions.map((option, i) => (
                  <button key={`${option}-${i}`} onClick={() => onSuggestion(option)}
                    className="suggestion-btn px-3 py-1.5 rounded-lg text-[11px] font-medium"
                    style={{ background: "var(--bg-primary)", border: "1px solid var(--accent)", color: "var(--accent)" }}>
                    {option}
                  </button>
                ))}
              </div>
            ) : inputPending ? (
              <div className="text-[11px] mt-2" style={{ color: "var(--text-tertiary)" }}>在下方直接回复，任务会从这里继续。</div>
            ) : null}
          </div>
        )}

        {approval && approvalStatus && (
          <div className="mt-3 rounded-xl p-3" style={{
            background: approvalStatus === "pending" || approvalStatus === "approved" ? "var(--accent-light)" : "var(--bg-secondary)",
            border: `1px solid ${approvalStatus === "pending" || approvalStatus === "approved" ? "var(--accent)" : "var(--border)"}`,
          }}>
            <div className="flex items-center gap-2">
              <AlertTriangle size={14} style={{ color: approvalStatus === "declined" ? "var(--text-tertiary)" : "var(--accent)" }} />
              <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>
                {approvalStatus === "pending" ? "需要批准工具操作" :
                 approvalStatus === "approved" ? "操作已批准，等待继续" :
                 approvalStatus === "consumed" ? "批准已使用" :
                 approvalStatus === "declined" ? "操作已拒绝" : "审批已失效"}
              </div>
              <span className="ml-auto text-[10px]" style={{ color: "var(--text-tertiary)" }}>
                一次性授权
              </span>
            </div>
            <div className="mt-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
              工具 <span className="font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{approval.tool_name}</span>
              {approval.risk ? ` · 风险级别 ${approval.risk}` : ""}
            </div>
            {Object.keys(approval.arguments || {}).length > 0 && (
              <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-lg p-2 text-[10px] leading-relaxed"
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                {JSON.stringify(approval.arguments, null, 2)}
              </pre>
            )}
            {approvalError && <div className="mt-2 text-[11px]" style={{ color: "var(--danger, #dc2626)" }}>{approvalError}</div>}
            {approvalStatus === "pending" && (
              <div className="mt-2.5 flex gap-2">
                <button onClick={() => void actOnApproval("decline")} disabled={approvalBusy}
                  className="px-3 py-1.5 rounded-lg text-[11px] font-medium disabled:opacity-50"
                  style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                  拒绝
                </button>
                <button onClick={() => void actOnApproval("approve")} disabled={approvalBusy || !onSuggestion}
                  className="px-3 py-1.5 rounded-lg text-[11px] font-medium disabled:opacity-50"
                  style={{ background: "var(--accent)", border: "1px solid var(--accent)", color: "white" }}>
                  {approvalBusy ? "处理中" : "批准并继续"}
                </button>
              </div>
            )}
            {approvalStatus === "approved" && onSuggestion && (
              <button onClick={() => onSuggestion("继续执行已批准的操作。只执行刚才批准的原始工具和参数。")}
                className="mt-2.5 px-3 py-1.5 rounded-lg text-[11px] font-medium"
                style={{ background: "var(--accent)", color: "white" }}>
                继续任务
              </button>
            )}
          </div>
        )}

        {/* Follow-up suggestions */}
        {msg.suggestions && msg.suggestions.length > 0 && onSuggestion && !inputPending && !inputResolved && !approval && (
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
          {displaySources.length > 0 && (
            <span className="inline-flex items-center gap-0.5 text-[10px] mr-1" style={{ color: "var(--text-tertiary)" }}><FileText size={10} />{displaySources.length}源</span>
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
        {feedbackOpen && (
          <div className="mt-2 max-w-[620px] rounded-xl p-3"
            style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
            <div className="flex items-start justify-between gap-3 mb-2">
              <div>
                <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>这条回答哪里需要改进？</div>
                <div className="text-[10.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
                  失败原因和本次运行证据会进入待复核队列；不会自动把错误答案当成标准答案。
                </div>
              </div>
              <button onClick={() => setFeedbackOpen(false)} className="p-1 rounded-md hover:bg-[var(--bg-tertiary)]" aria-label="关闭反馈">
                <X size={13} style={{ color: "var(--text-tertiary)" }} />
              </button>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {([
                ["incorrect", "事实或结论错误"], ["unsupported", "证据不足"],
                ["retrieval_miss", "漏掉应有资料"], ["wrong_tool", "工具调用错误"],
                ["incomplete", "任务未完成"], ["instruction_miss", "没有遵守要求"],
                ["unsafe", "安全或隐私风险"], ["too_slow", "太慢或步骤过多"],
                ["other", "其他问题"],
              ] as Array<[FeedbackReason, string]>).map(([code, label]) => (
                <button key={code} onClick={() => setFeedbackReason(code)}
                  className="px-2.5 py-1.5 rounded-lg text-[11px] transition-colors"
                  style={{
                    color: feedbackReason === code ? "var(--accent)" : "var(--text-secondary)",
                    background: feedbackReason === code ? "var(--accent-light)" : "var(--bg-tertiary)",
                    border: `1px solid ${feedbackReason === code ? "var(--accent)" : "var(--border)"}`,
                  }}>{label}</button>
              ))}
            </div>
            <textarea value={feedbackComment} onChange={e => setFeedbackComment(e.target.value.slice(0, 1000))}
              placeholder="可选：具体指出错在哪里，便于复现和修复"
              className="w-full mt-2 rounded-lg px-2.5 py-2 text-[11px] resize-y min-h-[58px] outline-none"
              style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            {feedbackError && <div className="text-[10.5px] mt-1" style={{ color: "var(--error)" }}>{feedbackError}</div>}
            <div className="flex justify-end gap-2 mt-2">
              <button onClick={() => setFeedbackOpen(false)} className="px-3 py-1.5 rounded-lg text-[11px]"
                style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}>取消</button>
              <button onClick={() => feedbackReason && persistFeedback("down", feedbackReason, feedbackComment)}
                disabled={!feedbackReason || feedbackBusy}
                className="px-3 py-1.5 rounded-lg text-[11px] text-white disabled:opacity-40"
                style={{ background: "var(--accent)" }}>{feedbackBusy ? "提交中…" : "提交并进入复核"}</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// V306 性能：用 React.memo 包裹，切断"每个流式 token 触发顶层 setState → 40 条已渲染气泡
// 全部重渲染"这条主卡顿链。窗口里的历史消息是不可变的（msg 内容/来源/文件不变），
// 只要这些没变就跳过重渲染——流式期间正在生成的那条走 ChatArea 里独立的流式气泡，
// 与这些历史气泡互不影响。
// 比较器**刻意忽略函数型 props**（onRegenerate/onEdit/onSuggestion 每次父渲染都是新引用，
// 但它们按行绑定、行为稳定；纳入比较会使 memo 永远失效，失去意义）。
function _msgEqual(a: Props, b: Props): boolean {
  if (a.index !== b.index || a.convId !== b.convId || a.showRegenerate !== b.showRegenerate) return false;
  const x = a.msg, y = b.msg;
  if (x === y) return true;
  if (!x || !y) return false;
  return (
    x.id === y.id &&
    x.role === y.role &&
    x.content === y.content &&
    x.feedback === y.feedback &&
    (x.thinking || "") === (y.thinking || "") &&
    (x.sources?.length || 0) === (y.sources?.length || 0) &&
    x.groundings?.status === y.groundings?.status &&
    (x.groundings?.supported_claims || 0) === (y.groundings?.supported_claims || 0) &&
    (x.groundings?.total_factual_claims || 0) === (y.groundings?.total_factual_claims || 0) &&
    (x.files?.length || 0) === (y.files?.length || 0) &&
    (x.steps?.length || 0) === (y.steps?.length || 0) &&
    (x.timeline?.length || 0) === (y.timeline?.length || 0) &&
    (x.run_manifest?.process?.timeline?.length || 0) === (y.run_manifest?.process?.timeline?.length || 0) &&
    (x.run_manifest?.process?.todo?.length || 0) === (y.run_manifest?.process?.todo?.length || 0) &&
    x.run_manifest?.process?.completion_status === y.run_manifest?.process?.completion_status &&
    (x.suggestions?.length || 0) === (y.suggestions?.length || 0)
  );
}

export const MsgBubble = memo(MsgBubbleImpl, _msgEqual);

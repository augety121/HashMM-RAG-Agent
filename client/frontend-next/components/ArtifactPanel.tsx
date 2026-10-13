"use client";
import { useState, useEffect, useMemo, useRef } from "react";
import { X, Download, ChevronLeft, ChevronRight, FileText, Table2, Presentation, ExternalLink, Maximize2, Minimize2, Loader2, Copy, Check, LayoutTemplate, Pencil, Save, Sparkles, Share2, BookmarkPlus, RefreshCw, AlertCircle, Link2, Trash2, Globe2, UsersRound, Palette } from "lucide-react";
import { withToken, previewFile, onPreviewFileUpdated, getCanvasVersions, saveCanvasVersion, saveConvFile, canvasAsk, saveCanvasQa, canvasPublish, canvasShareStatus, canvasUnpublish, saveCanvasTemplate, canvasCommentsRead, canvasResetPasscode, canvasLock, retryDispatch, replanDispatch, uploadConversationFile, invalidatePreviewFile, listCanvasEvidence, unlinkCanvasEvidence, type CanvasEvidenceLink, type CanvasEvidenceSummary, type FilePreviewData } from "@/lib/api";
import { getFiles } from "@/lib/desktop";
import { ensureCanvasBridge } from "@/lib/canvasBridge";
import { ListTree, Search as SearchIcon, Plus as InsertIcon } from "lucide-react";
import { useStore } from "@/lib/store";
import { highlightToLines, langFromFilename } from "@/lib/highlight";
import { insertContextIntoChat } from "@/lib/contextInsert";
import { canvasVersionScopeKey } from "@/lib/canvasVersions";
import { openEvidenceInInspector } from "@/lib/browserInspector";

export interface ArtifactItem {
  convId?: string;
  type: "pptx" | "docx" | "xlsx" | "pdf" | "html" | "image" | "code";
  filename: string;
  download_url: string;
  preview_url?: string;     // For HTML/image preview
  pages?: number;           // For PPTX page count
  outline?: string[];       // For PPTX slide titles
  size?: number;
}

interface Props {
  artifact: ArtifactItem | null;
  onClose: () => void;
  embedded?: boolean;
}

type OfficeHandoffState = {
  id: string;
  changed: boolean;
  busy: boolean;
  revision?: number;
  sha256?: string;
  harnessState?: "active" | "changed" | "observed" | "committed";
  synced?: boolean;
  error?: string;
};

const TYPE_META: Record<string, { icon: React.ElementType; label: string; color: string }> = {
  pptx: { icon: Presentation, label: "演示文稿", color: "#d97706" },
  docx: { icon: FileText, label: "Word 文档", color: "#2563eb" },
  xlsx: { icon: Table2, label: "Excel 表格", color: "#059669" },
  pdf: { icon: FileText, label: "PDF 文档", color: "#dc2626" },
  html: { icon: ExternalLink, label: "Artifact 画布", color: "#7c3aed" },
  image: { icon: FileText, label: "图片", color: "#ec4899" },
  code: { icon: FileText, label: "代码文件", color: "#06b6d4" },
};

export function ArtifactPanel({ artifact, onClose, embedded = false }: Props) {
  const [currentPage, setCurrentPage] = useState(1);
  const [previewPages, setPreviewPages] = useState(1);
  const [expanded, setExpanded] = useState(false);
  const draft = useStore(s => s.artifactDraft);
  const isCode = artifact?.type === "code";
  const [previewHtml, setPreviewHtml] = useState<string | null>(null);
  const [previewSha256, setPreviewSha256] = useState("");
  const [codeContent, setCodeContent] = useState<string | null>(null);
  const [codeLang, setCodeLang] = useState<string>("text");
  const [codeLoading, setCodeLoading] = useState(false);
  const [officeHandoff, setOfficeHandoff] = useState<OfficeHandoffState | null>(null);
  const [officeRevision, setOfficeRevision] = useState(0);

  useEffect(() => {
    setCurrentPage(1);
    setPreviewPages(1);
    setPreviewHtml(null);
    setPreviewSha256("");
    setCodeContent(null);
    setOfficeHandoff(null);
    // For HTML artifacts, fetch content
    // V218 修复：openArtifact 从不设置 preview_url（lib/artifact.ts），此前条件永远为假 →
    // 画布成品打开即"骨架屏 + 源码 0 字符"（实机截图实锤）。改为：preview_url 优先，
    // 缺省用 withToken(download_url) 直取全文（下载端点回原文件、无截断），
    // 再兜底 previewFile（会话预览端点，content ≤100k）。
    if (artifact?.type === "html") {
      const direct = artifact.preview_url || (artifact.download_url ? withToken(artifact.download_url) : "");
      const fallback = () => {
        const cid = artifact.download_url?.includes("/conversations/")
          ? artifact.download_url.split("/conversations/")[1].split("/")[0] : undefined;
        previewFile(artifact.filename, cid)
          .then(r => {
            setPreviewSha256(r.sha256 || "");
            setPreviewHtml(r.content || "<p>预览加载失败</p>");
          })
          .catch(() => setPreviewHtml("<p>预览加载失败</p>"));
      };
      if (direct) {
        fetch(direct)
          .then(async r => {
            if (!r.ok) throw new Error(String(r.status));
            setPreviewSha256(r.headers.get("X-HashMM-Content-Sha256") || "");
            return r.text();
          })
          .then(setPreviewHtml)
          .catch(fallback);
      } else if (artifact.filename) {
        fallback();
      }
    }
    // For code/text files, fetch the actual file content to show in panel (对标 Claude 右侧看代码)
    if (artifact?.type === "code" && artifact.filename && !artifact.download_url) {
      return;  // V55: 草稿直播模式（file_delta），内容来自 store.artifactDraft，无需取数
    }
    if (artifact?.type === "code" && artifact.filename) {
      setCodeLoading(true);
      // 从 download_url 解析 convId
      const convId = artifact.download_url.includes("/conversations/") ? artifact.download_url.split("/conversations/")[1].split("/")[0] : undefined;
      previewFile(artifact.filename, convId)
        .then(r => { setCodeContent(r.content || "（文件为空或无法预览）"); setCodeLang(r.language || "text"); })
        .catch(() => setCodeContent("（预览加载失败，可点下载查看）"))
        .finally(() => setCodeLoading(false));
    }
  }, [artifact]);

  // V86: Esc 关闭右栏（对标 Claude；输入框/文本域聚焦时不抢按键）
  useEffect(() => {
    if (!artifact) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [artifact, onClose]);

  useEffect(() => {
    if (!officeHandoff?.id || officeHandoff.busy) return;
    const files = getFiles();
    if (!files?.officeHandoffStatus) return;
    const id = officeHandoff.id;
    const poll = () => files.officeHandoffStatus!(id).then((r) => {
      setOfficeHandoff((s) => s && s.id === id
        ? {
            ...s,
            changed: !!r.changed,
            revision: r.revision ?? s.revision,
            sha256: r.sha256 ?? s.sha256,
            harnessState: r.harness?.state ?? s.harnessState,
            synced: r.changed ? false : s.synced,
            error: r.ok ? undefined : r.error,
          }
        : s);
    }).catch(() => {});
    poll();
    const timer = setInterval(poll, 2000);
    return () => clearInterval(timer);
  }, [officeHandoff?.id, officeHandoff?.busy]);

  if (!artifact) return null;

  const meta = TYPE_META[artifact.type] || TYPE_META.code;
  const Icon = meta.icon;
  const totalPages = Math.max(artifact.pages || 1, previewPages);
  const nativeOffice = artifact.type === "docx" || artifact.type === "xlsx" || artifact.type === "pptx";

  const openInOffice = async () => {
    const files = getFiles();
    if (!files?.officeHandoffOpen || !artifact.download_url) return;
    setOfficeHandoff((s) => ({ id: s?.id || "", changed: false, busy: true, revision: s?.revision }));
    try {
      const response = await fetch(withToken(artifact.download_url));
      if (!response.ok) throw new Error(`下载失败（${response.status}）`);
      const blob = await response.blob();
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(new Error("无法读取办公文件"));
        reader.readAsDataURL(blob);
      });
      const r = await files.officeHandoffOpen({ filename: artifact.filename, content: dataUrl, isDataUrl: true });
      if (!r.ok || !r.id) throw new Error(r.error || "系统办公应用未能打开文件");
      setOfficeHandoff({
        id: r.id, changed: false, busy: false,
        revision: r.revision, sha256: r.sha256,
        harnessState: r.harness?.state, synced: false,
      });
    } catch (e) {
      setOfficeHandoff({ id: "", changed: false, busy: false, error: e instanceof Error ? e.message : String(e) });
    }
  };

  const syncOfficeChange = async () => {
    const files = getFiles();
    if (!officeHandoff?.id || !files?.officeHandoffRead) return;
    const location = fileLocation(artifact.download_url, artifact.filename);
    if (!location.convId) {
      setOfficeHandoff((s) => s ? { ...s, error: "当前文件不属于可同步的对话工作区" } : s);
      return;
    }
    setOfficeHandoff((s) => s ? { ...s, busy: true, error: undefined } : s);
    try {
      const local = await files.officeHandoffRead(officeHandoff.id);
      if (!local.ok || !local.dataUrl || !local.filename || !local.sha256) {
        throw new Error(local.error || "无法读取本机修改");
      }
      const blob = await (await fetch(local.dataUrl)).blob();
      const uploaded = await uploadConversationFile(location.convId, new File([blob], local.filename, { type: blob.type }));
      if (!uploaded.ok || uploaded.sha256 !== local.sha256) {
        throw new Error("服务器保存结果与本机修订不一致，未确认本次同步");
      }
      await invalidatePreviewFile(local.filename, location.convId);
      if (!files.officeHandoffAcknowledge) throw new Error("当前桌面端不支持修订确认");
      const acknowledged = await files.officeHandoffAcknowledge(officeHandoff.id, local.sha256);
      if (!acknowledged.ok) throw new Error(acknowledged.error || "修订确认失败，请重新同步");
      setOfficeHandoff((s) => s ? {
        ...s, changed: false, busy: false, synced: true,
        revision: acknowledged.revision ?? local.revision ?? s.revision,
        sha256: acknowledged.sha256 ?? local.sha256,
        harnessState: acknowledged.harness?.state ?? local.harness?.state ?? s.harnessState,
      } : s);
      setOfficeRevision((v) => v + 1);
    } catch (e) {
      setOfficeHandoff((s) => s ? { ...s, busy: false, error: e instanceof Error ? e.message : String(e) } : s);
    }
  };

  return (
    <div className={`flex flex-col h-full w-full min-w-0 ${isCode ? "ap-code-root" : ""} ${expanded ? "fixed inset-0 z-50" : ""}`}
      style={{ background: isCode ? undefined : "var(--bg-primary)",
               borderLeft: expanded || embedded ? "none" : "1px solid var(--border)" }}>

      {/* Header（代码型走暗色一体化主题，与内容统一——此前白头配暗身割裂）。
          该面板位于统一检查器的第二行，不应再次套用窗口标题栏避让；否则
          右侧文件操作组会被错误推到红框位置。真正的窗口级避让只留给 Chat 顶栏。 */}
      <div className={`flex items-center gap-2 px-4 py-3 flex-shrink-0 ${isCode ? "ap-code-head" : ""}`}
        style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: `${meta.color}15` }}>
          <Icon size={14} style={{ color: meta.color }} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>
            {artifact.filename}
          </div>
          <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
            {meta.label}{totalPages > 1 ? ` · ${totalPages} 页` : ""}
          </div>
        </div>
        <div className="flex items-center gap-0.5 ml-2 pl-2"
          style={{ borderLeft: "1px solid var(--border)" }}>
          {/* V217: 画布链路的可见入口 —— 任何非画布产物（代码/文档/表格…）都能一键请 agent
              生成配套工作画布。走既有 pendingPrompt 链路：只回填输入框，用户审一眼再发，绝不代发。 */}
          {artifact.type !== "html" && (
            <button onClick={() => useStore.getState().set({ pendingPrompt:
              `用 work-canvas 技能，把「${artifact.filename}」这轮的工作整理成一张工作画布（生成 .html 文件）：进度到哪了、改了什么（关键 diff 用词级标注）、验证结果、下一步待办。` })}
              className="w-8 h-8 inline-flex items-center justify-center rounded-md transition-colors hover:bg-[var(--bg-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              aria-label="生成工作画布"
              title="生成工作画布：指令回填到 Chat 输入框，确认后发送">
              <LayoutTemplate size={14} style={{ color: "var(--text-tertiary)" }} />
            </button>
          )}
          {artifact.download_url && (
            <a href={withToken(artifact.download_url)} download
              className="w-8 h-8 inline-flex items-center justify-center rounded-md transition-colors hover:bg-[var(--bg-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              aria-label="下载文件" title="下载文件">
              <Download size={14} style={{ color: "var(--text-tertiary)" }} />
            </a>
          )}
          {nativeOffice && artifact.download_url && getFiles()?.officeHandoffOpen && (
            <button onClick={openInOffice} disabled={!!officeHandoff?.busy}
              className="w-8 h-8 inline-flex items-center justify-center rounded-md transition-colors hover:bg-[var(--bg-secondary)] disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              aria-label="使用本机办公应用继续编辑" title="使用本机默认办公应用继续编辑（支持已注册的 vivo 办公套件）">
              {officeHandoff?.busy ? <Loader2 size={14} className="animate-spin" /> : <ExternalLink size={14} style={{ color: "var(--text-tertiary)" }} />}
            </button>
          )}
          <button onClick={() => setExpanded(!expanded)}
            className="w-8 h-8 inline-flex items-center justify-center rounded-md transition-colors hover:bg-[var(--bg-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            aria-label={expanded ? "退出全屏" : "全屏预览"}
            title={expanded ? "退出全屏" : "全屏预览"}>
            {expanded ? <Minimize2 size={14} style={{ color: "var(--text-tertiary)" }} /> : <Maximize2 size={14} style={{ color: "var(--text-tertiary)" }} />}
          </button>
          {!embedded && <button onClick={onClose}
            className="w-8 h-8 inline-flex items-center justify-center rounded-md transition-colors hover:bg-[var(--bg-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            aria-label="关闭文件预览" title="关闭文件预览">
            <X size={14} style={{ color: "var(--text-tertiary)" }} />
          </button>}
        </div>
      </div>

      {nativeOffice && officeHandoff && (officeHandoff.id || officeHandoff.error) && (
        <div className="flex items-center gap-2 px-4 py-2 text-[11.5px] flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", color: officeHandoff.error ? "var(--danger)" : "var(--text-secondary)" }}>
          {officeHandoff.error ? <AlertCircle size={13} /> : officeHandoff.changed ? <RefreshCw size={13} /> : <Check size={13} />}
          <span className="flex-1 min-w-0 truncate">
            {officeHandoff.error || (officeHandoff.changed
              ? `检测到本地修订 #${officeHandoff.revision || 1}，尚未同步到当前任务`
              : officeHandoff.synced
                ? `修订 #${officeHandoff.revision || 1} 已同步，Chat 下轮将读取新文件`
                : `已在本机办公应用中打开${officeHandoff.revision ? `（修订 #${officeHandoff.revision}）` : ""}；保存后会提示同步`)}
          </span>
          {officeHandoff.changed && (
            <button onClick={syncOfficeChange} disabled={officeHandoff.busy}
              className="h-7 px-3 rounded-lg font-medium text-white disabled:opacity-50" style={{ background: "var(--accent)" }}>
              {officeHandoff.busy ? "同步中" : "同步回当前任务"}
            </button>
          )}
        </div>
      )}

      {/* Preview area — code 类型铺满整个面板（无白边），其他类型保留内边距 */}
      <div className={`flex-1 overflow-auto ${artifact.type === "code" ? "p-0" : "p-4"}`}>
        {artifact.type === "pptx" && (
          <PptxPreview key={`pptx-${officeRevision}`}
            filename={artifact.filename}
            downloadUrl={artifact.download_url}
            outline={artifact.outline}
            totalPages={totalPages}
            currentPage={currentPage}
            onPages={setPreviewPages}
          />
        )}

        {artifact.type === "docx" && (
          <DocxPreview key={`docx-${officeRevision}`} filename={artifact.filename} downloadUrl={artifact.download_url} />
        )}

        {artifact.type === "pdf" && (
          <PdfPreview filename={artifact.filename} downloadUrl={artifact.download_url} />
        )}

        {artifact.type === "image" && (
          <ImagePreview filename={artifact.filename} downloadUrl={artifact.download_url} />
        )}

        {artifact.type === "xlsx" && (
          <XlsxPreview key={`xlsx-${officeRevision}`} downloadUrl={artifact.download_url} />
        )}

        {artifact.type === "code" && !artifact.download_url && (
          <CodePane content={draft && draft.filename === artifact.filename ? draft.content : ""}
            lang={langFromFilename(artifact.filename)} filename={artifact.filename} follow />
        )}
        {artifact.type === "code" && !!artifact.download_url && (
          codeLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 size={20} className="animate-spin" style={{ color: "var(--text-tertiary)" }} />
              <span className="ml-2 text-[13px]" style={{ color: "var(--text-secondary)" }}>加载代码…</span>
            </div>
          ) : (
            <CodePane content={codeContent || ""} lang={codeLang} filename={artifact.filename} />
          )
        )}

        {artifact.type === "html" && (
          <CanvasPreview
            onSaved={setPreviewHtml}
            key={artifact.filename + "|" + artifact.download_url}
            filename={artifact.filename}
            downloadUrl={artifact.download_url}
            previewHtml={previewHtml}
            previewSha256={previewSha256}
            draftContent={!artifact.download_url && draft && draft.filename === artifact.filename ? draft.content : ""}
            localDraft={!artifact.download_url && !artifact.convId}
            expanded={expanded}
          />
        )}

        {!["pptx", "docx", "xlsx", "pdf", "image", "html", "code"].includes(artifact.type) && (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <Icon size={48} style={{ color: "var(--text-tertiary)", opacity: 0.3 }} />
            <p className="mt-4 text-[13px]" style={{ color: "var(--text-secondary)" }}>
              暂不支持此格式的在线预览
            </p>
            <a href={withToken(artifact.download_url)} download
              className="mt-3 inline-flex items-center gap-2 px-4 py-2 rounded-lg text-[12px] font-medium"
              style={{ background: "var(--accent)", color: "#fff" }}>
              <Download size={14} /> 下载文件
            </a>
          </div>
        )}
      </div>

      {/* Footer — page navigation for PPTX */}
      {artifact.type === "pptx" && totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 px-4 py-2.5 flex-shrink-0"
          style={{ borderTop: "1px solid var(--border)" }}>
          <button onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
            disabled={currentPage <= 1}
            className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-secondary)] disabled:opacity-30">
            <ChevronLeft size={16} style={{ color: "var(--text-secondary)" }} />
          </button>
          <span className="text-[12px] font-mono" style={{ color: "var(--text-secondary)" }}>
            {currentPage} / {totalPages}
          </span>
          <button onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
            disabled={currentPage >= totalPages}
            className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-secondary)] disabled:opacity-30">
            <ChevronRight size={16} style={{ color: "var(--text-secondary)" }} />
          </button>
        </div>
      )}

      {/* Outline sidebar for PPTX */}
      {artifact.type === "pptx" && artifact.outline && artifact.outline.length > 0 && (
        <div className="px-4 pb-3 flex-shrink-0">
          <div className="text-[10px] font-semibold uppercase tracking-wider mb-1.5" style={{ color: "var(--text-tertiary)" }}>
            大纲
          </div>
          <div className="space-y-0.5 max-h-[200px] overflow-y-auto">
            {artifact.outline.map((title, i) => (
              <button key={i}
                onClick={() => setCurrentPage(i + 1)}
                className="w-full text-left px-2 py-1 rounded-md text-[11px] truncate transition-colors"
                style={{
                  background: currentPage === i + 1 ? "var(--accent-light)" : "transparent",
                  color: currentPage === i + 1 ? "var(--accent)" : "var(--text-secondary)",
                }}>
                {i + 1}. {title}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


// ── Sub-previews ──

/** V215: 工作画布（work-canvas）预览 — 右栏的所见即所得宿主。
 *  三件事（对标 Claude Artifacts 右栏体验）：
 *  1) 直播：草稿模式（download_url 为空）从 store.artifactDraft 取流式 HTML，350ms 防抖重渲染
 *     避免 iframe 抖动，生成中显示脉冲徽章；权威 file 事件落地后无缝切成品预览（原逻辑不变）。
 *  2) 主题同步（宿主→画布）：dark / accent / fontSize 三个既有 store 字段变化即 postMessage
 *     wc:theme 进 iframe；画布侧由 skills/packs/work-canvas 的 canvas.js 自守卫接收，随 App 换肤。
 *  3) 回喂（画布→宿主）：画布发 wc:prompt → 写 pendingPrompt（与 RunsView 复跑同一条既有链路），
 *     ChatArea 自动回填输入框，用户审一眼再发——非破坏，绝不代发。
 *  安全（对齐 CSswitch 纪律）：iframe 仅 sandbox="allow-scripts"（无同源），消息必须来自本 iframe
 *  的 contentWindow 才处理，文本裁到 4000 字符；宿主绝不向画布注入任何 token / 凭证。
 *  V216 增补（对标 Claude Artifacts）：
 *  4) 设计工具条（右栏原生）：主色 / 字号的画布级覆盖，经 wc:theme 推给画布——只作用当前画布，
 *     不改 App 全局 store；「跟随 App」一键回到全局主题。
 *  5) 版本历史：权威成品每次变化快照一版，v1…vN 一键切换查看；新版本到达或进入流式自动跳回最新。
 *  V217 增补：
 *  6) 版本历史跨会话落盘（用户拍板）：previewFile 同链路的 canvas-versions 端点，服务端存
 *     工作区隐藏侧车 .wc-versions/<文件名>.json（上限 10 版滚动、尾版去重）；本地 Map 降级为
 *     缓存——首开合并服务端历史，新快照静默 PUT，任何网络失败都不拦预览。
 *  7) 可见入口：非画布产物头部「画布讲解」按钮（pendingPrompt 链路回填指令，用户确认后发）；
 *     composer 新增「画布」chip；两者与画布内 composer 三路汇入同一条回喂链。 */

/* V216 内存级版本历史 → V217 升格为跨会话落盘（用户拍板）：服务端存工作区隐藏侧车
 * .wc-versions/<文件名>.json（与 previewFile 同一条会话文件链路：同鉴权、同目录、同防穿越），
 * 本地 Map 降级为缓存层——首开合并服务端历史，新快照本地入史 + 静默 PUT 落盘（失败不拦预览）。
 * 侧车放隐藏目录：list_conv_files 只列顶层文件，用户文件视图零污染，"非破坏"红线不破。 */
const CANVAS_VERSIONS = new Map<string, { ts: number; html: string }[]>();
const CANVAS_SEEDED = new Set<string>();   // convId|filename → 本页已从服务端拉过，避免重复请求
const CANVAS_ACCENTS = ["#2563eb", "#7c3aed", "#059669", "#d97706", "#e11d48"];

function CanvasPreview({ filename, downloadUrl, previewHtml, previewSha256, draftContent, localDraft, expanded, onSaved }: {
  filename: string; downloadUrl: string; previewHtml: string | null; draftContent: string; expanded: boolean;
  previewSha256?: string;
  localDraft?: boolean;
  onSaved?: (html: string) => void;   // V224: 就地编辑保存后回抛给父级 setPreviewHtml（子组件无该 setter——上轮红字根因）
}) {
  const [tab, setTab] = useState<"preview" | "source">("preview");
  const draftMode = !downloadUrl;
  const streaming = draftMode && !localDraft;
  const html = draftMode ? draftContent : (previewHtml || "");
  const [docHtml, setDocHtml] = useState(html);
  const baseShaRef = useRef(previewSha256 || "");
  useEffect(() => {
    if (previewSha256) baseShaRef.current = previewSha256;
  }, [previewSha256]);
  // V237: convId 上移至组件顶部——修复 TS "used before its declaration"（307/314 两个 useEffect 曾前引）
  const convId = downloadUrl.includes("/conversations/") ? downloadUrl.split("/conversations/")[1].split("/")[0] : undefined;
  const versionKey = canvasVersionScopeKey(convId, filename);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const dark = useStore(s => s.dark);
  const accent = useStore(s => s.accent);
  const fontSize = useStore(s => s.fontSize);
  // V216: 画布级覆盖（null = 跟随 App 全局）；只影响当前画布的 wc:theme 推送，不写回 store
  const [ovAccent, setOvAccent] = useState<string | null>(null);
  const [ovFont, setOvFont] = useState<number | null>(null);
  const [verIdx, setVerIdx] = useState<number | null>(null);   // null = 最新版本
  const [share, setShare] = useState<{ published: boolean; url?: string; share_id?: string; views?: number; visibility?: string; passcode?: string; comments_count?: number; comments_new?: number } | null>(null);   // V237: 上移（307 前引）
  const [evidenceLinks, setEvidenceLinks] = useState<CanvasEvidenceLink[]>([]);
  const [evidenceSummary, setEvidenceSummary] = useState<CanvasEvidenceSummary>({ linked: 0, current: 0, stale: 0, unavailable: 0, ready: true });
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [publishError, setPublishError] = useState("");
  const [lockError, setLockError] = useState("");
  // V222 画布 2.0：编辑态与锁（V237 上移——锁心跳 useEffect 依赖数组曾前引）
  const [editOn, setEditOn] = useState(false);
  const [toc, setToc] = useState<{ id: string; level: number; text: string }[]>([]);   // V259 画布大纲
  const [tocOpen, setTocOpen] = useState(false);
  const [findOpen, setFindOpen] = useState(false);      // V268 画布查找替换
  const [insertOpen, setInsertOpen] = useState(false);  // V270 画布「插入」菜单（表格/待办/分隔线/代码块）
  const [findQ, setFindQ] = useState("");
  const [replQ, setReplQ] = useState("");
  const [findStat, setFindStat] = useState({ n: 0, cur: 0 });
  const lockSession = useRef(Math.random().toString(36).slice(2, 12)).current;
  const editOnRef = useRef(editOn);
  useEffect(() => { editOnRef.current = editOn; }, [editOn]);
  useEffect(() => {
    setShare(null); setShareMenu(false);
    if (convId && filename) canvasShareStatus(convId, filename).then(setShare).catch(() => setShare(null));
  }, [convId, filename]);
  useEffect(() => {
    if (!convId || !filename) {
      setEvidenceLinks([]);
      setEvidenceSummary({ linked: 0, current: 0, stale: 0, unavailable: 0, ready: true });
      return;
    }
    let active = true;
    setEvidenceLoading(true);
    listCanvasEvidence(convId, filename)
      .then(result => {
        if (!active) return;
        setEvidenceLinks(Array.isArray(result.items) ? result.items : []);
        setEvidenceSummary(result.summary || { linked: 0, current: 0, stale: 0, unavailable: 0, ready: true });
      })
      .catch(() => {
        if (active) setEvidenceSummary({ linked: 0, current: 0, stale: 0, unavailable: 1, ready: false });
      })
      .finally(() => { if (active) setEvidenceLoading(false); });
    return () => { active = false; };
  }, [convId, filename]);
  useEffect(() => {   // 编辑中每 10s 心跳；退出/卸载即释放（TTL 20s 兜底可抢）
    if (!editOn || !convId) return;
    const loseLock = (message: string) => {
      setEditOn(false);
      setLockError(message);
      iframeRef.current?.contentWindow?.postMessage({ type: "wc:edit", on: false }, "*");
    };
    const t = setInterval(() => {
      canvasLock(convId, filename, lockSession, "beat")
        .then(result => { if (!result.held) loseLock("画布编辑权已经失效，当前已切回只读，未确认的更改不会覆盖服务器版本。"); })
        .catch(() => loseLock("无法向服务器续租画布编辑权，当前已安全切回只读。"));
    }, 10000);
    return () => { clearInterval(t); canvasLock(convId, filename, lockSession, "release").catch(() => { /* */ }); };
  }, [editOn, convId, filename, lockSession]);
  const [ask, setAsk] = useState<{ q: string; ctx: string; ans: string; busy: boolean; askId: string } | null>(null);   // askId 非空=原文已打标记，可「替换原文」
  const [saveTick, setSaveTick] = useState<"" | "saving" | "saved">("");
  // V226 Artifacts 化：发布态（组织内 Viewer 链接）与存模板反馈
  const [shareMenu, setShareMenu] = useState(false);
  const [tplTick, setTplTick] = useState("");
  const [aiMenu, setAiMenu] = useState(false);   // V228 AI 续写菜单
  // V254 新手引导：首次打开任意画布自动显示一次（之后靠工具条 ? 按钮随时回看）
  const [guideOpen, setGuideOpen] = useState(false);
  useEffect(() => {
    try {
      if (typeof window !== "undefined" && !localStorage.getItem("hmm_canvas_guide_seen")) {
        setGuideOpen(true);
        localStorage.setItem("hmm_canvas_guide_seen", "1");
      }
    } catch { /* 隐私模式等：静默 */ }
  }, []);
  const [diffOn, setDiffOn] = useState(false);   // V229 版本对比：选中历史版时可开
  const [, setVerTick] = useState(0);   // V217: 服务端历史合并进模块 Map 后触发重渲染
  const effAccent = ovAccent ?? accent;
  const effFont = ovFont ?? fontSize;

  // V217: 首开从服务端拉历史版本，按 ts 去重合并进本地缓存（跨会话续上上次的 v1…vN）
  useEffect(() => {
    if (!convId || !filename) return;
    const seedKey = versionKey;
    if (CANVAS_SEEDED.has(seedKey)) return;
    CANVAS_SEEDED.add(seedKey);
    getCanvasVersions(convId, filename).then(r => {
      const remote = Array.isArray(r?.versions) ? r.versions : [];
      if (!remote.length) return;
      const list = CANVAS_VERSIONS.get(versionKey) || [];
      const seen = new Set(list.map(v => v.ts));
      for (const v of remote) {
        if (v && typeof v.html === "string" && v.html && !seen.has(Number(v.ts))) {
          list.push({ ts: Number(v.ts) || Date.now(), html: v.html });
        }
      }
      list.sort((a, b) => a.ts - b.ts);
      while (list.length > 10) list.shift();
      CANVAS_VERSIONS.set(versionKey, list);
      setVerTick(t => t + 1);
    }).catch(() => { /* 拉取失败静默：本地缓存照常可用 */ });
  }, [convId, filename, versionKey]);

  // 直播防抖：流式期间 350ms 才重建 srcDoc；成品/停流立即渲染
  useEffect(() => {
    if (!streaming) { setDocHtml(html); return; }
    const t = setTimeout(() => setDocHtml(html), 350);
    return () => clearTimeout(t);
  }, [html, streaming]);

  // V216: 权威成品每次变化 → 快照进版本历史（加载失败占位不入史）；流式期间强制看最新
  // V217: 真正新增的快照同步静默 PUT 落盘（服务端按尾版去重，失败绝不拦预览）
  useEffect(() => {
    if (streaming || !previewHtml || previewHtml === "<p>预览加载失败</p>") return;
    const list = CANVAS_VERSIONS.get(versionKey) || [];
    if (!list.length || list[list.length - 1].html !== previewHtml) {
      list.push({ ts: Date.now(), html: previewHtml });
      while (list.length > 10) list.shift();
      CANVAS_VERSIONS.set(versionKey, list);
      if (convId) saveCanvasVersion(convId, filename, previewHtml).catch(() => { /* 离线/失败静默 */ });
    }
    setVerIdx(null);   // 新版本到达 → 跳回最新
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filename, streaming, previewHtml, versionKey, convId]);
  useEffect(() => { if (streaming) setVerIdx(null); }, [streaming]);
  const versions = CANVAS_VERSIONS.get(versionKey) || [];
  const shownHtml = verIdx != null && versions[verIdx] ? versions[verIdx].html : docHtml;
  // Keep the current preview in a ref so the iframe event listener can hand off
  // the exact canvas version without re-registering a window listener for every
  // streamed chunk. createFeatureContext applies the shared size limits before
  // the data is sent to Chat.
  const canvasHandoffRef = useRef({ convId, filename, html: shownHtml });
  canvasHandoffRef.current = { convId, filename, html: shownHtml };
  const handoffCanvas = (prompt: string) => {
    const current = canvasHandoffRef.current;
    insertContextIntoChat(
      "document",
      `当前画布：${current.filename}`,
      { filename: current.filename, conv_id: current.convId, html: current.html },
      prompt,
      `canvas:${current.convId || "local"}:${current.filename}`,
    );
  };
  const handoffCanvasForReview = (mode: "browser" | "team", prompt: string) => {
    handoffCanvas(prompt);
    useStore.getState().set({ pendingRunMode: mode });
  };
  // V249: V246 的滚动条注入升级为 ensureCanvasBridge（lib/canvasBridge.ts）——
  // 同一根因的完整修法：AI 生成的画布 HTML 没有 canvas.js，工具条的 主色/字号/编辑/划选
  // （wc:theme/wc:edit/wc:flush/wc:apply）发进去无人接收＝"点了没反应"。
  // 现在塞 srcDoc 前统一注入：细滚动条 CSS + （缺协议时）运行时协议桥；官方模板零注入。
  const injectScrollbar = (h: string): string => ensureCanvasBridge(h);

  // 宿主 → 画布：主题推送（wc:ready 握手后与任一字段变化时；V216 覆盖值优先）
  const pushTheme = () => {
    try { iframeRef.current?.contentWindow?.postMessage({ type: "wc:theme", dark, accent: effAccent, fontSize: effFont }, "*"); }
    catch { /* no-op：画布未就绪时静默 */ }
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { pushTheme(); }, [dark, effAccent, effFont, shownHtml]);

  // 画布 → 宿主：wc:ready 回推主题；wc:prompt 回填聊天框
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (!iframeRef.current || e.source !== iframeRef.current.contentWindow) return;
      const m = e.data as { type?: string; text?: string; context?: string; html?: string; askId?: string; tid?: string; tids?: string[]; items?: unknown } | null;
      if (!m || typeof m !== "object") return;
      if (m.type === "wc:ready") { pushTheme(); if (editOn) iframeRef.current?.contentWindow?.postMessage({ type: "wc:edit", on: true }, "*"); iframeRef.current?.contentWindow?.postMessage({ type: "wc:gettoc" }, "*"); return; }
      if (m.type === "wc:toc" && Array.isArray(m.items)) { setToc((m.items as { id: string; level: number; text: string }[]).filter(x => x && x.id && x.text)); return; }
      if (m.type === "wc:findcount") { setFindStat({ n: Number((m as { n?: number }).n) || 0, cur: Number((m as { cur?: number }).cur) || 0 }); return; }
      // V222「选中即问」：画布划选 → 就地小问答（独立轻通道，不进主对话）
      if (m.type === "wc:retry_all" && Array.isArray(m.tids) && m.tids.length) {   // V234 批量重试（串行，防打爆）
        (async () => {
          for (const t of m.tids!.slice(0, 10)) {
            try { await retryDispatch(String(t)); } catch { /* 死信/老后端：单项静默 */ }
          }
        })();
        return;
      }
      if (m.type === "wc:replan" && m.tid) {   // V235 死信重新规划：原目标重发 plan，新任务树回同一会话
        replanDispatch(String(m.tid)).catch(() => { /* 老后端：按钮已自灰，静默 */ });
        return;
      }
      if (m.type === "wc:retry" && m.tid) {   // V233 任务树失败节点一键重试（后端复建任务并复位节点）
        retryDispatch(String(m.tid)).catch(() => { /* 老后端无 retry 端点：按钮已自灰，静默 */ });
        return;
      }
      if (m.type === "wc:ask" && typeof m.text === "string" && m.text.trim()) {
        const q = String(m.text).slice(0, 500);
        const ctx = String(m.context || "").slice(0, 2000);
        const askId = String(m.askId || "");
        setAsk({ q, ctx, ans: "", busy: true, askId });
        canvasAsk(q, ctx)
          .then(r => {
            setAsk(a => a && a.q === q ? { ...a, ans: r.answer || "（空回答）", busy: false } : a);
            // V223 用户要求"可以有，但得有记录"：轻通道答完，Q&A 以标记消息落回本会话历史
            //（cu_save 既有链路，不触发任何生成，只是持久化两条消息，主对话可回看）。
            if (convId && r.answer) saveCanvasQa(convId, "【画布小问答】" + q, r.answer).catch(() => { /* 留痕失败不影响气泡 */ });
          })
          .catch(e => setAsk(a => a && a.q === q ? { ...a, ans: "小问答暂不可用：" + ((e as Error)?.message || "请求失败"), busy: false } : a));
        return;
      }
      // V222 就地编辑：画布回传整页 → 覆写原文件（版本快照链随 previewHtml 变化自动跟上）
      if (m.type === "wc:save" && typeof m.html === "string" && m.html.length > 30) {
        if (!filename) return;
        if (!convId) {
          // 本地草稿也必须可编辑。此前无会话时直接 return，导致 Artifact 看似能开、
          // 实际编辑内容却丢失；这里先更新本地权威草稿，连接恢复后再由 Chat/Agent
          // 按正常文件链路落盘。
          useStore.getState().set({ artifactDraft: { filename, content: m.html } });
          onSaved?.(m.html);
          setSaveTick("saved");
          setTimeout(() => setSaveTick(""), 1600);
          return;
        }
        // V256 防毁保护：可视化编辑回传的是整页 HTML——若原文件不是 .html/.htm
        //（比如 .md），覆写会把源文件毁成 HTML。改为另存 <原名>.html，原件不动。
        const fn = /\.(html?|HTML?)$/.test(filename) ? filename : filename.replace(/\.[^.]*$/, "") + ".html";
        setSaveTick("saving");
        const saveWithRevision = async () => {
          const base = baseShaRef.current;
          if (!base) {
            throw new Error("当前画布缺少可验证的基线版本，请刷新后再编辑");
          }
          const lease = await canvasLock(convId, fn, lockSession, "acquire");
          if (!lease.held) {
            throw new Error("画布正在其他位置编辑，当前更改未覆盖服务端版本");
          }
          try {
            const result = await saveConvFile(convId, fn, m.html, {
              base_sha256: base,
              lock_session: lockSession,
            });
            baseShaRef.current = result.sha256;
            await invalidatePreviewFile(fn, convId);
            onSaved?.(m.html);
            setLockError("");
            setSaveTick("saved");
            setTimeout(() => setSaveTick(""), 1600);
          } finally {
            if (!editOnRef.current) {
              canvasLock(convId, fn, lockSession, "release").catch(() => { /* TTL 兜底 */ });
            }
          }
        };
        saveWithRevision().catch((error) => {
          setSaveTick("");
          setEditOn(false);
          iframeRef.current?.contentWindow?.postMessage({ type: "wc:edit", on: false }, "*");
          setLockError((error as Error)?.message || "画布保存冲突，已安全切回只读");
        });
        return;
      }
      if (m.type === "wc:prompt") {
        const text = String(m.text || "").trim().slice(0, 4000);
        if (text) handoffCanvas(text);
      }
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dark, effAccent, effFont]);

  // 新窗口打开：Blob 方案对草稿/成品统一生效，不带任何鉴权信息
  const openInWindow = () => {
    const src = shownHtml || html;
    if (!src) return;
    try {
      const blob = new Blob([src], { type: "text/html;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank", "noopener");
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch { /* no-op */ }
  };

  const segBtn = (k: "preview" | "source", label: string) => (
    <button key={k} onClick={() => setTab(k)}
      className="px-2.5 py-1 rounded-lg text-[11px] font-medium transition-colors"
      style={{ background: tab === k ? "var(--accent-light)" : "transparent", color: tab === k ? "var(--accent)" : "var(--text-tertiary)" }}>
      {label}
    </button>
  );
  const removeEvidenceLink = async (link: CanvasEvidenceLink) => {
    if (!convId) return;
    try {
      const result = await unlinkCanvasEvidence(convId, filename, link.link_id);
      setEvidenceLinks(items => items.filter(item => item.link_id !== link.link_id));
      setEvidenceSummary(result.summary);
    } catch { /* keep the visible server-backed link when deletion was not acknowledged */ }
  };
  const openEvidence = (link: CanvasEvidenceLink) => {
    if (!convId) return;
    openEvidenceInInspector({
      url: link.evidence.url,
      title: link.evidence.page_title,
      evidenceId: link.evidence.evidence_id,
      convId,
      locator: link.evidence.locator,
      verifyOnOpen: true,
    });
  };

  return (
    <div className="h-full flex flex-col" style={{ minHeight: 420, WebkitAppRegion: "no-drag", position: "relative", zIndex: 10 } as React.CSSProperties}>
      <div className="flex items-center gap-1 mb-2 flex-shrink-0" style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}>
        {segBtn("preview", "预览")}
        {segBtn("source", "源码")}
        <div className="flex-1" />
        {streaming && (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10.5px] font-medium mr-1"
            style={{ background: "var(--accent-light)", color: "var(--accent)" }}>
            <span className="w-[6px] h-[6px] rounded-full animate-pulse" style={{ background: "var(--accent)" }} />
            生成中
          </span>
        )}
        {localDraft && (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10.5px] font-medium mr-1"
            style={{ background: "var(--bg-secondary)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
            本地草稿
          </span>
        )}
        <button onClick={openInWindow} disabled={!(shownHtml || html)} title="在新窗口打开"
          className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-secondary)] disabled:opacity-30">
          <ExternalLink size={13} style={{ color: "var(--text-tertiary)" }} />
        </button>
      </div>

      {tab === "preview" && (evidenceLoading || evidenceLinks.length > 0 || !evidenceSummary.ready) && (
        <div className="mb-2 rounded-xl flex-shrink-0 overflow-hidden" style={{
          border: `1px solid ${evidenceSummary.ready ? "var(--border)" : "rgba(180,83,9,.30)"}`,
          background: evidenceSummary.ready ? "var(--bg-secondary)" : "rgba(180,83,9,.06)",
        }}>
          <div className="px-2.5 py-2 flex items-center gap-2">
            {evidenceLoading ? <Loader2 size={12} className="animate-spin" /> : <Link2 size={12} style={{ color: evidenceSummary.ready ? "var(--accent)" : "#b45309" }} />}
            <span className="text-[10.5px] font-semibold" style={{ color: "var(--text-primary)" }}>网页证据</span>
            <span className="text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>
              {evidenceLoading ? "正在核对" : `${evidenceSummary.current}/${evidenceSummary.linked} 当前有效`}
            </span>
            {!evidenceSummary.ready && <span className="ml-auto text-[9.5px] font-medium" style={{ color: "#b45309" }}>
              {evidenceSummary.stale ? `${evidenceSummary.stale} 条已变化` : `${evidenceSummary.unavailable} 条无法定位`}
            </span>}
          </div>
          {evidenceLinks.length > 0 && <div className="px-2 pb-2 flex gap-1.5 overflow-x-auto">
            {evidenceLinks.map(link => {
              const status = link.evidence.freshness?.status || "unavailable";
              const current = status === "current";
              return <div key={link.link_id} className="min-w-[180px] max-w-[260px] rounded-lg px-2 py-1.5 flex items-start gap-1.5"
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                <button onClick={() => openEvidence(link)} className="min-w-0 flex-1 text-left" title="打开原网页、定位引用并重新验证">
                  <div className="truncate text-[10.5px] font-medium" style={{ color: "var(--text-primary)" }}>{link.evidence.page_title || "网页证据"}</div>
                  <div className="truncate text-[9.5px] mt-0.5" style={{ color: current ? "#15803d" : "#b45309" }}>
                    {current ? "内容一致" : status === "stale" ? "来源已变化" : "需要重新定位"}
                  </div>
                </button>
                <button onClick={() => void removeEvidenceLink(link)} className="p-1 rounded-md hover:bg-[var(--bg-tertiary)]"
                  title="从当前画布移除引用" aria-label="移除网页证据"><Trash2 size={10} style={{ color: "var(--text-tertiary)" }} /></button>
              </div>;
            })}
          </div>}
        </div>
      )}

      {/* V216: 设计工具条 —— 右栏原生"自动设计 + 用户可调"。覆盖只推给当前画布（wc:theme），不动全局。 */}
      {tab === "preview" && (
        <div className="canvas-design-toolbar flex items-center gap-1.5 mb-2 flex-shrink-0" role="toolbar" aria-label="画布设计工具">
          <details className="canvas-toolbar-menu relative">
            <summary className="canvas-toolbar-button" title="调整当前画布的主色与字号">
              <Palette size={10} /> 样式
            </summary>
            <div className="canvas-style-popover">
              <div className="text-[10px] font-semibold mb-2" style={{ color: "var(--text-primary)" }}>当前画布样式</div>
              <div className="flex items-center gap-2">
                <span className="text-[9.5px] w-8" style={{ color: "var(--text-tertiary)" }}>主色</span>
                {CANVAS_ACCENTS.map(c => (
                  <button key={c} onClick={() => setOvAccent(c)} title={c} aria-label={`画布主色 ${c}`}
                    className="w-[17px] h-[17px] rounded-full transition-transform hover:scale-110"
                    style={{ background: c, boxShadow: effAccent === c ? "0 0 0 2px var(--bg-primary), 0 0 0 3.5px var(--accent)" : "inset 0 0 0 1px rgba(0,0,0,0.12)" }} />
                ))}
              </div>
              <div className="flex items-center gap-2 mt-3">
                <span className="text-[9.5px] w-8" style={{ color: "var(--text-tertiary)" }}>字号</span>
                <button onClick={() => setOvFont(Math.max(11, effFont - 1))} aria-label="画布字号减小"
                  className="px-2 py-1 rounded-md text-[10.5px] font-semibold hover:bg-[var(--bg-tertiary)]"
                  style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}>A-</button>
                <span className="text-[10.5px] font-mono min-w-7 text-center" style={{ color: "var(--text-secondary)" }}>{effFont}</span>
                <button onClick={() => setOvFont(Math.min(18, effFont + 1))} aria-label="画布字号增大"
                  className="px-2 py-1 rounded-md text-[10.5px] font-semibold hover:bg-[var(--bg-tertiary)]"
                  style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}>A+</button>
                {(ovAccent != null || ovFont != null) && (
                  <button onClick={() => { setOvAccent(null); setOvFont(null); }}
                    className="ml-auto text-[9.5px]" style={{ color: "var(--accent)" }}>跟随 App</button>
                )}
              </div>
            </div>
          </details>
          {/* V254 新手引导：画布功能多但入口分散（划选提问/编辑/版本/发布/AI续写），
              新用户"不知道能干嘛"。加 ? 按钮 + 首次打开自动弹一次功能地图（localStorage 记住）。 */}
          <button onClick={() => setGuideOpen(true)} aria-label="画布使用指引"
            className="px-1.5 py-0.5 rounded-md text-[10.5px] font-semibold transition-colors hover:bg-[var(--bg-tertiary)]"
            style={{ color: "var(--text-tertiary)", border: "1px solid var(--border)" }}
            title="画布怎么用？30 秒功能地图">?</button>
          {/* V228 AI 续写：一键把画布交回主对话继续创作（回填输入框，你确认后发送） */}
          <div className="relative inline-block">
            <button onClick={() => setAiMenu(v => !v)}
              className="px-1.5 py-0.5 rounded-md text-[10.5px] inline-flex items-center gap-1"
              style={{ color: aiMenu ? "var(--accent)" : "var(--text-tertiary)", border: "1px solid var(--border)" }}
              title="让 Agent 基于当前版本定向修改：继续完善 / 检查问题 / 提炼要点">
              <Sparkles size={10} /> 让 Agent 修改
            </button>
            {aiMenu && (
              <div className="absolute top-full right-0 mt-1 py-1 rounded-xl z-20 w-[196px]"
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
                <button onClick={() => {
                    setAiMenu(false);
                    handoffCanvas(`基于画布《${filename}》的当前内容继续完善：补齐空缺段落、深化要点，保持既有结构与风格，直接更新画布文件。`);
                  }}
                  className="w-full text-left px-3 py-1.5 text-[11.5px] hover:bg-[var(--bg-secondary)]"
                  style={{ color: "var(--text-primary)" }}>继续完善</button>
                <button onClick={() => {
                    setAiMenu(false);
                    handoffCanvas(`通读画布《${filename}》，找出事实错误、逻辑漏洞、遗漏风险，逐条列出并给修改建议；确认后直接修订画布。`);
                  }}
                  className="w-full text-left px-3 py-1.5 text-[11.5px] hover:bg-[var(--bg-secondary)]"
                  style={{ color: "var(--text-primary)" }}>检查问题</button>
                <button onClick={() => {
                    setAiMenu(false);
                    handoffCanvas(`把画布《${filename}》提炼成 5 条以内的执行要点，附一句话结论，追加到画布末尾。`);
                  }}
                  className="w-full text-left px-3 py-1.5 text-[11.5px] hover:bg-[var(--bg-secondary)]"
                  style={{ color: "var(--text-primary)" }}>提炼要点</button>
                <div className="my-1" style={{ borderTop: "1px solid var(--border)" }} />
                <button onClick={() => {
                    setAiMenu(false);
                    handoffCanvasForReview("browser", `使用受控浏览器核验画布《${filename}》中的关键事实、数据和链接。只把实际打开并读取到的网页作为证据；列出来源、访问时间和不一致项，未经我确认不要覆盖画布。`);
                  }}
                  className="w-full text-left px-3 py-1.5 text-[11.5px] hover:bg-[var(--bg-secondary)] inline-flex items-center gap-2"
                  style={{ color: "var(--text-primary)" }}>
                  <Globe2 size={11} style={{ color: "var(--accent)" }} />浏览器事实核验
                </button>
                <button onClick={() => {
                    setAiMenu(false);
                    handoffCanvasForReview("team", `对画布《${filename}》发起并行评审：分别检查事实与证据、逻辑与遗漏、用户可读性；各角色独立给出结论，最后由主 Agent 汇总分歧、风险和可执行修改，未经我确认不要覆盖画布。`);
                  }}
                  className="w-full text-left px-3 py-1.5 text-[11.5px] hover:bg-[var(--bg-secondary)] inline-flex items-center gap-2"
                  style={{ color: "var(--text-primary)" }}>
                  <UsersRound size={11} style={{ color: "var(--accent)" }} />多 Agent 并行评审
                </button>
              </div>
            )}
          </div>
          {/* V226 Artifacts：发布 → 组织内私有链接（认证只读、同链接永远最新版） */}
          <div className="relative inline-block">
            <button onClick={() => {
                if (share?.published && share.url) {
                  try { navigator.clipboard.writeText(window.location.origin + share.url); } catch { /* */ }
                  setShareMenu(v => {
                    const nv = !v;
                    if (nv && share.share_id && (share.comments_new || 0) > 0) {   // V229 打开即已读回执
                      canvasCommentsRead(share.share_id).catch(() => { /* */ });
                      setShare(s0 => (s0 ? { ...s0, comments_new: 0 } : s0));
                    }
                    return nv;
                  });
                } else setShareMenu(v => !v);
              }}
              className="px-1.5 py-0.5 rounded-md text-[10.5px] inline-flex items-center gap-1"
              style={{ background: share?.published ? "var(--accent-light)" : "transparent",
                       color: share?.published ? "var(--accent)" : "var(--text-tertiary)", border: "1px solid var(--border)" }}
              title={share?.published ? "已发布：点击复制链接（组织内认证只读，同链接永远最新版）" : "发布：生成组织内私有链接，团队认证访问、只读查看、始终最新"}>
              <Share2 size={10} /> {share?.published ? "已发布" : "发布"}
            </button>
            {shareMenu && (
              <div className="absolute top-full right-0 mt-1 py-1 rounded-xl z-20 w-[188px]"
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
                {share?.published ? (<>
                  <div className="px-3 py-1.5 text-[10px]" style={{ color: "var(--text-tertiary)" }}>
                    链接已复制 · 编辑保存即全网最新
                    {typeof share.views === "number" ? ` · 已被查看 ${share.views} 次` : ""}
                    {typeof share.comments_count === "number" && share.comments_count > 0 ? ` · ${share.comments_count} 条评论${(share.comments_new || 0) > 0 ? `（${share.comments_new} 新）` : ""}` : ""}
                    {share.visibility === "link" && share.passcode ? ` · 口令 ${share.passcode}` : ""}
                  </div>
                  {share.visibility === "link" && (
                    <button onClick={async () => {
                        try {
                          if (!share.share_id) return;
                          const r = await canvasResetPasscode(share.share_id);
                          setShare(s0 => (s0 ? { ...s0, passcode: r.passcode } : s0));
                          try { navigator.clipboard.writeText(`${window.location.origin}${share.url}\n口令：${r.passcode}`); } catch { /* */ }
                        } catch { /* */ }
                      }}
                      className="w-full text-left px-3 py-1.5 text-[11px] hover:bg-[var(--bg-secondary)]" style={{ color: "var(--text-primary)" }}
                      title="生成新口令并复制；旧口令即刻失效">重置口令（旧码失效）</button>
                  )}
                  <button onClick={async () => { try { if (share.share_id) await canvasUnpublish(share.share_id); setShare({ published: false }); } catch { /* */ } setShareMenu(false); }}
                    className="w-full text-left px-3 py-1.5 text-[11px] hover:bg-[var(--bg-secondary)]" style={{ color: "var(--error)" }}>停止分享</button>
                </>) : (<>
                  {([["org", "组织内可见", "所有登录用户可查看"], ["private", "仅自己", "只有你能打开链接"], ["link", "链接 + 口令（对外）", "无需登录，凭 6 位口令查看"]] as const).map(([vis, label, sub]) => (
                    <button key={vis} onClick={async () => {
                        setShareMenu(false);
                        setPublishError("");
                        try {
                          const r = await canvasPublish(convId!, filename, vis);
                          setShare({ published: true, url: r.url, share_id: r.share_id, visibility: r.visibility, passcode: r.passcode });
                          const full = window.location.origin + r.url;
                          try { navigator.clipboard.writeText(vis === "link" && r.passcode ? `${full}\n口令：${r.passcode}` : full); } catch { /* */ }
                        } catch (e) {
                          setPublishError((e as Error)?.message || "画布发布失败，请检查证据状态后重试。");
                        }
                      }}
                      className="w-full text-left px-3 py-1.5 hover:bg-[var(--bg-secondary)]">
                      <div className="text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>{label}</div>
                      <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{sub}</div>
                    </button>
                  ))}
                </>)}
              </div>
            )}
          </div>
          {/* V226 存为我的模板：起稿菜单里一键再用 */}
          <button onClick={async () => {
              const name = window.prompt("模板名称（≤40字）", filename.replace(/\.html?$/i, ""));
              if (!name) return;
              try { await saveCanvasTemplate(name.trim().slice(0, 40), shownHtml || html); setTplTick("已存"); setTimeout(() => setTplTick(""), 1500); }
              catch (e) { setTplTick("失败"); setTimeout(() => setTplTick(""), 1500); }
            }}
            className="px-1.5 py-0.5 rounded-md text-[10.5px] inline-flex items-center gap-1"
            style={{ color: tplTick === "已存" ? "var(--success, #22a06b)" : "var(--text-tertiary)", border: "1px solid var(--border)" }}
            title="把当前画布存为「我的模板」——输入框「画布」菜单里一键再用">
            <BookmarkPlus size={10} /> {tplTick || "存模板"}
          </button>
          {/* V222：就地编辑开关 + 手动保存（自动保存为停笔 1.2s，wc:flush 立即） */}
          <button onClick={async () => {
              const on = !editOn;
              if (on && convId) {   // V230: 进入编辑先拿互斥锁——另一处在编则只读并提示，不再互相覆盖
                setLockError("");
                try {
                  const r = await canvasLock(convId, filename, lockSession, "acquire");
                  if (!r.held) {
                    setLockError(`另一处正在编辑这张画布（${r.age ?? "?"} 秒前活跃），当前保持只读。`);
                    return;
                  }
                } catch {
                  setLockError("无法确认画布编辑权，已按安全策略保持只读。");
                  return;
                }
              }
              setEditOn(on);
              iframeRef.current?.contentWindow?.postMessage({ type: "wc:edit", on }, "*");
            }}
            className="px-1.5 py-0.5 rounded-md text-[10.5px] inline-flex items-center gap-1 transition-colors"
            style={{ background: editOn ? "var(--accent-light)" : "transparent", color: editOn ? "var(--accent)" : "var(--text-tertiary)", border: "1px solid var(--border)" }}
            title="就地编辑：直接在画布里改文字/代码，停笔自动保存回原文件，agent 下一轮就能看到你的修改">
            <Pencil size={10} /> {editOn ? "编辑中" : "编辑"}
          </button>
          <div className="relative inline-flex">
            <button onClick={() => setInsertOpen(v => !v)}
              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] transition-colors hover:bg-[var(--bg-tertiary)]"
              style={{ color: insertOpen ? "var(--accent)" : "var(--text-tertiary)" }}
              title="插入：表格 / 待办清单 / 分隔线 / 代码块（光标处，未定位则文末）">
              <InsertIcon size={10} /> 插入
            </button>
            {insertOpen && (
              <div className="absolute top-full left-0 mt-1 py-1 rounded-lg z-30 min-w-[124px]"
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
                {([["table", "表格 3×3"], ["todo", "待办清单"], ["divider", "分隔线"], ["code", "代码块"]] as const).map(([k, label]) => (
                  <button key={k}
                    onClick={() => { iframeRef.current?.contentWindow?.postMessage({ type: "wc:insert", kind: k }, "*"); setInsertOpen(false); }}
                    className="w-full text-left px-3 py-1.5 text-[11.5px] hover:bg-[var(--bg-tertiary)]"
                    style={{ color: "var(--text-primary)" }}>{label}</button>
                ))}
              </div>
            )}
          </div>
          <button onClick={() => { setFindOpen(v => { const nv = !v; if (!nv) iframeRef.current?.contentWindow?.postMessage({ type: "wc:findclear" }, "*"); return nv; }); }}
            className="px-1.5 py-0.5 rounded-md text-[10.5px] inline-flex items-center gap-1 transition-colors"
            style={{ background: findOpen ? "var(--accent-light)" : "transparent", color: findOpen ? "var(--accent)" : "var(--text-tertiary)", border: "1px solid var(--border)" }}
            title="查找（编辑态可替换）：高亮全部匹配，↑↓ 跳转">
            <SearchIcon size={10} /> 查找
          </button>
          {toc.length > 1 && (
            <button onClick={() => setTocOpen(v => !v)}
              className="px-1.5 py-0.5 rounded-md text-[10.5px] inline-flex items-center gap-1 transition-colors"
              style={{ background: tocOpen ? "var(--accent-light)" : "transparent", color: tocOpen ? "var(--accent)" : "var(--text-tertiary)", border: "1px solid var(--border)" }}
              title="大纲：画布标题一览，点击直达对应段落">
              <ListTree size={10} /> 大纲
            </button>
          )}
          {editOn && (
            <button onClick={() => iframeRef.current?.contentWindow?.postMessage({ type: "wc:flush" }, "*")}
              className="px-1.5 py-0.5 rounded-md text-[10.5px] inline-flex items-center gap-1"
              style={{ color: saveTick === "saved" ? "var(--success, #22a06b)" : "var(--text-tertiary)", border: "1px solid var(--border)" }}
              title="立即保存">
              <Save size={10} /> {saveTick === "saving" ? "保存中…" : saveTick === "saved" ? "已保存" : "保存"}
            </button>
          )}
          {versions.length > 1 && (
            <>
              <div className="flex-1" />
              <span className="text-[10.5px] font-medium" style={{ color: "var(--text-tertiary)" }}>版本</span>
              {versions.map((_, i) => {
                const active = verIdx == null ? i === versions.length - 1 : verIdx === i;
                return (
                  <button key={i} onClick={() => setVerIdx(i === versions.length - 1 ? null : i)}
                    title={new Date(versions[i].ts).toLocaleTimeString()} aria-pressed={active}
                    className="px-1.5 py-0.5 rounded-md text-[10.5px] font-mono transition-colors hover:bg-[var(--bg-tertiary)]"
                    style={{ background: active ? "var(--accent-light)" : "transparent", color: active ? "var(--accent)" : "var(--text-tertiary)" }}>
                    v{i + 1}
                  </button>
                );
              })}
              {verIdx != null && !diffOn && (
                <button onClick={() => setDiffOn(true)}
                  className="px-1.5 py-0.5 rounded-md text-[10px] ml-1"
                  style={{ border: "1px dashed var(--accent)", color: "var(--accent)" }}
                  title="逐行对比该历史版与最新版（红删绿增）">对比 latest</button>
              )}
            </>
          )}
        </div>
      )}
      {publishError && <div className="mb-2 px-2.5 py-2 rounded-lg text-[10.5px] flex-shrink-0"
        style={{ color: "#b42318", background: "rgba(180,35,24,.07)", border: "1px solid rgba(180,35,24,.16)" }}>
        {publishError}
      </div>}
      {lockError && <div className="mb-2 px-2.5 py-2 rounded-lg text-[10.5px] flex-shrink-0"
        style={{ color: "#b45309", background: "rgba(180,83,9,.06)", border: "1px solid rgba(180,83,9,.18)" }}>
        {lockError}
      </div>}

      {tab === "source" ? (
        <div className="flex-1 min-h-0 overflow-auto rounded-lg" style={{ border: "1px solid var(--border)" }}>
          <CodePane content={html} lang="html" filename={filename} follow={streaming} />
        </div>
      ) : shownHtml ? (
        <div className="flex-1 min-h-0 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)" }}>
          {ask && (
        <div className="absolute right-3 bottom-3 z-10 rounded-2xl shadow-lg"
          style={{ width: "min(360px, 86%)", background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
          <div className="flex items-center gap-2 px-3 py-2" style={{ borderBottom: "1px solid var(--border)" }}>
            <Sparkles size={13} style={{ color: "var(--accent)" }} />
            <span className="text-[12px] font-semibold flex-1" style={{ color: "var(--text-primary)" }}>就地小问答</span>
            <button onClick={() => { if (ask.askId) iframeRef.current?.contentWindow?.postMessage({ type: "wc:apply", mode: "cancel", askId: ask.askId }, "*"); setAsk(null); }} className="p-1 rounded-md hover:bg-[var(--bg-secondary)]" aria-label="关闭">
              <X size={12} style={{ color: "var(--text-tertiary)" }} />
            </button>
          </div>
          <div className="px-3 py-2 max-h-[260px] overflow-auto">
            <div className="text-[11px] mb-1.5 px-2 py-1 rounded-lg font-mono"
              style={{ background: "var(--bg-secondary)", color: "var(--text-secondary)" }}>{ask.q}</div>
            {ask.busy
              ? <div className="text-[12px] py-2 inline-flex items-center gap-2" style={{ color: "var(--text-tertiary)" }}><Loader2 size={12} className="animate-spin" /> 思考中（轻量通道，答完自动留痕到本对话）…</div>
              : <div className="text-[12.5px] whitespace-pre-wrap" style={{ color: "var(--text-primary)", lineHeight: 1.7 }}>{ask.ans}</div>}
          </div>
          {!ask.busy && (
            <div className="flex items-center gap-1.5 px-3 py-2" style={{ borderTop: "1px solid var(--border)" }}>
              <button onClick={() => { try { navigator.clipboard.writeText(ask.ans); } catch { /* */ } }}
                className="px-2 py-1 rounded-lg text-[10.5px]" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>复制</button>
              {ask.askId && (
                <button onClick={() => { iframeRef.current?.contentWindow?.postMessage({ type: "wc:apply", mode: "replace", askId: ask.askId, text: ask.ans }, "*"); setAsk(null); }}
                  className="px-2 py-1 rounded-lg text-[10.5px] font-medium" style={{ background: "var(--accent)", color: "#fff" }}
                  title="用答案替换画布里被提问的那段原文（自动保存并进版本历史）">替换原文</button>
              )}
              <button onClick={() => { iframeRef.current?.contentWindow?.postMessage({ type: "wc:apply", mode: "append", askId: ask.askId, text: ask.ans }, "*"); setAsk(null); }}
                className="px-2 py-1 rounded-lg text-[10.5px]" style={{ border: "1px solid var(--border)", color: "var(--accent)" }}
                title="把答案作为补充段落插进画布正文末尾（自动保存并进版本历史）">插入文末</button>
              <button onClick={() => { handoffCanvas(`围绕这段继续深入（画布小问答的延伸）：\n> ${ask.q}\n${ask.ans.slice(0, 400)}`); setAsk(null); }}
                className="px-2 py-1 rounded-lg text-[10.5px]" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}
                title="把这个问题升级到主对话（回填输入框，你确认后发送）">转主对话深挖</button>
            </div>
          )}
        </div>
      )}
      {/* V254 画布新手引导浮层 */}
      {guideOpen && (
        <div className="absolute inset-0 z-30 flex items-center justify-center p-6" style={{ background: "rgba(0,0,0,0.4)" }}
          onClick={e => { if (e.target === e.currentTarget) setGuideOpen(false); }}>
          <div className="w-full max-w-[430px] rounded-2xl px-5 py-4 anim-fade-up"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
            <div className="text-[14px] font-bold mb-0.5" style={{ color: "var(--text-primary)" }}>工作画布 · 30 秒上手</div>
            <div className="text-[11px] mb-3" style={{ color: "var(--text-tertiary)" }}>这是一份可继续对话、编辑和回退版本的 Artifact，不只是预览。</div>
            {([
              ["就地编辑", "点上方「编辑」，直接改文字；停笔自动保存，agent 下一轮就能看到你的修改。"],
              ["划选提问", "用鼠标划选画布里任意一段 → 就地弹小问答，答案可一键替换原文。"],
              ["版本历史", "每次成品变化自动存一版（v1…vN），可回看、可红绿对比差异。"],
              ["Agent 修改", "「让 Agent 修改」把当前 Artifact 连同你的要求交回 Chat，继续完善、检查或提炼。"],
              ["发布分享", "「发布」生成组织内只读链接——同链接永远最新版，多智能体/任务树画布还能看直播。"],
            ] as const).map(([h, d]) => (
              <div key={h} className="flex gap-2.5 py-1.5">
                <span className="text-[12px] flex-shrink-0 w-[86px] font-semibold" style={{ color: "var(--accent)" }}>{h}</span>
                <span className="text-[12px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>{d}</span>
              </div>
            ))}
            <div className="flex justify-end mt-3">
              <button onClick={() => setGuideOpen(false)}
                className="px-4 py-1.5 rounded-xl text-[12px] font-semibold text-white" style={{ background: "var(--accent)" }}>开始使用</button>
            </div>
          </div>
        </div>
      )}
      {verIdx != null && diffOn ? (
        <div className="flex-1 overflow-auto px-4 py-3 text-[12px] leading-relaxed" style={{ background: "var(--bg-primary)" }}>
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[11px] font-semibold" style={{ color: "var(--text-secondary)" }}>
              版本对比：v{verIdx + 1} → 最新（<span style={{ color: "#b42318" }}>红=旧版删除</span> · <span style={{ color: "#067647" }}>绿=最新新增</span>）
            </span>
            <button onClick={() => setDiffOn(false)} className="ml-auto text-[10.5px] px-2 py-0.5 rounded-md"
              style={{ border: "1px solid var(--border)", color: "var(--text-tertiary)" }}>退出对比</button>
          </div>
          {computeDiff(versions[verIdx]?.html || "", docHtml).map((ln, i) => (
            <div key={i} className="px-2 rounded"
              style={ln.t === "-" ? { background: "rgba(180,35,24,.10)", color: "#b42318", textDecoration: "line-through" }
                : ln.t === "+" ? { background: "rgba(6,118,71,.10)", color: "#067647" }
                : { color: "var(--text-secondary)" }}>
              {ln.t === "-" ? "− " : ln.t === "+" ? "＋ " : "　"}{ln.s || " "}
            </div>
          ))}
        </div>
      ) : (
        <>
          {findOpen && (
            <div className="absolute left-3 top-12 z-20 rounded-xl p-2 shadow-lg flex flex-col gap-1.5"
              style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", width: 250 }}>
              <div className="flex items-center gap-1.5">
                <input autoFocus value={findQ}
                  onChange={e => { setFindQ(e.target.value); iframeRef.current?.contentWindow?.postMessage({ type: "wc:find", q: e.target.value }, "*"); }}
                  onKeyDown={e => { if (e.key === "Enter") iframeRef.current?.contentWindow?.postMessage({ type: "wc:findnav", dir: e.shiftKey ? "prev" : "next" }, "*"); if (e.key === "Escape") { setFindOpen(false); iframeRef.current?.contentWindow?.postMessage({ type: "wc:findclear" }, "*"); } }}
                  placeholder="查找…" className="flex-1 px-2 py-1 rounded-lg text-[11.5px] outline-none min-w-0"
                  style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
                <span className="text-[10px] tabular-nums shrink-0" style={{ color: "var(--text-tertiary)" }}>{findStat.n ? `${findStat.cur}/${findStat.n}` : "0"}</span>
                <button onClick={() => iframeRef.current?.contentWindow?.postMessage({ type: "wc:findnav", dir: "prev" }, "*")}
                  className="px-1 rounded hover:bg-[var(--bg-tertiary)] text-[11px]" style={{ color: "var(--text-secondary)" }}>↑</button>
                <button onClick={() => iframeRef.current?.contentWindow?.postMessage({ type: "wc:findnav", dir: "next" }, "*")}
                  className="px-1 rounded hover:bg-[var(--bg-tertiary)] text-[11px]" style={{ color: "var(--text-secondary)" }}>↓</button>
              </div>
              {editOn && (
                <div className="flex items-center gap-1.5">
                  <input value={replQ} onChange={e => setReplQ(e.target.value)}
                    placeholder="替换为…" className="flex-1 px-2 py-1 rounded-lg text-[11.5px] outline-none min-w-0"
                    style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
                  <button onClick={() => iframeRef.current?.contentWindow?.postMessage({ type: "wc:replace", text: replQ, all: false }, "*")}
                    className="px-1.5 py-0.5 rounded-lg text-[10.5px] shrink-0" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>替换</button>
                  <button onClick={() => iframeRef.current?.contentWindow?.postMessage({ type: "wc:replace", text: replQ, all: true }, "*")}
                    className="px-1.5 py-0.5 rounded-lg text-[10.5px] shrink-0" style={{ border: "1px solid var(--border)", color: "var(--accent)" }}>全部</button>
                </div>
              )}
            </div>
          )}
          {tocOpen && toc.length > 1 && (
            <div className="absolute right-3 top-12 z-20 rounded-xl py-2 px-1 max-h-[60%] overflow-y-auto shadow-lg"
              style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", width: 220 }}>
              <div className="px-2 pb-1 text-[10px] font-bold" style={{ color: "var(--text-tertiary)" }}>大纲 · 点击直达</div>
              {toc.map(h => (
                <button key={h.id}
                  onClick={() => iframeRef.current?.contentWindow?.postMessage({ type: "wc:scrollto", id: h.id }, "*")}
                  className="block w-full text-left px-2 py-1 rounded-lg text-[11px] truncate hover:bg-[var(--bg-tertiary)]"
                  style={{ color: h.level === 1 ? "var(--text-primary)" : "var(--text-secondary)", paddingLeft: 8 + (h.level - 1) * 12, fontWeight: h.level === 1 ? 600 : 400 }}>
                  {h.text}
                </button>
              ))}
            </div>
          )}
          <iframe ref={iframeRef} srcDoc={injectScrollbar(shownHtml)} sandbox="allow-scripts" title={filename}
            className="w-full h-full" style={{ border: "none", background: "var(--bg-primary)", minHeight: expanded ? "calc(100vh - 160px)" : 420 }} />
        </>
      )}
        </div>
      ) : (
        <div className="space-y-3 py-8" role="status" aria-label="画布生成中">
          <div className="skeleton-shimmer h-4 w-[80%] mx-auto" />
          <div className="skeleton-shimmer h-4 w-[65%] mx-auto" />
          <div className="skeleton-shimmer h-4 w-[70%] mx-auto" />
        </div>
      )}
    </div>
  );
}

/** V49: 右侧面板代码视图 — 行号 + 零依赖语法高亮 + 自动换行 + 复制，铺满面板（对标 Claude artifact） */
function CodePane({ content, lang, filename, follow }: { content: string; lang: string; filename: string; follow?: boolean }) {
  const [copied, setCopied] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  // V55: 直播草稿模式跟随滚动（像看人现场写代码）
  useEffect(() => {
    if (follow && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [content, follow]);
  const effLang = (!lang || lang === "text") ? langFromFilename(filename) : lang;
  const hlLines = useMemo(() => highlightToLines(content, effLang), [content, effLang]);

  const copyAll = async () => {
    try { await navigator.clipboard.writeText(content); }
    catch {
      const ta = document.createElement("textarea");
      ta.value = content; ta.style.position = "fixed"; ta.style.left = "-9999px";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch { /* no-op */ }
      document.body.removeChild(ta);
    }
    setCopied(true); setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="cp-root">
      <div className="cp-toolbar">
        <span className="cp-lang">{effLang}</span>
        <span className="cp-meta">{hlLines.length} 行 · {content.length.toLocaleString()} 字符</span>
        <button className="cp-toolbar-btn" onClick={copyAll}>
          {copied ? <><Check size={12} className="text-green-500" /> 已复制</> : <><Copy size={12} /> 复制</>}
        </button>
      </div>
      <div className="cp-scroll" ref={scrollRef}>
        {hlLines.map((html, i) => (
          <div key={i} className="cp-line">
            <span className="cp-ln">{i + 1}</span>
            {/* 高亮器输出已全量转义（scripts/check_highlight.mjs 锁住该不变量），安全 */}
            <span className="cp-code" dangerouslySetInnerHTML={{ __html: html || "\u200b" }} />
          </div>
        ))}
      </div>
    </div>
  );
}

function fileLocation(downloadUrl: string, fallback = ""): { filename: string; convId?: string } {
  const path = downloadUrl.split("?")[0].split("#")[0];
  const filename = decodeURIComponent(path.split("/").pop() || fallback);
  const convId = path.includes("/conversations/") ? path.split("/conversations/")[1].split("/")[0] : undefined;
  return { filename, convId };
}

function PptxPreview({ filename, downloadUrl, outline, totalPages, currentPage, onPages }: {
  filename: string; downloadUrl: string; outline?: string[]; totalPages: number; currentPage: number;
  onPages: (pages: number) => void;
}) {
  const [slides, setSlides] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    const location = fileLocation(downloadUrl, filename);
    if (!location.filename) { setLoading(false); setError("无法识别演示文稿文件"); return; }
    let active = true;
    const apply = (data: FilePreviewData) => {
      if (!active) return;
      if (data.rich?.kind !== "slides") {
        setError(data.error || "服务器没有返回可预览的幻灯片内容");
        setSlides(null);
      } else {
        const nextSlides = data.rich.slides || [];
        setSlides(nextSlides);
        onPages(Math.max(1, data.rich.pages || nextSlides.length));
        setError("");
      }
      setLoading(false);
    };
    setLoading(true); setError(""); setSlides(null);
    const unsubscribe = onPreviewFileUpdated(location.filename, location.convId, apply);
    previewFile(location.filename, location.convId).then(apply).catch((reason: unknown) => {
      if (!active) return;
      setLoading(false);
      setError(reason instanceof Error ? reason.message : "演示文稿预览加载失败");
    });
    return () => { active = false; unsubscribe(); };
  }, [downloadUrl, filename, onPages, retryKey]);

  const raw = slides ? (slides[currentPage - 1] || "") : (outline?.[currentPage - 1] || "");
  const lines = raw.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const title = lines[0] || `第 ${currentPage} 页`;
  const body = lines.slice(1);

  if (loading) return (
    <div className="flex flex-col items-center py-10">
      <div className="w-full max-w-[760px] aspect-[16/9] rounded-xl p-8 shadow-sm" style={{ background: "#fff", border: "1px solid var(--border)" }}>
        <div className="skeleton-shimmer h-7 w-[58%] mb-8" />
        <div className="space-y-4"><div className="skeleton-shimmer h-4 w-[86%]" /><div className="skeleton-shimmer h-4 w-[70%]" /><div className="skeleton-shimmer h-4 w-[78%]" /></div>
      </div>
      <div className="mt-4 text-[12px]" style={{ color: "var(--text-tertiary)" }}>正在准备第 {currentPage} 页</div>
    </div>
  );

  if (error) return (
    <PreviewFailure title="演示文稿暂时无法预览" detail={error} downloadUrl={downloadUrl} downloadLabel="下载 PPTX" onRetry={() => setRetryKey(v => v + 1)} />
  );

  return (
    <div className="flex flex-col items-center py-2">
      <div className="relative w-full max-w-[760px] aspect-[16/9] rounded-xl overflow-auto p-[7%] shadow-md"
        style={{ background: "#fff", color: "#172033", border: "1px solid rgba(15,23,42,.12)" }}>
        <div className="absolute left-0 top-0 bottom-0 w-1.5" style={{ background: "var(--accent)" }} />
        <div className="absolute right-5 top-4 text-[10px] font-medium" style={{ color: "#94a3b8" }}>{currentPage} / {totalPages}</div>
        <h2 className="text-[clamp(18px,2.2vw,30px)] font-semibold leading-tight pr-10">{title}</h2>
        <div className="w-12 h-1 rounded-full mt-4 mb-6" style={{ background: "var(--accent)" }} />
        {body.length ? (
          <ul className="space-y-3 text-[clamp(12px,1.2vw,16px)] leading-relaxed">
            {body.map((line, index) => <li key={index} className="flex gap-3"><span style={{ color: "var(--accent)" }}>•</span><span>{line.replace(/^[•·\-–—]\s*/, "")}</span></li>)}
          </ul>
        ) : <p className="text-[13px]" style={{ color: "#64748b" }}>本页没有可提取文本。完整排版和媒体内容请下载原文件查看。</p>}
      </div>
      <div className="mt-4 flex items-center gap-3 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
        <span className="truncate max-w-[280px]">{filename}</span><span>第 {currentPage} / {totalPages} 页</span>
      </div>
    </div>
  );
}

function PreviewFailure({ title, detail, downloadUrl, downloadLabel, onRetry }: {
  title: string; detail: string; downloadUrl: string; downloadLabel: string; onRetry: () => void;
}) {
  return (
    <div className="max-w-[560px] mx-auto mt-8 rounded-2xl p-6 text-center" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
      <div className="w-11 h-11 mx-auto rounded-xl flex items-center justify-center" style={{ background: "#fef2f2" }}><AlertCircle size={21} style={{ color: "#dc2626" }} /></div>
      <h3 className="mt-3 text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>{title}</h3>
      <p className="mt-1 text-[11.5px] leading-relaxed break-words" style={{ color: "var(--text-tertiary)" }}>{detail}</p>
      <div className="mt-5 flex items-center justify-center gap-2">
        <button onClick={onRetry} className="inline-flex items-center gap-1.5 h-9 px-4 rounded-lg text-[12px] font-medium" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}><RefreshCw size={13} />重新加载</button>
        <a href={withToken(downloadUrl)} download className="inline-flex items-center gap-1.5 h-9 px-4 rounded-lg text-[12px] font-medium text-white" style={{ background: "var(--accent)" }}><Download size={13} />{downloadLabel}</a>
      </div>
    </div>
  );
}

function DocxPreview({ filename, downloadUrl }: { filename: string; downloadUrl: string }) {
  const [html, setHtml] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    const location = fileLocation(downloadUrl, filename);
    if (!location.filename) { setLoading(false); setError("无法识别 Word 文档"); return; }
    let active = true;
    const apply = (data: FilePreviewData) => {
      if (!active) return;
      if (data.rich?.kind === "html" && data.rich.html) { setHtml(data.rich.html); setError(""); }
      else { setHtml(null); setError(data.error || "服务器没有返回可预览的 Word 内容"); }
      setLoading(false);
    };
    setLoading(true); setError(""); setHtml(null);
    const unsubscribe = onPreviewFileUpdated(location.filename, location.convId, apply);
    previewFile(location.filename, location.convId).then(apply).catch((reason: unknown) => {
      if (!active) return;
      setLoading(false); setError(reason instanceof Error ? reason.message : "Word 预览加载失败");
    });
    return () => { active = false; unsubscribe(); };
  }, [downloadUrl, filename, retryKey]);

  if (loading) return (
    <div className="mx-auto max-w-[760px] rounded-xl p-8 min-h-[460px] shadow-sm" style={{ background: "#fff", border: "1px solid var(--border)" }}>
      <div className="skeleton-shimmer h-7 w-[45%] mx-auto mb-9" />
      <div className="space-y-4"><div className="skeleton-shimmer h-4 w-full" /><div className="skeleton-shimmer h-4 w-[92%]" /><div className="skeleton-shimmer h-4 w-[85%]" /><div className="skeleton-shimmer h-4 w-[96%]" /></div>
    </div>
  );
  if (error || !html) return <PreviewFailure title="Word 文档暂时无法预览" detail={error || "预览内容为空"} downloadUrl={downloadUrl} downloadLabel="下载 DOCX" onRetry={() => setRetryKey(v => v + 1)} />;

  return (
    <div className="mx-auto max-w-[820px] pb-4">
      <div className="mb-3 flex items-center justify-between text-[11px]" style={{ color: "var(--text-tertiary)" }}><span>只读预览</span><span className="truncate max-w-[260px]">{filename}</span></div>
      <article className="min-h-[560px] rounded-sm px-[8%] py-[7%] shadow-md overflow-hidden" style={{ background: "#fff", color: "#111827", border: "1px solid rgba(15,23,42,.10)" }}>
        <div className="docx-reader-content" dangerouslySetInnerHTML={{ __html: html }} />
      </article>
    </div>
  );
}

function PdfPreview({ filename, downloadUrl }: { filename: string; downloadUrl: string }) {
  const inlineUrl = withToken(`${downloadUrl}${downloadUrl.includes("?") ? "&" : "?"}inline=1`);
  return (
    <div className="h-full min-h-[560px] flex flex-col rounded-xl overflow-hidden" style={{ background: "#e9edf2", border: "1px solid var(--border)" }}>
      <div className="h-9 px-3 flex items-center justify-between text-[11px] flex-shrink-0" style={{ background: "var(--bg-primary)", borderBottom: "1px solid var(--border)", color: "var(--text-tertiary)" }}><span>PDF 只读预览</span><span className="truncate max-w-[260px]">{filename}</span></div>
      <iframe src={inlineUrl} title={`${filename} PDF 预览`} className="flex-1 w-full border-0" />
    </div>
  );
}

function ImagePreview({ filename, downloadUrl }: { filename: string; downloadUrl: string }) {
  return (
    <div className="min-h-[480px] rounded-xl p-6 flex items-center justify-center" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={withToken(downloadUrl)} alt={filename} className="max-w-full max-h-[72vh] object-contain rounded-lg shadow-sm" />
    </div>
  );
}

function XlsxPreview({ downloadUrl }: { downloadUrl: string }) {
  const [data, setData] = useState<{ sheets: Array<{ name: string; headers: string[]; rows: string[][]; total_rows: number }> } | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeSheet, setActiveSheet] = useState(0);

  useEffect(() => {
    const filename = decodeURIComponent(downloadUrl.split("/").pop() || "");
    const convId = downloadUrl.includes("/conversations/") ? downloadUrl.split("/conversations/")[1].split("/")[0] : undefined;
    if (!filename) { setLoading(false); return; }
    previewFile(filename, convId)
      .then((r: any) => { if (r?.rich?.kind === "sheets") setData({ sheets: r.rich.sheets || [] }); setLoading(false); })
      .catch(() => setLoading(false));
  }, [downloadUrl]);

  if (loading) return (
    <div className="space-y-2 py-8">
      <div className="skeleton-shimmer h-8 w-full" />
      <div className="skeleton-shimmer h-6 w-full" />
      <div className="skeleton-shimmer h-6 w-full" />
      <div className="skeleton-shimmer h-6 w-[80%]" />
    </div>
  );

  if (data?.sheets && data.sheets.length > 0) {
    const sheet = data.sheets[activeSheet] || data.sheets[0];
    return (
      <div>
        {/* Sheet tabs */}
        {data.sheets.length > 1 && (
          <div className="flex gap-1 mb-3 overflow-x-auto">
            {data.sheets.map((s, i) => (
              <button key={i} onClick={() => setActiveSheet(i)}
                className="px-3 py-1 rounded-lg text-[11px] font-medium transition-colors flex-shrink-0"
                style={{
                  background: i === activeSheet ? "var(--accent-light)" : "var(--bg-tertiary)",
                  color: i === activeSheet ? "var(--accent)" : "var(--text-tertiary)",
                }}>
                {s.name}
              </button>
            ))}
          </div>
        )}
        {/* Table */}
        <div className="rounded-lg overflow-auto" style={{ border: "1px solid var(--border)", maxHeight: 400 }}>
          <table className="w-full text-[12px]" style={{ borderCollapse: "collapse" }}>
            <thead>
              <tr>
                {sheet.headers.map((h, i) => (
                  <th key={i} className="text-left px-3 py-2 font-semibold whitespace-nowrap sticky top-0"
                    style={{ background: "var(--bg-secondary)", borderBottom: "2px solid var(--border)", color: "var(--text-secondary)" }}>
                    {h || `列${i + 1}`}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sheet.rows.slice(0, 50).map((row, ri) => (
                <tr key={ri}>
                  {row.map((cell, ci) => (
                    <td key={ci} className="px-3 py-1.5" style={{ borderBottom: "1px solid var(--border-light)", color: "var(--text-primary)" }}>
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between mt-2">
          <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
            {sheet.total_rows} 行 × {sheet.headers.length} 列
            {sheet.rows.length < sheet.total_rows ? ` (显示前 ${sheet.rows.length} 行)` : ""}
          </span>
          <a href={withToken(downloadUrl)} download
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium"
            style={{ background: "#059669", color: "#fff" }}>
            <Download size={13} /> 下载 XLSX
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center py-8">
      <Table2 size={48} style={{ color: "#059669", opacity: 0.4 }} className="mb-3" />
      <p className="text-[13px] mb-4" style={{ color: "var(--text-secondary)" }}>
        无法预览此表格
      </p>
      <a href={withToken(downloadUrl)} download
        className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-[13px] font-medium"
        style={{ background: "#059669", color: "#fff" }}>
        <Download size={15} /> 下载 XLSX
      </a>
    </div>
  );
}

// ── V229 版本对比：HTML→纯文本行 + LCS 行级 diff（cap 800 行防爆） ──
function htmlToLines(html: string): string[] {
  try {
    const doc = new DOMParser().parseFromString(html || "", "text/html");
    const t = (doc.body?.innerText || "").replace(/\u00a0/g, " ");
    return t.split(/\n+/).map(x => x.trim()).filter(Boolean).slice(0, 800);
  } catch { return []; }
}
function computeDiff(oldHtml: string, newHtml: string): { t: "=" | "-" | "+"; s: string }[] {
  const a = htmlToLines(oldHtml), b = htmlToLines(newHtml);
  const n = a.length, m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: { t: "=" | "-" | "+"; s: string }[] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push({ t: "=", s: a[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ t: "-", s: a[i] }); i++; }
    else { out.push({ t: "+", s: b[j] }); j++; }
  }
  while (i < n) { out.push({ t: "-", s: a[i++] }); }
  while (j < m) { out.push({ t: "+", s: b[j++] }); }
  return out.length ? out : [{ t: "=", s: "（两版文本一致或均为空）" }];
}

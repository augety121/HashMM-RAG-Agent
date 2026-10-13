"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowRight, BarChart3, CheckSquare, FileText, LayoutTemplate,
  Loader2, MessageSquareText, Plus, RefreshCw, Scale, Sparkles, Users,
} from "lucide-react";
import {
  createConversation, getCanvasTemplate, listCanvasTemplates, listConvFiles,
  saveConvFile,
} from "@/lib/api";
import { openArtifact } from "@/lib/artifact";
import { canvasTemplateHtml, type CanvasTplKind } from "@/lib/canvasTemplate";
import { useStore } from "@/lib/store";
import { Badge, PageHeader, PanelShell } from "./ui/PanelKit";

type Starter = {
  kind: CanvasTplKind;
  title: string;
  description: string;
  prompt: string;
  icon: typeof LayoutTemplate;
};

const STARTERS: Starter[] = [
  {
    kind: "blank",
    title: "自由创作",
    description: "从空白页开始写作、整理或设计",
    prompt: "新画布",
    icon: Plus,
  },
  {
    kind: "progress",
    title: "整理一项工作",
    description: "把进展、决定和下一步放到一页",
    prompt: "工作进展",
    icon: CheckSquare,
  },
  {
    kind: "compare",
    title: "比较并做决定",
    description: "并排比较选择，留下结论和理由",
    prompt: "方案对比",
    icon: Scale,
  },
  {
    kind: "data",
    title: "呈现数据",
    description: "把数字和结论整理成清晰看板",
    prompt: "数据看板",
    icon: BarChart3,
  },
  {
    kind: "warroom",
    title: "共同完成",
    description: "记录目标、分工、交接与验收",
    prompt: "协作成果",
    icon: Users,
  },
];

function canvasFileName(title: string): string {
  const date = new Date();
  const pad = (value: number) => String(value).padStart(2, "0");
  const safeTitle = title.replace(/[\\/:*?"<>|]/g, " ").replace(/\s+/g, " ").trim().slice(0, 48) || "新画布";
  return `${safeTitle}-${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}.html`;
}

function displayCanvasName(filename: string): string {
  return filename
    .replace(/\.html?$/i, "")
    .replace(/-\d{4}-\d{4}$/, "")
    .replace(/[_-]+/g, " ")
    .trim() || "未命名画布";
}

function fileIcon(filename: string) {
  if (/数据|看板|报表/i.test(filename)) return BarChart3;
  if (/对比|比较|选择/i.test(filename)) return Scale;
  if (/协作|共同|团队/i.test(filename)) return Users;
  return FileText;
}

export function CanvasHomeView() {
  const sid = useStore(s => s.sid);
  const sessions = useStore(s => s.sessions);
  const set = useStore(s => s.set);
  const addSession = useStore(s => s.addSession);
  const currentConversation = sessions.find(item => item.id === sid);
  const recentConversationKey = useMemo(() => [...sessions]
    .filter(item => !item.archived)
    .sort((a, b) => (b.updated_at || b.created) - (a.updated_at || a.created))
    .slice(0, 8)
    .map(item => item.id)
    .join(","), [sessions]);
  const [recentFiles, setRecentFiles] = useState<Array<{
    convId: string;
    conversationTitle: string;
    filename: string;
    download_url?: string;
  }>>([]);
  const [templates, setTemplates] = useState<Array<{ id: string; name: string; created: number }>>([]);
  const [orgTemplates, setOrgTemplates] = useState<Array<{ id: string; name: string; by: string }>>([]);
  const [busy, setBusy] = useState(true);
  const [creating, setCreating] = useState("");
  const [error, setError] = useState("");
  const [selectedKind, setSelectedKind] = useState<CanvasTplKind>("blank");
  const [title, setTitle] = useState("");
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [selectedArtifactKey, setSelectedArtifactKey] = useState("");

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const recentConversations = [...useStore.getState().sessions]
        .filter(item => !item.archived)
        .sort((a, b) => {
          if (a.id === sid) return -1;
          if (b.id === sid) return 1;
          return (b.updated_at || b.created) - (a.updated_at || a.created);
        })
        .slice(0, 8);
      const [templateResult, fileResults] = await Promise.all([
        listCanvasTemplates().catch(() => ({ items: [], org_items: [] })),
        Promise.all(recentConversations.map(async conversation => ({
          conversation,
          result: await listConvFiles(conversation.id).catch(() => ({ files: [] })),
        }))),
      ]);
      setTemplates(templateResult.items || []);
      setOrgTemplates(templateResult.org_items || []);
      setRecentFiles(fileResults.flatMap(({ conversation, result }) =>
        (result.files || [])
          .filter(file => String(file.filename || "").toLowerCase().endsWith(".html"))
          .slice(-8)
          .reverse()
          .map(file => ({
            convId: conversation.id,
            conversationTitle: conversation.title || "对话",
            filename: String(file.filename),
            download_url: String(file.download_url || ""),
          })),
      ).slice(0, 24));
      setError("");
    } catch (cause) {
      setError((cause as Error)?.message || "画布暂时无法更新");
    } finally {
      setBusy(false);
    }
  }, [recentConversationKey, sid]);

  useEffect(() => { void refresh(); }, [refresh]);

  const ensureConversation = async (conversationTitle: string): Promise<string> => {
    const current = useStore.getState().sid;
    if (current) return current;
    const id = `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
    await createConversation(id, conversationTitle);
    addSession({ id, title: conversationTitle, messages: [], created: Date.now() });
    set({ sid: id });
    return id;
  };

  const createCanvas = async (kind: CanvasTplKind, canvasTitle: string, html?: string) => {
    if (creating) return;
    const finalTitle = canvasTitle.trim() || STARTERS.find(item => item.kind === kind)?.prompt || "新画布";
    setCreating(finalTitle);
    setError("");
    const filename = canvasFileName(finalTitle);
    try {
      const convId = await ensureConversation(finalTitle);
      const result = await saveConvFile(convId, filename, html || canvasTemplateHtml(kind));
      set({ artifactDraft: null, desktopView: null });
      openArtifact(convId, { filename, download_url: result.download_url });
      setTitle("");
    } catch (cause) {
      const content = html || canvasTemplateHtml(kind);
      set({ artifactDraft: { filename, content }, desktopView: null });
      openArtifact(useStore.getState().sid, { filename, download_url: "" });
      setError(`服务器暂时不可用，已打开本地草稿：${(cause as Error)?.message || "尚未保存"}`);
    } finally {
      setCreating("");
    }
  };

  const installed = useMemo(
    () => [...templates.map(item => ({ ...item, by: "" })), ...orgTemplates],
    [orgTemplates, templates],
  );
  const selectedStarter = STARTERS.find(item => item.kind === selectedKind) || STARTERS[0];
  const selectedFile = recentFiles.find(file => `${file.convId}:${file.filename}` === selectedArtifactKey) || recentFiles[0] || null;

  return (
    <PanelShell className="canvas-home-shell">
      <div className="mx-auto w-full max-w-[1040px]">
        <PageHeader
          icon={LayoutTemplate}
          title="画布"
          subtitle="把 Chat 里的结果变成可继续编辑、预览和交付的工作成果；每次修改都回到原会话。"
          actions={<div className="flex items-center gap-2">
            <Badge tone={sid ? "accent" : "neutral"}>{sid ? "当前会话" : "从新会话开始"}</Badge>
            <button
              type="button"
              onClick={() => set({ desktopView: null })}
              className="workspace-secondary-action"
              title="回到当前 Chat，继续提出修改要求"
            >
              <MessageSquareText size={12} /> 回到对话
            </button>
            <button onClick={() => setCreateOpen(true)} className="workspace-primary-action"><Plus size={13} /> 新建画布</button>
            <button onClick={() => void refresh()} className="rounded-xl p-2 hover:bg-[var(--bg-tertiary)]" aria-label="刷新画布"><RefreshCw size={14} className={busy ? "animate-spin" : ""} /></button>
          </div>}
        />
        {error && <div className="mb-4 rounded-xl px-3 py-2 text-[10px]" style={{ color: "#b42318", background: "rgba(180,35,24,.06)", border: "1px solid rgba(180,35,24,.16)" }}>{error}</div>}
        <div className="overflow-hidden rounded-2xl workspace-object-shell" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
          <aside className="workspace-object-list">
            <div className="flex items-center gap-2 px-3 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
              <div className="min-w-0 flex-1"><div className="text-[11.5px] font-semibold" style={{ color: "var(--text-primary)" }}>最近画布</div><div className="text-[9px]" style={{ color: "var(--text-tertiary)" }}>{recentFiles.length} 份成果</div></div>
              <button onClick={() => setTemplatesOpen(value => !value)} className="rounded-lg p-1.5 hover:bg-[var(--bg-tertiary)]" title="模板"><LayoutTemplate size={13} /></button>
            </div>
            {templatesOpen && <div className="p-2" style={{ borderBottom: "1px solid var(--border)" }}>
              <div className="px-2 py-1 text-[9px] font-semibold" style={{ color: "var(--text-tertiary)" }}>模板</div>
              {installed.slice(0, 8).map(template => <button key={`${template.by || "personal"}:${template.id}`} onClick={async () => { try { const detail = await getCanvasTemplate(template.id); await createCanvas("blank", detail.name, detail.html); } catch (cause) { setError((cause as Error)?.message || "模板暂时无法打开"); } }} className="workspace-object-row"><FileText size={13} /><span className="min-w-0 flex-1 truncate text-[10px]">{template.name}</span></button>)}
              {!installed.length && <div className="px-2 py-3 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>成熟画布可保存为模板。</div>}
            </div>}
            <div className="max-h-[570px] overflow-y-auto p-2">
              {recentFiles.map(file => {
                const Icon = fileIcon(file.filename);
                const artifactKey = `${file.convId}:${file.filename}`;
                return <button key={artifactKey} onClick={() => setSelectedArtifactKey(artifactKey)} className="workspace-object-row" data-selected={selectedFile ? `${selectedFile.convId}:${selectedFile.filename}` === artifactKey : false}><span className="workspace-object-icon"><Icon size={14} /></span><span className="min-w-0 flex-1"><span className="block truncate text-[10.8px] font-medium" style={{ color: "var(--text-primary)" }}>{displayCanvasName(file.filename)}</span><span className="block truncate text-[9px]" style={{ color: "var(--text-tertiary)" }}>{file.conversationTitle}</span></span></button>;
              })}
              {!recentFiles.length && <div className="px-3 py-10 text-center text-[10px]" style={{ color: "var(--text-tertiary)" }}><LayoutTemplate size={22} className="mx-auto mb-2" />还没有画布</div>}
            </div>
          </aside>
          <section className="workspace-object-detail">
            {selectedFile ? (
              <div className="flex min-h-[560px] flex-col">
                <div className="flex items-start gap-4 px-6 py-5" style={{ borderBottom: "1px solid var(--border)" }}>
                  <span className="flex h-10 w-10 items-center justify-center rounded-xl" style={{ color: "var(--accent)", background: "var(--accent-light)" }}><Sparkles size={18} /></span>
                  <div className="min-w-0 flex-1"><h2 className="truncate text-[17px] font-semibold" style={{ color: "var(--text-primary)" }}>{displayCanvasName(selectedFile.filename)}</h2><p className="mt-1 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>来自「{selectedFile.conversationTitle}」</p></div>
                  <button onClick={() => { set({ desktopView: null, sid: selectedFile.convId }); openArtifact(selectedFile.convId, { filename: selectedFile.filename, download_url: selectedFile.download_url || `/api/conversations/${selectedFile.convId}/download/${encodeURIComponent(selectedFile.filename)}` }); }} className="workspace-primary-action">打开编辑 <ArrowRight size={12} /></button>
                </div>
                <div className="grid flex-1 place-items-center p-8 text-center">
                  <div className="max-w-[440px]"><MessageSquareText size={28} className="mx-auto" style={{ color: "var(--accent)" }} /><h3 className="mt-4 text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>继续这份成果</h3><p className="mt-2 text-[10.5px] leading-5" style={{ color: "var(--text-tertiary)" }}>回到原对话继续修改，或在右侧直接打开成果。HashMM 会沿用原来的资料与讨论，不从头生成。</p><div className="mt-5 flex justify-center gap-2"><button onClick={() => startCanvasChat(set, displayCanvasName(selectedFile.filename), selectedFile.convId)} className="workspace-secondary-action">回到原对话</button><button onClick={() => { set({ desktopView: null, sid: selectedFile.convId }); openArtifact(selectedFile.convId, { filename: selectedFile.filename, download_url: selectedFile.download_url || "" }); }} className="workspace-primary-action">查看成果</button></div></div>
                </div>
              </div>
            ) : (
              <div className="flex min-h-[560px] flex-col items-center justify-center px-8 text-center"><Sparkles size={27} style={{ color: "var(--accent)" }} /><h2 className="mt-4 text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>从对话里长出来的成果</h2><p className="mt-2 max-w-[390px] text-[10.5px] leading-5" style={{ color: "var(--text-tertiary)" }}>可以直接新建，也可以在 Chat 中说“做成画布”。HashMM 会保留来源、修改记录和可验收版本。</p><button onClick={() => setCreateOpen(true)} className="workspace-primary-action mt-4"><Plus size={12} /> 创建画布</button></div>
            )}
          </section>
        </div>
        {createOpen && <div className="workspace-sheet-backdrop" onMouseDown={() => setCreateOpen(false)}><div className="workspace-sheet workspace-sheet-wide" onMouseDown={event => event.stopPropagation()}>
          <div className="flex items-start gap-3"><div className="min-w-0 flex-1"><h2 className="text-[16px] font-semibold" style={{ color: "var(--text-primary)" }}>新建画布</h2><p className="mt-1 text-[10px]" style={{ color: "var(--text-tertiary)" }}>{sid ? `保存到「${currentConversation?.title || "当前对话"}」` : "会自动建立一段对话"}</p></div><button onClick={() => setCreateOpen(false)}><Plus size={15} className="rotate-45" /></button></div>
          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">{STARTERS.map(item => { const Icon = item.icon; return <button key={item.kind} onClick={() => { setSelectedKind(item.kind); if (!title.trim()) setTitle(item.prompt); }} className="canvas-purpose-card" data-active={selectedKind === item.kind}><Icon size={15} /><span><strong>{item.title}</strong><small>{item.description}</small></span></button>; })}</div>
          <input value={title} onChange={event => setTitle(event.target.value)} maxLength={64} placeholder={`给这份${selectedStarter.title}起个名字`} className="workspace-field mt-4" />
          <div className="mt-4 flex justify-end gap-2"><button onClick={() => setCreateOpen(false)} className="workspace-secondary-action">取消</button><button onClick={async () => { await createCanvas(selectedKind, title || selectedStarter.prompt); setCreateOpen(false); }} disabled={Boolean(creating)} className="workspace-primary-action">{creating ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />} 创建并打开</button></div>
        </div></div>}
      </div>
    </PanelShell>
  );
}

function startCanvasChat(set: ReturnType<typeof useStore.getState>["set"], name: string, convId: string) {
  set({ sid: convId, desktopView: null, pendingPrompt: `请继续完善画布「${name}」。先读取现有内容和实际证据，再问我希望修改的部分；不要重新从头生成。` });
}

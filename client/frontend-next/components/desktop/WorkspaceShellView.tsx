"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle, ArrowRight, BookOpen, CheckCircle2, Clock3, FileText,
  Download, FolderKanban, Laptop, Loader2, PackageOpen, Plus, RefreshCw, ShieldCheck,
  Search, Sparkles, Target, WifiOff, X, ClipboardCheck,
  LibraryBig, PlayCircle, UploadCloud,
} from "lucide-react";
import {
  applyOkfPack,
  createWorkProject,
  getWorkspaceSnapshot,
  listKnowledgeDocuments,
  listOkfPacks,
  listWorkProjects,
  okfExportUrl,
  previewOkfPack,
  subscribeWorkspaceSnapshot,
  type OkfPack,
  type OkfPreview,
  type WorkProject,
  type WorkspaceRunV2,
  type WorkspaceSnapshotV2,
} from "@/lib/api";
import { useStore } from "@/lib/store";
import { getProjectDesktop } from "@/lib/desktop";
import {
  readAccountProjects,
  writeAccountProjects,
  readActiveProject,
  writeActiveProject,
  readProjectSources,
  writeProjectSources,
} from "@/lib/accountWorkspaceCache";
import { createFeatureContext } from "@/lib/chatContext";

type WorkspaceSection = "today" | "projects" | "library" | "devices" | "results";

const STATE_LABELS: Record<string, string> = {
  draft: "草稿",
  planned: "已规划",
  ready: "待开始",
  running: "进行中",
  waiting_user: "需要补充",
  waiting_approval: "等待确认",
  blocked: "暂时受阻",
  review: "等待验收",
  accepted: "已验收",
  change_requested: "修改中",
  completed: "已完成",
  failed: "未完成",
  cancelled: "已取消",
  interrupted: "已中断",
  observed: "历史记录",
};

function ago(ts: number): string {
  if (!ts) return "尚无时间";
  const delta = Math.max(0, Date.now() / 1000 - ts);
  if (delta < 60) return "刚刚";
  if (delta < 3600) return `${Math.floor(delta / 60)} 分钟前`;
  if (delta < 86400) return `${Math.floor(delta / 3600)} 小时前`;
  return new Date(ts * 1000).toLocaleDateString();
}

function folderLabel(sourcePath: string): string {
  const parts = String(sourcePath || "").split(/[\\/]+/).filter(Boolean);
  return parts[parts.length - 1] || sourcePath;
}

function RunRow({
  run,
  open,
  emphasized = false,
}: {
  run: WorkspaceRunV2;
  open: (run: WorkspaceRunV2) => void;
  emphasized?: boolean;
}) {
  const evidenceCount = Number(run.evidence?.summary?.evidence_count || 0);
  return (
    <button
      onClick={() => open(run)}
      className="group w-full flex items-center gap-3.5 px-4 py-3.5 text-left transition-colors hover:bg-[var(--bg-secondary)]"
      style={{ borderBottom: "1px solid var(--border)" }}
    >
      <span
        className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
        style={{
          color: emphasized ? "#b45309" : "var(--text-secondary)",
          background: emphasized ? "color-mix(in srgb, #f59e0b 12%, transparent)" : "var(--bg-secondary)",
        }}
      >
        {emphasized ? <AlertCircle size={17} /> : run.artifacts.length ? <FileText size={17} /> : <Clock3 size={17} />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="truncate text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
            {run.title || run.contract.goal || "一项工作"}
          </span>
          <span
            className="flex-shrink-0 rounded-full px-2 py-0.5 text-[9.5px]"
            style={{
              color: emphasized ? "#b45309" : "var(--text-tertiary)",
              background: emphasized ? "color-mix(in srgb, #f59e0b 11%, transparent)" : "var(--bg-tertiary)",
            }}
          >
            {STATE_LABELS[run.state] || "状态更新"}
          </span>
        </span>
        <span className="mt-1 block truncate text-[11px]" style={{ color: "var(--text-tertiary)" }}>
          {run.current_step || run.next_action || "打开查看最新记录"}
        </span>
        <span className="mt-1.5 flex items-center gap-3 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>
          <span>{ago(run.updated_at)}</span>
          <span>{run.artifacts.length} 个成果</span>
          <span>{evidenceCount} 条证据</span>
        </span>
      </span>
      <ArrowRight
        size={14}
        className="flex-shrink-0 opacity-40 transition-all group-hover:translate-x-0.5 group-hover:opacity-100"
        style={{ color: "var(--text-tertiary)" }}
      />
    </button>
  );
}

function SectionCard({
  title,
  subtitle,
  children,
  action,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <section
      className="overflow-hidden rounded-2xl"
      style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}
    >
      <div className="flex items-center gap-3 px-4 py-3.5" style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="min-w-0 flex-1">
          <h2 className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{title}</h2>
          <p className="mt-0.5 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{subtitle}</p>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function WorkspaceShellView({ section }: { section: WorkspaceSection }) {
  const user = useStore(s => s.user);
  const token = useStore(s => s.token);
  const set = useStore(s => s.set);
  const [snapshot, setSnapshot] = useState<WorkspaceSnapshotV2 | null>(null);
  const [documents, setDocuments] = useState<Array<Record<string, unknown>>>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [streamState, setStreamState] = useState<"connecting" | "open" | "fallback">("connecting");
  const [projectFormOpen, setProjectFormOpen] = useState(false);
  const [creatingProject, setCreatingProject] = useState(false);
  const [projectSourceFolders, setProjectSourceFolders] = useState<string[]>([]);
  const [selectedProjectSources, setSelectedProjectSources] = useState<string[]>([]);
  const [libraryQuery, setLibraryQuery] = useState("");
  const [libraryType, setLibraryType] = useState<"all" | "document" | "pdf" | "slides" | "sheet">("all");
  const [selectedDocuments, setSelectedDocuments] = useState<string[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState(() => readActiveProject(useStore.getState().user));
  const [accountProjects, setAccountProjects] = useState<WorkProject[]>(() => readAccountProjects(useStore.getState().user));
  const [selectedDocumentName, setSelectedDocumentName] = useState("");
  const [libraryDetailOpen, setLibraryDetailOpen] = useState(false);
  const [okfPacks, setOkfPacks] = useState<OkfPack[]>([]);
  const [okfPreview, setOkfPreview] = useState<OkfPreview | null>(null);
  const [okfBusy, setOkfBusy] = useState(false);
  const okfInput = useRef<HTMLInputElement | null>(null);
  const [projectDraft, setProjectDraft] = useState({
    name: "",
  });
  const refreshInFlight = useRef<Promise<void> | null>(null);

  useEffect(() => {
    setSelectedProjectId(readActiveProject(user));
    setAccountProjects(readAccountProjects(user));
  }, [user?.id, user?.username]);

  useEffect(() => {
    setSelectedProjectSources(selectedProjectId ? readProjectSources(user, selectedProjectId) : []);
  }, [selectedProjectId, user?.id, user?.username]);

  const refresh = useCallback(async (force = false) => {
    if (!token) {
      setSnapshot(null);
      setBusy(false);
      return;
    }
    if (refreshInFlight.current) return refreshInFlight.current;
    const work = (async () => {
      try {
        const [next, freshProjects] = await Promise.all([
          getWorkspaceSnapshot("personal", force),
          listWorkProjects().catch(() => null),
        ]);
        setSnapshot(next);
        if (freshProjects) {
          setAccountProjects(freshProjects);
          writeAccountProjects(user, freshProjects);
        }
        if (section === "library") {
          const [result, packs] = await Promise.all([
            listKnowledgeDocuments(),
            listOkfPacks().catch(() => ({ packs: [] as OkfPack[] })),
          ]);
          setDocuments(result.documents || []);
          setOkfPacks(packs.packs || []);
        }
        setError("");
      } catch (cause) {
        setError((cause as Error)?.message || "工作空间暂时无法更新");
      } finally {
        setBusy(false);
        refreshInFlight.current = null;
      }
    })();
    refreshInFlight.current = work;
    return work;
  }, [section, token, user]);

  useEffect(() => {
    let disposed = false;
    let unsubscribe = () => {};
    void refresh().then(() => {
      if (disposed || !token) return;
      unsubscribe = subscribeWorkspaceSnapshot({
        workspaceId: "personal",
        afterCursor: snapshot?.sync.high_water_cursor || 0,
        onState: setStreamState,
        // Stream payloads are deltas. Re-read the complete ETag projection so
        // older runs are never accidentally dropped from the visible workspace.
        onSnapshot: () => { void refresh(true); },
      });
    });
    const timer = window.setInterval(() => { void refresh(false); }, 60_000);
    const online = () => { void refresh(true); };
    window.addEventListener("hmm-backend-online", online);
    return () => {
      disposed = true;
      unsubscribe();
      window.clearInterval(timer);
      window.removeEventListener("hmm-backend-online", online);
    };
    // snapshot is deliberately excluded: reconnecting the stream for every
    // delta would create an owner-authenticated polling loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh, token]);

  const openRun = (run: WorkspaceRunV2) => {
    set({
      desktopView: "work-detail",
      workDetailId: run.id,
      workDetailParent: section === "results" ? "work-results" : "work-active",
    });
  };

  const startChat = (prompt = "") => {
    useStore.getState().newChat();
    set({ desktopView: null, pendingPrompt: prompt });
  };

  const startWithDocuments = (
    kind: "ask" | "compare" | "deliver",
    documentNames: string[] = selectedDocuments,
  ) => {
    const selected = Array.from(new Set(documentNames.map(name => String(name || "").trim()).filter(Boolean)));
    if (!selected.length) return;
    const sourceList = selected.map(name => `《${name}》`).join("、");
    useStore.getState().attachFeatureContext(createFeatureContext(
      "document",
      `已选资料（${selected.length}）`,
      { instruction: "仅在这些资料范围内检索；若资料不足，明确说明缺口，不得静默扩大到整个资料库。", documents: selected },
      "workspace-library",
      selected,
    ));
    const prompt = kind === "ask"
      ? `请只基于我选择的资料 ${sourceList} 回答下面的问题，并为关键结论标明实际依据：`
      : kind === "compare"
        ? `请比较我选择的资料 ${sourceList}，列出一致点、差异、冲突与仍需核实的信息，并标明实际依据。`
        : `请基于我选择的资料 ${sourceList} 生成可交付成果。先和我确认交付格式及验收标准，再开始制作；所有事实都要能追溯到资料。`;
    startChat(prompt);
  };

  const createProject = async () => {
    const name = projectDraft.name.trim();
    if (!name || creatingProject) return;
    setCreatingProject(true);
    try {
      const created = await createWorkProject({
        name,
        goal: "",
        deliverable: "",
        success_criteria: [],
        permission_mode: "ask",
      });
      const nextProject = created.project;
      const nextProjects = [
        nextProject,
        ...accountProjects.filter(project => project.id !== nextProject.id),
      ];
      setAccountProjects(nextProjects);
      writeAccountProjects(user, nextProjects);
      setSelectedProjectId(nextProject.id);
      writeActiveProject(user, created.id);
      writeProjectSources(user, created.id, projectSourceFolders);
      setSelectedProjectSources(projectSourceFolders);
      if (projectSourceFolders[0]) {
        const bridge = getProjectDesktop();
        if (bridge) {
          const activated = await bridge.activateSource(projectSourceFolders[0]);
          if (!activated.ok) setError(activated.error || "项目已创建，但资料目录暂时无法启用");
        }
      }
      setProjectDraft({ name: "" });
      setProjectSourceFolders([]);
      setProjectFormOpen(false);
      void refresh(true);
      useStore.getState().newChat();
    } catch (cause) {
      setError((cause as Error)?.message || "项目没有创建");
    } finally {
      setCreatingProject(false);
    }
  };

  const chooseProjectFolders = async () => {
    const bridge = getProjectDesktop();
    if (!bridge) {
      setError("资料文件夹只能在 HashMM 桌面端选择；网页版仍可先创建云端项目。");
      return;
    }
    const result = await bridge.pickSourceFolders();
    if (result.ok && result.paths?.length) {
      setProjectSourceFolders(current => Array.from(new Set([...current, ...result.paths!])).slice(0, 8));
      setError("");
    } else if (!result.canceled) {
      setError(result.error || "没有选中可用的资料文件夹");
    }
  };

  const selectProject = async (project: WorkProject) => {
    setSelectedProjectId(project.id);
    writeActiveProject(user, project.id);
    const sources = readProjectSources(user, project.id);
    setSelectedProjectSources(sources);
    if (sources[0]) {
      const result = await getProjectDesktop()?.activateSource(sources[0]);
      if (result && !result.ok) setError(result.error || "项目资料目录暂时无法启用");
    }
  };

  const inspectOkf = async (file?: File) => {
    if (!file || okfBusy) return;
    setOkfBusy(true);
    try {
      const result = await previewOkfPack(file);
      setOkfPreview(result.preview);
      setError("");
    } catch (cause) {
      setError((cause as Error)?.message || "知识包预检失败");
    } finally {
      setOkfBusy(false);
      if (okfInput.current) okfInput.current.value = "";
    }
  };

  const confirmOkf = async () => {
    if (!okfPreview || okfBusy) return;
    setOkfBusy(true);
    try {
      await applyOkfPack(okfPreview.preview_id);
      setOkfPreview(null);
      await refresh(true);
    } catch (cause) {
      setError((cause as Error)?.message || "知识包没有导入");
    } finally {
      setOkfBusy(false);
    }
  };

  const title = {
    today: ["今天", "继续正在做的事，优先处理需要你确认的事项。"],
    projects: ["项目", "每个项目保留目标、资料、对话、成果与验收标准。"],
    library: ["资料库", "这里的资料可以直接用于对话、检索和交付。"],
    devices: ["设备接力", "在电脑与手机之间继续同一项工作。"],
    results: ["成果", "集中查看已生成的内容、证据和验收状态。"],
  }[section];

  const visibleRuns = useMemo(() => {
    if (!snapshot) return [];
    if (section === "results") return snapshot.today.recent_results;
    return snapshot.today.in_progress;
  }, [section, snapshot]);
  const visibleDocuments = useMemo(() => {
    const q = libraryQuery.trim().toLowerCase();
    return documents.filter(doc => {
      const name = String(doc.filename || doc.name || doc.title || "").toLowerCase();
      if (q && !name.includes(q)) return false;
      if (libraryType === "all") return true;
      if (libraryType === "pdf") return name.endsWith(".pdf");
      if (libraryType === "slides") return /\.(ppt|pptx|odp)$/.test(name);
      if (libraryType === "sheet") return /\.(xls|xlsx|csv|tsv|ods)$/.test(name);
      return !/\.(pdf|ppt|pptx|odp|xls|xlsx|csv|tsv|ods)$/.test(name);
    });
  }, [documents, libraryQuery, libraryType]);
  const focusRun = snapshot?.today.needs_user[0] || snapshot?.today.in_progress[0] || null;
  const snapshotProjects = (snapshot?.projects || []) as unknown as WorkProject[];
  const projects = accountProjects.length ? accountProjects : snapshotProjects;
  const selectedProject = projects.find(project => project.id === selectedProjectId)
    || projects[0]
    || null;
  const selectedDocument = visibleDocuments.find((doc, index) => {
    const name = String(doc.filename || doc.name || doc.title || `资料 ${index + 1}`);
    return name === selectedDocumentName;
  }) || visibleDocuments[0] || null;

  return (
    <div className="flex-1 min-h-0 overflow-y-auto" style={{ background: "var(--bg-secondary)" }}>
      <div className="mx-auto w-full max-w-[1040px] px-7 py-7">
        <header className="flex items-start gap-4">
          <div className="min-w-0 flex-1">
            <h1 className="text-[24px] font-semibold tracking-[-0.025em]" style={{ color: "var(--text-primary)" }}>{title[0]}</h1>
            <p className="mt-1 text-[12px] leading-5" style={{ color: "var(--text-tertiary)" }}>{title[1]}</p>
            <div className="mt-3 flex flex-wrap items-center gap-2" aria-label="工作区状态">
              <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px]"
                style={{ color: streamState === "open" ? "var(--success, #15803d)" : "var(--text-secondary)", background: streamState === "open" ? "color-mix(in srgb, var(--success) 10%, transparent)" : "var(--bg-primary)", border: "1px solid var(--border)" }}>
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: streamState === "open" ? "var(--success, #15803d)" : "var(--text-tertiary)" }} />
                {streamState === "open" ? "实时同步" : streamState === "fallback" ? "同步稍后恢复" : "正在连接"}
              </span>
              {section === "today" && <span className="rounded-full px-2.5 py-1 text-[10px]" style={{ color: "var(--text-secondary)", background: "var(--bg-primary)", border: "1px solid var(--border)" }}>{snapshot?.today.needs_user.length || 0} 项待你确认</span>}
              {section === "projects" && <span className="rounded-full px-2.5 py-1 text-[10px]" style={{ color: "var(--text-secondary)", background: "var(--bg-primary)", border: "1px solid var(--border)" }}>{projects.length} 个长期项目</span>}
              {section === "library" && <span className="rounded-full px-2.5 py-1 text-[10px]" style={{ color: "var(--text-secondary)", background: "var(--bg-primary)", border: "1px solid var(--border)" }}>{documents.length} 份资料 · {okfPacks.length} 个知识包</span>}
              {section === "devices" && <span className="rounded-full px-2.5 py-1 text-[10px]" style={{ color: "var(--text-secondary)", background: "var(--bg-primary)", border: "1px solid var(--border)" }}>{snapshot?.devices.online_count || 0} 台设备在线</span>}
              {section === "results" && <span className="rounded-full px-2.5 py-1 text-[10px]" style={{ color: "var(--text-secondary)", background: "var(--bg-primary)", border: "1px solid var(--border)" }}>{snapshot?.today.recent_results.length || 0} 项可验收成果</span>}
            </div>
          </div>
          <button
            onClick={() => {
              if (section === "projects") {
                setProjectDraft({ name: "" });
                setProjectSourceFolders([]);
                setProjectFormOpen(true);
              } else if (section === "library") {
                set({ desktopView: "docstudio" });
              } else {
                startChat();
              }
            }}
            className="inline-flex items-center gap-1.5 rounded-xl px-3.5 py-2 text-[12px] font-medium text-white"
            style={{ background: "var(--accent)" }}
          >
            <Plus size={14} /> {section === "projects" ? "新建项目" : section === "library" ? "添加资料" : "开始工作"}
          </button>
          <button
            onClick={() => { setBusy(true); void refresh(true); }}
            aria-label="刷新"
            title={streamState === "open" ? "工作已同步" : streamState === "fallback" ? "连接恢复中，点击重试" : "正在连接"}
            className="rounded-xl p-2 hover:bg-[var(--bg-tertiary)]"
            style={{ color: "var(--text-tertiary)" }}
          >
            <RefreshCw size={15} className={busy ? "animate-spin" : ""} />
          </button>
        </header>

        {error && (
          <div className="mt-5 flex items-center gap-2 rounded-xl px-3.5 py-3 text-[11px]"
            style={{ color: "#b42318", background: "color-mix(in srgb, #ef4444 8%, var(--bg-primary))", border: "1px solid color-mix(in srgb, #ef4444 22%, var(--border))" }}>
            <AlertCircle size={14} /> <span className="flex-1">{error}</span>
            <button onClick={() => void refresh(true)} className="font-medium">重试</button>
          </div>
        )}

        {busy && !snapshot ? (
          <div className="flex min-h-[360px] items-center justify-center gap-2 text-[12px]" style={{ color: "var(--text-tertiary)" }}>
            <Loader2 size={17} className="animate-spin" /> 正在恢复工作空间
          </div>
        ) : section === "today" ? (
          <div className="mt-7 space-y-5">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(280px,.65fr)]">
              <section className="rounded-2xl p-5" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                <div className="flex items-center gap-2 text-[10.5px] font-medium" style={{ color: "var(--text-tertiary)" }}>
                  <Target size={13} /> {snapshot?.today.needs_user[0]?.id === focusRun?.id ? "需要你决定" : "继续上次工作"}
                </div>
                {focusRun ? (
                  <>
                    <div className="mt-5 flex items-center gap-2">
                      <span className="rounded-full px-2 py-0.5 text-[9.5px]"
                        style={{ color: snapshot?.today.needs_user[0]?.id === focusRun.id ? "#b45309" : "var(--accent)", background: snapshot?.today.needs_user[0]?.id === focusRun.id ? "color-mix(in srgb, #f59e0b 11%, transparent)" : "var(--accent-light)" }}>
                        {STATE_LABELS[focusRun.state] || "进行中"}
                      </span>
                      <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{ago(focusRun.updated_at)}</span>
                    </div>
                    <h2 className="mt-3 text-[18px] font-semibold leading-7" style={{ color: "var(--text-primary)" }}>
                      {focusRun.title || focusRun.contract.goal || "继续当前工作"}
                    </h2>
                    <p className="mt-2 line-clamp-2 text-[11.5px] leading-5" style={{ color: "var(--text-tertiary)" }}>
                      {focusRun.next_action || focusRun.current_step || "打开工作记录，查看已完成内容和下一步。"}
                    </p>
                    <div className="mt-5 flex flex-wrap items-center gap-2">
                      <button onClick={() => openRun(focusRun)}
                        className="inline-flex items-center gap-1.5 rounded-xl px-3.5 py-2 text-[11.5px] font-medium text-white"
                        style={{ background: "var(--accent)" }}>
                        <PlayCircle size={14} /> {snapshot?.today.needs_user[0]?.id === focusRun.id ? "处理这件事" : "继续工作"}
                      </button>
                      <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
                        {focusRun.artifacts.length} 个成果 · {Number(focusRun.evidence?.summary?.evidence_count || 0)} 条依据
                      </span>
                    </div>
                  </>
                ) : (
                  <div className="py-10 text-center">
                    <CheckCircle2 size={24} className="mx-auto" style={{ color: "var(--success)" }} />
                    <div className="mt-3 text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>当前没有积压事项</div>
                    <div className="mt-1 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>可以从一段对话、一组资料或一个新项目开始。</div>
                  </div>
                )}
              </section>
              <section className="overflow-hidden rounded-2xl" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                <div className="px-4 py-3.5" style={{ borderBottom: "1px solid var(--border)" }}>
                  <h2 className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>从哪里开始</h2>
                  <p className="mt-0.5 text-[10px]" style={{ color: "var(--text-tertiary)" }}>不必先选择工具，告诉 HashMM 你手里有什么</p>
                </div>
                {([
                  { label: "从资料开始", desc: "阅读、比较或整理已有文件", Icon: BookOpen, action: () => set({ desktopView: "hub-knowledge" }) },
                  { label: "建立长期项目", desc: "固定目标、交付物与验收标准", Icon: FolderKanban, action: () => set({ desktopView: "gworkspace" }) },
                  { label: "接力到另一台设备", desc: "继续需要文件或电脑操作的工作", Icon: Laptop, action: () => set({ desktopView: "remote" }) },
                ] as const).map(({ label, desc, Icon, action }) => (
                  <button key={label} onClick={action} className="group flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-[var(--bg-secondary)]"
                    style={{ borderBottom: "1px solid var(--border)" }}>
                    <span className="flex h-8 w-6 items-center justify-center" style={{ color: "var(--accent)" }}><Icon size={15} /></span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>{label}</span>
                      <span className="mt-0.5 block truncate text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>{desc}</span>
                    </span>
                    <ArrowRight size={13} className="opacity-35 group-hover:opacity-80" />
                  </button>
                ))}
              </section>
            </div>
            <div className={`grid grid-cols-1 gap-5 ${(snapshot?.today.needs_user.length || 0) > 1 ? "xl:grid-cols-2" : ""}`}>
              {(snapshot?.today.needs_user.length || 0) > 1 && <SectionCard
                title="其他待处理"
                subtitle="首要事项已在上方显示；这里仅列其余需要确认的工作。"
                action={<span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{(snapshot?.today.needs_user.length || 1) - 1} 项</span>}
              >
                {snapshot!.today.needs_user.slice(1).map(run => <RunRow key={run.id} run={run} open={openRun} emphasized />)}
              </SectionCard>}
              <SectionCard title="正在替你处理" subtitle="可以离开这个页面；任务会从已记录的位置继续。">
                {visibleRuns.length
                  ? visibleRuns.map(run => <RunRow key={run.id} run={run} open={openRun} />)
                  : <div className="px-4 py-6 text-[11px]" style={{ color: "var(--text-tertiary)" }}>目前没有正在处理的工作。</div>}
              </SectionCard>
            </div>
            <SectionCard title="最近完成" subtitle="回到结果、继续追问，或把它用到下一项工作。">
              {snapshot?.today.recent_results.length
                ? snapshot.today.recent_results.slice(0, 5).map(run => <RunRow key={run.id} run={run} open={openRun} />)
                : <div className="px-4 py-6 text-[11px]" style={{ color: "var(--text-tertiary)" }}>完成的工作会出现在这里。</div>}
            </SectionCard>
          </div>
        ) : section === "projects" ? (
          <div className="mt-7 grid min-h-[520px] gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
            <aside className="overflow-hidden rounded-2xl" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
              <div className="flex items-center gap-2 px-4 py-3.5" style={{ borderBottom: "1px solid var(--border)" }}>
                <div className="min-w-0 flex-1">
                  <div className="text-[11.5px] font-semibold" style={{ color: "var(--text-primary)" }}>全部项目</div>
                  <div className="mt-0.5 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>{projects.length ? `${projects.length} 个项目` : "从本地文件夹或空白项目开始"}</div>
                </div>
                <button onClick={() => setProjectFormOpen(true)} aria-label="新建项目" className="rounded-lg p-1.5 hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--accent)" }}><Plus size={14} /></button>
              </div>
              <div className="max-h-[600px] overflow-y-auto p-2">
                {projects.map(project => (
                  <button key={project.id} onClick={() => void selectProject(project)}
                    className="workspace-object-row"
                    data-selected={selectedProject?.id === project.id}>
                    <span className="workspace-object-icon"><FolderKanban size={15} /></span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>{project.name}</span>
                      <span className="mt-0.5 block truncate text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>{project.goal || "目标待补充"}</span>
                    </span>
                    <span className="text-[9px]" style={{ color: "var(--text-tertiary)" }}>{ago(Number(project.updated_at || 0))}</span>
                  </button>
                ))}
                {!projects.length && <div className="px-3 py-10 text-center text-[10px]" style={{ color: "var(--text-tertiary)" }}>建立项目后，相关对话、资料和成果会收在一起。</div>}
              </div>
            </aside>
            <section className="overflow-hidden rounded-2xl" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
              {selectedProject ? (
                <>
                  <div className="flex items-start gap-4 px-6 py-5" style={{ borderBottom: "1px solid var(--border)" }}>
                    <span className="flex h-10 w-10 items-center justify-center rounded-xl" style={{ color: "var(--accent)", background: "var(--accent-light)" }}><FolderKanban size={18} /></span>
                    <div className="min-w-0 flex-1">
                      <h2 className="truncate text-[18px] font-semibold" style={{ color: "var(--text-primary)" }}>{selectedProject.name}</h2>
                      <p className="mt-1 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>更新于 {ago(Number(selectedProject.updated_at || 0))} · {selectedProject.conv_count || 0} 次相关讨论</p>
                    </div>
                    <button onClick={() => {
                      writeActiveProject(user, selectedProject.id);
                      startChat(`继续项目「${selectedProject.name}」。目标：${selectedProject.goal || "请先和我补全目标"}。先恢复最近进度，再说明下一步。`);
                    }} className="inline-flex items-center gap-1.5 rounded-xl px-3.5 py-2 text-[11px] font-medium text-white" style={{ background: "var(--accent)" }}>
                      <PlayCircle size={13} /> 继续讨论与工作
                    </button>
                  </div>
                  <div className="grid gap-6 p-6 xl:grid-cols-[minmax(0,1fr)_260px]">
                    <div className="space-y-5">
                      <div>
                        <div className="workspace-detail-label">项目资料</div>
                        {selectedProjectSources.length ? (
                          <div className="mt-2 overflow-hidden rounded-xl" style={{ border: "1px solid var(--border)" }}>
                            {selectedProjectSources.map((source, index) => (
                              <button key={source} onClick={() => void getProjectDesktop()?.activateSource(source)}
                                className="flex w-full items-center gap-3 px-3.5 py-3 text-left hover:bg-[var(--bg-secondary)]"
                                style={{ borderBottom: index < selectedProjectSources.length - 1 ? "1px solid var(--border)" : undefined }}>
                                <FolderKanban size={14} style={{ color: "var(--accent)" }} />
                                <span className="min-w-0 flex-1">
                                  <span className="block truncate text-[10.8px] font-medium" style={{ color: "var(--text-primary)" }}>{folderLabel(source)}</span>
                                  <span className="mt-0.5 block truncate text-[9px]" style={{ color: "var(--text-tertiary)" }}>{source}</span>
                                </span>
                                <span className="text-[9px]" style={{ color: index === 0 ? "var(--accent)" : "var(--text-tertiary)" }}>{index === 0 ? "当前工作目录" : "切换"}</span>
                              </button>
                            ))}
                          </div>
                        ) : (
                          <div className="mt-2 rounded-xl px-3.5 py-3 text-[10.5px] leading-5" style={{ color: "var(--text-tertiary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                            这个项目没有绑定本地文件夹。仍可在 Chat 中工作；需要读取或编辑本机文件时，再新建一个带资料文件夹的项目。
                          </div>
                        )}
                      </div>
                      <div>
                        <div className="workspace-detail-label">先说清要做什么</div>
                        <p className="mt-2 text-[12px] leading-6" style={{ color: "var(--text-secondary)" }}>{selectedProject.goal || "尚未明确。继续工作时，HashMM 会先和你一起补全。"}</p>
                      </div>
                      <div>
                        <div className="workspace-detail-label">最终交付</div>
                        <div className="mt-2 rounded-xl px-3.5 py-3 text-[11px]" style={{ color: "var(--text-secondary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>{selectedProject.deliverable || "还没有约定交付形式"}</div>
                      </div>
                      <div>
                        <div className="workspace-detail-label">约定怎样算完成</div>
                        <div className="mt-2 overflow-hidden rounded-xl" style={{ border: "1px solid var(--border)" }}>
                          {selectedProject.success_criteria?.length ? selectedProject.success_criteria.map((criterion, index) => (
                            <div key={`${criterion}:${index}`} className="flex items-start gap-2.5 px-3.5 py-2.5 text-[10.5px]" style={{ color: "var(--text-secondary)", borderBottom: "1px solid var(--border)" }}>
                              <span className="mt-0.5 flex h-4 w-4 items-center justify-center rounded-full text-[8px]" style={{ color: "var(--accent)", background: "var(--accent-light)" }}>{index + 1}</span>{criterion}
                            </div>
                          )) : <div className="px-3.5 py-4 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>还没有验收标准。HashMM 会先向你确认，避免做完才发现方向不对。</div>}
                        </div>
                      </div>
                    </div>
                    <aside className="space-y-3">
                      <div className="rounded-xl p-4" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                        <div className="workspace-detail-label">项目状态</div>
                        <div className="mt-3 text-[12px] font-medium" style={{ color: "var(--text-primary)" }}>{selectedProject.status === "completed" ? "已完成" : selectedProject.status === "paused" ? "已暂停" : "正在推进"}</div>
                        <p className="mt-1 text-[9.5px] leading-4" style={{ color: "var(--text-tertiary)" }}>权限按需询问；涉及外发、删除或控制设备时仍会单独确认。</p>
                      </div>
                      <button onClick={() => set({ desktopView: "work-active" })} className="workspace-secondary-action">查看任务与进度 <ArrowRight size={12} /></button>
                      <button onClick={() => set({ desktopView: "hub-knowledge" })} className="workspace-secondary-action">为项目选择资料 <BookOpen size={12} /></button>
                    </aside>
                  </div>
                </>
              ) : (
                <div className="flex min-h-[520px] flex-col items-center justify-center px-8 text-center">
                  <FolderKanban size={26} style={{ color: "var(--text-tertiary)" }} />
                  <h2 className="mt-3 text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>把一项长期工作交给 HashMM</h2>
                  <p className="mt-1 max-w-[360px] text-[10.5px] leading-5" style={{ color: "var(--text-tertiary)" }}>项目只保存用户能理解的目标、交付物和完成标准；工具选择与 Agent 编排由系统按需完成。</p>
                  <button onClick={() => setProjectFormOpen(true)} className="mt-4 rounded-xl px-3.5 py-2 text-[11px] text-white" style={{ background: "var(--accent)" }}>建立第一个项目</button>
                </div>
              )}
            </section>
            {projectFormOpen && (
              <div className="workspace-sheet-backdrop" onMouseDown={() => setProjectFormOpen(false)}>
                <form className="workspace-sheet" onMouseDown={event => event.stopPropagation()} onSubmit={event => { event.preventDefault(); void createProject(); }}>
                  <div className="flex items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <h2 className="text-[17px] font-semibold" style={{ color: "var(--text-primary)" }}>创建项目</h2>
                      <p className="mt-1 text-[10.5px] leading-5" style={{ color: "var(--text-tertiary)" }}>项目会把相关对话留在一起；本地资料目录只保存在这台电脑。</p>
                    </div>
                    <button type="button" onClick={() => setProjectFormOpen(false)} className="rounded-lg p-1.5 hover:bg-[var(--bg-tertiary)]"><X size={14} /></button>
                  </div>
                  <div className="mt-5 space-y-4">
                    <label className="block">
                      <span className="mb-1.5 block text-[10.5px] font-medium" style={{ color: "var(--text-secondary)" }}>项目名称</span>
                      <input autoFocus value={projectDraft.name} maxLength={120} placeholder="例如：新品发布方案" onChange={event => setProjectDraft({ name: event.target.value })} className="workspace-field" />
                    </label>
                    <div>
                      <div className="mb-1.5 text-[10.5px] font-medium" style={{ color: "var(--text-secondary)" }}>资料文件夹</div>
                      <button type="button" onClick={() => void chooseProjectFolders()}
                        className="flex min-h-[96px] w-full flex-col items-center justify-center rounded-xl px-4 py-5 text-center transition-colors hover:bg-[var(--bg-tertiary)]"
                        style={{ color: "var(--text-secondary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                        <UploadCloud size={19} />
                        <span className="mt-2 text-[10.8px] font-medium">{getProjectDesktop() ? "添加 HashMM 可读取和编辑的文件夹" : "桌面端可添加本地资料文件夹"}</span>
                        <span className="mt-1 text-[9px]" style={{ color: "var(--text-tertiary)" }}>最多 8 个；第一个文件夹作为当前工作的权限边界</span>
                      </button>
                      {projectSourceFolders.length > 0 && (
                        <div className="mt-2 overflow-hidden rounded-xl" style={{ border: "1px solid var(--border)" }}>
                          {projectSourceFolders.map((source, index) => (
                            <div key={source} className="flex items-center gap-2.5 px-3 py-2.5" style={{ borderBottom: index < projectSourceFolders.length - 1 ? "1px solid var(--border)" : undefined }}>
                              <FolderKanban size={13} style={{ color: "var(--accent)" }} />
                              <span className="min-w-0 flex-1 truncate text-[10px]" title={source} style={{ color: "var(--text-secondary)" }}>{folderLabel(source)}</span>
                              <button type="button" onClick={() => setProjectSourceFolders(items => items.filter(item => item !== source))} aria-label={`移除 ${folderLabel(source)}`} className="rounded-md p-1 hover:bg-[var(--bg-tertiary)]"><X size={11} /></button>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="flex justify-end gap-2 pt-1">
                      <button type="button" onClick={() => setProjectFormOpen(false)} className="workspace-secondary-action">取消</button>
                      <button type="submit" disabled={!projectDraft.name.trim() || creatingProject} className="workspace-primary-action">
                        {creatingProject && <Loader2 size={12} className="animate-spin" />} 创建项目
                      </button>
                    </div>
                  </div>
                </form>
              </div>
            )}
          </div>
        ) : section === "library" ? (
          <div className="mt-7 space-y-4">
            <section className="overflow-hidden rounded-2xl" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
              <div className="flex flex-col gap-3 p-3.5 sm:flex-row sm:items-center" style={{ borderBottom: "1px solid var(--border)" }}>
                <label className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-xl px-3" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                  <Search size={13} style={{ color: "var(--text-tertiary)" }} />
                  <input value={libraryQuery} onChange={event => setLibraryQuery(event.target.value)} placeholder="搜索资料" className="min-w-0 flex-1 bg-transparent text-[10.5px] outline-none" style={{ color: "var(--text-primary)" }} />
                </label>
                <div className="flex gap-1 overflow-x-auto">
                  {([["all", "全部"], ["document", "文档"], ["pdf", "PDF"], ["slides", "演示"], ["sheet", "表格"]] as const).map(([value, label]) => (
                    <button key={value} onClick={() => setLibraryType(value)} className="whitespace-nowrap rounded-lg px-2 py-1 text-[9.5px]" style={{ color: libraryType === value ? "var(--accent)" : "var(--text-tertiary)", background: libraryType === value ? "var(--accent-light)" : "transparent" }}>{label}</button>
                  ))}
                </div>
                <input ref={okfInput} type="file" accept=".zip,.okf" className="hidden" onChange={event => void inspectOkf(event.target.files?.[0])} />
                <button onClick={() => okfInput.current?.click()} className="workspace-secondary-action shrink-0" title="导入可追溯知识包"><PackageOpen size={12} /> 导入 OKF</button>
              </div>
              <div className="flex items-center justify-between px-4 py-3">
                <div>
                  <div className="text-[11.5px] font-semibold" style={{ color: "var(--text-primary)" }}>你的资料</div>
                  <div className="mt-0.5 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>{visibleDocuments.length} 份可见{okfPacks.length ? ` · ${okfPacks.length} 个知识包` : ""}</div>
                </div>
                <button onClick={() => set({ desktopView: "docstudio" })} className="workspace-primary-action"><Plus size={12} /> 添加资料</button>
              </div>
              <div className="max-h-[560px] overflow-y-auto" style={{ borderTop: "1px solid var(--border)" }}>
                {visibleDocuments.slice(0, 120).map((doc, index) => {
                  const name = String(doc.filename || doc.name || doc.title || `资料 ${index + 1}`);
                  const selected = selectedDocuments.includes(name);
                  const type = /\.pdf$/i.test(name) ? "PDF" : /\.(ppt|pptx|odp)$/i.test(name) ? "演示" : /\.(xls|xlsx|csv|tsv|ods)$/i.test(name) ? "表格" : "文档";
                  return (
                    <div key={`${name}:${index}`} className="group flex items-center gap-3 px-4 py-3 hover:bg-[var(--bg-secondary)]" style={{ borderBottom: "1px solid var(--border)" }}>
                      <button onClick={() => setSelectedDocuments(values => selected ? values.filter(value => value !== name) : [...values, name])}
                        aria-label={selected ? `取消选择 ${name}` : `选择 ${name}`}
                        className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md"
                        style={{ color: selected ? "#fff" : "transparent", background: selected ? "var(--accent)" : "var(--bg-primary)", border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}` }}>
                        <CheckCircle2 size={12} />
                      </button>
                      <button onClick={() => { setSelectedDocumentName(name); setLibraryDetailOpen(true); }} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg" style={{ color: "var(--text-secondary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }}><FileText size={14} /></span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>{name}</span>
                          <span className="mt-0.5 block text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>已建立索引 · 原文可核对</span>
                        </span>
                        <span className="rounded-lg px-2 py-1 text-[9px]" style={{ color: "var(--text-tertiary)", background: "var(--bg-secondary)" }}>{type}</span>
                        <ArrowRight size={12} className="opacity-30 group-hover:opacity-80" style={{ color: "var(--text-tertiary)" }} />
                      </button>
                    </div>
                  );
                })}
                {!visibleDocuments.length && <div className="px-3 py-16 text-center text-[10.5px]" style={{ color: "var(--text-tertiary)" }}><LibraryBig size={23} className="mx-auto mb-2" />{documents.length ? "没有匹配的资料" : "还没有资料，添加后即可在 Chat 中使用"}</div>}
              </div>
              {selectedDocuments.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 px-4 py-3" style={{ background: "var(--bg-secondary)", borderTop: "1px solid var(--border)" }}>
                  <span className="mr-auto text-[10px]" style={{ color: "var(--text-secondary)" }}>已选 {selectedDocuments.length} 份资料</span>
                  <button onClick={() => startWithDocuments("ask")} className="workspace-secondary-action">在 Chat 中使用</button>
                  <button disabled={selectedDocuments.length < 2} onClick={() => startWithDocuments("compare")} className="workspace-secondary-action disabled:opacity-40">帮我比较</button>
                  <button onClick={() => startWithDocuments("deliver")} className="workspace-primary-action"><Sparkles size={12} /> 生成成果</button>
                </div>
              )}
            </section>
            {libraryDetailOpen && selectedDocument && (
              <div className="workspace-sheet-backdrop" onMouseDown={() => setLibraryDetailOpen(false)}>
                <div className="workspace-sheet workspace-sheet-wide" onMouseDown={event => event.stopPropagation()}>
                  <div className="flex items-start gap-3">
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl" style={{ color: "var(--accent)", background: "var(--accent-light)" }}><FileText size={18} /></span>
                    <div className="min-w-0 flex-1">
                      <h2 className="break-words text-[15px] font-semibold" style={{ color: "var(--text-primary)" }}>{String(selectedDocument.filename || selectedDocument.name || selectedDocument.title || "资料")}</h2>
                      <p className="mt-1 text-[10px]" style={{ color: "var(--text-tertiary)" }}>已进入当前账号的私有检索范围 · 原文可核对</p>
                    </div>
                    <button type="button" onClick={() => setLibraryDetailOpen(false)} className="rounded-lg p-1.5 hover:bg-[var(--bg-tertiary)]"><X size={14} /></button>
                  </div>
                  <p className="mt-4 text-[10.5px] leading-5" style={{ color: "var(--text-secondary)" }}>Chat 只会使用实际检索到的内容，并为关键结论保留可以核对的出处。资料本身不会因为打开详情而发送给模型。</p>
                  <div className="mt-4 grid grid-cols-2 gap-2">
                    <div className="workspace-info-cell"><span>索引状态</span><strong>可检索</strong></div>
                    <div className="workspace-info-cell"><span>资料归属</span><strong>仅当前账号</strong></div>
                    <div className="workspace-info-cell"><span>片段数量</span><strong>{String(selectedDocument.chunks || selectedDocument.chunk_count || "按需读取")}</strong></div>
                    <div className="workspace-info-cell"><span>可信度</span><strong>原文可核对</strong></div>
                  </div>
                  <div className="mt-5">
                    <div className="workspace-detail-label mb-2">接下来想做什么</div>
                    <div className="flex flex-wrap gap-2">
                      <button onClick={() => { const name = String(selectedDocument.filename || selectedDocument.name || selectedDocument.title || "资料"); setSelectedDocuments([name]); startWithDocuments("ask", [name]); }} className="workspace-primary-action">和这些资料聊一聊 <ArrowRight size={12} /></button>
                      <button onClick={() => { const name = String(selectedDocument.filename || selectedDocument.name || selectedDocument.title || "资料"); setSelectedDocuments(values => Array.from(new Set([...values, name]))); setLibraryDetailOpen(false); }} className="workspace-secondary-action">加入资料篮 <Plus size={12} /></button>
                    </div>
                  </div>
                  {okfPacks.length > 0 && (
                    <div className="mt-5">
                      <div className="workspace-detail-label mb-2">知识包</div>
                      <div className="overflow-hidden rounded-xl" style={{ border: "1px solid var(--border)" }}>
                        {okfPacks.map(pack => (
                          <div key={pack.id} className="flex items-center gap-3 px-3.5 py-2.5" style={{ borderBottom: "1px solid var(--border)" }}>
                            <PackageOpen size={13} style={{ color: "var(--accent)" }} />
                            <span className="min-w-0 flex-1"><span className="block truncate text-[10.5px] font-medium" style={{ color: "var(--text-primary)" }}>{pack.name}</span><span className="block text-[9px]" style={{ color: "var(--text-tertiary)" }}>{pack.concept_count} 个概念</span></span>
                            <a href={okfExportUrl(pack.id)} download className="rounded-lg p-1.5 hover:bg-[var(--bg-tertiary)]" title="导出知识包"><Download size={12} /></a>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}
            {okfPreview && (
              <div className="workspace-sheet-backdrop" onMouseDown={() => setOkfPreview(null)}>
                <div className="workspace-sheet workspace-sheet-wide" onMouseDown={event => event.stopPropagation()}>
                  <div className="flex items-start gap-3">
                    <PackageOpen size={20} style={{ color: "var(--accent)" }} />
                    <div className="min-w-0 flex-1"><h2 className="text-[15px] font-semibold" style={{ color: "var(--text-primary)" }}>{okfPreview.name}</h2><p className="mt-1 text-[10px]" style={{ color: "var(--text-tertiary)" }}>{okfPreview.concept_count} 个概念 · {okfPreview.types.join("、") || "未分类"}</p></div>
                    <button onClick={() => setOkfPreview(null)}><X size={14} /></button>
                  </div>
                  <div className="mt-4 grid grid-cols-3 gap-2">
                    <div className="workspace-info-cell"><span>人工审核</span><strong>{okfPreview.trust["human-reviewed"]}</strong></div>
                    <div className="workspace-info-cell"><span>机器确认</span><strong>{okfPreview.trust["machine-confirmed"]}</strong></div>
                    <div className="workspace-info-cell"><span>待核实</span><strong>{okfPreview.trust.unverified}</strong></div>
                  </div>
                  <div className="mt-4 max-h-44 overflow-y-auto rounded-xl" style={{ border: "1px solid var(--border)" }}>
                    {okfPreview.concepts.map(concept => <div key={concept.path} className="flex items-center gap-3 px-3 py-2.5" style={{ borderBottom: "1px solid var(--border)" }}><span className="rounded-md px-1.5 py-0.5 text-[8.5px]" style={{ color: "var(--accent)", background: "var(--accent-light)" }}>{concept.type}</span><span className="min-w-0 flex-1 truncate text-[10.5px]" style={{ color: "var(--text-primary)" }}>{concept.title}</span><span className="text-[9px]" style={{ color: "var(--text-tertiary)" }}>{concept.trust === "human-reviewed" ? "人工审核" : concept.trust === "machine-confirmed" ? "机器确认" : "待核实"}</span></div>)}
                  </div>
                  {okfPreview.warning_count > 0 && <div className="mt-3 rounded-xl px-3 py-2 text-[9.5px] leading-4" style={{ color: "#92400e", background: "rgba(245,158,11,.08)", border: "1px solid rgba(245,158,11,.2)" }}>{okfPreview.warning_count} 个非阻断提醒。缺少来源或断开的链接不会被伪装成已验证事实。</div>}
                  <p className="mt-3 text-[9.5px] leading-4" style={{ color: "var(--text-tertiary)" }}>{okfPreview.notice}</p>
                  <div className="mt-4 flex justify-end gap-2"><button onClick={() => setOkfPreview(null)} className="workspace-secondary-action">取消</button><button onClick={() => void confirmOkf()} disabled={okfBusy} className="workspace-primary-action">{okfBusy && <Loader2 size={12} className="animate-spin" />} 确认导入</button></div>
                </div>
              </div>
            )}
          </div>
        ) : section === "devices" ? (
          <div className="mt-7 space-y-4">
            <SectionCard title="已连接设备" subtitle="在线状态来自服务器设备注册表；无法确认时会显示未知，不会假装在线。">
              {snapshot?.devices.items.length ? snapshot.devices.items.map(device => (
                <button key={device.id} onClick={() => set({ desktopView: "remote" })}
                  className="group flex w-full items-center gap-3.5 px-4 py-4 text-left hover:bg-[var(--bg-secondary)]"
                  style={{ borderBottom: "1px solid var(--border)" }}>
                  <span className="flex h-10 w-10 items-center justify-center rounded-xl" style={{ color: device.online ? "#15803d" : "var(--text-tertiary)", background: device.online ? "color-mix(in srgb, #22c55e 11%, transparent)" : "var(--bg-secondary)" }}>
                    <Laptop size={18} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-[12.5px] font-medium" style={{ color: "var(--text-primary)" }}>{device.name || "电脑"}</span>
                    <span className="mt-1 block text-[10px]" style={{ color: "var(--text-tertiary)" }}>{device.online ? "在线，可以接力工作" : `离线 · ${ago(device.last_seen)}`}</span>
                  </span>
                  <ArrowRight size={14} className="opacity-40 group-hover:opacity-100" />
                </button>
              )) : (
                <div className="px-4 py-7">
                  <div className="flex items-center gap-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
                    {snapshot?.devices.authoritative ? <WifiOff size={15} /> : <ShieldCheck size={15} />}
                    {snapshot?.devices.authoritative ? "当前没有在线设备" : "暂时无法确认设备状态"}
                  </div>
                  <button onClick={() => set({ desktopView: "remote" })} className="mt-3 text-[10.5px] font-medium" style={{ color: "var(--accent)" }}>打开设备连接</button>
                </div>
              )}
            </SectionCard>
          </div>
        ) : (
          <div className="mt-7">
            <SectionCard title="最近成果" subtitle="成果与原任务、证据和验收状态保持关联。">
              {visibleRuns.length
                ? visibleRuns.map(run => <RunRow key={run.id} run={run} open={openRun} />)
                : <div className="px-4 py-7 text-[11px]" style={{ color: "var(--text-tertiary)" }}>还没有可展示的成果。</div>}
            </SectionCard>
          </div>
        )}
      </div>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  BookOpen, Check, ChevronDown, ChevronUp, FileArchive, Github,
  Globe2, Loader2, PackagePlus, RefreshCw, ShieldCheck, Trash2,
} from "lucide-react";
import {
  deleteMySkillPack, getMySkillPack, getSkillPack, importMySkillPack, listMySkillPacks,
  listSkillPacks, toggleMySkillPack, uploadMySkillPack, type SkillPack,
} from "@/lib/api";

type ImportKind = "zip" | "website" | "github" | "codex" | "claude";
type Detail = { skill_md: string; files: { path: string; size: number }[] };

const SOURCE_COPY: Record<ImportKind, { label: string; desc: string }> = {
  zip: { label: "压缩包", desc: "导入一个或多个包含 SKILL.md 的目录" },
  website: { label: "网站", desc: "公开 HTTPS 的 ZIP 或 SKILL.md 直链" },
  github: { label: "GitHub", desc: "仓库或 tree 子目录链接" },
  codex: { label: "从 Codex", desc: "把 Codex skills 目录压缩成 ZIP 后导入" },
  claude: { label: "从 Claude Code", desc: "把 .claude/skills 或插件 skills 目录压缩后导入" },
};

function sourceLabel(source: string): string {
  if (source.startsWith("codex:")) return "Codex";
  if (source.startsWith("claude:")) return "Claude Code";
  if (source.startsWith("github:")) return "GitHub";
  if (source.startsWith("website:")) return "网站";
  if (source.startsWith("agent-skills:")) return "Agent Skills";
  if (source.startsWith("upload:")) return "压缩包";
  if (source === "builtin") return "系统内置";
  return "自定义";
}

function SkillCard({ pack, mine, expanded, detail, busy, onExpand, onToggle, onDelete }: {
  pack: SkillPack; mine: boolean; expanded: boolean; detail?: Detail; busy: boolean;
  onExpand: () => void; onToggle: () => void; onDelete: () => void;
}) {
  return (
    <article className="rounded-2xl overflow-hidden" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
      <button onClick={onExpand} className="w-full p-4 flex items-start gap-3 text-left hover:bg-[var(--bg-secondary)] transition-colors">
        <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
          style={{ background: pack.enabled ? "var(--accent-light)" : "var(--bg-secondary)", color: pack.enabled ? "var(--accent)" : "var(--text-tertiary)" }}>
          <BookOpen size={16} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[12.5px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>{pack.name}</span>
            <span className="text-[9px] px-1.5 py-0.5 rounded-full" style={{ color: "var(--text-tertiary)", background: "var(--bg-tertiary)" }}>{sourceLabel(pack.source || "")}</span>
          </div>
          <p className="text-[10.5px] mt-1 leading-4 line-clamp-2" style={{ color: "var(--text-tertiary)" }}>{pack.description || "这个技能没有提供说明。"}</p>
        </div>
        {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>
      {expanded && (
        <div className="px-4 pb-4 pt-3" style={{ borderTop: "1px solid var(--border)" }}>
          <div className="flex flex-wrap gap-1.5 mb-3">
            {pack.triggers?.slice(0, 8).map(trigger => <span key={trigger} className="px-2 py-1 rounded-lg text-[9.5px]" style={{ background: "var(--bg-secondary)", color: "var(--text-secondary)" }}>{trigger}</span>)}
            <span className="px-2 py-1 rounded-lg text-[9.5px]" style={{ background: "var(--bg-secondary)", color: "var(--text-secondary)" }}>{pack.file_count} 个文件</span>
          </div>
          {detail ? (
            <>
              <div className="max-h-[180px] overflow-y-auto rounded-xl p-3 font-mono text-[10px] leading-5 whitespace-pre-wrap"
                style={{ background: "var(--bg-secondary)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
                {detail.skill_md}
              </div>
              {detail.files.length > 1 && <div className="text-[9.5px] mt-2 truncate" style={{ color: "var(--text-tertiary)" }}>{detail.files.map(file => file.path).join(" · ")}</div>}
            </>
          ) : <div className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>正在读取技能说明…</div>}
          {mine && (
            <div className="mt-3 flex items-center gap-2">
              <button onClick={onToggle} disabled={busy} className="px-3 py-1.5 rounded-lg text-[10.5px] font-medium disabled:opacity-50"
                style={pack.enabled ? { background: "var(--accent)", color: "white" } : { border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                {pack.enabled ? "已按需用于 Chat" : "启用"}
              </button>
              <button onClick={onDelete} disabled={busy} className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)] disabled:opacity-50" title="删除技能">
                <Trash2 size={13} style={{ color: "var(--error)" }} />
              </button>
            </div>
          )}
        </div>
      )}
    </article>
  );
}

export function UserSkillsSettings() {
  const [mine, setMine] = useState<SkillPack[]>([]);
  const [system, setSystem] = useState<SkillPack[]>([]);
  const [loading, setLoading] = useState(true);
  const [importOpen, setImportOpen] = useState(false);
  const [kind, setKind] = useState<ImportKind>("zip");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [expanded, setExpanded] = useState("");
  const [details, setDetails] = useState<Record<string, Detail>>({});
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    const [myResult, systemResult] = await Promise.allSettled([listMySkillPacks(), listSkillPacks()]);
    setMine(myResult.status === "fulfilled" ? myResult.value.packs || [] : []);
    setSystem(systemResult.status === "fulfilled" ? systemResult.value.packs || [] : []);
    if (myResult.status === "rejected") setMessage(myResult.reason instanceof Error ? myResult.reason.message : "个人技能加载失败");
    setLoading(false);
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const openDetail = async (pack: SkillPack, isMine: boolean) => {
    const key = `${isMine ? "mine" : "system"}:${pack.id}`;
    setExpanded(value => value === key ? "" : key);
    if (details[key]) return;
    try {
      const result = isMine ? await getMySkillPack(pack.id) : await getSkillPack(pack.id);
      setDetails(current => ({ ...current, [key]: { skill_md: result.skill_md, files: result.files || [] } }));
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "技能详情加载失败");
    }
  };

  const upload = async (file?: File) => {
    if (!file) return;
    setBusy("import"); setMessage("");
    try {
      const source = kind === "codex" ? "codex" : kind === "claude" ? "claude" : "upload";
      const result = await uploadMySkillPack(file, source);
      setMessage(`已导入 ${result.installed?.length || 0} 个技能`);
      setImportOpen(false);
      await refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "技能导入失败");
    } finally {
      setBusy("");
    }
  };

  const importUrl = async () => {
    if (!url.trim()) return;
    setBusy("import"); setMessage("");
    try {
      const result = await importMySkillPack({
        kind: kind === "github" ? "github" : "url",
        url: url.trim(),
        source: kind === "github" ? "github" : "website",
      });
      setMessage(`已导入 ${result.installed?.length || 0} 个技能`);
      setUrl(""); setImportOpen(false);
      await refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "技能导入失败");
    } finally {
      setBusy("");
    }
  };

  return (
    <div>
      <section className="rounded-2xl p-4 mb-5" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
        <div className="flex items-start gap-3">
          <ShieldCheck size={17} style={{ color: "var(--accent)" }} />
          <div className="min-w-0 flex-1">
            <div className="text-[12.5px] font-semibold" style={{ color: "var(--text-primary)" }}>技能按任务需要加载</div>
            <p className="text-[10.5px] mt-1 leading-5" style={{ color: "var(--text-tertiary)" }}>
              HashMM 先读取技能名称和说明，命中当前任务时才加载完整 SKILL.md。导入内容按不可信操作手册处理；声明的工具、网络和文件权限不会自动获得授权。
            </p>
          </div>
          <button onClick={() => setImportOpen(value => !value)} className="px-3 py-1.5 rounded-lg text-[10.5px] font-medium text-white flex items-center gap-1.5" style={{ background: "var(--accent)" }}>
            <PackagePlus size={13} /> 添加技能
          </button>
          <button onClick={() => void refresh()} className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]" title="刷新"><RefreshCw size={14} className={loading ? "animate-spin" : ""} /></button>
        </div>
      </section>

      {importOpen && (
        <section className="rounded-2xl p-4 mb-5" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
          <div className="grid grid-cols-5 gap-1 p-1 rounded-xl" style={{ background: "var(--bg-secondary)" }}>
            {(Object.keys(SOURCE_COPY) as ImportKind[]).map(item => (
              <button key={item} onClick={() => { setKind(item); setMessage(""); }}
                className="px-2 py-2 rounded-lg text-[10.5px] font-medium"
                style={kind === item ? { background: "var(--bg-primary)", color: "var(--accent)", boxShadow: "var(--shadow-sm)" } : { color: "var(--text-secondary)" }}>
                {SOURCE_COPY[item].label}
              </button>
            ))}
          </div>
          <div className="mt-4">
            <div className="text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>{SOURCE_COPY[kind].label}</div>
            <div className="text-[10.5px] mt-1" style={{ color: "var(--text-tertiary)" }}>{SOURCE_COPY[kind].desc}</div>
            {kind === "website" || kind === "github" ? (
              <div className="flex gap-2 mt-3">
                <div className="flex-1 h-9 px-3 rounded-xl flex items-center gap-2" style={{ border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
                  {kind === "github" ? <Github size={14} /> : <Globe2 size={14} />}
                  <input value={url} onChange={event => setUrl(event.target.value)}
                    placeholder={kind === "github" ? "https://github.com/owner/repo/tree/main/skill" : "https://example.com/skill.zip"}
                    className="min-w-0 flex-1 bg-transparent outline-none text-[11px]" style={{ color: "var(--text-primary)" }} />
                </div>
                <button onClick={() => void importUrl()} disabled={busy === "import" || !url.trim()} className="px-4 rounded-xl text-[11px] text-white disabled:opacity-50" style={{ background: "var(--accent)" }}>
                  {busy === "import" ? "导入中…" : "导入"}
                </button>
              </div>
            ) : (
              <div className="mt-3">
                <input ref={fileRef} type="file" accept=".zip,application/zip" hidden onChange={event => { void upload(event.target.files?.[0]); event.target.value = ""; }} />
                <button onClick={() => fileRef.current?.click()} disabled={busy === "import"}
                  className="w-full py-5 rounded-xl flex flex-col items-center gap-2 disabled:opacity-50 hover:bg-[var(--bg-tertiary)]"
                  style={{ border: "1px dashed var(--border)", color: "var(--text-secondary)" }}>
                  {busy === "import" ? <Loader2 size={18} className="animate-spin" /> : <FileArchive size={18} />}
                  <span className="text-[11px]">选择 ZIP 文件</span>
                </button>
              </div>
            )}
          </div>
        </section>
      )}

      {message && <div className="mb-4 px-3 py-2 rounded-xl text-[10.5px]" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: message.includes("失败") || message.includes("HTTP") ? "var(--error)" : "var(--text-secondary)" }}>{message}</div>}

      <div className="flex items-center justify-between mb-2">
        <h2 className="text-[12px] font-semibold" style={{ color: "var(--text-secondary)" }}>我的技能</h2>
        <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{mine.filter(pack => pack.enabled).length}/{mine.length} 已启用</span>
      </div>
      {loading ? <div className="h-28 flex items-center justify-center"><Loader2 size={17} className="animate-spin" /></div> : mine.length === 0 ? (
        <div className="rounded-2xl px-5 py-8 text-center mb-6" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
          <div className="text-[12px]" style={{ color: "var(--text-primary)" }}>还没有个人技能</div>
          <div className="text-[10.5px] mt-1" style={{ color: "var(--text-tertiary)" }}>可从 ZIP、网站、GitHub、Codex 或 Claude Code 导入。</div>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 mb-7 user-skills-grid">
          {mine.map(pack => {
            const key = `mine:${pack.id}`;
            return <SkillCard key={pack.id} pack={pack} mine expanded={expanded === key} detail={details[key]} busy={busy === pack.id}
              onExpand={() => void openDetail(pack, true)}
              onToggle={async () => { setBusy(pack.id); await toggleMySkillPack(pack.id, !pack.enabled).then(refresh).catch(reason => setMessage(reason instanceof Error ? reason.message : "设置失败")); setBusy(""); }}
              onDelete={async () => { setBusy(pack.id); await deleteMySkillPack(pack.id).then(refresh).catch(reason => setMessage(reason instanceof Error ? reason.message : "删除失败")); setBusy(""); }} />;
          })}
        </div>
      )}

      <div className="flex items-center justify-between mb-2">
        <h2 className="text-[12px] font-semibold" style={{ color: "var(--text-secondary)" }}>系统技能</h2>
        <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>由管理员维护</span>
      </div>
      <div className="grid grid-cols-2 gap-3 user-skills-grid">
        {system.map(pack => <SkillCard key={pack.id} pack={pack} mine={false} expanded={expanded === `system:${pack.id}`} busy={false}
          onExpand={() => void openDetail(pack, false)} onToggle={() => {}} onDelete={() => {}} />)}
        {system.length === 0 && <div className="col-span-2 text-[10.5px] py-5" style={{ color: "var(--text-tertiary)" }}>当前没有管理员配置的系统技能。</div>}
      </div>
    </div>
  );
}

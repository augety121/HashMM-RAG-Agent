"use client";

/** Codex-style repository pane: working-tree diff, layered guidance and review. */
import { useCallback, useEffect, useMemo, useState } from "react";
import { getLocal, type RepoInspection } from "@/lib/desktop";
import { repoReview, type RepoReviewResult } from "@/lib/api";
import { DiffBlock } from "@/components/DiffBlock";
import { AlertTriangle, CheckCircle2, FileDiff, Loader2, RefreshCw, ScrollText, ShieldCheck } from "lucide-react";
import { WorktreePanel } from "./WorktreePanel";

type Pane = "changes" | "instructions" | "worktrees" | "review";

function statusLabel(index: string, worktree: string, untracked?: boolean, conflicted?: boolean) {
  if (conflicted) return "冲突";
  if (untracked) return "未跟踪";
  const labels: Record<string, string> = { M: "修改", A: "新增", D: "删除", R: "重命名", C: "复制", " ": "" };
  return [index !== " " ? `暂存:${labels[index] || index}` : "", worktree !== " " ? `工作区:${labels[worktree] || worktree}` : ""].filter(Boolean).join(" · ");
}

export function RepoChangesView({ cwd, onWorkspaceChange }: { cwd?: string; onWorkspaceChange?: (dir: string) => void }) {
  const [repo, setRepo] = useState<RepoInspection | null>(null);
  const [pane, setPane] = useState<Pane>("changes");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reviewing, setReviewing] = useState(false);
  const [review, setReview] = useState<RepoReviewResult | null>(null);

  const load = useCallback(async () => {
    const local = getLocal();
    if (!local?.gitInspect) { setError("仓库上下文接口不可用，请重装 V337+ 桌面端"); return; }
    setLoading(true); setError("");
    try {
      const result = await local.gitInspect(cwd || undefined);
      setRepo(result);
      if (!result.ok) setError(result.error || "当前工作区不是 Git 仓库");
    } catch (e) { setError((e as Error)?.message || "仓库检查失败"); }
    finally { setLoading(false); }
  }, [cwd]);

  useEffect(() => { load(); }, [load]);

  const patch = useMemo(() => [repo?.staged?.text || "", repo?.unstaged?.text || ""].filter(Boolean).join("\n"), [repo]);
  const chunks = useMemo(() => [...(repo?.staged?.chunks || []).map(c => ({ ...c, area: "已暂存" })),
    ...(repo?.unstaged?.chunks || []).map(c => ({ ...c, area: "未暂存" }))], [repo]);

  async function runReview() {
    if (!patch || reviewing) return;
    setReviewing(true); setError(""); setReview(null); setPane("review");
    try {
      setReview(await repoReview({ diff: patch, instructions: repo?.instructions?.combined || "", scope: "working_tree" }));
    } catch (e) { setError((e as Error)?.message || "审查失败"); }
    finally { setReviewing(false); }
  }

  const severityColor: Record<string, string> = { P0: "#b91c1c", P1: "#dc2626", P2: "#d97706", P3: "#2563eb" };

  return (
    <div className="flex flex-1 min-h-0 flex-col">
      <div className="flex items-center gap-1.5 px-3 py-2 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <FileDiff size={14} style={{ color: "var(--accent)" }} />
        <span className="text-[12px] font-semibold max-w-[180px] truncate" title={repo?.root}>{repo?.branch || "仓库变更"}</span>
        {repo?.head && <span className="text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>{repo.head.slice(0, 8)}</span>}
        <span className="flex-1" />
        {([["changes", `变更 ${repo?.files?.length || 0}`], ["instructions", `指令 ${repo?.instructions?.sources?.length || 0}`], ["worktrees", "Worktree"], ["review", "Review"]] as [Pane, string][]).map(([value, label]) => (
          <button key={value} onClick={() => setPane(value)} className="px-2 py-1 rounded-md text-[10.5px]"
            style={{ background: pane === value ? "var(--bg-tertiary)" : "transparent", color: pane === value ? "var(--text-primary)" : "var(--text-tertiary)" }}>{label}</button>
        ))}
        <button onClick={runReview} disabled={!patch || reviewing} className="px-2.5 py-1 rounded-md text-[10.5px] font-medium disabled:opacity-40"
          style={{ border: "1px solid var(--border)", color: "var(--accent)" }}>{reviewing ? "审查中…" : "审查改动"}</button>
        <button onClick={load} disabled={loading} title="刷新仓库上下文" className="p-1 rounded-md" style={{ color: "var(--text-tertiary)" }}>
          {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
        </button>
      </div>

      {error && <div className="px-3 py-2 text-[11px]" style={{ color: "var(--danger,#dc2626)" }}>{error}</div>}

      <div className="flex-1 min-h-0 overflow-auto px-3 pb-3">
        {pane === "changes" && repo?.ok && <>
          {(repo.staged?.truncated || repo.unstaged?.truncated) && <div className="mt-2 text-[10.5px]" style={{ color: "#b45309" }}>补丁超过 350 KiB，已截断；Review 只覆盖当前显示部分。</div>}
          {(repo.files || []).length > 0 && <div className="mt-2 grid gap-1">
            {(repo.files || []).map((file) => <div key={`${file.path}-${file.original_path || ""}`} className="flex items-center gap-2 px-2 py-1 rounded-lg text-[10.5px]" style={{ background: "var(--bg-secondary)" }}>
              <span className="font-mono flex-1 truncate" title={file.path}>{file.path}</span>
              <span style={{ color: file.conflicted ? "#dc2626" : "var(--text-tertiary)" }}>{statusLabel(file.index, file.worktree, file.untracked, file.conflicted)}</span>
            </div>)}
          </div>}
          {chunks.slice(0, 30).map((chunk, index) => <div key={`${chunk.area}-${chunk.file}-${index}`}>
            <div className="mt-2 text-[10px] font-semibold" style={{ color: chunk.area === "已暂存" ? "#15803d" : "#b45309" }}>{chunk.area}</div>
            <DiffBlock diff={chunk.diff} filename={chunk.file} />
          </div>)}
          {!loading && !(repo.files || []).length && <div className="py-16 text-center text-[12px]" style={{ color: "var(--text-tertiary)" }}>工作区干净，没有 staged/unstaged 变更。</div>}
          {(repo.files || []).some(f => f.untracked) && <div className="mt-2 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>未跟踪文件只列名称；加入 Git 后才会进入统一 diff 与 Review。</div>}
        </>}

        {pane === "instructions" && repo?.ok && <>
          <div className="mt-3 text-[11px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
            按 global → 仓库根 → 当前目录合并；每个目录只取 AGENTS.override.md、AGENTS.md 或 fallback 中第一个非空文件，后面的更具体。
          </div>
          {repo.instructions?.truncated && <div className="mt-2 text-[10.5px]" style={{ color: "#b45309" }}>指令达到 32 KiB 上限，后续内容未注入。</div>}
          {(repo.instructions?.sources || []).map((source) => <details key={source.path} className="mt-2 rounded-xl p-2" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
            <summary className="cursor-pointer text-[11px] font-mono" style={{ color: "var(--text-secondary)" }}>{source.scope === "global" ? "全局" : "项目"} · {source.relative} · {source.bytes} B</summary>
            <pre className="mt-2 max-h-[220px] overflow-auto whitespace-pre-wrap text-[10.5px] leading-relaxed" style={{ color: "var(--text-primary)" }}>{source.content}</pre>
          </details>)}
          {!repo.instructions?.sources?.length && <div className="py-10 text-center text-[11.5px]" style={{ color: "var(--text-tertiary)" }}>未发现 AGENTS.md。可在仓库根创建它，固化构建、测试和评审要求。</div>}
          {repo.plan && <details className="mt-3 rounded-xl p-2" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }} open>
            <summary className="cursor-pointer text-[11px] font-mono" style={{ color: "var(--accent)" }}>PLANS.md · {repo.plan.relative}</summary>
            <pre className="mt-2 max-h-[220px] overflow-auto whitespace-pre-wrap text-[10.5px]" style={{ color: "var(--text-primary)" }}>{repo.plan.content}</pre>
          </details>}
        </>}

        {pane === "worktrees" && <WorktreePanel cwd={cwd} repo={repo} onActivated={onWorkspaceChange} />}

        {pane === "review" && <>
          <div className="mt-3 flex items-start gap-2 rounded-xl p-3 text-[10.5px] leading-relaxed" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-tertiary)" }}>
            <AlertTriangle size={12} className="mt-0.5 flex-shrink-0" style={{ color: "#b45309" }} />
            <span>Review 会把当前显示的 staged/unstaged diff 与已加载的 AGENTS 指令发送到当前配置的模型后端；若使用远程后端，代码内容会离开本机。未跟踪文件和被截断部分不会发送、也不会被审查。</span>
          </div>
          {reviewing && <div className="py-16 flex items-center justify-center gap-2 text-[12px]" style={{ color: "var(--text-tertiary)" }}><Loader2 size={14} className="animate-spin" /> 正在按仓库指令审查本次 diff…</div>}
          {review && <div className="pt-3">
            <div className="flex items-start gap-2 rounded-xl p-3" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
              <ShieldCheck size={14} style={{ color: "var(--accent)" }} className="mt-0.5" />
              <div className="text-[11px] leading-relaxed" style={{ color: "var(--text-secondary)" }}><div>{review.summary || "审查完成"}</div><div className="mt-1 text-[10px]" style={{ color: "var(--text-tertiary)" }}>{review.model} · 丢弃 {review.discarded_findings} 条无补丁证据的候选 · {review.notice}</div></div>
            </div>
            {review.findings.map((finding, index) => <div key={`${finding.file}-${finding.line}-${index}`} className="mt-2 rounded-xl p-3" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
              <div className="flex items-center gap-2"><span className="px-1.5 py-0.5 rounded text-[10px] font-bold text-white" style={{ background: severityColor[finding.severity] }}>{finding.severity}</span><span className="text-[11.5px] font-semibold">{finding.title}</span><span className="ml-auto text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>{finding.file}:{finding.line}</span></div>
              <div className="mt-1.5 text-[11px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>{finding.body}</div>
              <code className="block mt-2 px-2 py-1 rounded text-[10px] whitespace-pre-wrap" style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)" }}>{finding.evidence}</code>
            </div>)}
            {!review.findings.length && <div className="py-12 text-center text-[12px]" style={{ color: "#15803d" }}><CheckCircle2 size={18} className="mx-auto mb-2" />未发现同时满足文件、变更行和精确证据约束的问题。</div>}
            {review.discarded_findings > 0 && <div className="mt-3 flex items-center gap-1.5 text-[10.5px]" style={{ color: "#b45309" }}><AlertTriangle size={11} />模型提出但无法锚定到补丁的候选已被自动丢弃，不展示为事实。</div>}
          </div>}
          {!reviewing && !review && <div className="py-16 text-center text-[11.5px]" style={{ color: "var(--text-tertiary)" }}><ScrollText size={20} className="mx-auto mb-2" />点击“审查改动”，只审 staged/unstaged 统一补丁。</div>}
        </>}
      </div>
    </div>
  );
}

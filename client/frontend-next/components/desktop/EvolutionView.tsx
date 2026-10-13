"use client";
/** components/desktop/EvolutionView.tsx — 自我进化（V203 重构）。
 *
 * 三层能力，从"人给的"到"自己长的"：
 *   ① 技能包（Agent Skills / SKILL.md）—— 人写的成套操作手册，可从 GitHub / zip / 服务器路径导入，
 *      与 Claude Code 的 skills 同格式（github.com/anthropics/skills 等仓库可直接导入）。
 *   ② 学习型技能 —— 系统从问答中自动沉淀的可复用片段（顶/踩驱动质量分）。
 *   ③ 经验回放 —— 每次问答的 episode 记录（问了什么/用了什么策略/结果如何）。
 */
import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import {
  Sparkles, RefreshCw, CheckCircle2, CircleHelp, XCircle, ThumbsUp, ThumbsDown, Trash2,
  Package, Upload, Github, FolderOpen, Plus, X, FileText, Download, BookOpen, Store, ShieldCheck, Wifi, HardDrive,
  MessageSquare, GitCompareArrows, RotateCcw, ShieldAlert, FlaskConical,
} from "lucide-react";
import {
  listEvolutionSkills, listEpisodes, skillFeedback, deleteEvolutionSkill,
  listSkillPacks, getSkillPack, toggleSkillPack, deleteSkillPack, importSkillPack,
  uploadSkillPack, seedBuiltinSkillPacks, getSkillEvolution, proposeSkillEvolution,
  evaluateSkillEvolution, approveSkillEvolution, rejectSkillEvolution, rollbackSkillEvolution,
  type SkillPack, type SkillEvolutionRun,
} from "@/lib/api";
import { PanelShell, PageHeader, Card, CardGrid, Button, Badge, StateView, SectionTitle, Toggle, inputClass, inputStyle } from "./ui/PanelKit";
import { sortSkills, skillsSummary, filterEpisodes, episodeOutcomeCounts, type SkillRec, type EpisodeRec, type SkillSort, type OutcomeFilter } from "@/lib/skillStats";
import { insertContextIntoChat } from "@/lib/contextInsert";

type Skill = { id?: string; name?: string; description?: string; quality_score?: number; use_count?: number; trigger_patterns?: string[]; scope?: string; owner_id?: string; [k: string]: any };
type Episode = { id?: string; query?: string; query_type?: string; strategy?: string; outcome?: string; key_insight?: string; elapsed_ms?: number; created_at?: number };

function outcomeStyle(outcome?: string): { color: string; Icon: any; label: string } {
  const o = (outcome || "").toLowerCase();
  if (["success", "ok", "good", "pass"].includes(o)) return { color: "var(--success)", Icon: CheckCircle2, label: outcome || "成功" };
  if (["fail", "error", "bad"].includes(o)) return { color: "var(--error)", Icon: XCircle, label: outcome || "失败" };
  return { color: "var(--text-tertiary)", Icon: CircleHelp, label: outcome || "未知" };
}

function fmtSize(n: number): string {
  if (!n) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

const SOURCE_LABEL = (s: string): { label: string; tone: "neutral" | "accent" | "success" } => {
  if (s === "builtin") return { label: "内置", tone: "accent" };
  if (s.startsWith("github:")) return { label: "GitHub", tone: "success" };
  if (s.startsWith("upload:")) return { label: "上传", tone: "neutral" };
  if (s.startsWith("path:")) return { label: "本地目录", tone: "neutral" };
  return { label: "自定义", tone: "neutral" };
};

/* ── 导入弹窗：三种方式（上传 zip / GitHub 链接 / 服务器目录）── */
function ImportModal({ onClose, onDone }: { onClose: () => void; onDone: (msg: string) => void }) {
  const [tab, setTab] = useState<"zip" | "github" | "path">("zip");
  const [ghUrl, setGhUrl] = useState("");
  const [dirPath, setDirPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const doUpload = async (f: File | undefined | null) => {
    if (!f) return;
    setBusy(true); setErr("");
    try {
      const r = await uploadSkillPack(f);
      onDone(`已导入 ${r.installed?.length ?? 0} 个技能包`);
    } catch (e) { setErr(e instanceof Error ? e.message : "导入失败"); }
    finally { setBusy(false); }
  };
  const doImport = async () => {
    setBusy(true); setErr("");
    try {
      const r = tab === "github"
        ? await importSkillPack({ kind: "github", url: ghUrl.trim() })
        : await importSkillPack({ kind: "path", path: dirPath.trim() });
      onDone(`已导入 ${(r as { installed?: unknown[] }).installed?.length ?? 0} 个技能包`);
    } catch (e) { setErr(e instanceof Error ? e.message : "导入失败"); }
    finally { setBusy(false); }
  };

  const TabBtn = ({ v, icon: Icon, label }: { v: typeof tab; icon: any; label: string }) => (
    <button onClick={() => { setTab(v); setErr(""); }}
      className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-[12px] font-medium transition-colors"
      style={tab === v ? { background: "var(--accent-light)", color: "var(--accent)" } : { color: "var(--text-secondary)" }}>
      <Icon size={13} /> {label}
    </button>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-[480px] max-w-[94vw] rounded-2xl overflow-hidden anim-fade-up"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
        <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-2">
            <Package size={16} style={{ color: "var(--accent)" }} />
            <span className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>导入技能包</span>
          </div>
          <button onClick={onClose} aria-label="关闭" className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]">
            <X size={16} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>

        <div className="p-5">
          <div className="flex gap-1 p-1 rounded-xl mb-4" style={{ background: "var(--bg-secondary)" }}>
            <TabBtn v="zip" icon={Upload} label="上传 zip" />
            <TabBtn v="github" icon={Github} label="GitHub 链接" />
            <TabBtn v="path" icon={FolderOpen} label="服务器目录" />
          </div>

          {tab === "zip" && (
            <div>
              <button onClick={() => fileRef.current?.click()} disabled={busy}
                className="w-full py-8 rounded-xl flex flex-col items-center gap-2 transition-colors hover:border-[var(--accent)] disabled:opacity-50"
                style={{ border: "1.5px dashed var(--border)", color: "var(--text-secondary)" }}>
                <Upload size={22} style={{ color: "var(--text-tertiary)" }} />
                <span className="text-[12.5px]">{busy ? "正在导入…" : "点击选择 .zip 文件"}</span>
                <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>单个技能目录或整个技能仓库的压缩包均可，自动识别其中所有 SKILL.md</span>
              </button>
              <input ref={fileRef} type="file" accept=".zip" className="hidden"
                onChange={e => { doUpload(e.target.files?.[0]); e.target.value = ""; }} />
            </div>
          )}
          {tab === "github" && (
            <div className="flex flex-col gap-2.5">
              <input value={ghUrl} onChange={e => setGhUrl(e.target.value)} className={inputClass} style={inputStyle}
                placeholder="https://github.com/anthropics/skills" autoFocus />
              <div className="text-[10.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
                支持整个仓库，或用 /tree/分支/子目录 精确到某个技能（如 …/tree/main/skills/mcp-builder）。
                由后端服务器下载，导入所有含 SKILL.md 的目录。请确认你有权使用目标仓库的内容（注意其 License）。
              </div>
              <Button variant="primary" busy={busy} disabled={!ghUrl.trim()} onClick={doImport}>从 GitHub 导入</Button>
            </div>
          )}
          {tab === "path" && (
            <div className="flex flex-col gap-2.5">
              <input value={dirPath} onChange={e => setDirPath(e.target.value)} className={inputClass} style={inputStyle}
                placeholder="/root/my-skills/pdf-tools（后端服务器上的目录）" autoFocus />
              <div className="text-[10.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
                指向后端服务器上一个含 SKILL.md 的目录，导入时会复制进数据区（之后删除原目录不影响）。
              </div>
              <Button variant="primary" busy={busy} disabled={!dirPath.trim()} onClick={doImport}>从目录导入</Button>
            </div>
          )}
          {err && <div className="text-[11.5px] mt-3" style={{ color: "var(--error)" }}>{err}</div>}
        </div>
      </div>
    </div>
  );
}

/* ── 详情抽屉：SKILL.md 全文 + 文件清单 ── */
function PackDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof getSkillPack>> | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { getSkillPack(id).then(setData).catch(e => setErr(e instanceof Error ? e.message : "加载失败")); }, [id]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-[720px] max-w-[94vw] h-[80vh] flex flex-col rounded-2xl overflow-hidden anim-fade-up"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
        <div className="flex items-center justify-between px-5 py-4 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-2 min-w-0">
            <BookOpen size={16} style={{ color: "var(--accent)" }} className="flex-shrink-0" />
            <span className="text-[14px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>{data?.pack?.name || id}</span>
            {data?.pack?.license && <Badge tone="neutral">{data.pack.license.slice(0, 24)}</Badge>}
          </div>
          <button onClick={onClose} aria-label="关闭" className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]">
            <X size={16} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">
          {!data && !err && <StateView kind="loading" />}
          {err && <StateView kind="error" message={err} />}
          {data && (<>
            {data.files?.length > 0 && (
              <div className="mb-4">
                <div className="text-[11px] font-semibold uppercase tracking-wide mb-1.5" style={{ color: "var(--text-tertiary)" }}>文件（{data.files.length}）</div>
                <div className="flex flex-wrap gap-1.5">
                  {data.files.slice(0, 24).map(f => (
                    <span key={f.path} className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10.5px] font-mono"
                      style={{ background: "var(--surface-2)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
                      <FileText size={10} /> {f.path} <span style={{ color: "var(--text-tertiary)" }}>{fmtSize(f.size)}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}
            <div className="text-[11px] font-semibold uppercase tracking-wide mb-1.5" style={{ color: "var(--text-tertiary)" }}>SKILL.md</div>
            <pre className="text-[12px] leading-relaxed whitespace-pre-wrap rounded-xl p-4"
              style={{ background: "var(--surface-2)", color: "var(--text-primary)", border: "1px solid var(--border)", fontFamily: "var(--mono)" }}>
              {data.skill_md || "（空）"}
            </pre>
          </>)}
        </div>
      </div>
    </div>
  );
}

function SkillEvolutionModal({ skill, onClose, onChanged }: {
  skill: Skill;
  onClose: () => void;
  onChanged: (message: string) => void;
}) {
  const skillId = skill.id || "";
  const [status, setStatus] = useState<Awaited<ReturnType<typeof getSkillEvolution>> | null>(null);
  const [selected, setSelected] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    if (!skillId) return;
    setError("");
    try {
      const next = await getSkillEvolution(skillId);
      setStatus(next);
      const open = next.active_run;
      if (open) setSelected(current => current || open.variants.find(v => v.status === "candidate")?.id || "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法读取技能演进记录");
    }
  }, [skillId]);
  useEffect(() => { load(); }, [load]);

  const run: SkillEvolutionRun | null = status?.active_run || status?.runs?.[0] || null;
  const candidate = run?.variants.find(item => item.id === selected) || null;
  const quality = candidate?.evaluation.quality;
  const releaseEligible = quality?.release_eligible === true;
  const decide = async (action: "approve" | "reject" | "rollback") => {
    if (!skillId || !run) return;
    if (action === "approve" && !candidate) {
      setError("请先选择一个候选版本");
      return;
    }
    setBusy(action); setError("");
    try {
      if (action === "approve" && candidate) {
        await approveSkillEvolution(skillId, run.id, candidate.id, run.baseline_hash, reason);
        onChanged("候选已采用，后续 Chat 命中该技能时将使用新版本");
      } else if (action === "reject") {
        await rejectSkillEvolution(skillId, run.id, reason);
        onChanged("候选已拒绝，线上技能没有变化");
      } else {
        await rollbackSkillEvolution(skillId, run.id, reason);
        onChanged("技能已回滚到审核时的基线版本");
      }
      setSelected("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally { setBusy(""); }
  };
  const evaluate = async () => {
    if (!skillId || !run || !candidate) {
      setError("请先选择一个候选版本");
      return;
    }
    setBusy("evaluate"); setError("");
    try {
      const result = await evaluateSkillEvolution(skillId, run.id, candidate.id);
      setStatus(prev => prev ? {
        ...prev,
        state: result.run.status,
        active_run: result.run,
        runs: [result.run, ...prev.runs.filter(item => item.id !== result.run.id)],
      } : {
        schema: "hashmm.governed-skill-evolution.v2",
        skill_id: skillId,
        state: result.run.status,
        runs: [result.run],
        active_run: result.run,
        automatic_promotion_allowed: false,
      });
      onChanged(result.release_eligible
        ? "候选已通过历史回放和安全门禁，仍需你确认后才能采用"
        : "候选未满足发布门，线上技能保持不变");
    } catch (e) {
      setError(e instanceof Error ? e.message : "离线评测失败");
    } finally { setBusy(""); }
  };
  const generate = async () => {
    if (!skillId) return;
    setBusy("generate"); setError("");
    try {
      const result = await proposeSkillEvolution(skillId);
      const first = result.run.variants.find(item => item.status === "candidate");
      setSelected(first?.id || "");
      setStatus(prev => prev ? {
        ...prev,
        state: result.run.status,
        active_run: result.run,
        runs: [result.run, ...prev.runs.filter(item => item.id !== result.run.id)],
      } : {
        schema: "hashmm.governed-skill-evolution.v2",
        skill_id: skillId,
        state: result.run.status,
        runs: [result.run],
        active_run: result.run,
        automatic_promotion_allowed: false,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "候选生成失败");
    } finally { setBusy(""); }
  };
  const statusLabel: Record<string, string> = {
    idle: "尚未开始", review_required: "等待确认", promoted: "已采用",
    rejected: "已拒绝", rolled_back: "已回滚", stale: "基线已变化",
  };
  const autoBlocked = status?.automatic_promotion_allowed === false;

  return (
    <div className="fixed inset-0 z-[210] flex items-center justify-center p-4"
      style={{ background: "rgba(15,23,42,.34)", backdropFilter: "blur(8px)" }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-[min(900px,96vw)] max-h-[88vh] flex flex-col rounded-2xl overflow-hidden"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
        <div className="px-5 py-4 flex items-start gap-3" style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
            style={{ background: "var(--accent-light)", color: "var(--accent)" }}>
            <GitCompareArrows size={17} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-[15px] font-semibold" style={{ color: "var(--text-primary)" }}>改进技能 · {skill.name || "未命名"}</div>
            <div className="text-[11.5px] mt-1 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
              候选在隔离区生成，先检查意图、凭据和权限变化，再由你明确采用。候选不会自动进入 Chat。
            </div>
          </div>
          <Badge tone={run?.status === "promoted" ? "success" : run?.status === "review_required" ? "accent" : "neutral"}>
            {statusLabel[run?.status || status?.state || "idle"] || run?.status || "尚未开始"}
          </Badge>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]" aria-label="关闭技能改进">
            <X size={17} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          {!status && !error && <StateView kind="loading" />}
          {autoBlocked && (
            <div className="mb-4 px-3.5 py-3 rounded-xl flex items-start gap-2.5"
              style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
              <ShieldCheck size={15} className="mt-0.5 flex-shrink-0" style={{ color: "var(--success)" }} />
              <div>
                <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>生产技能受人工决策保护</div>
                <div className="text-[11px] mt-0.5 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
                  候选必须使用同一模型完成基线/候选成对回放，并通过安全对抗、成本和延迟门禁；最后仍由你决定是否采用。
                </div>
              </div>
            </div>
          )}
          {error && (
            <div className="mb-4 px-3.5 py-2.5 rounded-xl flex items-center gap-2 text-[11.5px]"
              style={{ background: "color-mix(in srgb,var(--error) 8%,transparent)", color: "var(--error)" }}>
              <ShieldAlert size={14} /> {error}
            </div>
          )}
          {!run && status && (
            <StateView kind="empty" icon={GitCompareArrows}
              title="还没有改进候选"
              message="模型可提出候选，但不会使用规则模板伪造成功；模型服务不可用时会明确失败。"
              action={<Button variant="primary" icon={Sparkles} busy={busy === "generate"} onClick={generate}>生成隔离候选</Button>} />
          )}
          {run && (
            <>
              <div className="flex items-center gap-2 mb-3">
                <span className="text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>审核基线</span>
                <code className="text-[10px] px-2 py-1 rounded-md" style={{ background: "var(--surface-2)", color: "var(--text-tertiary)" }}>
                  {run.baseline_hash.slice(0, 16)}
                </code>
                <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>权限扩大：否</span>
                {run.work_run_id && <span className="ml-auto text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>运行 {run.work_run_id.slice(-10)}</span>}
              </div>
              {run.status === "review_required" && (
                <>
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                    {run.variants.filter(item => item.status === "candidate").map(item => {
                      const chosen = item.id === selected;
                      const failed = item.evaluation.failed_required?.length || 0;
                      const replay = item.evaluation.quality;
                      return (
                        <button key={item.id} type="button" onClick={() => setSelected(item.id)}
                          className="text-left p-4 rounded-xl transition-colors"
                          style={{
                            background: chosen ? "var(--accent-light)" : "var(--bg-secondary)",
                            border: `1px solid ${chosen ? "var(--accent)" : "var(--border)"}`,
                          }}>
                          <div className="flex items-center gap-2">
                            <span className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>候选 {item.id.slice(-6)}</span>
                            <Badge tone={failed ? "error" : "success"}>{failed ? `${failed} 项未通过` : "静态门禁通过"}</Badge>
                            {replay?.status && replay.status !== "not_evaluable" && (
                              <Badge tone={replay.release_eligible ? "success" : "error"}>
                                {replay.release_eligible ? "可发布" : replay.status === "insufficient_evidence" ? "证据不足" : "回放未通过"}
                              </Badge>
                            )}
                          </div>
                          <div className="mt-3 text-[11.5px] leading-[1.7] whitespace-pre-wrap"
                            style={{ color: "var(--text-secondary)" }}>{item.prompt_text || item.prompt_preview}</div>
                          <div className="mt-3 pt-2.5 space-y-1" style={{ borderTop: "1px dashed var(--border)" }}>
                            {(item.evaluation.checks || []).map(check => (
                              <div key={check.id} className="flex items-center gap-1.5 text-[10px]"
                                style={{ color: check.passed ? "var(--text-tertiary)" : "var(--error)" }}>
                                {check.passed ? <CheckCircle2 size={10} /> : <XCircle size={10} />}
                                <span>{check.id}</span><span className="ml-auto font-mono">{check.detail}</span>
                              </div>
                            ))}
                          </div>
                        </button>
                      );
                    })}
                  </div>
                  {candidate && (
                    <div className="mt-3 rounded-xl px-4 py-3.5"
                      style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                      <div className="flex items-center gap-2">
                        <FlaskConical size={14} style={{ color: "var(--accent)" }} />
                        <span className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>发布前回放</span>
                        <Badge tone={releaseEligible ? "success" : quality?.status && quality.status !== "not_evaluable" ? "error" : "neutral"}>
                          {releaseEligible ? "全部门禁通过" : quality?.status === "insufficient_evidence" ? "历史证据不足" : quality?.status === "failed" ? "存在阻断项" : "尚未评测"}
                        </Badge>
                        {quality?.historical_cases !== undefined && (
                          <span className="ml-auto text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>
                            历史 {quality.historical_cases} · 对抗 {quality.adversarial_cases || 0}
                          </span>
                        )}
                      </div>
                      {quality?.baseline && quality?.candidate && quality?.delta ? (
                        <>
                          <div className="grid grid-cols-3 gap-2 mt-3">
                            {[
                              ["基线质量", quality.baseline.mean_score?.toFixed(3)],
                              ["候选质量", quality.candidate.mean_score?.toFixed(3)],
                              ["质量增量", `${(quality.delta.mean_score || 0) >= 0 ? "+" : ""}${quality.delta.mean_score?.toFixed(3)}`],
                              ["基线成本", `${quality.baseline.output_tokens_estimated || 0} token`],
                              ["候选成本", `${quality.candidate.output_tokens_estimated || 0} token`],
                              ["候选延迟", `${quality.candidate.latency_ms || 0} ms`],
                            ].map(([label, value]) => (
                              <div key={label} className="rounded-lg px-2.5 py-2" style={{ background: "var(--surface-2)" }}>
                                <div className="text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>{label}</div>
                                <div className="text-[11px] font-mono mt-0.5" style={{ color: "var(--text-primary)" }}>{value}</div>
                              </div>
                            ))}
                          </div>
                          <div className="mt-2.5 space-y-1">
                            {(quality.gates || []).map(gate => (
                              <div key={gate.id} className="flex items-center gap-1.5 text-[10px]"
                                style={{ color: gate.passed ? "var(--text-tertiary)" : "var(--error)" }}>
                                {gate.passed ? <CheckCircle2 size={10} /> : <XCircle size={10} />}
                                <span>{gate.id}</span><span className="ml-auto font-mono">{gate.detail}</span>
                              </div>
                            ))}
                          </div>
                        </>
                      ) : (
                        <div className="text-[10.5px] mt-2 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
                          将使用当前账号已确认的历史案例，分别运行基线和候选；原始问题与回答不会写入评测表。
                        </div>
                      )}
                      {quality?.status === "insufficient_evidence" && (
                        <div className="text-[10.5px] mt-2" style={{ color: "var(--warning)" }}>
                          至少需要 2 条相关案例。请先在 Chat 中真实使用该技能并反馈，或在质量看板确认失败案例。
                        </div>
                      )}
                      {quality?.limitation && (
                        <div className="text-[9.5px] mt-2 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
                          {quality.limitation}
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
              {run.status !== "review_required" && (
                <div className="p-4 rounded-xl" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                  <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>
                    {statusLabel[run.status] || run.status}
                  </div>
                  <div className="text-[11px] mt-1" style={{ color: "var(--text-tertiary)" }}>
                    {run.decision_reason || (run.status === "promoted" ? "新版本已经写入生产技能，Chat 将在下次命中时读取。" : "生产技能未被候选覆盖。")}
                  </div>
                </div>
              )}
              <div className="mt-4">
                <label className="text-[10.5px] font-medium" style={{ color: "var(--text-tertiary)" }}>决策说明（可选）</label>
                <input value={reason} onChange={e => setReason(e.target.value)} maxLength={500}
                  className={`${inputClass} mt-1.5`} style={inputStyle}
                  placeholder="记录采用、拒绝或回滚的依据" />
              </div>
            </>
          )}
        </div>

        <div className="px-5 py-3.5 flex items-center gap-2" style={{ borderTop: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
          {run?.status === "review_required" && <>
            <Button variant="secondary" busy={busy === "reject"} onClick={() => decide("reject")}>拒绝全部候选</Button>
            <span className="flex-1" />
            <Button variant="secondary" icon={FlaskConical} busy={busy === "evaluate"} disabled={!candidate}
              onClick={evaluate}>运行离线评测</Button>
            <Button variant="primary" icon={CheckCircle2} busy={busy === "approve"} disabled={!candidate || !releaseEligible}
              onClick={() => decide("approve")}>采用所选版本</Button>
          </>}
          {run?.rollback_available && <>
            <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>仅当线上版本未再次变化时可以精确回滚</span>
            <span className="flex-1" />
            <Button variant="secondary" icon={RotateCcw} busy={busy === "rollback"} onClick={() => decide("rollback")}>回滚到基线</Button>
          </>}
          {run && !run.rollback_available && run.status !== "review_required" && <>
            <span className="flex-1" />
            <Button variant="secondary" onClick={generate} busy={busy === "generate"} icon={Sparkles}>生成新一轮候选</Button>
          </>}
          {!run && <><span className="flex-1" /><Button variant="secondary" onClick={onClose}>关闭</Button></>}
        </div>
      </div>
    </div>
  );
}

export function EvolutionView() {
  // ① 技能包
  const [packs, setPacks] = useState<SkillPack[] | undefined>(undefined);
  const [packDenied, setPackDenied] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  // V300 第四期：技能市场
  const [catalog, setCatalog] = useState<import("@/lib/api").CatalogSkill[] | undefined>(undefined);
  const [marketOpen, setMarketOpen] = useState(false);
  const [installing, setInstalling] = useState("");
  // ② 学习型技能 / ③ 经验回放
  const [skills, setSkills] = useState<Skill[] | undefined>(undefined);
  const [eps, setEps] = useState<Episode[] | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [evolutionSkill, setEvolutionSkill] = useState<Skill | null>(null);
  const [skillSort, setSkillSort] = useState<SkillSort>("default");
  const [epFilter, setEpFilter] = useState<OutcomeFilter>("all");
  const shownSkills = useMemo(() => sortSkills((skills || []) as SkillRec[], skillSort) as Skill[], [skills, skillSort]);
  const skSummary = useMemo(() => skillsSummary((skills || []) as SkillRec[]), [skills]);
  const epCounts = useMemo(() => episodeOutcomeCounts((eps || []) as EpisodeRec[]), [eps]);
  const shownEps = useMemo(() => filterEpisodes((eps || []) as EpisodeRec[], epFilter) as Episode[], [eps, epFilter]);

  const loadPacks = useCallback(async () => {
    try {
      const r = await listSkillPacks();
      setPacks(Array.isArray(r?.packs) ? r.packs : []);
      setPackDenied(false);
    } catch { setPacks([]); setPackDenied(true); }
  }, []);
  // V300 第四期：加载技能市场 + 一键安装
  const loadCatalog = useCallback(async () => {
    try {
      const { listSkillCatalog } = await import("@/lib/api");
      const r = await listSkillCatalog();
      setCatalog(Array.isArray(r?.catalog) ? r.catalog : []);
    } catch { setCatalog([]); }
  }, []);
  const onInstallCatalog = useCallback(async (catalogId: string) => {
    setInstalling(catalogId);
    try {
      const { installFromCatalog } = await import("@/lib/api");
      const r = await installFromCatalog(catalogId);
      setNotice((r as { message?: string }).message || "已安装");
      await loadCatalog();
      await loadPacks();
    } catch { setNotice("安装失败（需要管理员权限）"); }
    finally { setInstalling(""); }
  }, [loadCatalog, loadPacks]);
  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [s, e] = await Promise.all([listEvolutionSkills().catch(() => null), listEpisodes(40).catch(() => null)]);
      setSkills(s && Array.isArray(s.skills) ? s.skills : []);
      setEps(e && Array.isArray(e.episodes) ? e.episodes : []);
      await loadPacks();
    } finally { setBusy(false); }
  }, [loadPacks]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (!notice) return; const t = setTimeout(() => setNotice(""), 4000); return () => clearTimeout(t); }, [notice]);

  const onTogglePack = async (p: SkillPack) => {
    setPacks(arr => arr?.map(x => x.id === p.id ? { ...x, enabled: !p.enabled } : x));   // 乐观更新
    try { await toggleSkillPack(p.id, !p.enabled); } catch { loadPacks(); setNotice("操作失败（需要管理员权限）"); }
  };
  const onDeletePack = async (p: SkillPack) => {
    setPacks(arr => arr?.filter(x => x.id !== p.id));
    try { await deleteSkillPack(p.id); } catch { loadPacks(); setNotice("删除失败（需要管理员权限）"); }
  };
  const onSeed = async () => {
    try { const r = await seedBuiltinSkillPacks(); setNotice(r?.added?.length ? `已补种 ${r.added.length} 个内置包` : "内置包已齐全"); loadPacks(); }
    catch { setNotice("操作失败（需要管理员权限）"); }
  };

  const onFeedback = async (id: string | undefined, fb: "up" | "down") => {
    if (!id) return;
    setSkills(arr => arr?.map(s => s.id === id ? { ...s, quality_score: Math.max(0, Math.min(1, (s.quality_score ?? 0) + (fb === "up" ? 0.1 : -0.15))) } : s));
    try { await skillFeedback(id, fb); } catch { load(); }
  };
  const onDeleteSkill = async (id: string | undefined) => {
    if (!id) return;
    setSkills(arr => arr?.filter(s => s.id !== id));
    try { await deleteEvolutionSkill(id); } catch { load(); }
  };

  const enabledCount = (packs || []).filter(p => p.enabled).length;
  const sendEvolutionToChat = () => insertContextIntoChat("evolution", "技能与经验回放快照", {
    skill_pack_counts: { total: (packs || []).length, enabled: enabledCount },
    enabled_packs: (packs || []).filter(p => p.enabled).slice(0, 20).map(p => ({ id: p.id, name: p.name, description: p.description })),
    learned_skill_summary: skSummary,
    learned_skills: shownSkills.slice(0, 20).map(s => ({ id: s.id, name: s.name, description: s.description, quality_score: s.quality_score, use_count: s.use_count })),
    episode_outcomes: epCounts,
    recent_failures: (eps || []).filter(e => ["fail", "error", "bad"].includes((e.outcome || "").toLowerCase())).slice(0, 12),
  }, "请基于我带回的技能与经验回放快照，判断哪些能力真的在服务当前 RAG-Agent、哪些长期未触发或经常失败；给出可验证的改进与回归测试建议，不要自动修改或上架技能。", "evolution-center");

  return (
    <PanelShell>
      <PageHeader icon={Sparkles} title="自我进化"
        subtitle="技能包（人写的操作手册）+ 学习型技能（自动沉淀）+ 经验回放 —— 系统越用越聪明"
        actions={<>
          <Button variant="primary" icon={MessageSquare} size="sm" onClick={sendEvolutionToChat}>带经验回 Chat</Button>
          <Button variant="primary" icon={Store} size="sm" onClick={() => { setMarketOpen(true); if (!catalog) loadCatalog(); }}>技能市场</Button>
          <Button variant="secondary" icon={Plus} size="sm" onClick={() => setImportOpen(true)}>导入技能包</Button>
          <Button icon={RefreshCw} busy={busy} size="sm" onClick={load}>刷新</Button>
        </>} />

      {marketOpen && (
        <SkillMarketModal
          catalog={catalog}
          installing={installing}
          onInstall={onInstallCatalog}
          onClose={() => setMarketOpen(false)}
        />
      )}
      {notice && (
        <div className="mb-4 px-3.5 py-2.5 rounded-xl text-[12px] inline-flex items-center gap-2"
          style={{ background: "var(--accent-light)", color: "var(--accent)" }}>
          <CheckCircle2 size={13} /> {notice}
        </div>
      )}

      {/* ── ① 技能包 ── */}
      <SectionTitle right={packs && packs.length > 0 && (
        <div className="flex items-center gap-2">
          <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{enabledCount}/{packs.length} 启用 · 命中当前任务时自动装载</span>
          <button onClick={onSeed} className="text-[11px] px-2 py-1 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]"
            style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}>补齐内置包</button>
        </div>
      )}>技能包</SectionTitle>
      <div className="text-[10.5px] -mt-1 mb-3" style={{ color: "var(--text-tertiary)" }}>
        与 Claude Code 同格式的 SKILL.md 操作手册。可导入 GitHub 上任何遵循 Agent Skills 规范的技能仓库；启用后按任务相关度自动注入给 Agent。
      </div>
      {packs !== undefined && packs.length > 0 && (
        <div className="text-[11.5px] -mt-1 mb-2" style={{ color: "var(--text-tertiary)" }}>
          已装 <b style={{ color: "var(--text-primary)" }}>{packs.length}</b> 个 · 启用 <b style={{ color: "var(--accent)" }}>{packs.filter(x => x.enabled).length}</b> 个 —— 启用的包按任务相关度自动注入 Agent
        </div>
      )}
      {packs === undefined && <StateView kind="loading" />}
      {packs !== undefined && packs.length === 0 && (
        <StateView kind="empty" icon={Package}
          title={packDenied ? "技能包接口不可用" : "还没有技能包"}
          message={packDenied ? "多为后端代码过旧（该接口为较新版本提供）或该账号无权限——你已登录的话，升级后端（unzip -o 新包 → ./hashmm-start.sh）后此区即恢复。" : "导入你的第一个技能包 —— 上传 zip、粘贴 GitHub 链接，或一键安装内置包。"}
          action={packDenied ? <Button variant="secondary" size="sm" icon={RefreshCw} onClick={() => loadPacks()}>重试</Button> : (
            <div className="flex items-center gap-2">
              <Button variant="primary" icon={Plus} size="sm" onClick={() => setImportOpen(true)}>导入技能包</Button>
              <Button variant="secondary" icon={Download} size="sm" onClick={onSeed}>安装内置包</Button>
            </div>
          )} />
      )}
      {packs !== undefined && packs.length > 0 && (
        <CardGrid min={320}>
          {packs.map(p => {
            const src = SOURCE_LABEL(p.source);
            return (
              <Card key={p.id} padding="p-4" className="flex flex-col">
                <div className="flex items-start gap-2.5">
                  <div className="w-8 h-8 rounded-[9px] flex items-center justify-center flex-shrink-0"
                    style={{ background: p.enabled ? "var(--accent-light)" : "var(--surface-2)" }}>
                    <Package size={15} style={{ color: p.enabled ? "var(--accent)" : "var(--text-tertiary)" }} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-[13px] font-bold truncate" style={{ color: "var(--text-primary)" }}>{p.name}</span>
                      <Badge tone={src.tone}>{src.label}</Badge>
                    </div>
                    <div className="text-[11px] mt-1 leading-relaxed" style={{ color: "var(--text-tertiary)", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                      {p.description || "（无描述）"}
                    </div>
                  </div>
                  <Toggle checked={p.enabled} onChange={() => onTogglePack(p)} ariaLabel={`启用技能包 ${p.name}`} />
                </div>
                <div className="flex items-center gap-2 mt-3 pt-2.5" style={{ borderTop: "1px dashed var(--border)" }}>
                  <span className="text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>{p.file_count} 文件 · {fmtSize(p.size_bytes)}</span>
                  <span className="flex-1" />
                  <button onClick={() => setDetailId(p.id)}
                    className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]"
                    style={{ color: "var(--text-secondary)" }}><BookOpen size={11} /> 查看</button>
                  <button onClick={() => onDeletePack(p)} title="删除技能包" aria-label={`删除技能包 ${p.name}`}
                    className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg transition-colors hover:bg-[color-mix(in_srgb,var(--error)_8%,transparent)]"
                    style={{ color: "var(--text-tertiary)" }}><Trash2 size={11} /></button>
                </div>
              </Card>
            );
          })}
        </CardGrid>
      )}

      {/* ── ② 学习型技能 ── */}
      <SectionTitle right={skills && skills.length > 0 ? (
        <div className="flex items-center gap-2">
          <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>平均质量 {skSummary.avgQuality}% · 累计用 {skSummary.totalUses}</span>
          <select value={skillSort} onChange={e => setSkillSort(e.target.value as SkillSort)}
            className="h-7 px-1.5 rounded-lg text-[11px] outline-none" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
            <option value="default">默认</option><option value="quality">按质量</option><option value="usage">按用量</option>
          </select>
          <Badge tone="neutral" mono>{skills.length}</Badge>
        </div>
      ) : (skills && <Badge tone="neutral" mono>{skills.length}</Badge>)}>学习型技能</SectionTitle>
      <div className="text-[10.5px] -mt-1 mb-3" style={{ color: "var(--text-tertiary)" }}>系统从问答中自动沉淀的可复用流程 —— 顶有用的、踩没用的、删错的，质量分越高越常被调用</div>
      {skills === undefined && <StateView kind="loading" />}
      {skills !== undefined && skills.length === 0 && <StateView kind="empty" icon={Sparkles} message="暂无自动沉淀的技能。多用 Agent 完成任务，它会把成功的解题套路记下来。" />}
      {skills !== undefined && skills.length > 0 && (
        <CardGrid min={320}>
          {shownSkills.map((s, i) => {
            const q = typeof s.quality_score === "number" ? s.quality_score : 0;
            const qPct = Math.round(q * 100);
            const qColor = q >= 0.6 ? "var(--success)" : q >= 0.3 ? "var(--warning)" : "var(--error)";
            const triggers = Array.isArray(s.trigger_patterns) ? s.trigger_patterns : [];
            return (
              <Card key={s.id || s.name || i} padding="p-4" className="flex flex-col">
                <div className="flex items-start justify-between gap-2">
                  <div className="text-[13px] font-bold flex-1 min-w-0" style={{ color: "var(--text-primary)" }}>{s.name || "（未命名技能）"}</div>
                  {typeof s.use_count === "number" && <span className="text-[10px] font-mono flex-shrink-0 mt-0.5" style={{ color: "var(--text-tertiary)" }}>用 {s.use_count} 次</span>}
                </div>
                {s.description && <div className="text-[11.5px] mt-1 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>{s.description}</div>}
                {triggers.length > 0 && (
                  <div className="flex gap-1 flex-wrap mt-2">
                    {triggers.slice(0, 6).map((t: string, j: number) => <Badge key={j} tone="neutral">{t}</Badge>)}
                  </div>
                )}
                <div className="flex items-center gap-2 mt-2.5">
                  <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>质量</span>
                  <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg-tertiary)" }}>
                    <div className="h-full rounded-full transition-all" style={{ width: `${qPct}%`, background: qColor }} />
                  </div>
                  <span className="text-[10px] font-mono" style={{ color: qColor }}>{qPct}%</span>
                </div>
                <div className="flex items-center gap-1.5 mt-2.5 pt-2.5" style={{ borderTop: "1px dashed var(--border)" }}>
                  <button onClick={() => onFeedback(s.id, "up")} disabled={!s.id} title="有用：提高被 Agent 调用的概率"
                    className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-secondary)" }}><ThumbsUp size={12} /> 有用</button>
                  <button onClick={() => onFeedback(s.id, "down")} disabled={!s.id} title="没用：降低被调用的概率"
                    className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-secondary)" }}><ThumbsDown size={12} /> 没用</button>
                  {s.scope === "personal" && (
                    <button onClick={() => setEvolutionSkill(s)} disabled={!s.id} title="生成隔离候选，经确认后再用于 Chat"
                      className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]"
                      style={{ color: "var(--accent)" }}><GitCompareArrows size={12} /> 改进</button>
                  )}
                  <button onClick={() => onDeleteSkill(s.id)} disabled={!s.id} title="删除这条技能" aria-label="删除这条技能"
                    className="ml-auto flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg transition-colors hover:bg-[color-mix(in_srgb,var(--error)_8%,transparent)]" style={{ color: "var(--text-tertiary)" }}><Trash2 size={12} /></button>
                </div>
              </Card>
            );
          })}
        </CardGrid>
      )}

      {/* ── ③ 经验回放 ── */}
      <SectionTitle right={eps && <Badge tone="neutral" mono>近 {eps.length} 条</Badge>}>经验回放</SectionTitle>
      {eps !== undefined && eps.length > 0 && (
        <div className="inline-flex rounded-lg overflow-hidden mb-3" style={{ border: "1px solid var(--border)" }}>
          {([["all", `全部 ${epCounts.total}`], ["success", `成功 ${epCounts.success}`], ["fail", `失败 ${epCounts.fail}`], ["unknown", `未知 ${epCounts.unknown}`]] as const).map(([v, label], i) => (
            <button key={v} onClick={() => setEpFilter(v)} className="px-2.5 py-1 text-[11px] transition-colors"
              style={{ background: epFilter === v ? "var(--accent-light)" : "transparent", color: epFilter === v ? "var(--accent)" : "var(--text-tertiary)", borderLeft: i ? "1px solid var(--border)" : "none" }}>{label}</button>
          ))}
        </div>
      )}
      {eps === undefined && <StateView kind="loading" />}
      {eps !== undefined && eps.length === 0 && <StateView kind="empty" icon={Sparkles} message="还没有经验记录。多用 Agent 问答，这里会逐条沉淀「问了什么 · 用了什么策略 · 结果如何 · 学到什么」。" />}
      {eps !== undefined && eps.length > 0 && shownEps.length === 0 && <div className="text-center py-8 text-[12.5px]" style={{ color: "var(--text-tertiary)" }}>该结果下暂无经验</div>}
      {shownEps.length > 0 && (
        <div className="flex flex-col gap-2">
          {shownEps.map((e, i) => {
            const os = outcomeStyle(e.outcome);
            return (
              <Card key={e.id || i} padding="p-4">
                <div className="flex items-start gap-2">
                  <os.Icon size={15} style={{ color: os.color }} className="flex-shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <div className="text-[12.5px] font-medium break-words" style={{ color: "var(--text-primary)" }}>{e.query || "（无查询）"}</div>
                    <div className="flex items-center gap-2 flex-wrap mt-1">
                      {e.strategy && <Badge tone="neutral">{e.strategy}</Badge>}
                      {e.query_type && <Badge tone="neutral">{e.query_type}</Badge>}
                      <span className="text-[10px]" style={{ color: os.color }}>{os.label}</span>
                      {typeof e.elapsed_ms === "number" && e.elapsed_ms > 0 && <span className="text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>{(e.elapsed_ms / 1000).toFixed(1)}s</span>}
                    </div>
                    {e.key_insight && <div className="text-[11.5px] mt-1.5 leading-relaxed pl-2" style={{ color: "var(--text-secondary)", borderLeft: "2px solid var(--accent)" }}>{e.key_insight}</div>}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {importOpen && <ImportModal onClose={() => setImportOpen(false)} onDone={(msg) => { setImportOpen(false); setNotice(msg); loadPacks(); }} />}
      {detailId && <PackDetail id={detailId} onClose={() => setDetailId(null)} />}
      {evolutionSkill && (
        <SkillEvolutionModal
          skill={evolutionSkill}
          onClose={() => setEvolutionSkill(null)}
          onChanged={(message) => { setNotice(message); load(); }}
        />
      )}
    </PanelShell>
  );
}

// V300 第四期：技能市场弹窗——浏览精选技能（Anthropic 官方 skills，已适配本项目）+ 一键安装 + 权限声明可见
function SkillMarketModal({ catalog, installing, onInstall, onClose }: {
  catalog: import("@/lib/api").CatalogSkill[] | undefined;
  installing: string;
  onInstall: (catalogId: string) => void;
  onClose: () => void;
}) {
  const fmtKb = (n: number) => (n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.round(n / 1024)} KB`);
  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,.35)" }} onClick={onClose}>
      <div className="w-[min(680px,94vw)] max-h-[84vh] overflow-auto rounded-2xl p-5" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center mb-1">
          <Store size={18} style={{ color: "var(--accent)" }} />
          <span className="ml-2 text-[15px] font-semibold flex-1" style={{ color: "var(--text-primary)" }}>技能市场</span>
          <button onClick={onClose} className="p-1" style={{ color: "var(--text-tertiary)" }}><X size={18} /></button>
        </div>
        <div className="text-[12px] mb-4" style={{ color: "var(--text-secondary)" }}>
          精选技能（Anthropic 官方 skills，已适配本项目的 SKILL.md 规范与权限模型）。安装后 Agent 遇到相关任务会自动参考。
        </div>
        {catalog === undefined ? (
          <div className="py-10 text-center text-[13px]" style={{ color: "var(--text-tertiary)" }}>加载市场…</div>
        ) : catalog.length === 0 ? (
          <div className="py-10 text-center text-[13px]" style={{ color: "var(--text-tertiary)" }}>市场暂无可用技能</div>
        ) : (
          <div className="space-y-2.5">
            {catalog.map((sk) => (
              <div key={sk.catalog_id} className="p-3.5 rounded-xl" style={{ border: "1px solid var(--border)" }}>
                <div className="flex items-start gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="text-[13.5px] font-semibold" style={{ color: "var(--text-primary)" }}>{sk.name}</div>
                    <div className="text-[12px] mt-1 leading-relaxed" style={{ color: "var(--text-secondary)" }}>{sk.description}</div>
                    <div className="flex flex-wrap items-center gap-2 mt-2">
                      <span className="text-[10.5px] px-1.5 py-0.5 rounded" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>{sk.file_count} 文件 · {fmtKb(sk.size_bytes)}</span>
                      {sk.allowed_tools.length > 0 && (
                        <span className="text-[10.5px] px-1.5 py-0.5 rounded inline-flex items-center gap-1" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>
                          <ShieldCheck size={10} /> 工具: {sk.allowed_tools.join(", ")}
                        </span>
                      )}
                      {sk.network && <span className="text-[10.5px] px-1.5 py-0.5 rounded inline-flex items-center gap-1" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}><Wifi size={10} /> 联网</span>}
                      {sk.filesystem && <span className="text-[10.5px] px-1.5 py-0.5 rounded inline-flex items-center gap-1" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}><HardDrive size={10} /> 写文件</span>}
                    </div>
                  </div>
                  {sk.installed ? (
                    <span className="text-[12px] shrink-0 inline-flex items-center gap-1" style={{ color: "var(--success, #22a06b)" }}><CheckCircle2 size={14} /> 已安装</span>
                  ) : (
                    <Button variant="primary" size="sm" busy={installing === sk.catalog_id} onClick={() => onInstall(sk.catalog_id)}>安装</Button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

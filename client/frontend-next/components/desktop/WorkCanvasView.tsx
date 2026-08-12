"use client";

import {
  AlertCircle, ArrowRight, Bot, Check, CheckCircle2, Circle, Clock3,
  Download, FileCheck2, FileText, GitBranch, History, Laptop, Loader2, Pause,
  Play, RefreshCw, RotateCcw, ShieldCheck, Sparkles, Undo2, XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  subscribeWorkFeed, withToken, workExecutionDevices, workRunAnnotation,
  workRunCommand, workRunDecision, workRunPlacement, workRunWorkspace,
  workWorkflowCandidate, workWorkflowPublish,
} from "@/lib/api";
import { useStore } from "@/lib/store";
import type {
  GovernedNextAction, WorkCanvas, WorkCanvasResult, WorkControlAction,
  WorkExecutionDevice,
} from "@/lib/types";

type Tab = "overview" | "process" | "evidence" | "results";

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "overview", label: "概览" },
  { id: "process", label: "过程" },
  { id: "evidence", label: "依据" },
  { id: "results", label: "成果" },
];

const STAGE_STYLE: Record<string, { color: string; bg: string }> = {
  done: { color: "#15803d", bg: "rgba(22,163,74,.09)" },
  running: { color: "var(--accent)", bg: "var(--accent-light)" },
  blocked: { color: "#b42318", bg: "rgba(180,35,24,.08)" },
  skipped: { color: "var(--text-tertiary)", bg: "var(--bg-secondary)" },
  pending: { color: "var(--text-tertiary)", bg: "var(--bg-secondary)" },
};

function ago(ts: number): string {
  if (!ts) return "尚未同步";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (seconds < 60) return "刚刚同步";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return new Date(ts * 1000).toLocaleDateString("zh-CN");
}

function artifactType(result: WorkCanvasResult): string {
  if (result.kind === "document") return "docx";
  if (result.kind === "presentation") return "pptx";
  if (result.kind === "spreadsheet") return "xlsx";
  if (result.kind === "canvas") return /\.svg$/i.test(result.name) ? "svg" : "html";
  if (result.kind === "image") return "image";
  if (result.kind === "pdf") return "pdf";
  return "code";
}

function StateIcon({ status, size = 15 }: { status: string; size?: number }) {
  if (status === "done" || status === "passed" || status === "verified" || status === "completed") {
    return <CheckCircle2 size={size} style={{ color: "#15803d" }} />;
  }
  if (status === "blocked" || status === "failed" || status === "invalid" || status === "missing") {
    return <XCircle size={size} style={{ color: "#b42318" }} />;
  }
  if (status === "running" || status === "ready") {
    return <Loader2 size={size} className={status === "running" ? "animate-spin" : ""} style={{ color: "var(--accent)" }} />;
  }
  return <Circle size={size} style={{ color: "var(--text-tertiary)" }} />;
}

function SectionCard({ title, hint, children }: {
  title: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl overflow-hidden"
      style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
      <div className="px-4 py-3.5" style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{title}</div>
        {hint && <div className="text-[10.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{hint}</div>}
      </div>
      {children}
    </section>
  );
}

export function WorkCanvasView() {
  const runId = useStore(s => s.workDetailId);
  const set = useStore(s => s.set);
  const [canvas, setCanvas] = useState<WorkCanvas | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [busy, setBusy] = useState(true);
  const [actionBusy, setActionBusy] = useState("");
  const [error, setError] = useState("");
  const [changeOpen, setChangeOpen] = useState(false);
  const [changeNote, setChangeNote] = useState("");
  const [annotationResult, setAnnotationResult] = useState<WorkCanvasResult | null>(null);
  const [annotationNote, setAnnotationNote] = useState("");
  const [devices, setDevices] = useState<WorkExecutionDevice[]>([]);
  const [devicesOpen, setDevicesOpen] = useState(false);
  const cursorRef = useRef(0);

  const refresh = useCallback(async (force = false, quiet = false) => {
    if (!runId) {
      setCanvas(null); setBusy(false); return;
    }
    if (!quiet) setBusy(true);
    try {
      const next = await workRunWorkspace(runId, force);
      setCanvas(next); setError("");
      cursorRef.current = Math.max(cursorRef.current, Number(next.sync?.change_cursor || 0));
    } catch (e) {
      setError((e as Error)?.message || "工作画布暂时无法更新");
    } finally {
      if (!quiet) setBusy(false);
    }
  }, [runId]);

  useEffect(() => {
    let disposed = false;
    let unsubscribe = () => {};
    void refresh().then(() => {
      if (disposed) return;
      unsubscribe = subscribeWorkFeed({
        afterCursor: cursorRef.current,
        onFeed: feed => {
          cursorRef.current = Math.max(cursorRef.current, Number(feed.next_cursor || 0));
          if ((feed.items || []).some(item => item.id === runId)) {
            void refresh(true, true);
          }
        },
      });
    });
    const timer = window.setInterval(() => refresh(false, true), 60_000);
    const online = () => refresh(true, true);
    window.addEventListener("hmm-backend-online", online);
    return () => {
      disposed = true;
      unsubscribe();
      window.clearInterval(timer);
      window.removeEventListener("hmm-backend-online", online);
    };
  }, [refresh]);

  const annotate = useCallback(async () => {
    if (!canvas || !annotationResult || annotationNote.trim().length < 2) {
      setError("请写明希望修改的具体内容");
      return;
    }
    const annotationId = globalThis.crypto.randomUUID();
    setActionBusy("annotation");
    try {
      const result = await workRunAnnotation(canvas.run_id, {
        annotation_id: annotationId,
        idempotency_key: `annotation:${annotationId}`,
        artifact_id: annotationResult.id,
        artifact_revision: annotationResult.version,
        target: { kind: "artifact", version_ref: annotationResult.version_ref },
        note: annotationNote.trim(),
        expected_revision: canvas.sync.revision,
      });
      if (!result.ok) throw new Error(result.error || "批注没有保存");
      setAnnotationResult(null); setAnnotationNote("");
      await refresh(true, true);
    } catch (e) {
      setError((e as Error)?.message || "工作状态已经变化，请刷新后重试");
    } finally {
      setActionBusy("");
    }
  }, [annotationNote, annotationResult, canvas, refresh]);

  const openConversation = useCallback((prompt = "") => {
    if (!canvas?.conversation_id) return;
    set({
      desktopView: null,
      sid: canvas.conversation_id,
      pendingPrompt: prompt,
    });
    try { history.pushState({}, "", `/chat/${canvas.conversation_id}`); } catch { /* no-op */ }
  }, [canvas, set]);

  const openResult = useCallback((result: WorkCanvasResult) => {
    if (!result.download_url) {
      openConversation(`继续处理成果“${result.name}”`);
      return;
    }
    set({
      rightPanelOpen: true,
      inspectorTab: "artifact",
      artifactPanel: {
        convId: canvas?.conversation_id,
        type: artifactType(result),
        filename: result.name,
        download_url: result.download_url,
      },
    });
  }, [canvas?.conversation_id, openConversation, set]);

  const runControl = useCallback(async (action: WorkControlAction) => {
    if (!canvas) return;
    setActionBusy(action);
    try {
      const result = await workRunCommand(canvas.run_id, action, canvas.sync.revision);
      if (!result.ok) throw new Error(result.error || "操作没有执行");
      await refresh(true, true);
    } catch (e) {
      setError((e as Error)?.message || "工作状态已经变化，请刷新后重试");
    } finally {
      setActionBusy("");
    }
  }, [canvas, refresh]);

  const decide = useCallback(async (action: "accept_delivery" | "request_changes") => {
    if (!canvas) return;
    if (action === "request_changes" && changeNote.trim().length < 2) {
      setError("请先写明需要修改的内容");
      return;
    }
    setActionBusy(action);
    try {
      const result = await workRunDecision(
        canvas.run_id, action, canvas.sync.revision,
        action === "request_changes" ? changeNote.trim() : "",
      );
      if (!result.ok) throw new Error(result.reason || result.error || "验收状态没有保存");
      setChangeOpen(false); setChangeNote("");
      await refresh(true, true);
    } catch (e) {
      setError((e as Error)?.message || "工作状态已经变化，请刷新后重试");
    } finally {
      setActionBusy("");
    }
  }, [canvas, changeNote, refresh]);

  const chooseDevice = useCallback(async (device: WorkExecutionDevice) => {
    if (!canvas) return;
    setActionBusy(`device:${device.device_id}`);
    try {
      const result = await workRunPlacement(
        canvas.run_id, device.device_id, canvas.sync.revision,
      );
      if (!result.ok) throw new Error(result.error || "电脑接力没有保存");
      setDevicesOpen(false);
      await refresh(true, true);
    } catch (e) {
      setError((e as Error)?.message || "设备状态已经变化，请刷新后重试");
    } finally {
      setActionBusy("");
    }
  }, [canvas, refresh]);

  const openDevices = useCallback(async () => {
    setActionBusy("devices");
    try {
      const payload = await workExecutionDevices();
      setDevices((payload.items || []).filter(item => item.online));
      setDevicesOpen(true);
      if (!(payload.items || []).some(item => item.online)) {
        setError("当前没有在线电脑。打开已登录的桌面端后再试。");
      }
    } catch (e) {
      setError((e as Error)?.message || "暂时无法读取在线电脑");
    } finally {
      setActionBusy("");
    }
  }, []);

  const saveWorkflow = useCallback(async () => {
    if (!canvas) return;
    setActionBusy("workflow");
    try {
      const result = await workWorkflowCandidate(
        canvas.run_id, canvas.overview.title,
      );
      if (!result.ok) throw new Error(
        result.error === "execution_evidence_required"
          ? "这项工作还没有完整执行凭证，暂时不能保存为工作流"
          : result.error || "工作流候选没有保存",
      );
      await refresh(true, true);
    } catch (e) {
      setError((e as Error)?.message || "工作流候选没有保存");
    } finally {
      setActionBusy("");
    }
  }, [canvas, refresh]);

  const publishWorkflow = useCallback(async () => {
    const candidate = canvas?.product?.workflow_candidate;
    if (!candidate?.id || !candidate.can_publish || candidate.revision < 1) return;
    setActionBusy("workflow-publish");
    try {
      const result = await workWorkflowPublish(candidate.id, candidate.revision);
      if (!result.ok) throw new Error(
        result.error === "revision_conflict"
          ? "工作流状态已变化，请刷新后再确认"
          : result.error || "工作流没有发布",
      );
      await refresh(true, true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "工作流没有发布");
    } finally {
      setActionBusy("");
    }
  }, [canvas, refresh]);

  const primaryResult = canvas?.results[0];
  const verifiedCriteria = useMemo(() => {
    const criteria = canvas?.completion_receipt.verification.criteria || [];
    return criteria.filter(item => String(item.status || "") === "passed").length;
  }, [canvas]);

  if (!runId) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <button onClick={() => set({ desktopView: "work-active" })}
          className="px-4 py-2 rounded-xl text-[12px]" style={{ color: "var(--accent)", border: "1px solid var(--border)" }}>
          返回进行中
        </button>
      </div>
    );
  }

  if (busy && !canvas) {
    return (
      <div className="flex-1 flex items-center justify-center gap-2 text-[12px]" style={{ color: "var(--text-tertiary)" }}>
        <Loader2 size={17} className="animate-spin" /> 正在整理工作画布
      </div>
    );
  }

  if (!canvas) {
    return (
      <div className="flex-1 flex items-center justify-center px-6">
        <div className="max-w-sm text-center">
          <AlertCircle size={24} className="mx-auto mb-3" style={{ color: "#b42318" }} />
          <div className="text-[13px] font-medium">{error || "没有找到这项工作"}</div>
          <button onClick={() => refresh(true)} className="mt-4 px-3.5 py-2 rounded-xl text-[12px]"
            style={{ background: "var(--accent)", color: "white" }}>重新读取</button>
        </div>
      </div>
    );
  }

  const receipt = canvas.completion_receipt;
  const product = canvas.product;
  const userNeedsChat = ["answer", "review_approval"].includes(canvas.overview.primary_action);

  return (
    <div className="flex-1 min-h-0 overflow-y-auto" style={{ background: "var(--canvas)" }}>
      <div className="w-full max-w-[1120px] mx-auto px-5 md:px-9 py-6 md:py-8">
        <header className="flex flex-col md:flex-row md:items-start gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
              <span>{canvas.overview.category}</span>
              <span>·</span>
              <span>{ago(canvas.sync.updated_at)}</span>
              {canvas.sync.invalidated_result_ids.length > 0 && (
                <span className="px-2 py-0.5 rounded-full" style={{ color: "#b45309", background: "rgba(180,83,9,.08)" }}>
                  有成果需要重新核验
                </span>
              )}
            </div>
            <div className="mt-1.5 flex items-center gap-2.5">
              <h1 className="text-[24px] md:text-[28px] font-semibold tracking-[-0.025em] truncate"
                style={{ color: "var(--text-primary)" }}>{canvas.overview.title}</h1>
              <span className="px-2.5 py-1 rounded-full text-[10.5px] flex-shrink-0"
                style={{ color: "var(--accent)", background: "var(--accent-light)" }}>
                {canvas.overview.status_label}
              </span>
            </div>
            <p className="mt-2 max-w-[760px] text-[12.5px] leading-6" style={{ color: "var(--text-secondary)" }}>
              {canvas.overview.goal}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {primaryResult && (
              <button onClick={() => { setTab("results"); openResult(primaryResult); }}
                className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-[12px] font-medium"
                style={{ color: "white", background: "var(--accent)" }}>
                <FileCheck2 size={14} /> 查看成果
              </button>
            )}
            {userNeedsChat && canvas.conversation_id && (
              <button onClick={() => openConversation()} className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-[12px]"
                style={{ color: "var(--accent)", border: "1px solid var(--border)" }}>
                回到对话 <ArrowRight size={13} />
              </button>
            )}
            <button onClick={() => refresh(true)} aria-label="刷新工作画布"
              className="p-2 rounded-xl hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-tertiary)" }}>
              <RefreshCw size={15} className={busy ? "animate-spin" : ""} />
            </button>
          </div>
        </header>

        {product && (
          <div className="mt-5 grid grid-cols-1 md:grid-cols-3 rounded-2xl overflow-hidden"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
            {[
              ["工作位置", product.placement.label, product.placement.state === "waiting" ? "等待可用设备" : "已由运行时确认"],
              ["执行方式", product.capability_plan.label, product.capability_plan.reason],
              ["可完成范围", product.autonomy.label, "权限、费用或外部操作变化时会先询问你"],
            ].map(([label, value, hint], index) => (
              <div key={label} className="px-4 py-3.5 min-w-0"
                style={index ? { borderLeft: "1px solid var(--border)" } : undefined}>
                <div className="text-[9.5px] font-medium" style={{ color: "var(--text-tertiary)" }}>{label}</div>
                <div className="mt-0.5 text-[12.5px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>{value}</div>
                <div className="mt-0.5 text-[9.5px] truncate" title={hint} style={{ color: "var(--text-tertiary)" }}>{hint}</div>
              </div>
            ))}
          </div>
        )}

        <div className="mt-3 flex flex-col md:flex-row md:items-center gap-2 rounded-2xl px-4 py-3"
          style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
          <div className="min-w-0 flex-1">
            <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>
              {canvas.operating.method_label}
            </div>
            <div className="mt-0.5 text-[10.5px] truncate" title={canvas.operating.reason}
              style={{ color: "var(--text-tertiary)" }}>
              {canvas.operating.route_label}{canvas.operating.reason ? ` · ${canvas.operating.reason}` : ""}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-1.5 text-[9.5px]"
            style={{ color: "var(--text-secondary)" }}>
            {canvas.operating.can_resume && <span className="px-2 py-1 rounded-lg" style={{ background: "var(--bg-secondary)" }}>可继续</span>}
            {canvas.operating.evidence_required && <span className="px-2 py-1 rounded-lg" style={{ background: "var(--bg-secondary)" }}>需要依据</span>}
            {canvas.operating.requires_confirmation && <span className="px-2 py-1 rounded-lg" style={{ background: "var(--bg-secondary)" }}>操作前确认</span>}
          </div>
        </div>

        {product && (product.placement.kind === "waiting_device" || devicesOpen) && (
          <div className="mt-3 rounded-2xl px-4 py-3.5"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
            <div className="flex flex-col md:flex-row md:items-center gap-3">
              <span className="w-9 h-9 rounded-xl flex items-center justify-center"
                style={{ color: "var(--accent)", background: "var(--accent-light)" }}>
                <Laptop size={17} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-[12.5px] font-semibold" style={{ color: "var(--text-primary)" }}>
                  {devicesOpen ? "选择继续执行的电脑" : "这项工作需要一台在线电脑"}
                </div>
                <div className="text-[10.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
                  选择只记录接力意图；电脑真正认领任务后才会取得限时执行权。
                </div>
              </div>
              {!devicesOpen ? (
                <button onClick={openDevices} disabled={Boolean(actionBusy)}
                  className="px-3.5 py-2 rounded-xl text-[11.5px] font-medium disabled:opacity-50"
                  style={{ color: "white", background: "var(--accent)" }}>
                  {actionBusy === "devices" ? "正在查找…" : "选择在线电脑"}
                </button>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {devices.length === 0 ? (
                    <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>没有在线电脑</span>
                  ) : devices.map(device => (
                    <button key={device.device_id} onClick={() => chooseDevice(device)}
                      disabled={Boolean(actionBusy)}
                      className="px-3 py-2 rounded-xl text-[11px] disabled:opacity-50"
                      style={{ color: "var(--accent)", background: "var(--accent-light)" }}>
                      {actionBusy === `device:${device.device_id}` ? "正在接力…" : device.name}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        <nav className="mt-6 flex gap-1 p-1 rounded-xl w-fit" style={{ background: "var(--bg-secondary)" }}>
          {TABS.map(item => (
            <button key={item.id} onClick={() => setTab(item.id)}
              className="px-3.5 py-1.5 rounded-lg text-[11.5px] font-medium transition-colors"
              style={{
                color: tab === item.id ? "var(--text-primary)" : "var(--text-tertiary)",
                background: tab === item.id ? "var(--bg-primary)" : "transparent",
                boxShadow: tab === item.id ? "0 1px 2px rgba(0,0,0,.06)" : "none",
              }}>
              {item.label}
            </button>
          ))}
        </nav>

        {error && (
          <div className="mt-4 px-3.5 py-3 rounded-xl text-[12px] flex items-start gap-2"
            style={{ color: "#b42318", background: "rgba(180,35,24,.07)" }}>
            <AlertCircle size={14} className="mt-0.5 flex-shrink-0" /> {error}
          </div>
        )}

        {tab === "overview" && (
          <div className="mt-5">
            {product && (
              <div className="mb-4 grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_340px] gap-4">
                <SectionCard title="工作地图" hint="目标、步骤、依据和成果来自持久记录；未记录的因果关系不会被猜测">
                  <div className="p-4">
                    <div className="text-[13px] font-semibold leading-6" style={{ color: "var(--text-primary)" }}>
                      {product.contract.goal || canvas.overview.goal}
                    </div>
                    {product.contract.constraints.length > 0 && (
                      <div className="mt-2 text-[10.5px] leading-5" style={{ color: "var(--text-secondary)" }}>
                        约束：{product.contract.constraints.slice(0, 3).join("；")}
                      </div>
                    )}
                    <div className="mt-4 grid grid-cols-3 md:grid-cols-6 gap-2">
                      {[
                        ["目标", product.work_twin.summary.goals],
                        ["条件", product.work_twin.summary.criteria],
                        ["步骤", product.work_twin.summary.tasks],
                        ["依据", product.work_twin.summary.evidence],
                        ["成果", product.work_twin.summary.artifacts],
                        ["待重验", product.work_twin.summary.stale],
                      ].map(([label, value]) => (
                        <div key={String(label)} className="rounded-xl px-2 py-2 text-center" style={{ background: "var(--bg-secondary)" }}>
                          <div className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{value}</div>
                          <div className="text-[9px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{label}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                </SectionCard>
                <SectionCard title="协作与接管" hint="只有测得并行收益且写入隔离成立时才启用多智能体">
                  <div className="p-4 space-y-3">
                    <div className="flex items-center gap-2">
                      <Bot size={15} style={{ color: "var(--accent)" }} />
                      <span className="text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>
                        {product.collaboration.user_summary}
                      </span>
                    </div>
                    <div className="text-[10.5px] leading-5" style={{ color: "var(--text-secondary)" }}>
                      写入隔离：{product.collaboration.write_isolation.status === "verified" ? "已验证" : "尚未证明，不允许宣称安全并行写入"}
                    </div>
                    <div className="text-[10.5px] leading-5" style={{ color: "var(--text-secondary)" }}>
                      {product.workflow_candidate.required_next_step}
                    </div>
                    {!product.workflow_candidate.id
                      && canvas.evidence.execution_receipts.some(item => item.valid && item.success) && (
                      <button onClick={saveWorkflow} disabled={Boolean(actionBusy)}
                        className="px-3 py-2 rounded-xl text-[10.5px] font-medium disabled:opacity-50"
                        style={{ color: "var(--accent)", background: "var(--accent-light)" }}>
                        {actionBusy === "workflow" ? "正在保存…" : "保存为工作流候选"}
                      </button>
                    )}
                    {product.workflow_candidate.can_publish && product.workflow_candidate.id && (
                      <button onClick={publishWorkflow} disabled={Boolean(actionBusy)}
                        className="px-3 py-2 rounded-xl text-[10.5px] font-medium disabled:opacity-50"
                        style={{ color: "white", background: "var(--accent)" }}>
                        {actionBusy === "workflow-publish" ? "正在发布…" : "确认并发布工作流"}
                      </button>
                    )}
                  </div>
                </SectionCard>
              </div>
            )}
            <div className="mb-4 rounded-2xl overflow-hidden"
              style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
              <div className="p-4 flex flex-col md:flex-row md:items-center gap-4">
                <span className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
                  style={{
                    color: canvas.assurance.delivery.can_deliver ? "#15803d" : "var(--accent)",
                    background: canvas.assurance.delivery.can_deliver
                      ? "rgba(22,163,74,.09)" : "var(--accent-light)",
                  }}>
                  <ShieldCheck size={18} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-[9.5px] font-medium" style={{ color: "var(--text-tertiary)" }}>
                    交付准备度
                  </div>
                  <div className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>
                    {canvas.assurance.user_summary.headline}
                  </div>
                  <div className="mt-1 text-[10.5px] leading-5" style={{ color: "var(--text-secondary)" }}>
                    {canvas.assurance.user_summary.detail}
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-2 min-w-[260px]">
                  {[
                    ["恢复点", canvas.assurance.recovery.checkpoint_count],
                    ["验收通过", `${canvas.assurance.evidence.passed}/${canvas.assurance.evidence.required}`],
                    ["阻断项", canvas.assurance.delivery.blockers.length],
                  ].map(([label, value]) => (
                    <div key={String(label)} className="rounded-xl px-2 py-2 text-center"
                      style={{ background: "var(--bg-secondary)" }}>
                      <div className="text-[12.5px] font-semibold" style={{ color: "var(--text-primary)" }}>{value}</div>
                      <div className="text-[9px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{label}</div>
                    </div>
                  ))}
                </div>
              </div>
              {canvas.assurance.delivery.blockers.length > 0 && (
                <div className="px-4 pb-4 flex flex-wrap gap-1.5">
                  {canvas.assurance.delivery.blockers.map(item => (
                    <span key={`${item.area}:${item.code}`} className="px-2 py-1 rounded-lg text-[9.5px]"
                      style={{ color: "#b45309", background: "rgba(180,83,9,.08)" }}>
                      {{
                        required_check_failed: "验收条件未通过",
                        stale_artifact: "成果需要重验",
                        agent_branch_blocked: "协作分支受阻",
                        approval_pending: "等待授权",
                        provider_incompatible: "模型能力不匹配",
                        invalid_execution_receipt: "执行凭证无效",
                      }[item.code] || "需要处理"}
                    </span>
                  ))}
                </div>
              )}
            </div>
            {canvas.change_impact?.requested && (
              <div className="mb-4 px-4 py-3.5 rounded-2xl"
                style={{ background: "rgba(180,83,9,.07)", border: "1px solid rgba(180,83,9,.16)" }}>
                <div className="text-[12.5px] font-semibold" style={{ color: "#92400e" }}>修改影响</div>
                <div className="mt-1 text-[11.5px] leading-5" style={{ color: "var(--text-secondary)" }}>
                  {canvas.change_impact.reason || "已提出修改要求"}
                </div>
                <div className="mt-2 text-[10.5px]" style={{ color: "#b45309" }}>
                  {canvas.change_impact.impacted_stage_ids.length} 个步骤、{canvas.change_impact.impacted_result_ids.length} 项成果需要继续处理。
                  {canvas.change_impact.next_action}
                </div>
              </div>
            )}
            <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_340px] gap-4">
            <div className="space-y-4">
              <SectionCard title="现在做到这里" hint="阶段来自真实运行状态，不使用模型自报进度">
                <div className="p-4">
                  <div className="text-[15px] font-semibold leading-6" style={{ color: "var(--text-primary)" }}>
                    {canvas.overview.current_step}
                  </div>
                  <div className="mt-2 text-[12px] leading-5" style={{ color: "var(--text-secondary)" }}>
                    下一步：{canvas.overview.next_action}
                  </div>
                  <div className="mt-4 flex items-center gap-3">
                    {canvas.overview.progress.mode === "criteria" ? (
                      <>
                        <div className="h-1.5 flex-1 max-w-[240px] rounded-full overflow-hidden" style={{ background: "var(--bg-tertiary)" }}>
                          <div className="h-full rounded-full" style={{
                            width: `${Math.round(canvas.overview.progress.completed / Math.max(1, canvas.overview.progress.total) * 100)}%`,
                            background: "var(--accent)",
                          }} />
                        </div>
                        <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
                          {canvas.overview.progress.label}
                        </span>
                      </>
                    ) : (
                      <span className="text-[10.5px] px-2.5 py-1 rounded-full"
                        style={{ color: "var(--text-secondary)", background: "var(--bg-secondary)" }}>
                        {canvas.overview.progress.label}
                      </span>
                    )}
                  </div>
                </div>
              </SectionCard>

              <SectionCard title="工作计划" hint="修改目标后，失效的依据和成果会被单独标出">
                <div className="p-4 space-y-3">
                  {canvas.process.stages.map((stage, index) => {
                    const style = STAGE_STYLE[stage.status] || STAGE_STYLE.pending;
                    return (
                      <div key={stage.id} className="flex items-start gap-3">
                        <div className="relative flex flex-col items-center">
                          <span className="w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-semibold"
                            style={{ color: style.color, background: style.bg }}>
                            {stage.status === "done" ? <Check size={13} /> : index + 1}
                          </span>
                          {index < canvas.process.stages.length - 1 && (
                            <span className="w-px h-6 mt-1" style={{ background: "var(--border)" }} />
                          )}
                        </div>
                        <div className="pt-1 min-w-0">
                          <div className="text-[12.5px] font-medium" style={{ color: "var(--text-primary)" }}>{stage.label}</div>
                          <div className="mt-0.5 text-[10.5px]" style={{ color: style.color }}>
                            {{ done: "已完成", running: "正在处理", blocked: "需要处理", skipped: "已跳过", pending: "等待开始" }[stage.status]}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </SectionCard>
            </div>

            <div className="space-y-4">
              <SectionCard title="建议的下一步" hint="建议不会自动执行，也不会扩大当前权限">
                <div className="divide-y" style={{ borderColor: "var(--border)" }}>
                  {canvas.next_actions.items.length === 0 ? (
                    <div className="px-4 py-7 text-center text-[11.5px]" style={{ color: "var(--text-tertiary)" }}>
                      当前没有需要额外决定的动作
                    </div>
                  ) : canvas.next_actions.items.slice(0, 6).map(item => (
                    <NextActionRow key={item.id} item={item} busy={actionBusy}
                      onControl={runControl}
                      onReview={() => setTab("results")}
                    />
                  ))}
                </div>
              </SectionCard>

              <SectionCard title="完成可信度">
                <div className="p-4">
                  <div className="flex items-center gap-2">
                    <ShieldCheck size={17} style={{ color: receipt.can_claim_verified ? "#15803d" : "var(--text-tertiary)" }} />
                    <span className="text-[12.5px] font-semibold" style={{ color: "var(--text-primary)" }}>
                      {receipt.can_claim_verified ? "已通过可观察条件核验" :
                        receipt.status === "awaiting_review" ? "等待你的最终验收" :
                          receipt.status === "blocked" ? "依据已变化，需要重新核验" : "已交付，但仍有核验边界"}
                    </span>
                  </div>
                  <div className="mt-3 grid grid-cols-3 gap-2">
                    {[
                      ["成果", receipt.summary.results],
                      ["依据", receipt.summary.sources],
                      ["检查", `${verifiedCriteria}/${receipt.verification.criteria.length}`],
                    ].map(([label, value]) => (
                      <div key={String(label)} className="rounded-xl px-2.5 py-2.5 text-center" style={{ background: "var(--bg-secondary)" }}>
                        <div className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>{value}</div>
                        <div className="text-[9.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{label}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </SectionCard>
            </div>
            </div>
          </div>
        )}

        {tab === "process" && (
          <div className="mt-5 grid grid-cols-1 lg:grid-cols-2 gap-4">
            <SectionCard title="执行过程" hint={`${canvas.process.event_count} 条持久工作记录`}>
              <div className="p-4 space-y-3">
                {canvas.process.latest_events.length === 0 ? (
                  <div className="py-8 text-center text-[11.5px]" style={{ color: "var(--text-tertiary)" }}>还没有可展示的执行记录</div>
                ) : canvas.process.latest_events.slice().reverse().map(event => (
                  <div key={event.id} className="flex gap-3">
                    <StateIcon status={event.status || event.type} size={14} />
                    <div className="min-w-0 flex-1">
                      <div className="text-[12px] leading-5" style={{ color: "var(--text-primary)" }}>{event.summary || "工作状态发生变化"}</div>
                      <div className="text-[9.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{ago(event.created_at)}</div>
                    </div>
                  </div>
                ))}
              </div>
            </SectionCard>
            <div className="space-y-4">
              <SectionCard title="恢复中心" hint="对话、文件和执行分别恢复，不把模型回复当成恢复点">
                <div className="divide-y" style={{ borderColor: "var(--border)" }}>
                  {product && (
                    <div className="px-4 py-3 grid grid-cols-3 gap-2">
                      {[
                        ["对话", product.recovery.conversation.available, product.recovery.conversation.checkpoint_count],
                        ["文件", product.recovery.files.available, product.recovery.files.previous_versions],
                        ["执行", product.recovery.execution.available, product.recovery.execution.checkpoint_count],
                      ].map(([label, available, count]) => (
                        <div key={String(label)} className="rounded-xl px-2 py-2 text-center" style={{ background: "var(--bg-secondary)" }}>
                          <div className="text-[11px] font-medium" style={{ color: available ? "#15803d" : "var(--text-tertiary)" }}>
                            {available ? "可恢复" : "无记录"}
                          </div>
                          <div className="text-[9px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{label} · {String(count)}</div>
                        </div>
                      ))}
                    </div>
                  )}
                  {canvas.process.checkpoints.length === 0 ? (
                    <div className="px-4 py-7 text-center text-[11.5px]" style={{ color: "var(--text-tertiary)" }}>当前运行没有留下恢复点</div>
                  ) : canvas.process.checkpoints.map(item => (
                    <div key={item.id} className="px-4 py-3 flex items-center gap-3">
                      <History size={15} style={{ color: "var(--accent)" }} />
                      <div className="min-w-0 flex-1">
                        <div className="text-[12px] font-medium" style={{ color: "var(--text-primary)" }}>{item.label}</div>
                        <div className="text-[9.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
                          {item.kind === "context" ? "上下文恢复点" : "执行恢复点"} · {ago(item.created_at)}
                        </div>
                      </div>
                      {item.can_resume && (
                        <button disabled={Boolean(actionBusy)} onClick={() => runControl("resume")}
                          className="text-[10.5px] px-2.5 py-1.5 rounded-lg" style={{ color: "var(--accent)", background: "var(--accent-light)" }}>
                          继续
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </SectionCard>
              <SectionCard title="并行协作" hint="每个分支仍受原任务权限与预算约束">
                <div className="p-4">
                  {canvas.process.branches.length === 0 ? (
                    <div className="py-5 text-center text-[11.5px]" style={{ color: "var(--text-tertiary)" }}>本次工作没有启用并行协作</div>
                  ) : canvas.process.branches.map(item => (
                    <div key={item.id} className="flex items-center gap-3 py-2">
                      <Bot size={15} style={{ color: "var(--accent)" }} />
                      <span className="text-[12px] flex-1" style={{ color: "var(--text-primary)" }}>{item.label}</span>
                      <span className="text-[10px]" style={{ color: (STAGE_STYLE[item.status] || STAGE_STYLE.pending).color }}>
                        {item.status}
                      </span>
                    </div>
                  ))}
                </div>
              </SectionCard>
            </div>
          </div>
        )}

        {tab === "evidence" && (
          <div className="mt-5 space-y-4">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-2.5">
              {[
                ["资料来源", canvas.evidence.summary.sources],
                ["运行检查", canvas.evidence.summary.checks],
                ["执行凭证", canvas.evidence.summary.receipts],
                ["无效凭证", canvas.evidence.summary.invalid_receipts],
                ["待重验节点", canvas.evidence.summary.stale_nodes],
              ].map(([label, value]) => (
                <div key={String(label)} className="rounded-xl px-3 py-3" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                  <div className="text-[17px] font-semibold" style={{ color: "var(--text-primary)" }}>{value}</div>
                  <div className="text-[10px] mt-1" style={{ color: "var(--text-tertiary)" }}>{label}</div>
                </div>
              ))}
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <SectionCard title="来源与依据" hint="来源变化只使依赖它的下游结果失效">
                <div className="divide-y" style={{ borderColor: "var(--border)" }}>
                  {canvas.evidence.sources.length === 0 ? (
                    <div className="px-4 py-8 text-center text-[11.5px]" style={{ color: "var(--text-tertiary)" }}>本次工作没有可展示的来源快照</div>
                  ) : canvas.evidence.sources.map(source => (
                    <div key={source.id} className="px-4 py-3 flex items-start gap-3">
                      <FileText size={15} className="mt-0.5" style={{ color: "var(--accent)" }} />
                      <div className="min-w-0 flex-1">
                        <div className="text-[12px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{source.label}</div>
                        <div className="text-[9.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
                          {source.kind} · 第 {source.revision} 版 · {source.status}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </SectionCard>
              <SectionCard title="验收检查">
                <div className="divide-y" style={{ borderColor: "var(--border)" }}>
                  {canvas.evidence.checks.length === 0 ? (
                    <div className="px-4 py-8 text-center text-[11.5px]" style={{ color: "var(--text-tertiary)" }}>当前没有独立运行检查</div>
                  ) : canvas.evidence.checks.map(check => (
                    <div key={check.id} className="px-4 py-3 flex items-start gap-3">
                      <StateIcon status={check.status} />
                      <div className="min-w-0">
                        <div className="text-[12px] font-medium" style={{ color: "var(--text-primary)" }}>{check.label}</div>
                        {check.detail && <div className="text-[10.5px] leading-5 mt-0.5" style={{ color: "var(--text-tertiary)" }}>{check.detail}</div>}
                      </div>
                    </div>
                  ))}
                </div>
              </SectionCard>
            </div>
            <SectionCard
              title="可核验工作记录"
              hint="目标、动作、运行观察和证据使用同一协议；不会展示模型的私有推理或原始工具参数"
            >
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 p-3">
                {[
                  ["验收条件", canvas.protocol.spec.criteria.length],
                  ["受控动作", canvas.protocol.actions.length],
                  ["运行观察", canvas.protocol.observations.length],
                  ["证据凭证", canvas.protocol.evidence.length],
                ].map(([label, value]) => (
                  <div key={String(label)} className="rounded-xl px-3 py-3"
                    style={{ background: "var(--bg-secondary)" }}>
                    <div className="text-[16px] font-semibold" style={{ color: "var(--text-primary)" }}>{value}</div>
                    <div className="text-[10px] mt-1" style={{ color: "var(--text-tertiary)" }}>{label}</div>
                  </div>
                ))}
              </div>
              <div className="px-4 py-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px]"
                style={{ borderTop: "1px solid var(--border)", color: "var(--text-tertiary)" }}>
                <span>权限：{canvas.protocol.spec.permission_mode}</span>
                <span>记录：{canvas.protocol.fingerprint.slice(0, 12)}</span>
                <span>原文未写入</span>
                <span>私有推理未写入</span>
              </div>
            </SectionCard>
            <SectionCard title="执行凭证" hint="不保存原始工具参数或原始返回，仅保存内容寻址证明">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2 p-3">
                {canvas.evidence.execution_receipts.length === 0 ? (
                  <div className="md:col-span-2 py-7 text-center text-[11.5px]" style={{ color: "var(--text-tertiary)" }}>本次工作没有执行外部工具</div>
                ) : canvas.evidence.execution_receipts.map(item => (
                  <div key={item.id} className="rounded-xl px-3 py-3 flex items-start gap-3" style={{ background: "var(--bg-secondary)" }}>
                    <ShieldCheck size={15} style={{ color: item.valid ? "#15803d" : "#b42318" }} />
                    <div className="min-w-0 flex-1">
                      <div className="text-[12px] font-medium" style={{ color: "var(--text-primary)" }}>{item.tool}</div>
                      <div className="text-[9.5px] mt-1" style={{ color: "var(--text-tertiary)" }}>
                        {item.status} · {item.side_effect} · {item.reversible ? "可恢复" : "不可自动撤回"}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </SectionCard>
            {canvas.learning.used_versions.length > 0 && (
              <SectionCard title="本次使用的技能" hint="只展示实际使用的已发布版本；运行记录不会自动晋升候选">
                <div className="divide-y" style={{ borderColor: "var(--border)" }}>
                  {canvas.learning.used_versions.map(skill => (
                    <div key={skill.version_ref} className="px-4 py-3 flex items-start gap-3">
                      <Sparkles size={15} className="mt-0.5" style={{ color: "var(--accent)" }} />
                      <div className="min-w-0 flex-1">
                        <div className="text-[12px] font-medium" style={{ color: "var(--text-primary)" }}>{skill.name}</div>
                        <div className="text-[9.5px] mt-0.5 truncate" style={{ color: "var(--text-tertiary)" }}>
                          {skill.scope} · {skill.version_ref}
                        </div>
                      </div>
                      <span className="text-[9.5px] px-2 py-1 rounded-full"
                        style={{ color: "#15803d", background: "rgba(22,163,74,.08)" }}>本次已使用</span>
                    </div>
                  ))}
                </div>
                <div className="px-4 py-3 text-[10.5px] leading-5"
                  style={{ color: "var(--text-tertiary)", borderTop: "1px solid var(--border)" }}>
                  {canvas.learning.limitation}
                </div>
              </SectionCard>
            )}
          </div>
        )}

        {tab === "results" && (
          <div className="mt-5 grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px] gap-4">
            <SectionCard title="交付成果" hint="文件、画布与 Office 内容使用同一版本和核验模型">
              <div className="divide-y" style={{ borderColor: "var(--border)" }}>
                {canvas.results.length === 0 ? (
                  <div className="px-4 py-12 text-center">
                    <FileText size={23} className="mx-auto mb-3" style={{ color: "var(--text-tertiary)" }} />
                    <div className="text-[12.5px] font-medium" style={{ color: "var(--text-primary)" }}>还没有可交付成果</div>
                    <div className="text-[10.5px] mt-1" style={{ color: "var(--text-tertiary)" }}>成果只有在服务端记录到真实文件或 Artifact 后才会出现。</div>
                  </div>
                ) : canvas.results.map(result => (
                  <div key={result.id} className="px-4 py-3.5 flex items-center gap-3">
                    <span className="w-9 h-9 rounded-xl flex items-center justify-center"
                      style={{ background: "var(--accent-light)", color: "var(--accent)" }}>
                      <FileText size={17} />
                    </span>
                    <button onClick={() => openResult(result)} className="min-w-0 flex-1 text-left">
                      <div className="text-[12.5px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{result.name}</div>
                      <div className="text-[9.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
                        第 {result.version} 版 · {result.verification === "verified" ? "已核验" :
                          result.verification === "stale" ? "来源变化，待重验" :
                            result.verification === "missing" ? "文件缺失" : "已记录"}
                      </div>
                    </button>
                    {result.download_url && (
                      <a href={withToken(result.download_url)} download aria-label={`下载 ${result.name}`}
                        className="p-2 rounded-lg hover:bg-[var(--bg-secondary)]" style={{ color: "var(--text-tertiary)" }}>
                        <Download size={14} />
                      </a>
                    )}
                    <button onClick={() => { setAnnotationResult(result); setAnnotationNote(""); }}
                      className="text-[10.5px] px-2.5 py-1.5 rounded-lg"
                      style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}>批注</button>
                    <button onClick={() => openResult(result)} className="text-[10.5px] px-2.5 py-1.5 rounded-lg"
                      style={{ color: "var(--accent)", background: "var(--accent-light)" }}>打开</button>
                  </div>
                ))}
              </div>
              {annotationResult && (
                <div className="p-4" style={{ borderTop: "1px solid var(--border)" }}>
                  <div className="text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>
                    给“{annotationResult.name}”第 {annotationResult.version} 版提出修改
                  </div>
                  <textarea value={annotationNote} onChange={event => setAnnotationNote(event.target.value)}
                    placeholder="说明要改什么；此批注只会使对应成果版本失效，不会改写其他成果。"
                    className="mt-2 w-full min-h-[84px] resize-y rounded-xl px-3 py-2.5 text-[11.5px] outline-none"
                    style={{ color: "var(--text-primary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }} />
                  <div className="mt-2 flex gap-2">
                    <button disabled={Boolean(actionBusy)} onClick={annotate}
                      className="px-3 py-2 rounded-xl text-[11px] text-white disabled:opacity-50"
                      style={{ background: "var(--accent)" }}>保存批注并标记待重验</button>
                    <button onClick={() => { setAnnotationResult(null); setAnnotationNote(""); }}
                      className="px-3 py-2 rounded-xl text-[11px]" style={{ color: "var(--text-tertiary)" }}>取消</button>
                  </div>
                </div>
              )}
              {product && product.annotations.length > 0 && (
                <div className="px-4 py-3" style={{ borderTop: "1px solid var(--border)" }}>
                  <div className="text-[10px] font-semibold" style={{ color: "var(--text-tertiary)" }}>已保存的修改要求</div>
                  {product.annotations.slice(-4).reverse().map(item => (
                    <div key={item.id} className="mt-2 text-[10.5px] leading-5" style={{ color: "var(--text-secondary)" }}>
                      第 {item.artifact_revision} 版 · {item.note}
                    </div>
                  ))}
                </div>
              )}
            </SectionCard>

            <div className="space-y-4">
              <SectionCard title="完成凭证" hint="完成、核验和用户验收是三个不同事实">
                <div className="p-4">
                  <div className="flex items-start gap-3">
                    <ShieldCheck size={18} style={{ color: receipt.can_claim_verified ? "#15803d" : receipt.status === "blocked" ? "#b42318" : "#b45309" }} />
                    <div className="min-w-0">
                      <div className="text-[12.5px] font-semibold" style={{ color: "var(--text-primary)" }}>
                        {receipt.status === "verified" ? "可观察完成条件已核验" :
                          receipt.status === "accepted_with_limits" ? "用户已接受，仍保留核验边界" :
                            receipt.status === "awaiting_review" ? "等待用户验收" :
                              receipt.status === "blocked" ? "凭证存在阻塞" : "完成凭证尚未闭环"}
                      </div>
                      <div className="text-[9.5px] mt-1 break-all" style={{ color: "var(--text-tertiary)" }}>
                        {receipt.receipt_id}
                      </div>
                    </div>
                  </div>
                  <div className="mt-4 space-y-2 text-[10.5px]" style={{ color: "var(--text-secondary)" }}>
                    <div className="flex justify-between"><span>来源</span><span>{receipt.summary.sources}</span></div>
                    <div className="flex justify-between"><span>运行检查</span><span>{receipt.summary.checks}</span></div>
                    <div className="flex justify-between"><span>外部操作</span><span>{receipt.summary.side_effects}</span></div>
                    <div className="flex justify-between"><span>不可自动撤回</span><span>{receipt.summary.irreversible_side_effects}</span></div>
                  </div>
                  {receipt.limitations.length > 0 && (
                    <div className="mt-4 rounded-xl px-3 py-2.5" style={{ background: "rgba(180,83,9,.07)" }}>
                      <div className="text-[10px] font-semibold" style={{ color: "#b45309" }}>仍需注意</div>
                      <ul className="mt-1.5 space-y-1 text-[10px] leading-4" style={{ color: "var(--text-secondary)" }}>
                        {receipt.limitations.slice(0, 4).map((item, index) => <li key={index}>· {item}</li>)}
                      </ul>
                    </div>
                  )}
                </div>
              </SectionCard>

              {receipt.status === "awaiting_review" && (
                <SectionCard title="验收这次交付" hint="验收不会把未核验内容改写成已核验">
                  <div className="p-4">
                    {!changeOpen ? (
                      <div className="flex gap-2">
                        <button disabled={Boolean(actionBusy)} onClick={() => decide("accept_delivery")}
                          className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl text-[11.5px] font-medium text-white disabled:opacity-50"
                          style={{ background: "var(--accent)" }}>
                          <Check size={13} /> 符合要求
                        </button>
                        <button disabled={Boolean(actionBusy)} onClick={() => setChangeOpen(true)}
                          className="flex-1 px-3 py-2 rounded-xl text-[11.5px] disabled:opacity-50"
                          style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
                          需要修改
                        </button>
                      </div>
                    ) : (
                      <div>
                        <textarea value={changeNote} onChange={e => setChangeNote(e.target.value)}
                          placeholder="写明需要补充或修改的内容"
                          className="w-full min-h-[96px] resize-y rounded-xl px-3 py-2.5 text-[11.5px] outline-none"
                          style={{ color: "var(--text-primary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }} />
                        <div className="mt-2 flex gap-2">
                          <button disabled={Boolean(actionBusy)} onClick={() => decide("request_changes")}
                            className="px-3 py-2 rounded-xl text-[11px] text-white disabled:opacity-50"
                            style={{ background: "var(--accent)" }}>保存并回到工作</button>
                          <button onClick={() => { setChangeOpen(false); setChangeNote(""); }}
                            className="px-3 py-2 rounded-xl text-[11px]" style={{ color: "var(--text-tertiary)" }}>取消</button>
                        </div>
                      </div>
                    )}
                  </div>
                </SectionCard>
              )}

              <SectionCard title="恢复与撤回">
                <div className="p-4 space-y-2.5">
                  <div className="flex items-center gap-2 text-[10.5px]" style={{ color: "var(--text-secondary)" }}>
                    <History size={13} /> {receipt.recovery.checkpoints} 个恢复点
                  </div>
                  <div className="flex items-center gap-2 text-[10.5px]" style={{ color: "var(--text-secondary)" }}>
                    <Undo2 size={13} /> {receipt.recovery.automatic_rollback_available ? "已记录的操作可恢复" : "没有统一自动撤回承诺"}
                  </div>
                  {receipt.recovery.can_retry && (
                    <button disabled={Boolean(actionBusy)} onClick={() => runControl("retry")}
                      className="w-full mt-1 inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl text-[11px]"
                      style={{ color: "var(--accent)", background: "var(--accent-light)" }}>
                      <RotateCcw size={12} /> 保留旧凭证并重新尝试
                    </button>
                  )}
                </div>
              </SectionCard>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function NextActionRow({ item, busy, onControl, onReview }: {
  item: GovernedNextAction;
  busy: string;
  onControl: (action: WorkControlAction) => void;
  onReview: () => void;
}) {
  const Icon = item.kind === "review" ? FileCheck2 :
    item.control_action === "pause" ? Pause :
      item.control_action === "resume" ? Play :
        item.control_action === "retry" ? RotateCcw :
          item.control_action === "cancel" ? XCircle : Sparkles;
  const execute = () => {
    if (item.kind === "review") onReview();
    else if (item.control_action) onControl(item.control_action);
  };
  const actionable = item.kind === "review" || Boolean(item.control_action);
  return (
    <div className="px-4 py-3.5 flex items-start gap-3">
      <span className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
        style={{ color: item.risk === "high" ? "#b42318" : "var(--accent)", background: item.risk === "high" ? "rgba(180,35,24,.08)" : "var(--accent-light)" }}>
        <Icon size={15} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-[11.5px] font-medium leading-5" style={{ color: "var(--text-primary)" }}>{item.label}</div>
        <div className="text-[9.5px] leading-4 mt-0.5" style={{ color: "var(--text-tertiary)" }}>{item.reason}</div>
        {!item.within_scope && (
          <div className="text-[9.5px] mt-1" style={{ color: "#b45309" }}>当前权限范围不足，不会自动执行</div>
        )}
      </div>
      {actionable && (
        <button disabled={Boolean(busy)} onClick={execute}
          className="mt-0.5 text-[10.5px] px-2.5 py-1.5 rounded-lg disabled:opacity-50"
          style={{ color: "var(--accent)", background: "var(--accent-light)" }}>
          {item.kind === "review" ? "检查" : "执行"}
        </button>
      )}
    </div>
  );
}

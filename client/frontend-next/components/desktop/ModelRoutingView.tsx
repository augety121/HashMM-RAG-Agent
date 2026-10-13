"use client";
/** components/desktop/ModelRoutingView.tsx — 模型路由（V203 重构）。
 *
 * 两层架构：本地 Qwen（省/快/私）vs 云端 DeepSeek（强）。每个内部角色/任务可配 本地/云端/自动。
 * 重构点（对标大厂设置页）：
 *   · 三张「策略预设」卡（极致省钱/智能均衡/质量优先）取代裸批量按钮 —— 一键成套配置，
 *     并高亮当前配置命中的预设；
 *   · 状态与分布放进一条概览行；未启用时给「怎么开」的分步说明卡而非一行灰字；
 *   · 无权限/未连接空态给出路（去连接后端）。
 * 逻辑不变：空配置=回退默认；保存只提交 local/cloud；「实测」真发请求看走哪个后端。
 */
import { useEffect, useState, useCallback, useMemo } from "react";
import { Network, Save, RefreshCw, HardDrive, Server, Zap, Loader2, CheckCircle2, XCircle, Wallet, Scale, Gem, Plug, ShieldCheck, Settings2, ShieldAlert, Activity, MessageSquare } from "lucide-react";
import { getLlmRouting, saveLlmRouting, testLlmRouting, listModels, getModelFallbacks, putModelFallbacks, modelsHealth } from "@/lib/api";
import { useStore } from "@/lib/store";
import { PanelShell, PageHeader, Card, Button, Badge, StateView, SectionTitle } from "./ui/PanelKit";
import { routingCounts, filterTasks, setAll as setAllRoutes, type RouteTask, type RouteFilter, type Setting as RSetting } from "@/lib/routingStats";
import { insertContextIntoChat } from "@/lib/contextInsert";

type Task = { task: string; label: string; setting: string; effective: string; default: string };
type Setting = "local" | "cloud" | "auto";

const PRESETS: { key: Setting; name: string; icon: any; desc: string; tone: string }[] = [
  { key: "local", name: "极致省钱", icon: Wallet, desc: "全部任务走本地模型 —— 零 API 成本、数据不出机，需已配本地模型", tone: "var(--success)" },
  { key: "auto", name: "智能均衡", icon: Scale, desc: "按任务类型自动分流 —— 杂活走本地省成本，重活走云端保质量（推荐）", tone: "var(--accent)" },
  { key: "cloud", name: "质量优先", icon: Gem, desc: "全部任务走云端大模型 —— 效果最好，成本最高", tone: "var(--warning)" },
];

export function ModelRoutingView() {
  const [cfg, setCfg] = useState<any>(undefined);
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [edits, setEdits] = useState<Record<string, Setting>>({});
  const [dirty, setDirty] = useState(false);
  const [testing, setTesting] = useState("");
  const [testRes, setTestRes] = useState<Record<string, any>>({});
  const [routeFilter, setRouteFilter] = useState<RouteFilter>("all");
  // ── V249 模型容灾链（OmniRoute 借鉴：主模型故障自动滑到备用；见 docs/借鉴设计-V249.md）──
  const [fbAll, setFbAll] = useState<{ id: string; name: string; is_default?: boolean }[] | null>(null);
  const [fbIds, setFbIds] = useState<string[]>([]);
  const [fbPrimary, setFbPrimary] = useState<string>("");
  const [fbSupported, setFbSupported] = useState<boolean | null>(null);   // null=检测中 false=后端过旧
  const [fbSaving, setFbSaving] = useState(false);
  const [fbMsg, setFbMsg] = useState("");
  const [health, setHealth] = useState<{ id: string; name: string; state: string; consecutive_fails: number; open_remaining_s: number; ok_count: number; fail_count: number; last_error: string }[] | null>(null);
  const set = useStore((s) => s.set);
  const user = useStore((s) => s.user);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const d = await getLlmRouting().catch(() => null);
      if (d === null) { setDenied(true); setCfg({}); }
      else {
        setDenied(false); setCfg(d || {});
        const e: Record<string, Setting> = {};
        (d?.tasks || []).forEach((t: Task) => { e[t.task] = (t.setting as Setting) || "auto"; });
        setEdits(e); setDirty(false);
      }
    } finally { setBusy(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const loadFb = useCallback(async () => {
    try {
      const [fb, ms] = await Promise.all([getModelFallbacks(), listModels().catch(() => [])]);
      setFbSupported(true);
      setFbIds((fb.fallbacks || []).map(f => f.id));
      setFbPrimary(fb.primary?.name || fb.primary?.id || "");
      setFbAll(Array.isArray(ms) ? (ms as any[]).map(m => ({ id: String(m.id), name: String(m.name || m.model_name || m.id), is_default: !!m.is_default })) : []);
      modelsHealth().then(h => setHealth(h.items || [])).catch(() => setHealth(null));
    } catch { setFbSupported(false); }
  }, []);
  useEffect(() => { loadFb(); }, [loadFb]);

  const toggleFb = (id: string) => {
    setFbMsg("");
    setFbIds(ids => ids.includes(id) ? ids.filter(x => x !== id) : (ids.length >= 5 ? ids : [...ids, id]));
  };
  const saveFb = async () => {
    setFbSaving(true); setFbMsg("");
    try { await putModelFallbacks(fbIds); setFbMsg("已保存——主模型故障时将按此顺序自动切换"); loadFb(); }
    catch (e) { setFbMsg("保存失败：" + ((e as Error)?.message || "请重试")); }
    finally { setFbSaving(false); }
  };

  const pick = (task: string, val: Setting) => { setEdits(prev => ({ ...prev, [task]: val })); setDirty(true); };
  const applyPreset = (val: RSetting) => { setEdits(setAllRoutes((cfg?.tasks || []) as RouteTask[], val)); setDirty(true); };

  const onTest = async (task: string) => {
    setTesting(task);
    setTestRes(prev => ({ ...prev, [task]: undefined }));
    try { const r = await testLlmRouting(task); setTestRes(prev => ({ ...prev, [task]: r })); }
    catch { setTestRes(prev => ({ ...prev, [task]: { ok: false, message: "测试失败" } })); }
    finally { setTesting(""); }
  };

  const save = async () => {
    setSaving(true);
    try {
      const routing: Record<string, string> = {};
      Object.entries(edits).forEach(([k, v]) => { if (v === "local" || v === "cloud") routing[k] = v; });
      const d = await saveLlmRouting(routing).catch(() => null);
      if (d) {
        setCfg(d);
        const e: Record<string, Setting> = {};
        (d?.tasks || []).forEach((t: Task) => { e[t.task] = (t.setting as Setting) || "auto"; });
        setEdits(e); setDirty(false);
      }
    } finally { setSaving(false); }
  };

  const d = cfg || {};
  const tasks: Task[] = Array.isArray(d.tasks) ? d.tasks : [];
  const routingOn = d.enabled === true;
  const counts = useMemo(() => routingCounts(tasks as RouteTask[], edits as Record<string, RSetting>), [tasks, edits]);
  const shownTasks = useMemo(() => filterTasks(tasks as RouteTask[], edits as Record<string, RSetting>, routeFilter) as Task[], [tasks, edits, routeFilter]);
  // 当前配置命中哪个预设（全 local / 全 cloud / 全 auto 才算命中）
  const activePreset: Setting | null = useMemo(() => {
    if (!tasks.length) return null;
    const vals = tasks.map(t => edits[t.task] || "auto");
    for (const k of ["local", "cloud", "auto"] as const) if (vals.every(v => v === k)) return k;
    return null;
  }, [tasks, edits]);
  const isAdmin = user?.role === "admin";
  const sendRoutingToChat = () => insertContextIntoChat("routing", "模型路由与容灾健康", {
    enabled: routingOn,
    distribution: counts,
    routes: tasks.map(t => ({ task: t.task, label: t.label, configured: edits[t.task] || "auto", effective: t.effective, default: t.default })),
    fallback_chain: { primary: fbPrimary, fallback_ids: fbIds },
    model_health: health,
    unsaved_changes: dirty,
  }, "请基于我带回的模型路由与容灾健康数据，分析哪些 RAG-Agent 组件应走本地、云端或自动路由，并指出熔断/降级风险。只给建议，不要假装已经保存配置。", "model-routing");

  const SegBtn = ({ task, val, cur }: { task: string; val: Setting; cur: Setting }) => {
    const active = cur === val;
    const label = val === "local" ? "本地" : val === "cloud" ? "云端" : "自动";
    return (
      <button onClick={() => pick(task, val)} className="px-2.5 py-1 text-[11px] transition-colors"
        style={{ background: active ? "var(--accent)" : "transparent", color: active ? "#fff" : "var(--text-secondary)" }}>{label}</button>
    );
  };

  return (
    <PanelShell>
      <PageHeader icon={Network} title="模型路由"
        subtitle="给每类任务选执行的大脑：本地模型（省/快/私）还是云端大模型（强）—— 多而便宜的杂活落本地，省成本"
        actions={<>
          <Button variant="primary" icon={MessageSquare} size="sm" onClick={sendRoutingToChat}>带路由回 Chat</Button>
          {dirty && <Button variant="primary" icon={Save} busy={saving} size="sm" onClick={save}>保存</Button>}
          <Button variant="secondary" icon={RefreshCw} busy={busy} size="sm" onClick={load}>刷新</Button>
        </>} />

      {denied && (
        <StateView kind="empty" icon={isAdmin ? Plug : ShieldCheck}
          title={isAdmin ? "后端未连接" : "需要管理员权限"}
          message={isAdmin
            ? "模型路由配置存放在后端。请先在「后端连接」里连上你的 HashMM 后端。"
            : "模型路由改变全局推理走向，属于管理动作。请使用管理员账号登录后再试。"}
          action={isAdmin
            ? <Button variant="primary" icon={Plug} size="sm" onClick={() => set({ adminOpen: true, adminTab: "backend" as never, desktopView: null })}>去连接后端</Button>
            : <Button variant="secondary" icon={RefreshCw} size="sm" onClick={load}>重试</Button>} />
      )}

      {!denied && cfg !== undefined && (<>
        {/* ── 概览行 ── */}
        <div className="flex items-center gap-2 flex-wrap mb-4">
          <Badge tone={routingOn ? "success" : "neutral"}>路由 {routingOn ? "已启用" : "未启用"}</Badge>
          <Badge tone={d.local_path_set ? "success" : "neutral"}><HardDrive size={11} /> 本地模型 {d.local_path_set ? "已配置" : "未配置"}</Badge>
          {tasks.length > 0 && (
            <span className="text-[10.5px] font-mono" style={{ color: "var(--text-tertiary)" }}>
              分布：本地 {counts.local} · 云端 {counts.cloud} · 自动 {counts.autoCount}
            </span>
          )}
          {dirty && <Badge tone="warning">有未保存的改动</Badge>}
        </div>

        {/* ── 未启用：怎么开（分步说明卡）── */}
        {!routingOn && (
          <Card padding="p-4" className="mb-5">
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 rounded-[9px] flex items-center justify-center flex-shrink-0" style={{ background: "var(--accent-light)" }}>
                <Settings2 size={15} style={{ color: "var(--accent)" }} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>路由尚未生效 —— 现在所有调用都走云端（即现状）</div>
                <div className="text-[11.5px] leading-relaxed mt-1.5" style={{ color: "var(--text-secondary)" }}>
                  这里的配置可以随时保存，作为启用后的预案。让它真正生效需要在后端启动前设置两个环境变量：
                  <span className="font-mono px-1.5 py-0.5 rounded mx-1" style={{ background: "var(--surface-2)" }}>HASHMM_LLM_ROUTING=1</span>
                  开启路由，
                  <span className="font-mono px-1.5 py-0.5 rounded mx-1" style={{ background: "var(--surface-2)" }}>HASHMM_LOCAL_LLM_PATH=/模型路径</span>
                  指向本地模型文件，然后重启后端。
                </div>
              </div>
            </div>
          </Card>
        )}

        {/* ── 策略预设 ── */}
        {tasks.length > 0 && (<>
          <SectionTitle>策略预设</SectionTitle>
          <div className="grid gap-3 mb-6" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
            {PRESETS.map(p => {
              const active = activePreset === p.key;
              return (
                <Card key={p.key} padding="p-4" interactive onClick={() => applyPreset(p.key)}
                  style={active ? { borderColor: p.tone, boxShadow: `0 0 0 1px ${p.tone}` } : undefined}>
                  <div className="flex items-center gap-2.5">
                    <div className="w-8 h-8 rounded-[9px] flex items-center justify-center flex-shrink-0"
                      style={{ background: `color-mix(in srgb, ${p.tone} 13%, transparent)` }}>
                      <p.icon size={15} style={{ color: p.tone }} />
                    </div>
                    <div className="text-[13px] font-bold flex-1" style={{ color: "var(--text-primary)" }}>{p.name}</div>
                    {active && <CheckCircle2 size={15} style={{ color: p.tone }} />}
                  </div>
                  <div className="text-[11px] leading-relaxed mt-2" style={{ color: "var(--text-tertiary)" }}>{p.desc}</div>
                </Card>
              );
            })}
          </div>
        </>)}

        {/* ── 分任务精调 ── */}
        {tasks.length > 0 && (
          <SectionTitle right={
            <div className="inline-flex rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)" }}>
              {([["all", `全部 ${counts.total}`], ["local", `本地 ${counts.local}`], ["cloud", `云端 ${counts.cloud}`], ["auto", `自动 ${counts.autoCount}`]] as const).map(([v, label], i) => (
                <button key={v} onClick={() => setRouteFilter(v)} className="px-2.5 py-1 text-[11px] transition-colors"
                  style={{ background: routeFilter === v ? "var(--accent-light)" : "transparent", color: routeFilter === v ? "var(--accent)" : "var(--text-tertiary)", borderLeft: i ? "1px solid var(--border)" : "none" }}>{label}</button>
              ))}
            </div>
          }>分任务精调</SectionTitle>
        )}
        {tasks.length === 0 && !busy && <StateView kind="empty" icon={Network} message="后端未返回可路由的任务清单。" />}
        {tasks.length > 0 && shownTasks.length === 0 && <div className="text-center py-8 text-[12.5px]" style={{ color: "var(--text-tertiary)" }}>该走向下暂无任务</div>}
        <div className="flex flex-col gap-1.5">
          {shownTasks.map((t) => {
            const cur = edits[t.task] || "auto";
            const eff = cur === "auto" ? t.default : cur;
            return (
              <Card key={t.task} padding="px-3.5 py-2.5">
                <div className="flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
                      {t.label}<span className="ml-2 text-[10.5px] font-mono font-normal" style={{ color: "var(--text-tertiary)" }}>{t.task}</span>
                    </div>
                    <div className="text-[10.5px] mt-0.5 inline-flex items-center gap-1" style={{ color: "var(--text-tertiary)" }}>
                      {eff === "local" ? <HardDrive size={10} /> : <Server size={10} />}
                      实际走 {eff === "local" ? "本地模型" : "云端大模型"}{cur === "auto" && "（按默认策略）"}
                    </div>
                  </div>
                  <button onClick={() => onTest(t.task)} disabled={testing === t.task}
                    className="flex items-center gap-1 text-[11px] px-2.5 py-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)] flex-shrink-0"
                    style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }} title="真发一句话，看实际走哪个后端、通不通">
                    {testing === t.task ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />} 实测
                  </button>
                  <div className="flex rounded-lg overflow-hidden flex-shrink-0" style={{ border: "1px solid var(--border)" }}>
                    <SegBtn task={t.task} val="local" cur={cur} />
                    <span style={{ width: 1, background: "var(--border)" }} />
                    <SegBtn task={t.task} val="cloud" cur={cur} />
                    <span style={{ width: 1, background: "var(--border)" }} />
                    <SegBtn task={t.task} val="auto" cur={cur} />
                  </div>
                </div>
                {testRes[t.task] && (
                  <div className="mt-2 pt-2 text-[11px]" style={{ borderTop: "1px dashed var(--border)" }}>
                    {testRes[t.task].ok ? (
                      <span className="inline-flex items-center gap-1.5 flex-wrap" style={{ color: "var(--success)" }}>
                        <CheckCircle2 size={12} /> 实测走 <b>{testRes[t.task].backend === "local" ? "本地模型" : "云端大模型"}</b> · {testRes[t.task].latency_ms}ms
                        {testRes[t.task].sample && <span style={{ color: "var(--text-tertiary)" }}>· 回复「{testRes[t.task].sample}」</span>}
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5" style={{ color: "var(--error)" }}><XCircle size={12} /> {testRes[t.task].message || "测试失败"}</span>
                    )}
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      </>)}

      {/* ── V249 模型容灾链 + 熔断健康（管理员）── */}
      {isAdmin && fbSupported !== null && (
        <div className="mt-6">
          <SectionTitle>模型容灾链（V249）</SectionTitle>
          {fbSupported === false ? (
            <Card><div className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
              后端未含 V249（/api/admin/model-route 404）——打上覆盖包并重启后端后，这里可配置主模型故障时的自动切换链。
            </div></Card>
          ) : (
            <Card>
              <div className="flex items-start gap-2.5 mb-3">
                <ShieldAlert size={16} style={{ color: "var(--accent)", marginTop: 2 }} />
                <div className="text-[11.5px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                  主模型（<b style={{ color: "var(--text-primary)" }}>{fbPrimary || "未设默认"}</b>）连续失败/超时会按下面的顺序
                  <b style={{ color: "var(--text-primary)" }}>自动切换</b>（连挂 3 次熔断 60s 后再探针恢复）。
                  点模型加入/移出，顺序即优先级，最多 5 个；<b style={{ color: "var(--text-primary)" }}>留空＝关闭容灾</b>（行为与旧版一致）。
                  流式/工具调用始终直通主模型——流中途换模型会产生拼接幻觉。
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5 mb-3">
                {(fbAll || []).filter(m => !m.is_default).map(m => {
                  const idx = fbIds.indexOf(m.id);
                  const on = idx >= 0;
                  return (
                    <button key={m.id} onClick={() => toggleFb(m.id)}
                      className="px-2.5 py-1 rounded-lg text-[11.5px] transition-colors"
                      style={{ background: on ? "var(--accent-light)" : "var(--bg-secondary)",
                               color: on ? "var(--accent)" : "var(--text-secondary)",
                               border: "1px solid " + (on ? "var(--accent)" : "var(--border)") }}>
                      {on ? `${idx + 1}. ` : ""}{m.name}
                    </button>
                  );
                })}
                {(fbAll || []).filter(m => !m.is_default).length === 0 && (
                  <span className="text-[11.5px]" style={{ color: "var(--text-tertiary)" }}>
                    只有一个模型——先到「模型/后端」再加一个备用模型，容灾链才有得选。
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2.5">
                <Button variant="primary" size="sm" icon={Save} busy={fbSaving} onClick={saveFb}>保存容灾链</Button>
                {fbMsg && <span className="text-[11.5px]" style={{ color: fbMsg.startsWith("已保存") ? "var(--success)" : "var(--error)" }}>{fbMsg}</span>}
              </div>
              {health && health.length > 0 && (
                <div className="mt-4">
                  <div className="flex items-center gap-1.5 text-[12px] font-semibold mb-2" style={{ color: "var(--text-primary)" }}>
                    <Activity size={13} /> 熔断健康
                  </div>
                  <div className="space-y-1.5">
                    {health.map(h => (
                      <div key={h.id} className="flex items-center gap-3 px-3 py-2 rounded-[10px] text-[11.5px]"
                        style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                        <Badge tone={h.state === "closed" ? "success" : h.state === "open" ? "error" : "warning"}>
                          {h.state === "closed" ? "正常" : h.state === "open" ? `熔断 ${h.open_remaining_s}s` : "探针中"}
                        </Badge>
                        <span style={{ color: "var(--text-primary)" }}>{h.name}</span>
                        <span style={{ color: "var(--text-tertiary)" }}>成功 {h.ok_count} · 失败 {h.fail_count}</span>
                        {h.last_error && <span className="truncate" style={{ color: "var(--text-tertiary)" }} title={h.last_error}>最近错误：{h.last_error}</span>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </Card>
          )}
        </div>
      )}
    </PanelShell>
  );
}

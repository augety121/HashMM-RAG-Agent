"use client";
/** components/desktop/AdvancedView.tsx — 高级能力（V205 新增）。
 *
 * 把本轮后端新增、原来「加了但用户点不到」的四块能力集中成一页，标签切换：
 *   ① 凭据仓   per-connector 密钥集中管理（写入即加密，列表只回掩码；配置里用 ${cred:名} 引用）
 *   ② 云上派活 把离散任务丢进队列，任意 runner（另一台桌面/服务器）认领执行
 *   ③ 质量隔离 解析质量分过低的文档在此人工复核：放行重入库 / 拒绝删除
 *   ④ 图片资源库 已入库图片的浏览与文本搜图（图2-④）
 * 视觉全部走 PanelKit，与其它面板一致；每块都有明确的空态与操作反馈。
 */
import { useCallback, useEffect, useState } from "react";
import { KeyRound, Send, ShieldAlert, ShieldCheck, Image as ImageIcon, RefreshCw, Trash2, Check, X, Plus, Boxes, Power, Webhook, UserCog, Users } from "lucide-react";
import {
  listCredentials, setCredential, deleteCredential,
  listDispatch, createDispatch,
  listQuarantine, approveQuarantine, rejectQuarantine,
  listImages, searchImages, deleteImage,
  listModules, toggleModule, getHealth, runtimeCapabilities,
  type CredItem, type DispatchTask, type QuarantineItem, type ImageItem, type AgentModule, type RunnerStatus,
  type RuntimeCapabilities,
  type ExecutionHookRule, type CodeHookItem,
  listHooks, createHook, deleteHook, toggleHook,
  getExecutionHookRules, saveExecutionHookRules, listCodeHooks, trustCodeHook, revokeCodeHook,
  getPrefs, putPrefs, delPrefs, listCanvasTemplates, deleteCanvasTemplate } from "@/lib/api";
import { PanelShell, PageHeader, Card, CardHeader, Button, Badge, StateView, SectionTitle, Field, inputClass, inputStyle } from "./ui/PanelKit";
import { capabilitySurfaceLabels, capabilityTruthPresentation } from "@/lib/runtimeCapabilities";

type Tab = "modules" | "creds" | "dispatch" | "quarantine" | "images" | "hooks" | "agenthooks" | "prefs" | "orgtpl";
const TABS: { id: Tab; label: string; icon: typeof KeyRound }[] = [
  { id: "modules", label: "能力模块", icon: Boxes },
  { id: "creds", label: "凭据仓", icon: KeyRound },
  { id: "dispatch", label: "云上派活", icon: Send },
  { id: "quarantine", label: "质量隔离", icon: ShieldAlert },
  { id: "images", label: "图片库", icon: ImageIcon },
  { id: "hooks", label: "自动触发", icon: Webhook },
  { id: "agenthooks", label: "执行 Hooks", icon: ShieldCheck },
  { id: "prefs", label: "AI 偏好", icon: UserCog },
  { id: "orgtpl", label: "组织模板", icon: Users },
];

export function AdvancedView() {
  const [tab, setTab] = useState<Tab>("modules");
  return (
    <PanelShell>
      <PageHeader icon={KeyRound} title="高级能力" subtitle="凭据 · 派活 · 质量治理 · 自动化 · 执行 Hooks" />
      <div className="flex gap-1.5 mb-4 flex-wrap">
        {TABS.map(t => {
          const on = tab === t.id;
          return (
            <button key={t.id} onClick={() => setTab(t.id)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-[10px] text-[12.5px] transition-colors"
              style={on
                ? { background: "var(--accent)", color: "#fff", fontWeight: 600 }
                : { background: "var(--bg-secondary)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
              <t.icon size={13} /> {t.label}
            </button>
          );
        })}
      </div>
      {tab === "modules" && <ModulesTab />}
      {tab === "creds" && <CredsTab />}
      {tab === "dispatch" && <DispatchTab />}
      {tab === "quarantine" && <QuarantineTab />}
      {tab === "images" && <ImagesTab />}
      {tab === "hooks" && <HooksTab />}
      {tab === "agenthooks" && <ExecutionHooksTab />}
      {tab === "prefs" && <PrefsTab />}
      {tab === "orgtpl" && <OrgTplTab />}
    </PanelShell>
  );
}

/* ── 能力模块（路线图阶段 A）：RAG 等可整体启停 ─────────────────── */
function ModulesTab() {
  const [items, setItems] = useState<AgentModule[] | undefined>(undefined);
  const [note, setNote] = useState("");
  const [busyKey, setBusyKey] = useState("");
  // V220: 模块接口不可用（老后端/未开放）要说人话，不能白屏
  const [modErr, setModErr] = useState(false);
  // V220: 运行档位卡（/api/health features）——preset 与高级旗标一眼可见
  const [feat, setFeat] = useState<{ preset?: string; advanced_on?: string[]; advanced_on_count?: number; advanced_total?: number } | null>(null);
  const [release, setRelease] = useState("");
  const [runtimeCaps, setRuntimeCaps] = useState<RuntimeCapabilities | null>(null);
  const [capsErr, setCapsErr] = useState(false);

  const load = useCallback(async () => {
    try { const r = await listModules(); setItems(r.modules); setNote(r.note); setModErr(false); }
    catch { setItems([]); setModErr(true); }
    try { const h = await getHealth(); setFeat(h.features || null); setRelease(h.release || ""); } catch { /* 档位卡可无 */ }
    try { setRuntimeCaps(await runtimeCapabilities()); setCapsErr(false); }
    catch { setRuntimeCaps(null); setCapsErr(true); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const flip = async (m: AgentModule) => {
    if (m.core) return;
    setBusyKey(m.key);
    try { await toggleModule(m.key, !m.enabled); await load(); }
    finally { setBusyKey(""); }
  };

  if (items === undefined) return <StateView kind="loading" message="加载能力模块…" />;
  return (
    <div className="space-y-3">
      {/* V220: 运行档位卡——后端版本 / HASHMM_PRESET 档位 / 已开启的高级旗标 */}
      {feat && (
        <Card padding="p-4">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[13.5px] font-semibold" style={{ color: "var(--text-primary)" }}>运行档位</span>
            <Badge tone="accent">{feat.preset || "basic"}</Badge>
            {release && <Badge tone="neutral">后端 {release}</Badge>}
            <span className="text-[11.5px]" style={{ color: "var(--text-tertiary)" }}>
              高级旗标 {feat.advanced_on_count ?? 0} / {feat.advanced_total ?? 0} 开启
            </span>
          </div>
          {(feat.advanced_on?.length ?? 0) > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {feat.advanced_on!.map(f => (
                <span key={f} className="px-1.5 py-0.5 rounded-md font-mono text-[10px]"
                  style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>{f}</span>
              ))}
            </div>
          )}
        </Card>
      )}
      <SectionTitle>实际进入工作流的能力</SectionTitle>
      {runtimeCaps ? (
        <Card padding="p-0 overflow-hidden">
          <div className="px-4 py-3 flex items-center gap-2 flex-wrap" style={{ borderBottom: "1px solid var(--border)" }}>
            <span className="text-[13.5px] font-semibold" style={{ color: "var(--text-primary)" }}>
              {runtimeCaps.ready_count} / {runtimeCaps.total_count} 项可直接使用
            </span>
            <Badge tone="accent">
              Chat 可调用 {runtimeCaps.chat_effective_tool_count ?? runtimeCaps.chat_tool_count} 个工具
            </Badge>
            {(runtimeCaps.chat_missing_executors?.length ?? 0) > 0 && (
              <Badge tone="warning">
                {runtimeCaps.chat_missing_executors!.length} 个工具待接入
              </Badge>
            )}
            <span className="ml-auto text-[10.5px] font-mono" style={{ color: "var(--text-tertiary)" }}>
              状态 {runtimeCaps.revision}
            </span>
          </div>
          <div className="grid grid-cols-1 xl:grid-cols-2">
            {runtimeCaps.capabilities.map((cap, index) => {
              const state = capabilityTruthPresentation(cap);
              const surfaces = capabilitySurfaceLabels(cap.surfaces);
              return (
                <div key={cap.id} className="px-4 py-3 min-w-0"
                  style={{ borderBottom: "1px solid var(--border)", borderRight: index % 2 === 0 ? "1px solid var(--border)" : undefined }}>
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>{cap.title}</span>
                    <Badge tone={state.tone}>{state.label}</Badge>
                    {cap.requires_desktop && <Badge tone="neutral">需在线电脑</Badge>}
                  </div>
                  <p className="text-[11.5px] leading-[1.55] mt-1" style={{ color: "var(--text-secondary)" }}>{cap.description}</p>
                  <div className="text-[10.5px] mt-1.5 flex gap-2 flex-wrap" style={{ color: "var(--text-tertiary)" }}>
                    <span>{surfaces.join(" · ")}</span><span>{cap.reason}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      ) : (
        <div className="px-3.5 py-3 rounded-[12px] text-[12px]"
          style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: capsErr ? "var(--warning)" : "var(--text-secondary)" }}>
          {capsErr ? "当前后端未提供统一能力状态；请升级后端后重试。" : "正在核对 Chat、桌面端与 App 的真实接入状态…"}
        </div>
      )}
      {items.length === 0 && (
        <StateView kind="empty" title={modErr ? "能力模块接口不可用" : "暂无可管理的能力模块"}
          message={modErr
            ? "多半是后端代码过旧（该接口为较新版本提供）。用完整源码包里的 upgrade-server.sh 升级后端并重启，此页即可用。"
            : "当前版本未开放可整体启停的模块。"}
          action={<Button size="sm" icon={RefreshCw} onClick={load}>重试</Button>} />
      )}
      <div className="text-[12px]" style={{ color: "var(--text-secondary)" }}>{note}</div>
      {items.map(m => (
        <Card key={m.key} padding="p-4">
          <div className="flex items-center gap-3">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[13.5px] font-semibold" style={{ color: "var(--text-primary)" }}>{m.title}</span>
                {m.core && <Badge tone="neutral">核心</Badge>}
                {m.enabled
                  ? <Badge tone={m.healthy && m.wired !== false ? "success" : "warning"}>{m.healthy && m.wired !== false ? "已接入 Chat" : "尚未完整接入"}</Badge>
                  : <Badge tone="neutral">已关闭</Badge>}
              </div>
              <div className="text-[11.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{m.desc}</div>
              <div className="text-[10.5px] mt-1" style={{ color: "var(--text-tertiary)" }}>
                Agent 可调用 {m.active_tools?.length ?? (m.enabled && m.healthy ? m.tools.length : 0)} 项
                {m.enabled && (!m.healthy || m.wired === false) ? ` · ${m.health_msg}` : ""}
              </div>
            </div>
            {!m.core && (
              <Button size="sm" variant={m.enabled ? "danger" : "primary"} icon={Power}
                busy={busyKey === m.key} onClick={() => flip(m)}>
                {m.enabled ? "关闭" : "启用"}
              </Button>
            )}
          </div>
        </Card>
      ))}
    </div>
  );
}

/* ── ① 凭据仓 ─────────────────────────────────────────────── */
function CredsTab() {
  const [items, setItems] = useState<CredItem[] | undefined>(undefined);
  const [hint, setHint] = useState("");
  const [name, setName] = useState("");
  const [val, setVal] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { const r = await listCredentials(); setItems(r.items); setHint(r.placeholder); }
    // V222: 用法示例常驻（此前只有空态一句，存了凭据后反而没人告诉你怎么引用）
    catch { setItems([]); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const add = async () => {
    if (!name.trim() || !val.trim()) return;
    setBusy(true);
    try { await setCredential(name.trim(), val.trim(), note.trim()); setName(""); setVal(""); setNote(""); await load(); }
    finally { setBusy(false); }
  };

  if (items === undefined) return <StateView kind="loading" message="加载凭据…" />;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="新增凭据" sub={hint} icon={Plus} />
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2.5 mt-3">
          <Field label="连接器名"><input className={inputClass} style={inputStyle} value={name} onChange={e => setName(e.target.value)} placeholder="github_mcp" /></Field>
          <Field label="密钥值"><input className={inputClass} style={inputStyle} type="password" value={val} onChange={e => setVal(e.target.value)} placeholder="粘贴密钥（写入即加密）" /></Field>
          <Field label="备注（可选）"><input className={inputClass} style={inputStyle} value={note} onChange={e => setNote(e.target.value)} placeholder="用途说明" /></Field>
        </div>
        <div className="mt-3"><Button variant="primary" icon={Plus} onClick={add} busy={busy} disabled={!name.trim() || !val.trim()}>保存凭据</Button></div>
      </Card>
      <div className="text-[11.5px] px-3 py-2 rounded-lg" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
        用法：在 Agent/工具/MCP 配置里写 <code style={{ color: "var(--accent)" }}>{"${cred:连接器名}"}</code>（如 <code>{"${cred:github_mcp}"}</code>），运行时自动解密替换——密钥不出现在任何配置明文里。
      </div>
      <SectionTitle right={<Button size="sm" variant="ghost" icon={RefreshCw} onClick={load}>刷新</Button>}>已保存（{items.length}）</SectionTitle>
      {items.length === 0
        ? <StateView kind="empty" title="还没有凭据" message="连接器密钥集中在这里加密保存，Agent/工具配置里用 ${cred:名称} 引用" />
        : <div className="space-y-2">
            {items.map(c => (
              <div key={c.connector} className="flex items-center gap-3 px-3.5 py-2.5 rounded-[12px]" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                <KeyRound size={15} style={{ color: "var(--accent)" }} />
                <div className="flex-1 min-w-0">
                  <div className="text-[13px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{c.connector}</div>
                  <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>{c.masked}{c.note ? ` · ${c.note}` : ""}</div>
                </div>
                <Button size="sm" variant="danger" icon={Trash2} onClick={async () => { await deleteCredential(c.connector); load(); }}>删除</Button>
              </div>
            ))}
          </div>}
    </div>
  );
}

/* ── ② 云上派活 ───────────────────────────────────────────── */
/** runner 实时心跳：poll 即心跳（15 秒内轮询过=在线），最近认领 + 今日执行数随任务列表每 5 秒刷新。 */
function RunnerHeartbeat({ runners, unavailable = false }: { runners: RunnerStatus[]; unavailable?: boolean }) {
  const ago = (ts: number | null) => {
    if (!ts) return "—";
    const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (s < 60) return `${s} 秒前`;
    if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
    if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
    return `${Math.floor(s / 86400)} 天前`;
  };
  if (runners.length === 0) return (
    <div className="flex items-center gap-2.5 px-3.5 py-2.5 rounded-[12px]" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
      <span className="inline-block w-2 h-2 rounded-full" style={{ background: "var(--text-tertiary)" }} />
      <div className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
        {unavailable
          ? <>派活接口不可用——多半是<b style={{ color: "var(--text-primary)" }}>后端代码过旧（缺 /api/dispatch，V205+）</b>。用源码包里的 upgrade-server.sh 升级后端并重启即可用；桌面端已自动暂停轮询、升级后自恢复。</>
          : <>尚未检测到 runner 心跳。桌面客户端启动后会常驻认领 <b style={{ color: "var(--text-primary)" }}>desktop</b> 队列（约 5 秒一轮）。</>}
      </div>
    </div>
  );
  return (
    <div className="space-y-2">
      {runners.map(r => (
        <div key={r.name} className="flex items-center gap-3 px-3.5 py-2.5 rounded-[12px]" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
          <span className="inline-block w-2 h-2 rounded-full shrink-0" style={{ background: r.online ? "var(--success, #22a06b)" : "var(--text-tertiary)" }} />
          <div className="flex-1 min-w-0 text-[12px]" style={{ color: "var(--text-secondary)" }}>
            <b style={{ color: "var(--text-primary)" }}>{r.name}</b>
            <span className="mx-1.5">{r.online ? "在线" : `离线（最后心跳 ${ago(r.last_seen)}）`}</span>
            <span className="mx-1.5" style={{ color: "var(--text-tertiary)" }}>·</span>
            最近认领 {ago(r.last_claim)}
            <span className="mx-1.5" style={{ color: "var(--text-tertiary)" }}>·</span>
            今日执行 <b style={{ color: "var(--text-primary)" }}>{r.today_done}</b> 条{r.today_failed > 0 ? <span style={{ color: "var(--error)" }}>（失败 {r.today_failed}）</span> : null}
          </div>
        </div>
      ))}
    </div>
  );
}

function DispatchTab() {
  const [items, setItems] = useState<DispatchTask[] | undefined>(undefined);
  const [runners, setRunners] = useState<RunnerStatus[]>([]);
  const [apiErr, setApiErr] = useState(false);   // V220: 派活接口不可用（老后端）要明说
  const [runner, setRunner] = useState("desktop");
  const [kind, setKind] = useState("browser_use");
  const [payload, setPayload] = useState('{"url": "https://news.ycombinator.com", "goal": "总结今日头条要点"}');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try { const r = await listDispatch(); setItems(r.items); setRunners(r.runners || []); setApiErr(false); } catch { setItems([]); setApiErr(true); }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load]);

  const send = async () => {
    setErr("");
    let obj: Record<string, unknown>;
    try { obj = JSON.parse(payload); } catch { setErr("payload 不是合法 JSON"); return; }
    setBusy(true);
    try { await createDispatch(runner.trim() || "default", kind.trim() || "task", obj); await load(); }
    finally { setBusy(false); }
  };

  const tone = (s: string) => s === "done" ? "success" : s === "failed" ? "error" : s === "claimed" ? "accent" : "neutral";
  if (items === undefined) return <StateView kind="loading" message="加载任务队列…" />;
  return (
    <div className="space-y-4">
      {/* V223 信息卡（对齐"能力模块"标准）：队列一眼概览 */}
      {!apiErr && items.length > 0 && (() => {
        const day = new Date().toDateString();
        const today = items.filter(t => t.created && new Date(t.created * 1000).toDateString() === day);
        const n = (st: string) => items.filter(t => t.status === st).length;
        return (
          <div className="flex flex-wrap items-center gap-2 px-3.5 py-2.5 rounded-[12px] text-[12px]"
            style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
            <span>队列：待认领 <b style={{ color: "var(--text-primary)" }}>{n("pending")}</b></span>
            <span>· 执行中 <b style={{ color: "var(--text-primary)" }}>{n("claimed")}</b></span>
            <span>· 完成 <b style={{ color: "var(--success, #22a06b)" }}>{n("done")}</b></span>
            <span>· 失败 <b style={{ color: "var(--error)" }}>{n("failed")}</b></span>
            <span style={{ color: "var(--text-tertiary)" }}>· 今日入队 {today.length}</span>
          </div>
        );
      })()}
      <RunnerHeartbeat runners={runners} unavailable={apiErr} />
      <Card>
        <CardHeader title="派一个任务" sub="任意 runner（另一台桌面 / 服务器 worker）轮询认领并执行，结果回填" icon={Send} />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5 mt-3">
          <Field label="目标 runner"><input className={inputClass} style={inputStyle} value={runner} onChange={e => setRunner(e.target.value)} /></Field>
          <Field label="任务类型">
            <select className={inputClass} style={inputStyle} value={kind} onChange={e => setKind(e.target.value)}>
              <option value="browser_use">browser_use · 让电脑用浏览器办事</option>
              <option value="file">file · 取/找电脑里的文件</option>
              <option value="seq">seq · 多步操作（逐步确认）</option>
              <option value="computer_use">computer_use · 通用电脑操作</option>
              <option value="team">team · 多智能体协作（并行角色 + 画布控制室）</option>
            </select>
          </Field>
        </div>
        <div className="flex flex-wrap gap-1.5 mb-1.5 text-[10.5px]">
          {([
            ["浏览器查", "browser_use", '{"goal": "用浏览器查一下今天的科技新闻，整理成要点", "conv_id": ""}'],
            ["取文件", "file", '{"task": "最近10个文件", "conv_id": ""}'],
            ["多步整理", "seq", '{"goal": "整理下载文件夹：按类型归类，重复的列出让我确认", "conv_id": ""}'],
            ["通用任务", "computer_use", '{"task": "打开记事本写一句你好", "conv_id": ""}'],
            ["多智能体", "team", '{"goal": "调研三款竞品并给出选型建议", "conv_id": ""}'],
          ] as const).map(([label, k, tpl]) => (
            <button key={label} onClick={() => { setKind(k); setPayload(tpl); }}
              className="px-2 py-0.5 rounded-md" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}
              title="一键填入示例（conv_id 填会话 ID 可把结果回帖到对话）">{label}</button>
          ))}
        </div>
        <Field label="payload（JSON）"><textarea className={inputClass} style={{ ...inputStyle, minHeight: 72, fontFamily: "var(--font-mono)" }} value={payload} onChange={e => setPayload(e.target.value)} /></Field>
        {err && <div className="text-[11px] mt-1" style={{ color: "var(--error)" }}>{err}</div>}
        <div className="mt-3"><Button variant="primary" icon={Send} onClick={send} busy={busy}>派活</Button></div>
      </Card>
      <SectionTitle right={<Button size="sm" variant="ghost" icon={RefreshCw} onClick={load}>刷新</Button>}>最近任务（{items.length}）</SectionTitle>
      {items.length === 0
        ? <StateView kind="empty" title="队列为空" message="派出的任务会显示在这里，claimed 超时自动回 pending 防卡死" />
        : <div className="space-y-2">
            {items.map(t => (
              <div key={t.task_id} className="flex items-center gap-3 px-3.5 py-2.5 rounded-[12px]" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                <div className="flex-1 min-w-0">
                  <div className="text-[12.5px] font-medium" style={{ color: "var(--text-primary)" }}>{t.kind} <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>→ {t.runner}</span></div>
                  <div className="text-[10.5px] font-mono truncate" style={{ color: "var(--text-tertiary)" }}>{t.task_id}</div>
                  {t.result ? <div className="text-[11px] mt-1 whitespace-pre-wrap break-words line-clamp-3" style={{ color: "var(--text-secondary)" }}>{t.result}</div> : null}
                </div>
                <Badge tone={tone(t.status) as any}>{t.status}</Badge>
              </div>
            ))}
          </div>}
    </div>
  );
}

/* ── ③ 质量隔离 ───────────────────────────────────────────── */
function QuarantineTab() {
  const [items, setItems] = useState<QuarantineItem[] | undefined>(undefined);
  const [threshold, setThreshold] = useState<number>(0.25);
  const [busyId, setBusyId] = useState("");

  const load = useCallback(async () => {
    try { const r = await listQuarantine(); setItems(r.items); setThreshold(r.threshold); } catch { setItems([]); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const act = async (qid: string, approve: boolean) => {
    setBusyId(qid);
    try { approve ? await approveQuarantine(qid) : await rejectQuarantine(qid); await load(); }
    finally { setBusyId(""); }
  };

  if (items === undefined) return <StateView kind="loading" message="加载隔离区…" />;
  return (
    <div className="space-y-4">
      {/* V223 信息卡：这道闸在管什么、现在有多少活 */}
      <div className="flex flex-wrap items-center gap-2 px-3.5 py-2.5 rounded-[12px] text-[12px]"
        style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
        <span className="font-semibold" style={{ color: "var(--text-primary)" }}>入库质量闸</span>
        <span>· 位置：文档解析 → <b style={{ color: "var(--text-primary)" }}>本闸</b> → 向量索引</span>
        <span>· 待复核 <b style={{ color: items.length ? "var(--warning, #e5a50a)" : "var(--text-primary)" }}>{items.length}</b></span>
        <span style={{ color: "var(--text-tertiary)" }}>· 坏文档在此拦下，检索索引不被污染</span>
      </div>
      <div className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
        解析质量分低于 <b style={{ color: "var(--accent)" }}>{threshold}</b> 的文档不会直接进索引，暂存在此等你复核。放行=跳过质量闸重新入库；拒绝=删除暂存。
      </div>
      <SectionTitle right={<Button size="sm" variant="ghost" icon={RefreshCw} onClick={load}>刷新</Button>}>待复核（{items.length}）</SectionTitle>
      {items.length === 0
        ? <StateView kind="empty" title="隔离区为空" message="没有低质量文档等待处理，坏文档不会污染检索索引" />
        : <div className="space-y-2">
            {items.map(q => (
              <Card key={q.qid} padding="p-4">
                <div className="flex items-start gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{q.filename}</span>
                      <Badge tone="warning">质量分 {q.score.toFixed(2)}</Badge>
                    </div>
                    {q.issues.length > 0 && (
                      <div className="text-[11px] mt-1" style={{ color: "var(--text-tertiary)" }}>{q.issues.slice(0, 3).join(" · ")}</div>
                    )}
                  </div>
                  <div className="flex gap-1.5 flex-shrink-0">
                    <Button size="sm" variant="primary" icon={Check} busy={busyId === q.qid} onClick={() => act(q.qid, true)}>放行</Button>
                    <Button size="sm" variant="danger" icon={X} onClick={() => act(q.qid, false)}>拒绝</Button>
                  </div>
                </div>
              </Card>
            ))}
          </div>}
    </div>
  );
}

/* ── ④ 图片库 ─────────────────────────────────────────────── */
function ImagesTab() {
  const [items, setItems] = useState<ImageItem[] | undefined>(undefined);
  const [q, setQ] = useState("");
  const [searching, setSearching] = useState(false);

  const load = useCallback(async () => {
    try { const r = await listImages(60); setItems(r.items); } catch { setItems([]); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const doSearch = async () => {
    if (!q.trim()) { load(); return; }
    setSearching(true);
    try { const r = await searchImages(q.trim(), 12); setItems(r.items); }
    finally { setSearching(false); }
  };

  if (items === undefined) return <StateView kind="loading" message="加载图片库…" />;
  return (
    <div className="space-y-4">
      {/* V223 信息卡：图库怎么进、怎么用 */}
      <div className="flex flex-wrap items-center gap-2 px-3.5 py-2.5 rounded-[12px] text-[12px]"
        style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
        <span className="font-semibold" style={{ color: "var(--text-primary)" }}>会话图库</span>
        <span>· 已入库 <b style={{ color: "var(--text-primary)" }}>{items.length}</b> 张</span>
        <span style={{ color: "var(--text-tertiary)" }}>· 上传/对话图片自动入库（sha256 去重）· Agent 可按描述检索复用，不必重复上传</span>
      </div>
      <div className="flex gap-2">
        <input className={inputClass} style={inputStyle} value={q} onChange={e => setQ(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") doSearch(); }} placeholder="按标题 / 说明 / 文件名搜图，回车搜索" />
        <Button variant="primary" busy={searching} onClick={doSearch}>搜图</Button>
        {q && <Button variant="ghost" onClick={() => { setQ(""); load(); }}>清除</Button>}
      </div>
      {items.length === 0
        ? <StateView kind="empty" title="图片库为空" message="上传或对话里发送的图片会自动入库（sha256 去重），可在此浏览与检索" />
        : <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {items.map(im => (
              <div key={im.id} className="flex items-center gap-3 px-3 py-2.5 rounded-[12px]" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                <ImageIcon size={16} style={{ color: "var(--accent)" }} />
                <div className="flex-1 min-w-0">
                  <div className="text-[12.5px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{im.filename}</div>
                  {im.caption && <div className="text-[11px] truncate" style={{ color: "var(--text-tertiary)" }}>{im.caption}</div>}
                </div>
                {typeof im.score === "number" && <Badge tone="accent" mono>{im.score.toFixed(2)}</Badge>}
                <Button size="sm" variant="danger" icon={Trash2} onClick={async () => { await deleteImage(im.id); load(); }} ariaLabel="删除图片" />
              </div>
            ))}
          </div>}
    </div>
  );
}

/* ── V231 ⑥ 触发器（webhook）：外部系统 POST 一下 → 自动派活 ── */
function HooksTab() {
  const [items, setItems] = useState<{ id: string; name: string; kind: string; hits: number; last_hit: number; url: string; enabled?: boolean; recent?: { ts: number; keys: string[] }[] }[] | undefined>(undefined);
  const [openId, setOpenId] = useState("");   // V234 命中详情展开
  const [detN, setDetN] = useState(10);   // V237 明细分页：每页 10
  const [name, setName] = useState("");
  const [kind, setKind] = useState("browser_use");
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { try { const r = await listHooks(); setItems(r.items || []); } catch { setItems([]); } }, []);
  useEffect(() => { load(); }, [load]);
  const add = async () => {
    if (!name.trim() || !goal.trim()) return;
    setBusy(true);
    try { const r = await createHook(name.trim(), kind, goal.trim());
      try { navigator.clipboard.writeText(window.location.origin + r.url); } catch { /* */ }
      window.alert("触发器已创建，完整 URL 已复制（含密钥，谁拿到谁能触发，妥善保管）");
      setName(""); setGoal(""); await load();
    } catch { window.alert("创建失败（后端版本过旧）"); }
    finally { setBusy(false); }
  };
  if (items === undefined) return <StateView kind="loading" message="加载触发器…" />;
  return (
    <div className="space-y-3">
      <div className="rounded-xl p-3 space-y-2" style={{ border: "1px solid var(--border)" }}>
        <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>新建触发器</div>
        <div className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
          外部系统（监控、CI、文件夹哨兵…）POST 这个 URL → 自动入派活队列，电脑端照常执行；body 里的 JSON 字段可覆盖预设。
        </div>
        <div className="flex gap-2 flex-wrap">
          <input value={name} onChange={e => setName(e.target.value)} placeholder="名称，如：新文件入库"
            className="px-2 py-1 rounded-lg text-[11.5px] flex-1 min-w-[140px]" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          <select value={kind} onChange={e => setKind(e.target.value)}
            className="px-2 py-1 rounded-lg text-[11.5px]" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
            <option value="browser_use">浏览器任务</option><option value="file">取文件</option>
            <option value="task">智能任务</option><option value="plan">多步规划</option>
          </select>
          <input value={goal} onChange={e => setGoal(e.target.value)} placeholder="要做什么（触发时执行的目标）"
            className="px-2 py-1 rounded-lg text-[11.5px] flex-[2] min-w-[200px]" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          <button onClick={add} disabled={busy || !name.trim() || !goal.trim()}
            className="px-3 py-1 rounded-lg text-[11.5px] font-medium disabled:opacity-40" style={{ background: "var(--accent)", color: "#fff" }}>创建并复制 URL</button>
        </div>
      </div>
      {items.length === 0
        ? <StateView kind="empty" title="还没有触发器" message="创建一个，把 URL 填进外部系统或「文件夹哨兵」，事件一来自动干活" />
        : items.map(h => (
          <div key={h.id} className="rounded-xl p-3" style={{ border: "1px solid var(--border)", opacity: h.enabled === false ? 0.55 : 1 }}>
            <div className="flex items-center gap-3">
            <div className="flex-1 min-w-0 cursor-pointer" onClick={() => setOpenId(openId === h.id ? "" : h.id)}
              title="点击展开/收起最近命中明细">
              <div className="text-[12px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{h.name}
                <span className="ml-2 text-[9.5px] px-1.5 py-0.5 rounded-full" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>{h.kind}</span>
                {h.enabled === false && <span className="ml-1.5 text-[9.5px] px-1.5 py-0.5 rounded-full" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>已暂停</span>}</div>
              <div className="text-[10px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>触发 {h.hits} 次{h.last_hit ? ` · 最近 ${new Date(h.last_hit * 1000).toLocaleString()}` : ""}{(h.recent?.length || 0) > 0 ? ` · ${openId === h.id ? "收起明细 ▴" : "命中明细 ▾"}` : ""}</div>
            </div>
            <button onClick={async () => { try { await toggleHook(h.id); load(); } catch { window.alert("操作失败（后端版本过旧）"); } }}
              className="text-[10.5px] hover:underline shrink-0" style={{ color: h.enabled === false ? "var(--accent)" : "var(--text-tertiary)" }}
              title={h.enabled === false ? "恢复接收外部触发" : "暂停后外部 POST 返回 403，URL 不作废"}>{h.enabled === false ? "启用" : "暂停"}</button>
            <button onClick={() => { try { navigator.clipboard.writeText(window.location.origin + h.url); window.alert("触发 URL 已复制"); } catch { /* */ } }}
              className="text-[10.5px] hover:underline shrink-0" style={{ color: "var(--accent)" }}>复制 URL</button>
            <button onClick={async () => { if (window.confirm(`删除触发器「${h.name}」？外部再 POST 将失效。`)) { await deleteHook(h.id).catch(() => { /* */ }); load(); } }}
              className="text-[10.5px] hover:underline shrink-0" style={{ color: "var(--error)" }}>删除</button>
            </div>
            {openId === h.id && (h.recent?.length || 0) > 0 && (
              <div className="mt-2 pt-2 space-y-1" style={{ borderTop: "1px dashed var(--border)" }}>
                {h.recent!.slice().reverse().slice(0, detN).map((r, i) => (
                  <div key={i} className="text-[10px] flex gap-2" style={{ color: "var(--text-tertiary)" }}>
                    <span className="font-mono shrink-0">{new Date(r.ts * 1000).toLocaleString()}</span>
                    <span className="truncate">覆盖字段：{r.keys.length ? r.keys.join(", ") : "（空 body）"}</span>
                  </div>
                ))}
                {(h.recent!.length > detN) && (
                  <button onClick={() => setDetN(n => n + 10)}
                    className="text-[10px] hover:underline" style={{ color: "var(--accent)" }}>
                    显示更多（还有 {h.recent!.length - detN} 条）
                  </button>
                )}
                <div className="flex items-center gap-2 pt-1">
                  <button onClick={() => {
                      const rows = [["时间", "覆盖字段"]].concat((h.recent || []).map(r => [
                        new Date(r.ts * 1000).toLocaleString(), (r.keys || []).join("|") || "(空)"]));
                      const csv = "\ufeff" + rows.map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\r\n");
                      const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
                      const a = document.createElement("a");
                      a.href = URL.createObjectURL(blob);
                      a.download = `触发器-${h.name}-命中记录.csv`;
                      a.click(); URL.revokeObjectURL(a.href);
                    }}
                    className="text-[10px] px-2 py-0.5 rounded-md" style={{ border: "1px solid var(--border)", color: "var(--accent)" }}>
                    导出 CSV
                  </button>
                  <span className="text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>只记字段名不记值——哨兵送来的文件路径等可能敏感</span>
                </div>
              </div>
            )}
          </div>
        ))}
    </div>
  );
}

/* ── V338 执行 Hooks：声明式安全规则 + 可执行 Hook 精确哈希信任 ── */
const CODE_HOOK_STATUS: Record<CodeHookItem["status"], {
  label: string; tone: "neutral" | "accent" | "success" | "warning" | "error";
}> = {
  disabled: { label: "运行时已关闭", tone: "neutral" },
  invalid: { label: "文件无效", tone: "error" },
  untrusted: { label: "未信任", tone: "warning" },
  changed: { label: "内容已变化 · 已停用", tone: "error" },
  active: { label: "已信任 · 运行中", tone: "success" },
  trusted_pending_restart: { label: "已信任 · 待重启", tone: "accent" },
  unsafe: { label: "不安全来源", tone: "error" },
  missing: { label: "文件缺失", tone: "error" },
};

function ExecutionHooksTab() {
  const [rules, setRules] = useState<ExecutionHookRule[] | undefined>(undefined);
  const [codeHooks, setCodeHooks] = useState<CodeHookItem[] | undefined>(undefined);
  const [codeEnabled, setCodeEnabled] = useState(true);
  const [canManage, setCanManage] = useState(false);
  const [runtimeWarning, setRuntimeWarning] = useState("");
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState("");
  const [busyKey, setBusyKey] = useState("");
  const [ruleName, setRuleName] = useState("");
  const [ruleTools, setRuleTools] = useState("");
  const [rulePattern, setRulePattern] = useState("");
  const [ruleAction, setRuleAction] = useState<ExecutionHookRule["action"]>("confirm");
  const [ruleMessage, setRuleMessage] = useState("");

  const load = useCallback(async () => {
    setLoadError("");
    const [rulesResult, codeResult] = await Promise.allSettled([
      getExecutionHookRules(), listCodeHooks(),
    ]);
    const errors: string[] = [];
    if (rulesResult.status === "fulfilled") {
      setRules(rulesResult.value.hooks || []);
    } else {
      setRules([]);
      errors.push("声明式规则读取失败");
    }
    if (codeResult.status === "fulfilled") {
      setCodeHooks(codeResult.value.items || []);
      setCodeEnabled(codeResult.value.enabled);
      setCanManage(codeResult.value.can_manage);
      setRuntimeWarning(codeResult.value.warning || "");
    } else {
      setCodeHooks([]);
      errors.push("Python Hook 清单读取失败");
    }
    setLoadError(errors.join("；"));
  }, []);

  useEffect(() => { load(); }, [load]);

  const persistRules = async (next: ExecutionHookRule[], success: string) => {
    setBusyKey("rules");
    setNotice("");
    try {
      const result = await saveExecutionHookRules(next);
      setRules(result.hooks || []);
      setNotice(success);
      return true;
    } catch (error) {
      setNotice(`保存失败：${error instanceof Error ? error.message : "请检查管理员权限与规则格式"}`);
      return false;
    } finally {
      setBusyKey("");
    }
  };

  const addRule = async () => {
    const tools = Array.from(new Set(ruleTools.split(",").map(v => v.trim()).filter(Boolean)));
    const pattern = rulePattern.trim();
    if (!tools.length && !pattern) {
      setNotice("至少填写一个工具名或参数正则；无匹配条件的规则不会保存。");
      return;
    }
    const next: ExecutionHookRule[] = [...(rules || []), {
      name: ruleName.trim() || "未命名规则",
      when: { tools, pattern },
      action: ruleAction,
      message: ruleMessage.trim(),
      enabled: true,
    }];
    if (await persistRules(next, "规则已保存，下一次工具调用立即生效。")) {
      setRuleName(""); setRuleTools(""); setRulePattern(""); setRuleMessage("");
    }
  };

  const refreshCodeHooks = async () => {
    const result = await listCodeHooks();
    setCodeHooks(result.items || []);
    setCodeEnabled(result.enabled);
    setCanManage(result.can_manage);
    setRuntimeWarning(result.warning || "");
  };

  const trust = async (item: CodeHookItem) => {
    const confirmed = window.confirm(
      `信任 Python Hook「${item.name}」的当前内容？\n\n` +
      `来源：${item.source}\nSHA-256：${item.sha256}\n\n` +
      "它会以后端系统用户的完整权限执行。只有逐行审查过该文件时才能继续；任何内容变化都会使本次信任失效。"
    );
    if (!confirmed) return;
    setBusyKey(`trust:${item.name}`);
    setNotice("");
    try {
      const result = await trustCodeHook(item.name, item.sha256);
      await refreshCodeHooks();
      setNotice(result.restart_required
        ? `已绑定当前 SHA-256；重启 HashMM 后才会加载「${item.name}」。`
        : `已信任「${item.name}」。`);
    } catch (error) {
      setNotice(`信任失败：${error instanceof Error ? error.message : "文件可能已变化"}`);
      await refreshCodeHooks().catch(() => { /* 保留当前清单 */ });
    } finally {
      setBusyKey("");
    }
  };

  const revoke = async (item: CodeHookItem) => {
    if (!window.confirm(`撤销「${item.name}」的执行信任？已加载的 Hook 会立即停止接收后续事件。`)) return;
    setBusyKey(`revoke:${item.name}`);
    setNotice("");
    try {
      await revokeCodeHook(item.name);
      await refreshCodeHooks();
      setNotice(`已撤销「${item.name}」；无需等待重启即可失效。`);
    } catch (error) {
      setNotice(`撤销失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setBusyKey("");
    }
  };

  if (rules === undefined || codeHooks === undefined) {
    return <StateView kind="loading" message="加载执行 Hooks 与信任状态…" />;
  }

  return (
    <div className="space-y-5">
      <Card padding="p-4" style={{ borderColor: "color-mix(in srgb, var(--accent) 35%, var(--border))" }}>
        <div className="flex gap-3 items-start">
          <ShieldCheck size={18} className="shrink-0 mt-0.5" style={{ color: "var(--accent)" }} />
          <div className="min-w-0">
            <div className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>确定性执行边界</div>
            <div className="text-[11.5px] mt-1 leading-relaxed" style={{ color: "var(--text-secondary)" }}>
              block / confirm 规则在统一工具入口、权限批准之前裁决，不依赖模型是否“记得遵守”。
              Python Hook 默认不加载；信任精确绑定当前 SHA-256，文件变化或撤销后立即失效。
            </div>
          </div>
        </div>
      </Card>

      {loadError && (
        <div role="alert" className="text-[11.5px] px-3 py-2 rounded-[10px]"
          style={{ color: "var(--error)", background: "color-mix(in srgb, var(--error) 9%, transparent)", border: "1px solid color-mix(in srgb, var(--error) 25%, transparent)" }}>
          {loadError}。没有获取成功的部分不会显示为“正常”。
        </div>
      )}
      {notice && (
        <div role="status" className="text-[11.5px] px-3 py-2 rounded-[10px]"
          style={{ color: "var(--text-secondary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
          {notice}
        </div>
      )}

      <div>
        <SectionTitle>声明式规则 · 无代码</SectionTitle>
        <Card padding="p-4" className="space-y-3">
          <CardHeader title="新建 PreToolUse 规则"
            sub="工具名与参数正则任一命中即触发；正则由后端校验，错误规则不会覆盖现有配置。" />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label="规则名称">
              <input className={inputClass} style={inputStyle} value={ruleName}
                onChange={e => setRuleName(e.target.value)} maxLength={60} placeholder="例如：生产目录写入前确认" />
            </Field>
            <Field label="工具名（英文逗号分隔）" hint="例如 write_file, run_shell；请使用实际工具名。">
              <input className={inputClass} style={inputStyle} value={ruleTools}
                onChange={e => setRuleTools(e.target.value)} placeholder="write_file, run_shell" />
            </Field>
            <Field label="参数正则（可选）" hint="匹配所有字符串/数字参数的拼接文本；不要放秘密值。">
              <input className={`${inputClass} font-mono`} style={inputStyle} value={rulePattern}
                onChange={e => setRulePattern(e.target.value)} maxLength={160} placeholder="rm\\s+-rf" />
            </Field>
            <Field label="动作">
              <select className={inputClass} style={inputStyle} value={ruleAction}
                onChange={e => setRuleAction(e.target.value as ExecutionHookRule["action"])}>
                <option value="notify">notify · 记录提醒后放行</option>
                <option value="confirm">confirm · 拒绝本次并请求确认</option>
                <option value="block">block · 确定性拒绝</option>
              </select>
            </Field>
          </div>
          <Field label="给用户与 Agent 的说明">
            <input className={inputClass} style={inputStyle} value={ruleMessage}
              onChange={e => setRuleMessage(e.target.value)} maxLength={200} placeholder="说明为什么提醒、确认或阻止" />
          </Field>
          <div className="flex justify-end">
            <Button variant="primary" icon={Plus} busy={busyKey === "rules"} disabled={!canManage}
              title={canManage ? "保存后立即生效" : "只有管理员可以改变全局执行规则"} onClick={addRule}>
              添加规则
            </Button>
          </div>
        </Card>

        <div className="space-y-2 mt-3">
          {rules.length === 0 ? (
            <StateView kind="empty" title="没有声明式执行规则"
              message="默认仍由内置权限、工作区边界和高风险审批保护。可添加更严格的项目级提醒、确认或阻止规则。" />
          ) : rules.map((rule, index) => {
            const actionTone = rule.action === "block" ? "error" : rule.action === "confirm" ? "warning" : "accent";
            const tools = rule.when?.tools || [];
            return (
              <Card key={`${rule.name}:${index}`} padding="p-4" style={{ opacity: rule.enabled === false ? 0.58 : 1 }}>
                <div className="flex items-start gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex gap-2 items-center flex-wrap">
                      <span className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{rule.name}</span>
                      <Badge tone={actionTone}>{rule.action}</Badge>
                      <Badge tone={rule.enabled === false ? "neutral" : "success"}>{rule.enabled === false ? "已停用" : "已启用"}</Badge>
                    </div>
                    {rule.message && <div className="text-[11.5px] mt-1" style={{ color: "var(--text-secondary)" }}>{rule.message}</div>}
                    <div className="text-[10.5px] mt-2 font-mono break-all" style={{ color: "var(--text-tertiary)" }}>
                      {tools.length > 0 && `tools = ${tools.join(", ")}`}
                      {tools.length > 0 && rule.when?.pattern && " · "}
                      {rule.when?.pattern && `pattern = ${rule.when.pattern}`}
                    </div>
                  </div>
                  <div className="flex gap-1.5 shrink-0">
                    <Button size="sm" disabled={!canManage || busyKey === "rules"}
                      onClick={() => persistRules(rules.map((r, i) => i === index ? { ...r, enabled: r.enabled === false } : r), "规则状态已更新。") }>
                      {rule.enabled === false ? "启用" : "停用"}
                    </Button>
                    <Button size="sm" variant="danger" icon={Trash2} disabled={!canManage || busyKey === "rules"}
                      ariaLabel={`删除规则 ${rule.name}`}
                      onClick={() => { if (window.confirm(`删除规则「${rule.name}」？`)) persistRules(rules.filter((_, i) => i !== index), "规则已删除。"); }} />
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      </div>

      <div>
        <SectionTitle right={<Button size="sm" icon={RefreshCw} onClick={refreshCodeHooks}>刷新摘要</Button>}>
          Python Hooks · 可执行代码
        </SectionTitle>
        <div className="text-[11.5px] leading-relaxed px-3 py-2.5 mb-3 rounded-[10px]"
          style={{ color: "var(--warning)", background: "color-mix(in srgb, var(--warning) 10%, transparent)", border: "1px solid color-mix(in srgb, var(--warning) 28%, transparent)" }}>
          {runtimeWarning || "Python Hook 会以后端系统用户的完整权限执行。SHA-256 只证明内容未变化，不证明代码安全或发布者身份。"}
          {!codeEnabled && " 当前 HASHMM_USER_HOOKS=0，运行时不会加载任何 Python Hook。"}
          {!canManage && " 当前账号只有查看权限；信任与撤销需要管理员。"}
        </div>

        {codeHooks.length === 0 ? (
          <StateView kind="empty" title="没有发现 Python Hook"
            message="将经审查的 .py 文件放入数据目录 hooks/ 后会在此出现；文件不会因为出现就被导入。" />
        ) : <div className="space-y-2">
          {codeHooks.map(item => {
            const meta = CODE_HOOK_STATUS[item.status] || { label: item.status, tone: "neutral" as const };
            const trustable = Boolean(item.sha256) && ["untrusted", "changed"].includes(item.status);
            return (
              <Card key={item.name} padding="p-4">
                <div className="flex gap-3 items-start">
                  <ShieldCheck size={17} className="shrink-0 mt-0.5"
                    style={{ color: item.active ? "var(--success)" : "var(--text-tertiary)" }} />
                  <div className="flex-1 min-w-0">
                    <div className="flex gap-2 items-center flex-wrap">
                      <span className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{item.name}.py</span>
                      <Badge tone={meta.tone}>{meta.label}</Badge>
                      {item.events.map(event => <Badge key={event} tone="neutral">{event}</Badge>)}
                    </div>
                    <div className="mt-2 grid gap-1 text-[10.5px] font-mono" style={{ color: "var(--text-tertiary)" }}>
                      <div className="break-all">source: {item.source}</div>
                      <div className="break-all select-all">sha256: {item.sha256 || "不可用"}</div>
                      <div>size: {item.size.toLocaleString()} bytes · modified: {item.modified ? new Date(item.modified * 1000).toLocaleString() : "未知"}</div>
                      {item.trusted_at > 0 && <div>trusted: {new Date(item.trusted_at * 1000).toLocaleString()}</div>}
                      {item.error && <div style={{ color: "var(--error)" }}>error: {item.error}</div>}
                    </div>
                  </div>
                  <div className="flex gap-1.5 shrink-0 flex-wrap justify-end">
                    {trustable && (
                      <Button size="sm" variant="primary" busy={busyKey === `trust:${item.name}`}
                        disabled={!canManage || !codeEnabled} onClick={() => trust(item)}>
                        信任此摘要
                      </Button>
                    )}
                    {item.receipt_present && (
                      <Button size="sm" variant="danger" busy={busyKey === `revoke:${item.name}`}
                        disabled={!canManage} onClick={() => revoke(item)}>
                        撤销信任
                      </Button>
                    )}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>}
      </div>
    </div>
  );
}

/* ── V231 ⑦ AI 偏好：每轮对话自动带上的长期偏好（可查可改可删） ── */
function PrefsTab() {
  const [text, setText] = useState("");
  const [updated, setUpdated] = useState(0);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState("");
  useEffect(() => { getPrefs().then(r => { setText(r.text || ""); setUpdated(r.updated || 0); }).catch(() => { /* */ }); }, []);
  return (
    <div className="space-y-2 max-w-[640px]">
      <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
        写在这里的偏好，Agent 每轮对话自动遵循（不复述）。例如：回复用中文、代码注释写详细、我的项目根目录是 D:\sheji、周报用三段式。
        {updated > 0 && ` · 上次更新 ${new Date(updated * 1000).toLocaleString()}`}
      </div>
      <textarea value={text} onChange={e => setText(e.target.value)} maxLength={2000} rows={9}
        placeholder="我的长期偏好…（≤2000 字，只有你和你的 Agent 看得到）"
        className="w-full rounded-xl p-3 text-[12px] leading-relaxed resize-y"
        style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
      <div className="flex gap-2 items-center">
        <button onClick={async () => { setBusy(true); try { await putPrefs(text); setTick("已保存"); setUpdated(Date.now() / 1000); } catch { setTick("保存失败"); } finally { setBusy(false); setTimeout(() => setTick(""), 1600); } }}
          disabled={busy} className="px-3 py-1 rounded-lg text-[11.5px] font-medium disabled:opacity-40" style={{ background: "var(--accent)", color: "#fff" }}>保存偏好</button>
        <button onClick={async () => { if (!window.confirm("清空偏好卡？Agent 将不再带任何长期偏好。")) return; try { await delPrefs(); setText(""); setUpdated(0); setTick("已清空"); setTimeout(() => setTick(""), 1600); } catch { /* */ } }}
          className="px-3 py-1 rounded-lg text-[11.5px]" style={{ border: "1px solid var(--border)", color: "var(--error)" }}>清空</button>
        <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{tick}</span>
      </div>
    </div>
  );
}

/* ── V232 ⑧ 组织模板管理：全员共享库的看板与治理（删除需管理员，403 会明说） ── */
function OrgTplTab() {
  const [items, setItems] = useState<{ id: string; name: string; by: string }[] | undefined>(undefined);
  const load = useCallback(async () => {
    try { const r = await listCanvasTemplates(); setItems(r.org_items || []); } catch { setItems([]); }
  }, []);
  useEffect(() => { load(); }, [load]);
  if (items === undefined) return <StateView kind="loading" message="加载组织模板…" />;
  return (
    <div className="space-y-3">
      <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
        全员共享的起稿模板（上限 20 个）。任何人可在「画布 › 我的模板 › ↑组织」升入；<b>删除需要管理员账号</b>。
        当前 {items.length}/20。
      </div>
      {items.length === 0
        ? <StateView kind="empty" title="组织模板库为空" message="把团队最好用的画布模板「↑组织」进来，人人一键起稿" />
        : items.map(t => (
          <div key={t.id} className="rounded-xl p-3 flex items-center gap-3" style={{ border: "1px solid var(--border)" }}>
            <div className="flex-1 min-w-0">
              <div className="text-[12px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{t.name}</div>
              <div className="text-[10px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>由 {t.by || "未知"} 分享 · 全员起稿菜单可见</div>
            </div>
            <button onClick={async () => {
                if (!window.confirm(`下架组织模板「${t.name}」？全员起稿菜单将不再显示。`)) return;
                try { await deleteCanvasTemplate(t.id); load(); }
                catch { window.alert("删除失败：组织模板需要管理员账号"); }
              }}
              className="text-[10.5px] hover:underline shrink-0" style={{ color: "var(--error)" }}>下架</button>
          </div>
        ))}
    </div>
  );
}

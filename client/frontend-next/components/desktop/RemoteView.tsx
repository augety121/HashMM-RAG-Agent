"use client";
/** components/desktop/RemoteView.tsx — 设备接力工作区。
 *
 * 首屏按设备组织，而不是把实现方式做成三个功能卡片：左侧选设备，
 * 右侧继续工作；局域网直连、TURN、RDP、Moonlight、隐私防护等真实
 * 能力保留在连接与设备设置中。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { getRemote, saveFile } from "@/lib/desktop";
import { getRemoteDevices, getRemoteDiagnostics, getRemoteReadiness, getRunners, type RemoteDiagnostics, type RemoteDeviceV2, type RunnerStatus } from "@/lib/api";
import { useStore } from "@/lib/store";
import { insertContextIntoChat } from "@/lib/contextInsert";
import {
  MonitorSmartphone, Monitor, Wifi, Copy, RefreshCw, Power, ShieldCheck, Globe,
  Settings, Download, Check, ChevronRight, Laptop, KeyRound, Pause, Play,
  MessageSquare, Smartphone, Network, Gamepad2, ExternalLink, ArrowRight,
  ArrowLeft, FolderOpen, Plus, History,
} from "lucide-react";
import { PanelShell, PageHeader, Card, Badge } from "./ui/PanelKit";

type RemoteQuality = {
  schema: "hashmm.remote-quality.v1"; source: "lan" | "account"; sampledAt: number; ageMs: number; stale: boolean;
  generation: number; tierIndex: number; tierName: string; direction: "up" | "down" | "hold"; reason: string; autoAdjusted: boolean;
  health: { grade: "good" | "fair" | "poor" | "unknown"; label: string };
  metrics: { rttMs: number | null; packetLossPct: number | null; availableOutgoingBitrateKbps: number | null; framesPerSecond: number | null };
};
type Status = { running: boolean; port?: number; clients?: number; paired?: number; hostConnected?: boolean; webrtcActive?: number; addrs?: string[]; privacy?: boolean; privacyMode?: "remote-content-shield"; physicalScreenBlanked?: false; quality?: RemoteQuality | null };
type RemoteReadiness = Awaited<ReturnType<typeof getRemoteReadiness>>;
type HandoffReceipt = { conv_id: string; title: string; run_id?: string; checkpoint_id?: string; received_at: number };
type NetworkBridgeStatus = {
  ok: boolean;
  overlayReady?: boolean;
  adapters?: { name: string; address: string; cidr: string; mac: string }[];
  clients?: {
    rdp: { available: boolean; path: string };
    moonlight: { available: boolean; path: string };
    easytier: { available: boolean; path: string };
  };
  mode?: "external-overlay-ready" | "native-webrtc";
  routingPolicy?: string[];
  easyTier?: { integration: string; installed: boolean; active: boolean; bundled: false; autoStart: false; secretCustody: string; licenseBoundary: string };
  disclosure?: string;
  error?: string;
};
type AccountHostState = { on?: boolean; mediaOn?: boolean; registered?: boolean; state?: string; detail?: string; deviceId?: string; signal?: string; attemptId?: string; traceId?: string; errorCode?: string; configRevision?: string; security?: Record<string, unknown> | null; updatedAt?: number; closeCode?: number; closeReason?: string };

const REMOTE_PENDING_STATES = new Set(["starting", "connecting", "issuing_ticket", "opening_socket", "authenticating", "reconnecting"]);
const REMOTE_ERROR_STATES = new Set(["error", "degraded", "auth_expired", "rejected", "renderer-error", "bootstrap_error"]);
const REMOTE_FAILURE_LABELS: Record<string, string> = {
  PUBLIC_ENDPOINT_INSECURE: "公网远程入口不是 HTTPS/WSS",
  PROXY_SECURITY_UNVERIFIED: "代理未能证明原始连接使用了 HTTPS/WSS",
  EDGE_HEADER_MISSING: "Cloudflare 边缘信息不完整",
  REMOTE_BOOTSTRAP_PROTOCOL_MISMATCH: "桌面端与服务器远程协议版本不一致",
  REMOTE_BOOTSTRAP_ENDPOINT_MISMATCH: "服务器下发的 API 与 WSS 地址不一致",
  REMOTE_BOOTSTRAP_INVALID: "服务器返回的远程入口配置无效",
  REMOTE_BOOTSTRAP_UNAVAILABLE: "暂时无法读取远程入口配置",
  TICKET_EXPIRED: "一次性连接凭证已过期",
  TICKET_REPLAYED: "一次性连接凭证已被使用",
  AUTH_TIMEOUT: "远程主机注册等待超时",
  TOKEN_INVALID: "账号登录凭证已失效",
};

function remoteFailureText(code?: string, fallback?: string) {
  return (code && REMOTE_FAILURE_LABELS[code]) || fallback || "尚未完成远程主机注册";
}

function describeRemoteHost(state?: string, registered?: boolean) {
  if (registered || state === "registered") return { label: "远程已上线", tone: "success" as const, detail: "同账号设备现在可以发现这台电脑" };
  if (REMOTE_PENDING_STATES.has(state || "")) return { label: state === "reconnecting" ? "正在重新连接" : "正在注册电脑", tone: "warning" as const, detail: "安全信令由桌面主进程维护，页面切换不会中断" };
  if (REMOTE_ERROR_STATES.has(state || "")) return { label: state === "auth_expired" ? "登录已过期" : "远程需要处理", tone: "error" as const, detail: "打开诊断可查看失败阶段与请求标识" };
  return { label: "远程未上线", tone: "neutral" as const, detail: "开启后，同账号 App 才能发现并请求连接" };
}

/** 统一的行内开关（与系统设置一致的 40×22 胶囊） */
function Switch({ on, onToggle, disabled }: { on: boolean; onToggle: () => void; disabled?: boolean }) {
  return (
    <button onClick={onToggle} role="switch" aria-checked={on} disabled={disabled}
      className="relative w-10 h-[22px] rounded-full transition-colors flex-shrink-0 disabled:opacity-40"
      style={{ background: on ? "var(--accent)" : "var(--border)" }}>
      <span className="absolute top-[2px] w-[18px] h-[18px] bg-white rounded-full transition-all" style={{ left: on ? "20px" : "2px" }} />
    </button>
  );
}

/** 高级设置里的分节标题 */
function AdvTitle({ icon: Icon, children }: { icon: React.ComponentType<{ size?: number }>; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 mb-2 text-[12.5px] font-semibold" style={{ color: "var(--text-primary)" }}>
      <Icon size={13} /> {children}
    </div>
  );
}

export function RemoteView() {
  const remote = getRemote();
  const token = useStore((s) => s.token);
  const isAdmin = useStore((s) => s.user?.role === "admin");
  const set = useStore((s) => s.set);
  const [st, setSt] = useState<Status | null>(null);
  const [code, setCode] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<number>(0);
  const [now, setNow] = useState<number>(Date.now());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string>("");
  const [copied, setCopied] = useState<string>("");
  const [acctOn, setAcctOn] = useState(false);
  const [accountHost, setAccountHost] = useState<AccountHostState>({ state: "stopped", registered: false });
  const [remoteDevices, setRemoteDevices] = useState<RemoteDeviceV2[]>([]);
  const [remoteDiagnostics, setRemoteDiagnostics] = useState<RemoteDiagnostics | null>(null);
  const [lastHandoff, setLastHandoff] = useState<HandoffReceipt | null>(null);
  const [autoOn, setAutoOn] = useState(true);
  const [remoteSection, setRemoteSection] = useState<"home" | "handoff" | "connect" | "device">("home");
  const [selectedDevice, setSelectedDevice] = useState("__local");
  const [runners, setRunners] = useState<RunnerStatus[]>([]);
  const [runnerState, setRunnerState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [runnerError, setRunnerError] = useState("");
  const [advOpen, setAdvOpen] = useState(false);
  const [connectOpen, setConnectOpen] = useState(false);
  const [readiness, setReadiness] = useState<RemoteReadiness | null>(null);
  const [networkBridge, setNetworkBridge] = useState<NetworkBridgeStatus | null>(null);
  const [privateTarget, setPrivateTarget] = useState("");
  const [privatePort, setPrivatePort] = useState("17690");
  const [privateMessage, setPrivateMessage] = useState("");
  // 高级：手动跨网 / ICE
  const [offerCode, setOfferCode] = useState("");
  const [answerIn, setAnswerIn] = useState("");
  const [manMsg, setManMsg] = useState("");
  const [iceText, setIceText] = useState("");
  const [iceMsg, setIceMsg] = useState("");
  const [savedViewer, setSavedViewer] = useState(false);
  // 高级：远程开机 / 画质 / 隐私 / 多屏
  const [wolTargets, setWolTargets] = useState<{ id: string; name: string; mac: string; address?: string }[]>([]);
  const [wolName, setWolName] = useState("");
  const [wolMac, setWolMac] = useState("");
  const [wolMsg, setWolMsg] = useState("");
  const [fps, setFps] = useState(8);
  const [jpeg, setJpeg] = useState(70);
  const [privacy, setPrivacy] = useState(false);
  const [monitors, setMonitors] = useState<{ id: number; index: number; primary: boolean; width: number; height: number; label: string }[]>([]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => { setAutoOn(typeof window === "undefined" ? true : localStorage.getItem("hmm_remote_auto") !== "0"); }, []);
  useEffect(() => {
    try {
      const stored = JSON.parse(localStorage.getItem("hmm_last_device_resume") || "null") as HandoffReceipt | null;
      if (stored?.conv_id) setLastHandoff(stored);
    } catch { /* no handoff has been received on this device */ }
    const receive = (event: Event) => {
      const value = (event as CustomEvent<HandoffReceipt>).detail;
      if (value?.conv_id) setLastHandoff(value);
    };
    window.addEventListener("hmm-device-resume-open", receive);
    return () => window.removeEventListener("hmm-device-resume-open", receive);
  }, []);
  useEffect(() => {
    if (!remote) return;
    const load = () => {
      remote.status().then((s) => { setSt(s as Status); setPrivacy(!!s.privacy); }).catch(() => {});
      remote.accountHostStatus?.().then((r) => {
        const next = (r || {}) as AccountHostState;
        setAccountHost(next);
        setAcctOn(!!next.registered);
      }).catch(() => {});
    };
    load();
    pollRef.current = setInterval(load, 2000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [remote]);
  useEffect(() => { const iv = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(iv); }, []);
  useEffect(() => {
    if (!token) { setReadiness(null); return; }
    let alive = true;
    const load = () => Promise.allSettled([getRemoteReadiness(), getRemoteDevices(true), getRemoteDiagnostics(accountHost.deviceId || "")]).then(([ready, devices, diagnostics]) => {
      if (!alive) return;
      if (ready.status === "fulfilled") setReadiness(ready.value);
      if (devices.status === "fulfilled") setRemoteDevices(devices.value.devices || []);
      if (diagnostics.status === "fulfilled") setRemoteDiagnostics(diagnostics.value);
    });
    load(); const timer = setInterval(load, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, [token, accountHost.deviceId]);
  const loadRunners = useCallback(async () => {
    if (!token) {
      setRunners([]);
      setRunnerState("idle");
      setRunnerError("");
      return;
    }
    setRunnerState("loading");
    try {
      const value = await getRunners();
      setRunners(value.runners || []);
      setRunnerState("ready");
      setRunnerError("");
    } catch (error) {
      setRunners([]);
      setRunnerState("error");
      setRunnerError(error instanceof Error ? error.message : "暂时无法读取设备列表。");
    }
  }, [token]);
  useEffect(() => {
    let alive = true;
    const load = async () => { if (alive) await loadRunners(); };
    void load();
    const timer = setInterval(() => { void load(); }, 15000);
    return () => { alive = false; clearInterval(timer); };
  }, [loadRunners]);
  useEffect(() => {
    if (!remote) return;
    remote.currentCode().then((r) => { if (r && r.ok && r.code) { setCode(r.code); if (r.expiresAt) setExpiresAt(r.expiresAt); } }).catch(() => {});
    remote.getIce?.().then((r) => { if (r && r.ok && r.iceServers?.length) setIceText(JSON.stringify(r.iceServers, null, 2)); }).catch(() => {});
    remote.listWolTargets?.().then((r) => { if (r.ok) setWolTargets(r.targets || []); }).catch(() => {});
    remote.listMonitors?.().then((r) => { if (r.ok && r.monitors) setMonitors(r.monitors); }).catch(() => {});
    remote.networkBridgeStatus?.().then((r) => setNetworkBridge(r)).catch(() => setNetworkBridge(null));
  }, [remote]);

  if (!remote) {
    return (
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="text-[12.5px] text-center leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
          远程桌面仅在 HashMM 桌面端可用。<br />（未检测到桌面桥，请用桌面客户端打开。）
        </div>
      </div>
    );
  }

  const running = !!st?.running;
  const port = st?.port || 17690;
  const addrs = st?.addrs || [];
  const remainSec = expiresAt ? Math.max(0, Math.round((expiresAt - now) / 1000)) : 0;
  const codeAlive = !!code && remainSec > 0;
  const quality = st?.quality && !st.quality.stale ? st.quality : null;

  const copy = (txt: string, tag: string) => { try { navigator.clipboard.writeText(txt); setCopied(tag); setTimeout(() => setCopied(""), 1500); } catch (_e) {} };
  const diagnoseQuality = () => {
    if (!quality) return;
    insertContextIntoChat("run", "远程会话质量快照", quality,
      "根据这份真实远程会话质量快照诊断当前体验，只使用已测得的指标；区分网络、设备和未知原因，并给出按优先级排序的安全调整建议。",
      `remote:${quality.source}:${quality.generation}`);
  };

  const start = async () => {
    setBusy(true); setErr("");
    try {
      const r = await remote.start({});
      if (r.ok) {
        if (r.code) setCode(r.code); if (r.expiresAt) setExpiresAt(r.expiresAt);
        setSt({ running: true, port: r.port, addrs: r.addrs, clients: 0, paired: 0 });
        if (token && remote.startAccountHost) { await remote.startAccountHost(token); const s = await remote.accountHostStatus?.(); setAcctOn(!!(s && s.registered)); }
      } else setErr(r.error || "启动失败");
    } catch (e: any) { setErr(e?.message || "启动失败"); }
    setBusy(false);
  };
  const stop = async () => {
    setBusy(true); setErr("");
    try { await remote.stop(); setSt({ running: false }); setCode(null); setExpiresAt(0); setOfferCode(""); setAcctOn(false); }
    catch (e: any) { setErr(e?.message || "停止失败"); }
    setBusy(false);
  };
  const toggleAuto = () => {
    const next = !autoOn; setAutoOn(next);
    try { localStorage.setItem("hmm_remote_auto", next ? "1" : "0"); } catch (_e) {}
    if (next && !running) start();
  };
  const reissue = async () => { setBusy(true); setErr(""); try { const r = await remote.issueCode(); if (r.ok && r.code) { setCode(r.code); if (r.expiresAt) setExpiresAt(r.expiresAt); } else setErr(r.error || "签发失败"); } catch (e: any) { setErr(e?.message || "签发失败"); } setBusy(false); };
  const toggleAcct = async () => {
    if (!token) { setErr("请先登录账号，才能用同账号跨网络直连"); return; }
    setBusy(true);
    try { if (acctOn) { await remote.stopAccountHost?.(); setAcctOn(false); } else { const a = await remote.startAccountHost?.(token); if (a && !a.ok) setErr(a.error || "开启账号直连失败"); const s = await remote.accountHostStatus?.(); setAcctOn(!!(s && s.registered)); } }
    catch (e: any) { setErr(e?.message || "操作失败"); }
    setBusy(false);
  };
  const connectMine = async () => { if (!token) { setErr("请先登录账号"); return; } try { await remote.openAccountViewer?.({ token }); } catch (e: any) { setErr(e?.message || "打开失败"); } };
  const refreshNetworkBridge = async () => {
    setPrivateMessage("");
    try {
      const result = await remote.networkBridgeStatus?.();
      if (result) setNetworkBridge(result);
    } catch (e: any) { setPrivateMessage(e?.message || "检测失败"); }
  };
  const openPrivate = async (kind: "hashmm" | "rdp" | "moonlight") => {
    const target = privateTarget.trim();
    if (!target) { setPrivateMessage("先填写另一台设备的私网 IP 或设备名"); return; }
    setPrivateMessage("正在打开客户端…");
    try {
      const result = kind === "hashmm"
        ? await remote.openDirectViewer?.({ target, port: Number(privatePort || 17690) })
        : kind === "rdp"
          ? await remote.launchRdp?.({ target, port: 3389 })
          : await remote.launchMoonlight?.({ target, app: "Desktop" });
      setPrivateMessage(result?.ok
        ? kind === "hashmm" ? "已打开 HashMM 控制窗口，请在目标电脑确认授权。" : kind === "rdp" ? "已交给系统远程桌面。" : "已交给 Moonlight；首次使用请先与 Sunshine 主机配对。"
        : result?.error || "客户端未能启动");
    } catch (e: any) { setPrivateMessage(e?.message || "客户端未能启动"); }
  };
  const openNamedDevice = async (target: string) => {
    const safeTarget = target.trim();
    if (!safeTarget) return;
    setPrivateTarget(safeTarget);
    setPrivateMessage("正在打开安全控制窗口…");
    try {
      const result = await remote.openDirectViewer?.({ target: safeTarget, port: Number(privatePort || 17690) });
      setPrivateMessage(result?.ok ? "控制窗口已打开，请在目标电脑确认授权。" : result?.error || "客户端未能启动");
    } catch (e: any) {
      setPrivateMessage(e?.message || "客户端未能启动");
    }
  };
  const genOffer = async () => { if (!remote.manualOffer) return; setManMsg("正在生成邀请码（捕获屏幕、收集网络信息，约数秒）…"); setOfferCode(""); try { const r = await remote.manualOffer(); if (r.ok && r.code) { setOfferCode(r.code); setManMsg("邀请码已生成，发给对方；对方在查看端「手动」粘贴后生成应答码回传"); } else setManMsg("生成失败：" + (r.error || "")); } catch (e: any) { setManMsg("生成失败：" + (e?.message || e)); } };
  const applyAnswer = async () => { if (!remote.manualAnswer || !answerIn.trim()) { setManMsg("请先粘贴对方的应答码"); return; } setManMsg("正在应用…"); try { const r = await remote.manualAnswer(answerIn.trim()); setManMsg(r.ok ? "应答已应用，正在建立点对点连接" : "失败：" + (r.error || "")); } catch (e: any) { setManMsg("失败：" + (e?.message || e)); } };
  const saveIce = async () => { if (!remote.setIce) return; setIceMsg(""); let arr: any[] = []; if (iceText.trim()) { try { arr = JSON.parse(iceText); if (!Array.isArray(arr)) throw new Error(); } catch (_e) { setIceMsg('格式应为 JSON 数组，如 [{"urls":"turn:host:3478","username":"u","credential":"p"}]'); return; } } try { const r = await remote.setIce(arr); setIceMsg(r.ok ? "已保存，新连接生效。" : "保存失败：" + (r.error || "")); } catch (e: any) { setIceMsg("保存失败：" + (e?.message || e)); } };
  const saveViewer = async () => { if (!remote.viewerHtml) return; try { const r = await remote.viewerHtml(); if (r.ok && r.html) { await saveFile("HashMM-远程查看端.html", r.html); setSavedViewer(true); setTimeout(() => setSavedViewer(false), 2500); } } catch (_e) {} };
  const addWol = async () => {
    if (!remote?.saveWolTarget) return;
    setWolMsg("");
    if (!/^[0-9a-fA-F]{2}([:-]?[0-9a-fA-F]{2}){5}$/.test(wolMac.trim())) { setWolMsg("MAC 格式应为 AA:BB:CC:DD:EE:FF"); return; }
    try { const r = await remote.saveWolTarget({ name: wolName.trim() || "我的电脑", mac: wolMac.trim() }); if (r.ok) { setWolTargets(r.targets || []); setWolName(""); setWolMac(""); } else setWolMsg(r.error || "保存失败"); }
    catch (e: any) { setWolMsg(String(e?.message || e)); }
  };
  const wake = async (mac: string, name: string) => {
    if (!remote?.wake) return;
    setWolMsg("正在发送开机指令…");
    try { const r = await remote.wake(mac); setWolMsg(r.ok ? `已向「${name}」发送开机指令（${r.sent} 次）。目标机已配置网络唤醒且在同一局域网时将上电开机。` : "发送失败：" + (r.error || "")); }
    catch (e: any) { setWolMsg("发送失败：" + String(e?.message || e)); }
  };
  const delWol = async (id: string) => { if (!remote?.removeWolTarget) return; try { const r = await remote.removeWolTarget(id); if (r.ok) setWolTargets(r.targets || []); } catch (_e) {} };
  const applyQuality = async (nf: number, nj: number) => { setFps(nf); setJpeg(nj); if (remote?.setRemoteQuality) { try { await remote.setRemoteQuality({ fps: nf, jpeg: nj }); } catch (_e) {} } };
  const togglePrivacy = async () => {
    const next = !privacy;
    if (!remote?.setPrivacy) return;
    try { const r = await remote.setPrivacy(next); if (r.ok) setPrivacy(!!r.privacy); }
    catch (_e) { /* keep server-observed state; the status poll will refresh it */ }
  };

  const remoteStatus = describeRemoteHost(accountHost.state, acctOn);
  const statusDot = remoteStatus.tone === "success" ? "var(--success)" : remoteStatus.tone === "warning" ? "var(--warning)" : remoteStatus.tone === "error" ? "var(--error)" : "var(--text-tertiary)";

  return (
    <PanelShell className="workbench-vnext remote-workbench">
      <div style={{ maxWidth: 980, margin: "0 auto" }}>
        <PageHeader icon={MonitorSmartphone} title="设备接力"
          subtitle="从刚才停下的位置继续，或安全地使用自己的另一台电脑"
          actions={<Badge tone={remoteStatus.tone}>
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: statusDot }} />{remoteStatus.label}
          </Badge>} />

        {remoteSection === "home" ? (
          <div className="mb-4 grid min-h-[440px] grid-cols-1 overflow-hidden rounded-2xl lg:grid-cols-[260px_minmax(0,1fr)]"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
            <aside className="p-3.5" style={{ background: "var(--bg-secondary)", borderRight: "1px solid var(--border)" }}>
              <div className="flex items-center justify-between px-2 pb-2">
                <span className="text-[11.5px] font-semibold" style={{ color: "var(--text-primary)" }}>我的设备</span>
                <button onClick={() => setRemoteSection("connect")} className="flex h-7 w-7 items-center justify-center rounded-lg hover:bg-[var(--bg-tertiary)]" title="连接其他电脑">
                  <Plus size={14} style={{ color: "var(--text-secondary)" }} />
                </button>
              </div>
              <div className="space-y-1">
                <button onClick={() => setSelectedDevice("__local")} className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2.5 text-left"
                  style={{ background: selectedDevice === "__local" ? "var(--accent-light)" : "transparent" }}>
                  <span className="flex h-8 w-6 items-center justify-center" style={{ color: "var(--accent)" }}><Laptop size={15} /></span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>这台电脑</span>
                    <span className="mt-0.5 flex items-center gap-1.5 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>
                      <span className="h-1.5 w-1.5 rounded-full" style={{ background: acctOn ? "var(--success)" : running ? "var(--warning)" : "var(--text-tertiary)" }} />
                      {acctOn ? "可远程" : running ? "仅本机服务" : "已暂停"}
                    </span>
                  </span>
                </button>
                <button onClick={() => setSelectedDevice("__handoff")} className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2.5 text-left"
                  style={{ background: selectedDevice === "__handoff" ? "var(--accent-light)" : "transparent" }}>
                  <span className="flex h-8 w-6 items-center justify-center" style={{ color: "var(--accent)" }}><Smartphone size={15} /></span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>App 接力</span>
                    <span className="mt-0.5 block truncate text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>{lastHandoff ? "有工作可以继续" : "等待新的接力"}</span>
                  </span>
                </button>
                {remoteDevices.filter(device => device.role === "host" && device.device_id !== accountHost.deviceId).map(device => (
                  <button key={device.device_id} onClick={() => setSelectedDevice(`remote:${device.device_id}`)}
                    className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2.5 text-left"
                    style={{ background: selectedDevice === `remote:${device.device_id}` ? "var(--accent-light)" : "transparent" }}>
                    <span className="flex h-8 w-8 items-center justify-center" style={{ color: device.remote_ready ? "var(--accent)" : "var(--text-tertiary)" }}><Monitor size={15} /></span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>{device.name || "电脑"}</span>
                      <span className="mt-0.5 flex items-center gap-1.5 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>
                        <span className="h-1.5 w-1.5 rounded-full" style={{ background: device.remote_ready ? "var(--success)" : device.online ? "var(--warning)" : "var(--text-tertiary)" }} />
                        {device.remote_ready ? "可远程" : device.online ? "仅在线" : "离线"}
                      </span>
                    </span>
                  </button>
                ))}
                {!!runners.length && <div className="px-2 pb-1 pt-3 text-[9px] font-semibold uppercase tracking-wide" style={{ color: "var(--text-tertiary)" }}>Agent 执行节点</div>}
                {runners.map(runner => (
                  <button key={runner.name} onClick={() => setSelectedDevice(runner.name)}
                    className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2.5 text-left"
                    style={{ background: selectedDevice === runner.name ? "var(--accent-light)" : "transparent" }}>
                    <span className="flex h-8 w-6 items-center justify-center" style={{ color: runner.online ? "var(--accent)" : "var(--text-tertiary)" }}><Monitor size={15} /></span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>{runner.name}</span>
                      <span className="mt-0.5 flex items-center gap-1.5 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>
                        <span className="h-1.5 w-1.5 rounded-full" style={{ background: runner.online ? "var(--success)" : "var(--text-tertiary)" }} />
                        {runner.online ? "在线" : "离线"}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
              {runnerState === "loading" && (
                <div className="px-3 py-2 text-[9.8px]" style={{ color: "var(--text-tertiary)" }}>正在同步账号设备…</div>
              )}
              {runnerState === "error" && (
                <div className="mx-1 mt-2 rounded-xl px-3 py-2.5" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                  <div className="text-[9.8px] leading-4" style={{ color: "var(--error)" }}>{runnerError}</div>
                  <button onClick={() => void loadRunners()} className="mt-2 text-[10px] font-medium" style={{ color: "var(--accent)" }}>重新同步</button>
                </div>
              )}
              <button onClick={() => setRemoteSection("connect")}
                className="mt-3 flex w-full items-center gap-2 rounded-xl px-3 py-2.5 text-left hover:bg-[var(--bg-tertiary)]"
                style={{ color: "var(--text-secondary)", border: "1px dashed var(--border)" }}>
                <Network size={14} /><span className="text-[10.5px]">添加或连接设备</span>
              </button>
            </aside>

            <section className="flex min-w-0 flex-col p-5 sm:p-7">
              {selectedDevice === "__handoff" ? (
                <>
                  <div className="flex items-center gap-3">
                    <span className="flex h-10 w-7 items-center justify-center" style={{ color: "var(--accent)" }}><Smartphone size={18} /></span>
                    <div>
                      <h2 className="text-[17px] font-semibold" style={{ color: "var(--text-primary)" }}>App 接力</h2>
                      <p className="mt-0.5 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>同一账号的对话、进度与成果会回到这里</p>
                    </div>
                  </div>
                  <div className="mt-5 rounded-2xl p-4 sm:p-5" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                    <div className="flex items-start gap-3">
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
                        style={{ color: "var(--accent)", background: "var(--accent-light)" }}><History size={16} /></span>
                      <div className="min-w-0 flex-1">
                        <div className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{lastHandoff?.title || "等待 App 发来工作"}</div>
                        <p className="mt-1 text-[10.5px] leading-5" style={{ color: "var(--text-tertiary)" }}>
                          {lastHandoff ? "它会回到原对话、原进度和原成果，不会创建重复副本。" : "在 App 中打开一项工作并选择“在电脑继续”，这台电脑会在同账号校验通过后接收。"}
                        </p>
                        {lastHandoff && (
                          <div className="mt-3 flex flex-wrap items-center gap-2">
                            <button onClick={() => set({ sid: lastHandoff.conv_id, desktopView: null })}
                              className="rounded-xl px-3.5 py-2 text-[11px] font-medium text-white" style={{ background: "var(--accent)" }}>继续原工作</button>
                            <span className="text-[9.8px]" style={{ color: "var(--text-tertiary)" }}>{new Date(lastHandoff.received_at).toLocaleString()}</span>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="mt-3 grid gap-3 sm:grid-cols-3">
                    {[
                      ["账号", token ? "已登录" : "需要登录"],
                      ["同步通道", acctOn ? "已连接" : token ? "正在准备" : "未连接"],
                      ["接力范围", "对话、进度与成果"],
                    ].map(([label, value]) => (
                      <div key={label} className="rounded-xl px-3 py-2.5" style={{ border: "1px solid var(--border)" }}>
                        <div className="text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>{label}</div>
                        <div className="mt-1 text-[11px] font-medium" style={{ color: "var(--text-primary)" }}>{value}</div>
                      </div>
                    ))}
                  </div>
                </>
              ) : selectedDevice === "__local" ? (
                <>
                  <div className="flex items-center gap-3">
                    <span className="flex h-10 w-7 items-center justify-center" style={{ color: "var(--accent)" }}><Laptop size={18} /></span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2"><h2 className="text-[17px] font-semibold" style={{ color: "var(--text-primary)" }}>这台电脑</h2><Badge tone={acctOn ? "success" : running ? "warning" : "neutral"}>{acctOn ? "可远程" : running ? "远程未注册" : "已暂停"}</Badge></div>
                      <p className="mt-0.5 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{acctOn ? "已在同账号设备目录中注册，可以接收经过你确认的连接请求" : running ? `本机服务已启动，但账号远程主机尚未注册：${remoteFailureText(accountHost.errorCode, accountHost.detail)}` : "其他设备暂时无法连接"}</p>
                    </div>
                  </div>
                  <div className="mt-5 rounded-2xl p-4 sm:p-5"
                    style={{ background: "linear-gradient(145deg, var(--accent-light), var(--bg-secondary) 68%)", border: "1px solid var(--border)" }}>
                    <div className="flex flex-wrap items-start gap-3">
                      <span className="flex h-11 w-11 items-center justify-center rounded-2xl"
                        style={{ color: "var(--accent)", background: "var(--bg-primary)", boxShadow: "var(--shadow-sm)" }}><Monitor size={20} /></span>
                      <div className="min-w-[220px] flex-1">
                        <div className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>{acctOn ? "这台电脑可以远程接力" : running ? "正在建立账号远程通道" : "开启后才能从其他设备访问"}</div>
                        <p className="mt-1 text-[10.5px] leading-5" style={{ color: "var(--text-tertiary)" }}>
                          每次连接都会显示请求方和权限范围；屏幕、文件与控制权限分别确认，并保留审计记录。
                        </p>
                        <p className="mt-1 text-[10px] leading-5" style={{ color: "var(--text-tertiary)" }}>
                          账号服务器仅负责身份、设备发现、审批与信令；画面优先端到端直连，其次 TURN，均失败才启用应急 HTTPS 中继。
                        </p>
                      </div>
                      <button onClick={() => running ? setRemoteSection("device") : void start()} disabled={busy}
                        className="rounded-xl px-3.5 py-2 text-[11px] font-medium text-white disabled:opacity-40" style={{ background: "var(--accent)" }}>
                        {running ? "管理接力权限" : busy ? "正在开启…" : "开启设备接力"}
                      </button>
                    </div>
                  </div>
                  <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    {[
                      ["服务状态", running ? "已运行" : "已暂停"],
                      ["已连接", `${st?.clients || 0} 台设备`],
                      ["控制通道", acctOn ? "已就绪" : accountHost.state === "reconnecting" ? "恢复中" : "未连接"],
                      ["内容保护", privacy ? "已开启" : "未开启"],
                    ].map(([label, value]) => (
                      <div key={label} className="rounded-xl px-3 py-2.5" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                        <div className="text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>{label}</div>
                        <div className="mt-1 text-[11px] font-medium" style={{ color: "var(--text-primary)" }}>{value}</div>
                      </div>
                    ))}
                  </div>
                  {err && <div className="mt-3 rounded-xl px-3 py-2 text-[10.5px]" style={{ color: "var(--error)", background: "var(--bg-secondary)" }}>{err}</div>}
                  {!acctOn && remoteDiagnostics?.steps?.length ? (
                    <div className="mt-3 rounded-2xl p-4" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                      <div className="flex items-center gap-2 text-[11.5px] font-semibold" style={{ color: "var(--text-primary)" }}><ShieldCheck size={13} />连接诊断</div>
                      <div className="mt-2 space-y-2">
                        {remoteDiagnostics.steps.map(step => (
                          <div key={step.id} className="flex items-start gap-2 text-[10.5px]">
                            <span className="mt-1 h-1.5 w-1.5 flex-shrink-0 rounded-full" style={{ background: step.state === "ok" ? "var(--success)" : step.state === "blocked" || step.state === "missing" ? "var(--error)" : "var(--warning)" }} />
                            <span style={{ color: "var(--text-secondary)" }}>{step.detail}</span>
                          </div>
                        ))}
                      </div>
                      {(accountHost.errorCode || remoteDiagnostics.latest_failure?.error_code) && (
                        <div className="mt-2 text-[10px]" style={{ color: "var(--error)" }}>
                          {remoteFailureText(accountHost.errorCode || remoteDiagnostics.latest_failure?.error_code)}
                        </div>
                      )}
                      {(accountHost.traceId || remoteDiagnostics.latest_failure?.trace_id) && <div className="mt-1 truncate font-mono text-[9px]" style={{ color: "var(--text-tertiary)" }}>跟踪 {accountHost.traceId || remoteDiagnostics.latest_failure?.trace_id}</div>}
                      {(accountHost.attemptId || remoteDiagnostics.latest_failure?.attempt_id) && <div className="mt-1 truncate font-mono text-[9px]" style={{ color: "var(--text-tertiary)" }}>尝试 {accountHost.attemptId || remoteDiagnostics.latest_failure?.attempt_id}</div>}
                    </div>
                  ) : null}
                </>
              ) : selectedDevice.startsWith("remote:") ? (() => {
                const device = remoteDevices.find(item => item.device_id === selectedDevice.slice(7));
                return (
                  <>
                    <div className="flex items-center gap-3">
                      <span className="flex h-10 w-10 items-center justify-center" style={{ color: device?.remote_ready ? "var(--accent)" : "var(--text-tertiary)" }}><Monitor size={18} /></span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2"><h2 className="truncate text-[17px] font-semibold" style={{ color: "var(--text-primary)" }}>{device?.name || "电脑"}</h2><Badge tone={device?.remote_ready ? "success" : device?.online ? "warning" : "neutral"}>{device?.remote_ready ? "可远程" : device?.online ? "仅在线" : "离线"}</Badge></div>
                        <p className="mt-0.5 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{device?.last_seen ? `最近上报 ${new Date(device.last_seen * 1000).toLocaleString("zh-CN")}` : "还没有在线记录"}</p>
                      </div>
                    </div>
                    <div className="mt-5 border-y py-4" style={{ borderColor: "var(--border)" }}>
                      <div className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{device?.remote_ready ? "这台电脑已注册远程主机" : device?.online ? "设备在线，但远程主机未注册" : "等待这台电脑上线"}</div>
                      <p className="mt-1 text-[10.5px] leading-5" style={{ color: "var(--text-tertiary)" }}>只有 remote_ready 为真才允许进入桌面；协议连接、Agent 心跳和远程主机注册不再混为同一个状态。</p>
                      <button disabled={!device?.remote_ready} onClick={connectMine} className="mt-3 rounded-xl px-3.5 py-2 text-[11px] font-medium text-white disabled:opacity-40" style={{ background: "var(--accent)" }}>进入桌面</button>
                    </div>
                    <div className="mt-3 grid gap-3 sm:grid-cols-3">
                      {[["版本", device?.app_version || "未知"], ["平台", device?.platform || "未知"], ["设备标识", device?.device_fingerprint || "未知"]].map(([label, value]) => <div key={label} className="border-b px-1 py-2.5" style={{ borderColor: "var(--border)" }}><div className="text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>{label}</div><div className="mt-1 truncate text-[11px] font-medium" style={{ color: "var(--text-primary)" }}>{value}</div></div>)}
                    </div>
                  </>
                );
              })() : (() => {
                const runner = runners.find(item => item.name === selectedDevice);
                return (
                  <>
                    <div className="flex items-center gap-3">
                      <span className="flex h-10 w-10 items-center justify-center rounded-xl" style={{ color: runner?.online ? "var(--accent)" : "var(--text-tertiary)", background: "var(--accent-light)" }}><Monitor size={18} /></span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2"><h2 className="truncate text-[17px] font-semibold" style={{ color: "var(--text-primary)" }}>{runner?.name || selectedDevice}</h2><Badge tone={runner?.online ? "success" : "neutral"}>{runner?.online ? "在线" : "离线"}</Badge></div>
                        <p className="mt-0.5 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{runner?.last_seen ? `最近在线 ${new Date(runner.last_seen * 1000).toLocaleString("zh-CN")}` : "还没有在线记录"}</p>
                      </div>
                    </div>
                    <div className="mt-5 rounded-2xl p-4 sm:p-5" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                      <div className="flex flex-wrap items-start gap-3">
                        <span className="flex h-10 w-10 items-center justify-center rounded-xl"
                          style={{ color: runner?.online ? "var(--accent)" : "var(--text-tertiary)", background: "var(--bg-primary)" }}><Monitor size={18} /></span>
                        <div className="min-w-[220px] flex-1">
                          <div className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{runner?.online ? "可以继续这台电脑上的工作" : "等待这台电脑上线"}</div>
                          <p className="mt-1 text-[10.5px] leading-5" style={{ color: "var(--text-tertiary)" }}>
                            优先尝试加密点对点连接；是否经过中继只以实际链路状态为准。
                          </p>
                        </div>
                        <button disabled={!runner?.online} onClick={() => void openNamedDevice(runner?.name || selectedDevice)}
                          className="rounded-xl px-3.5 py-2 text-[11px] font-medium text-white disabled:opacity-40" style={{ background: "var(--accent)" }}>进入桌面</button>
                      </div>
                    </div>
                    <div className="mt-3 grid gap-3 sm:grid-cols-3">
                      {[
                        ["今日完成", `${runner?.today_done || 0} 项`],
                        ["今日失败", `${runner?.today_failed || 0} 项`],
                        ["最近接单", runner?.last_claim ? new Date(runner.last_claim * 1000).toLocaleString("zh-CN") : "尚无"],
                      ].map(([label, value]) => (
                        <div key={label} className="rounded-xl px-3 py-2.5" style={{ border: "1px solid var(--border)" }}>
                          <div className="text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>{label}</div>
                          <div className="mt-1 truncate text-[11px] font-medium" style={{ color: "var(--text-primary)" }}>{value}</div>
                        </div>
                      ))}
                    </div>
                  </>
                );
              })()}
              <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
                <button onClick={connectMine} disabled={!token} className="flex items-center justify-center gap-1.5 rounded-xl px-3 py-2.5 text-[10.5px] disabled:opacity-40" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}><MonitorSmartphone size={13} />我的设备</button>
                <button onClick={() => setRemoteSection("connect")} className="flex items-center justify-center gap-1.5 rounded-xl px-3 py-2.5 text-[10.5px]" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}><FolderOpen size={13} />文件与连接</button>
                <button onClick={() => setRemoteSection("device")} className="col-span-2 flex items-center justify-center gap-1.5 rounded-xl px-3 py-2.5 text-[10.5px] sm:col-span-1" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}><Settings size={13} />更多设置</button>
              </div>
            </section>
          </div>
        ) : (
          <button onClick={() => setRemoteSection("home")} className="mb-4 inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[10.5px] hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-secondary)" }}>
            <ArrowLeft size={12} /> 返回设备接力
          </button>
        )}

        <div className="flex flex-col gap-4">
          <Card padding="p-5" className={remoteSection === "handoff" ? "" : "hidden"}>
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
                style={{ background: acctOn ? "var(--accent-light)" : "var(--surface-2)" }}>
                <Smartphone size={18} style={{ color: acctOn ? "var(--accent)" : "var(--text-tertiary)" }} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>接回 App 上的工作</div>
                  <Badge tone={token && acctOn ? "success" : "neutral"}>{token && acctOn ? "同账号已就绪" : token ? "正在连接" : "需要登录"}</Badge>
                </div>
                <div className="text-[11px] leading-5 mt-1" style={{ color: "var(--text-tertiary)" }}>
                  在 App 中选择“在电脑继续”，这里会直接回到原对话、原进度和原成果。
                </div>
                {lastHandoff ? (
                  <button onClick={() => set({ sid: lastHandoff.conv_id, desktopView: null })}
                    className="mt-3 w-full flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-left hover:bg-[var(--bg-tertiary)]"
                    style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                    <MessageSquare size={14} style={{ color: "var(--accent)" }} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>{lastHandoff.title || "来自 App 的对话"}</span>
                      <span className="block mt-0.5 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>
                        最近接力 · {new Date(lastHandoff.received_at).toLocaleString()}
                      </span>
                    </span>
                    <span className="text-[10.5px] font-medium" style={{ color: "var(--accent)" }}>继续</span>
                  </button>
                ) : (
                  <div className="mt-3 rounded-xl px-3 py-2.5 text-[10.5px]"
                    style={{ color: "var(--text-tertiary)", background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                    还没有新的接力。使用同一账号在 App 中选择“在电脑继续”即可。
                  </div>
                )}
              </div>
            </div>
          </Card>

          <Card padding="p-5" className={remoteSection === "connect" ? "" : "hidden"}>
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
                style={{ background: networkBridge?.overlayReady ? "var(--accent-light)" : "var(--surface-2)" }}>
                <Network size={18} style={{ color: networkBridge?.overlayReady ? "var(--accent)" : "var(--text-tertiary)" }} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>连接另一台电脑</div>
                  <Badge tone={networkBridge?.overlayReady ? "success" : "neutral"}>
                    {networkBridge?.overlayReady ? "可以连接" : "需要先准备"}
                  </Badge>
                  <button onClick={() => setConnectOpen(value => !value)} className="ml-auto rounded-lg px-2.5 py-1.5 text-[10.5px] font-medium" style={{ color: "var(--accent)", background: "var(--accent-light)" }}>
                    {connectOpen ? "收起" : "选择连接方式"}
                  </button>
                </div>
                <p className="text-[11px] leading-5 mt-1" style={{ color: "var(--text-tertiary)" }}>
                  根据你的需要选择安全控制、远程办公或流畅画面。连接前会说明准备条件，HashMM 不会读取或保存组网密钥。
                </p>
                {connectOpen && (
                  <div className="mt-4 rounded-2xl p-3.5" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                    <label className="block">
                      <span className="mb-1.5 block text-[10.5px] font-medium" style={{ color: "var(--text-secondary)" }}>另一台电脑的设备名或私网地址</span>
                      <input value={privateTarget} onChange={(event) => setPrivateTarget(event.target.value)}
                        placeholder="例如：办公室电脑，或 10.144.2.9"
                        className="w-full rounded-xl px-3 py-2.5 text-[11.5px] outline-none"
                        style={{ color: "var(--text-primary)", background: "var(--bg-primary)", border: "1px solid var(--border)" }} />
                    </label>
                    <div className="mt-3 grid grid-cols-1 sm:grid-cols-3 gap-2">
                      <button onClick={() => openPrivate("hashmm")} className="rounded-xl px-3 py-3 text-left hover:bg-[var(--bg-tertiary)]" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                        <span className="flex items-center gap-1.5 text-[11.5px] font-semibold" style={{ color: "var(--text-primary)" }}><MonitorSmartphone size={13} /> 安全控制</span>
                        <span className="block mt-1 text-[9.5px] leading-4" style={{ color: "var(--text-tertiary)" }}>每次确认权限，操作可以回看</span>
                      </button>
                      <button onClick={() => openPrivate("rdp")} disabled={!networkBridge?.clients?.rdp.available}
                        className="rounded-xl px-3 py-3 text-left hover:bg-[var(--bg-tertiary)] disabled:opacity-45"
                        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                        <span className="flex items-center gap-1.5 text-[11.5px] font-semibold" style={{ color: "var(--text-primary)" }}><Monitor size={13} /> 远程办公</span>
                        <span className="block mt-1 text-[9.5px] leading-4" style={{ color: "var(--text-tertiary)" }}>适合办公软件和完整桌面</span>
                      </button>
                      <button onClick={() => openPrivate("moonlight")} disabled={!networkBridge?.clients?.moonlight.available}
                        className="rounded-xl px-3 py-3 text-left hover:bg-[var(--bg-tertiary)] disabled:opacity-45"
                        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                        <span className="flex items-center gap-1.5 text-[11.5px] font-semibold" style={{ color: "var(--text-primary)" }}><Gamepad2 size={13} /> 流畅画面</span>
                        <span className="block mt-1 text-[9.5px] leading-4" style={{ color: "var(--text-tertiary)" }}>适合对画面和延迟要求高的场景</span>
                      </button>
                    </div>
                    {privateMessage && <p className="mt-2 text-[10.5px]" style={{ color: /失败|未找到|填写/.test(privateMessage) ? "var(--error)" : "var(--text-secondary)" }}>{privateMessage}</p>}
                    <details className="mt-3 rounded-xl" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                      <summary className="cursor-pointer px-3 py-2 text-[10.5px] font-medium" style={{ color: "var(--text-secondary)" }}>连接帮助</summary>
                      <div className="border-t px-3 py-3" style={{ borderColor: "var(--border)" }}>
                        <p className="text-[10px] leading-5" style={{ color: "var(--text-tertiary)" }}>默认依次尝试 WebRTC 点对点、TURN、已批准的私网适配器，最后才使用应急 HTTPS 画面中继。EasyTier 由用户独立安装和保管组网密钥，HashMM 不捆绑、不静默启动。</p>
                        {networkBridge?.easyTier && <p className="mt-1 text-[9.5px]" style={{ color: networkBridge.easyTier.active ? "var(--success)" : "var(--text-tertiary)" }}>EasyTier：{networkBridge.easyTier.active ? "虚拟网卡已就绪" : networkBridge.easyTier.installed ? "客户端已发现，等待用户建立私网" : "未安装（可选）"}</p>}
                        <div className="mt-2 flex flex-wrap items-center gap-1.5">
                          {networkBridge?.adapters?.map(adapter => (
                            <button key={`${adapter.name}:${adapter.address}`} onClick={() => copy(adapter.address, `adapter:${adapter.address}`)} className="rounded-lg px-2 py-1 text-[9.5px]" style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
                              {adapter.name} · {copied === `adapter:${adapter.address}` ? "已复制" : adapter.address}
                            </button>
                          ))}
                          <button onClick={refreshNetworkBridge} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[9.5px]" style={{ color: "var(--accent)" }}><RefreshCw size={10} />重新检测</button>
                          <button onClick={() => remote.openNetworkGuide?.()} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[9.5px]" style={{ color: "var(--accent)" }}>查看准备方法 <ExternalLink size={9} /></button>
                        </div>
                        <label className="mt-2 flex items-center gap-2 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>
                          HashMM 控制端口
                          <input value={privatePort} onChange={(event) => setPrivatePort(event.target.value)} inputMode="numeric" className="w-24 rounded-lg px-2 py-1.5 outline-none" style={{ color: "var(--text-primary)", border: "1px solid var(--border)" }} />
                        </label>
                      </div>
                    </details>
                  </div>
                )}
              </div>
            </div>
          </Card>

          {/* ① 本机状态 · 极简一行 */}
          <Card padding="p-5" className={remoteSection === "device" ? "" : "hidden"}>
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
                style={{ background: running ? "var(--accent-light)" : "var(--surface-2)" }}>
                <Laptop size={19} style={{ color: running ? "var(--accent)" : "var(--text-tertiary)" }} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>这台电脑</div>
                <div className="text-[11.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
                  {running
                    ? (st?.clients ? `${st.clients} 台设备正在连接` : "已经准备好，可以接收连接请求")
                    : "远程已暂停，其他设备暂时无法连接本机"}
                </div>
              </div>
              <button onClick={running ? stop : start} disabled={busy}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11.5px] transition-colors hover:bg-[var(--bg-tertiary)] disabled:opacity-50 flex-shrink-0"
                style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
                {running ? <Pause size={13} /> : <Play size={13} />}{busy ? "…" : running ? "暂停" : "开启"}
              </button>
            </div>

            {quality && (
              <div className="mt-4 rounded-xl px-3.5 py-3" style={{ background: "var(--surface-2)", border: "1px solid var(--hairline)" }}>
                <div className="flex items-center gap-2 mb-2.5">
                  <Wifi size={13} style={{ color: quality.health.grade === "good" ? "var(--success)" : quality.health.grade === "poor" ? "var(--error)" : "var(--warning)" }} />
                  <span className="text-[11.5px] font-semibold" style={{ color: "var(--text-primary)" }}>{quality.health.label}</span>
                  <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{quality.tierName} · {quality.source === "account" ? "账号连接" : "局域网连接"}</span>
                  <button onClick={diagnoseQuality} className="ml-auto text-[10.5px] font-medium hover:underline" style={{ color: "var(--accent)" }}>交给 Chat 诊断</button>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {[
                    ["延迟", quality.metrics.rttMs == null ? "未测得" : `${Math.round(quality.metrics.rttMs)} ms`],
                    ["丢包", quality.metrics.packetLossPct == null ? "未测得" : `${quality.metrics.packetLossPct.toFixed(1)}%`],
                    ["可用带宽", quality.metrics.availableOutgoingBitrateKbps == null ? "未测得" : `${Math.round(quality.metrics.availableOutgoingBitrateKbps)} kbps`],
                    ["发送帧率", quality.metrics.framesPerSecond == null ? "未测得" : `${Math.round(quality.metrics.framesPerSecond)} fps`],
                  ].map(([label, value]) => (
                    <div key={label} className="min-w-0">
                      <div className="text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>{label}</div>
                      <div className="text-[11.5px] font-mono truncate mt-0.5" style={{ color: "var(--text-secondary)" }}>{value}</div>
                    </div>
                  ))}
                </div>
                {quality.reason && <div className="text-[10.5px] leading-relaxed mt-2.5" style={{ color: "var(--text-tertiary)" }}>{quality.reason}</div>}
              </div>
            )}

            {/* 同账号直连 + 自动开启：两行轻设置 */}
            <div className="mt-4 pt-4 flex flex-col gap-3" style={{ borderTop: "1px solid var(--hairline)" }}>
              <div className="flex items-center gap-2.5">
                <Globe size={14} style={{ color: acctOn ? "var(--success)" : "var(--text-tertiary)" }} className="flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-[12.5px]" style={{ color: "var(--text-primary)" }}>允许接收连接请求</div>
                  <div className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{!token ? "登录后可用：同账号设备可以申请连接" : acctOn ? "已开启 · 收到请求后仍需在本机确认授权范围" : "已关闭"}</div>
                </div>
                <Switch on={acctOn} onToggle={toggleAcct} disabled={busy || !token || !running} />
              </div>
              <div className="flex items-center gap-2.5">
                <Power size={14} style={{ color: autoOn ? "var(--accent)" : "var(--text-tertiary)" }} className="flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-[12.5px]" style={{ color: "var(--text-primary)" }}>随客户端自动开启</div>
                  <div className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>打开 HashMM 即就绪，无需手动开启远程</div>
                </div>
                <Switch on={autoOn} onToggle={toggleAuto} />
              </div>
            </div>
          </Card>

          {remoteSection === "device" && token && readiness && isAdmin && (
            <Card padding="p-4">
              <div className="flex items-start gap-3">
                <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
                  style={{ background: readiness.production_ready ? "var(--success-light)" : "var(--surface-2)" }}>
                  <ShieldCheck size={17} style={{ color: readiness.production_ready ? "var(--success)" : "var(--text-tertiary)" }} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[12.5px] font-semibold" style={{ color: "var(--text-primary)" }}>复杂网络接力</span>
                    <Badge tone={readiness.production_ready ? "success" : "neutral"}>
                      {readiness.production_ready ? "已通过生产验收" : "待真实环境验收"}
                    </Badge>
                  </div>
                  <div className="text-[10.5px] leading-relaxed mt-1" style={{ color: "var(--text-tertiary)" }}>
                    {readiness.production_ready
                      ? "自有中继、公网多设备与 24 小时稳定性记录均在有效期内。"
                      : readiness.criteria?.temporary_turn_credentials
                        ? "自有中继已配置；公网多设备或 24 小时记录尚未全部达到验收条件。"
                        : "点对点连接仍可使用；复杂 NAT 下可能回退兼容画面，请由管理员配置自有中继。"}
                  </div>
                  {isAdmin && !readiness.production_ready && (
                    <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2 text-[10px]" style={{ color: "var(--text-tertiary)" }}>
                      <span>安全链路 {readiness.criteria?.secure_transport_enforced ? "已开" : "未开"}</span>
                      <span>短期 TURN {readiness.criteria?.temporary_turn_credentials ? "已配" : "未配"}</span>
                      <span>公网双端 {readiness.criteria?.public_multi_device_acceptance ? "已验" : "未验"}</span>
                      <span>24 小时 {readiness.criteria?.twenty_four_hour_soak ? "已验" : "未验"}</span>
                    </div>
                  )}
                </div>
              </div>
            </Card>
          )}

          {/* 临时连接码：普通用户需要时再展开，不占据主任务视觉层级。 */}
          {remoteSection === "connect" && running && (
            <details className="overflow-hidden rounded-2xl" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
              <summary className="flex cursor-pointer items-center gap-2.5 px-5 py-4 text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
                <KeyRound size={15} style={{ color: "var(--text-tertiary)" }} />
                用临时连接码连接
                <span className="ml-auto text-[10.5px] font-normal" style={{ color: "var(--text-tertiary)" }}>适合身边的可信设备</span>
              </summary>
              <div className="border-t px-5 py-4" style={{ borderColor: "var(--border)" }}>
                {codeAlive ? (
                  <div className="flex items-center gap-4 flex-wrap">
                    <span className="font-mono font-bold tracking-[0.3em] text-[30px] leading-none" style={{ color: "var(--text-primary)" }}>{code}</span>
                    <button onClick={() => copy(code || "", "code")} className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11.5px]" style={{ border: "1px solid var(--border)", color: copied === "code" ? "var(--success)" : "var(--text-secondary)" }}>
                      {copied === "code" ? <><Check size={12} /> 已复制</> : <><Copy size={12} /> 复制</>}
                    </button>
                    <span className="ml-auto text-[11px]" style={{ color: remainSec < 30 ? "var(--error)" : "var(--text-tertiary)" }}>{Math.floor(remainSec / 60)}:{String(remainSec % 60).padStart(2, "0")} 后失效</span>
                  </div>
                ) : <button onClick={reissue} className="text-[11.5px] font-medium" style={{ color: "var(--accent)" }}>生成新的连接码</button>}
                <div className="mt-3 flex items-center justify-between gap-3">
                  <p className="text-[10.5px] leading-5" style={{ color: "var(--text-tertiary)" }}>只把连接码发给你信任的设备。实际连接仍会在本机确认权限。</p>
                  <button onClick={reissue} disabled={busy} className="inline-flex shrink-0 items-center gap-1 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}><RefreshCw size={10} />换一个</button>
                </div>
              </div>
            </details>
          )}

          {/* ③ 控制我的其他设备 */}
          <Card padding="p-5" className={remoteSection === "handoff" ? "" : "hidden"}>
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: "var(--surface-2)" }}>
                <Monitor size={19} style={{ color: "var(--text-secondary)" }} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>我的在线电脑</div>
                <div className="text-[11.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>查看同一账号下可连接的电脑，再选择要继续工作的设备</div>
              </div>
              <button onClick={connectMine} disabled={!token}
                className="px-4 py-2 rounded-xl text-[12.5px] font-medium text-white flex items-center gap-1.5 transition-all hover:brightness-110 disabled:opacity-40 flex-shrink-0"
                style={{ background: "var(--accent)", boxShadow: "var(--shadow-sm)" }}>
                <Monitor size={14} /> 查看电脑
              </button>
            </div>
            {!token && <div className="text-[11px] mt-2.5" style={{ color: "var(--text-tertiary)" }}>请先登录账号</div>}
          </Card>

          {err && <div className="text-[11.5px] px-1" style={{ color: "var(--error)" }}>{err}</div>}

          {/* ④ 高级设置（一处收纳全部） */}
          <Card padding="p-0" className={remoteSection === "device" ? "" : "hidden"}>
            <button onClick={() => setAdvOpen(v => !v)} aria-expanded={advOpen}
              className="w-full flex items-center gap-2.5 px-5 py-4 transition-colors hover:bg-[var(--surface-hover)]">
              <Settings size={15} style={{ color: "var(--text-tertiary)" }} />
              <span className="text-[13px] font-medium flex-1 text-left" style={{ color: "var(--text-primary)" }}>高级设置</span>
              <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{isAdmin ? "画质 · 隐私 · 网络与中继 · 远程开机" : "画质 · 隐私防护 · 远程开机 · 显示器"}</span>
              <ChevronRight size={15} className="transition-transform" style={{ color: "var(--text-tertiary)", transform: advOpen ? "rotate(90deg)" : "none" }} />
            </button>

            {advOpen && (
              <div className="px-5 pb-5 flex flex-col gap-5" style={{ borderTop: "1px solid var(--hairline)", paddingTop: 18 }}>
                {/* 画质 */}
                <div>
                  <AdvTitle icon={Monitor}>画质（兼容通道）</AdvTitle>
                  <div className="flex items-center gap-1.5">
                    {[{ k: "流畅", f: 12, j: 55 }, { k: "均衡", f: 8, j: 70 }, { k: "真彩", f: 6, j: 90 }].map(({ k, f, j }) => (
                      <button key={k} onClick={() => applyQuality(f, j)}
                        className="flex-1 px-2 py-1.5 rounded-lg text-[11.5px] transition-all"
                        style={(fps === f && jpeg === j) ? { background: "var(--accent-light)", color: "var(--accent)", fontWeight: 600 } : { background: "var(--surface-2)", color: "var(--text-secondary)" }}>
                        {k}
                      </button>
                    ))}
                  </div>
                  <div className="text-[10.5px] mt-1.5" style={{ color: "var(--text-tertiary)" }}>当前 {fps} 帧 · 压缩质量 {jpeg}。点对点接通时走硬件编码，此设置只影响兼容回退通道。</div>
                </div>

                {/* 隐私防护 */}
                <div className="flex items-center gap-2.5">
                  <ShieldCheck size={14} style={{ color: privacy ? "var(--accent)" : "var(--text-tertiary)" }} className="flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-[12.5px] font-medium" style={{ color: "var(--text-primary)" }}>远端内容保护</div>
                    <div className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>向远端发送遮罩画面并停止远端输入；不会声称已熄灭本机物理屏幕</div>
                  </div>
                  <Switch on={privacy} onToggle={togglePrivacy} />
                </div>

                {/* 局域网地址属于部署细节，仅管理员可见。 */}
                {isAdmin && <div>
                  <AdvTitle icon={Wifi}>局域网 / 浏览器直连</AdvTitle>
                  <div className="text-[11px] mb-2" style={{ color: "var(--text-tertiary)" }}>同一局域网时，另一台设备用浏览器打开以下地址即可，免安装：</div>
                  {addrs.length ? addrs.map((a) => { const url = `http://${a}:${port}/`; return (
                    <div key={a} className="flex items-center justify-between rounded-lg px-3 py-1.5 mb-1.5" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                      <span className="font-mono text-[11.5px] truncate" style={{ color: "var(--text-primary)" }}>{url}</span>
                      <button onClick={() => copy(url, a)} className="text-[10.5px] px-1.5 py-0.5 rounded flex-shrink-0 ml-2" style={{ color: "var(--text-secondary)" }}>{copied === a ? "已复制" : "复制"}</button>
                    </div>); }) : <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>本机自测：http://127.0.0.1:{port}/</div>}
                </div>}

                {/* 远程开机 */}
                <div>
                  <AdvTitle icon={Power}>远程开机（网络唤醒）</AdvTitle>
                  <div className="flex flex-col gap-1.5">
                    {wolTargets.map((t) => (
                      <div key={t.id} className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg" style={{ background: "var(--surface-2)" }}>
                        <span className="flex-1 truncate text-[12px]" style={{ color: "var(--text-secondary)" }}>{t.name} <span className="font-mono text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{t.mac}</span></span>
                        <button onClick={() => wake(t.mac, t.name)} className="px-2 py-1 rounded-md text-[11px] text-white" style={{ background: "var(--accent)" }}>开机</button>
                        <button onClick={() => delWol(t.id)} className="px-1.5 py-1 rounded-md text-[11px]" style={{ color: "var(--text-tertiary)" }}>删除</button>
                      </div>
                    ))}
                    {wolTargets.length === 0 && <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>还没有保存的开机目标。添加目标机器的网卡 MAC 地址即可一键唤醒。</div>}
                    <div className="flex items-center gap-1.5 mt-1">
                      <input value={wolName} onChange={(e) => setWolName(e.target.value)} placeholder="名称（如 公司主机）" className="w-[34%] px-2 py-1.5 rounded-lg text-[11.5px] outline-none" style={{ background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
                      <input value={wolMac} onChange={(e) => setWolMac(e.target.value)} placeholder="AA:BB:CC:DD:EE:FF" className="flex-1 px-2 py-1.5 rounded-lg text-[11.5px] font-mono outline-none" style={{ background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
                      <button onClick={addWol} className="px-2.5 py-1.5 rounded-lg text-[11.5px] font-medium" style={{ background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>添加</button>
                    </div>
                    {wolMsg && <div className="text-[11px] mt-1 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>{wolMsg}</div>}
                  </div>
                </div>

                {/* 多屏 */}
                <div>
                  <AdvTitle icon={Monitor}>本机显示器</AdvTitle>
                  {monitors.length ? (
                    <div className="flex flex-col gap-1.5">
                      {monitors.map((m) => (
                        <div key={m.id} className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg" style={{ background: "var(--surface-2)" }}>
                          <Monitor size={13} style={{ color: m.primary ? "var(--accent)" : "var(--text-tertiary)" }} />
                          <span className="flex-1 truncate text-[12px]" style={{ color: "var(--text-secondary)" }}>{m.label || `显示器 ${m.index + 1}`}</span>
                          <span className="font-mono text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{m.width}×{m.height}</span>
                          {m.primary && <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>主屏</span>}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>检测到单显示器。被控端连上后，可在远程会话窗口顶部切换要查看的显示器。</div>
                  )}
                </div>

                {/* 手工 SDP 会绕开正常产品流程，只保留给管理员排障。 */}
                {isAdmin && <div>
                  <AdvTitle icon={Globe}>跨网络手动连接（无账号时的兜底）</AdvTitle>
                  <div className="text-[11px] leading-relaxed mb-2" style={{ color: "var(--text-tertiary)" }}>不登录又跨网络时：把查看端网页发给对方，再互换邀请码 / 应答码。</div>
                  <button onClick={saveViewer} className="w-full mb-2 px-3 py-1.5 rounded-lg text-[11.5px] flex items-center justify-center gap-1.5 transition-colors hover:bg-[var(--bg-tertiary)]" style={{ border: "1px solid var(--border)", color: "var(--text-primary)" }}>{savedViewer ? <><Check size={12} style={{ color: "var(--success)" }} /> 已保存</> : <><Download size={12} /> 1. 保存查看端网页（发给对方）</>}</button>
                  <button onClick={genOffer} className="w-full mb-2 px-3 py-1.5 rounded-lg text-[11.5px] font-medium text-white transition-all hover:brightness-110" style={{ background: "var(--accent)" }}>2. 生成邀请码</button>
                  {offerCode && (<div className="mb-2"><textarea readOnly value={offerCode} className="w-full text-[10px] font-mono rounded-lg p-2" style={{ height: 56, background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-secondary)", resize: "vertical" }} /><button onClick={() => copy(offerCode, "offer")} className="mt-1 text-[10.5px] font-medium" style={{ color: "var(--accent)" }}>{copied === "offer" ? "已复制" : "复制邀请码"}</button></div>)}
                  <textarea value={answerIn} onChange={(e) => setAnswerIn(e.target.value)} placeholder="3. 粘贴对方回传的应答码…" className="w-full text-[10px] font-mono rounded-lg p-2 mb-2" style={{ height: 50, background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)", resize: "vertical" }} />
                  <button onClick={applyAnswer} className="w-full px-3 py-1.5 rounded-lg text-[11.5px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]" style={{ border: "1px solid var(--border)", color: "var(--text-primary)" }}>4. 应用应答码，开始连接</button>
                  {manMsg && <div className="text-[10.5px] mt-2 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>{manMsg}</div>}
                </div>}

                {/* TURN 凭据是管理配置，普通用户绝不能读取或修改。 */}
                {isAdmin && <div>
                  <AdvTitle icon={Settings}>中继服务器（TURN / ICE，选填）</AdvTitle>
                  <div className="text-[11px] leading-relaxed mb-2" style={{ color: "var(--text-tertiary)" }}>默认使用公共 STUN，多数网络够用。少数严格 NAT 环境需要 TURN 中继（JSON 数组）：</div>
                  <textarea value={iceText} onChange={(e) => setIceText(e.target.value)} placeholder='[{"urls":"turn:turn.example:3478","username":"u","credential":"p"}]' className="w-full text-[10px] font-mono rounded-lg p-2 mb-2" style={{ height: 64, background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)", resize: "vertical" }} />
                  <button onClick={saveIce} className="px-3 py-1.5 rounded-lg text-[11.5px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]" style={{ border: "1px solid var(--border)", color: "var(--text-primary)" }}>保存</button>
                  {iceMsg && <div className="text-[10.5px] mt-2" style={{ color: "var(--text-tertiary)" }}>{iceMsg}</div>}
                </div>}

                <div className="text-[10.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
                  文件传输在远程会话窗口内使用：批准“文件”权限后优先走加密点对点数据通道；受限网络下会使用已认证信令回退，当前单文件安全上限 256MB。
                </div>
              </div>
            )}
          </Card>
        </div>
      </div>
    </PanelShell>
  );
}

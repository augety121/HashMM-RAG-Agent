"use client";
/** components/desktop/RemoteView.tsx — 远程桌面（简洁版）。V103.17
 *
 * 两件事，分两块：
 *   ① 让别人控制这台电脑（被控）：开启远程后，同账号设备可**跨网络直连**；也给 6 位授权码 + 局域网网址兜底。
 *   ② 控制我的其他电脑（控制）：一键打开查看端，列出同账号在线设备，点一下即连。
 * 跨网络靠你已有的公网账号后端做信令碰头点（零额外服务器）；媒体走 WebRTC P2P 直连（硬件编码、DTLS 加密）。
 */
import { useEffect, useRef, useState } from "react";
import { getRemote, saveFile } from "@/lib/desktop";
import { useStore } from "@/lib/store";
import { MonitorSmartphone, Monitor, Wifi, Copy, RefreshCw, Power, ShieldCheck, Globe, Settings, Download, Check, ChevronRight } from "lucide-react";
import { PanelShell, PageHeader } from "./ui/PanelKit";

type Status = { running: boolean; port?: number; clients?: number; paired?: number; hostConnected?: boolean; webrtcActive?: number; addrs?: string[] };

export function RemoteView() {
  const remote = getRemote();
  const token = useStore((s) => s.token);
  const [st, setSt] = useState<Status | null>(null);
  const [code, setCode] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<number>(0);
  const [now, setNow] = useState<number>(Date.now());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string>("");
  const [copied, setCopied] = useState<string>("");
  const [acctOn, setAcctOn] = useState(false);
  const [showLan, setShowLan] = useState(false);
  const [showManual, setShowManual] = useState(false);
  const [showIce, setShowIce] = useState(false);
  const [offerCode, setOfferCode] = useState("");
  const [answerIn, setAnswerIn] = useState("");
  const [manMsg, setManMsg] = useState("");
  const [iceText, setIceText] = useState("");
  const [iceMsg, setIceMsg] = useState("");
  const [savedViewer, setSavedViewer] = useState(false);
  // V103.51 对标 UU 远程的扩展能力 state
  const [showMore, setShowMore] = useState(false);
  const [wolTargets, setWolTargets] = useState<{ id: string; name: string; mac: string; address?: string }[]>([]);
  const [wolName, setWolName] = useState("");
  const [wolMac, setWolMac] = useState("");
  const [wolMsg, setWolMsg] = useState("");
  const [fps, setFps] = useState(8);
  const [jpeg, setJpeg] = useState(70);
  const [privacy, setPrivacy] = useState(false);
  const [monitors, setMonitors] = useState<{ id: number; index: number; primary: boolean; width: number; height: number; label: string }[]>([]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!remote) return;
    const load = () => { remote.status().then((s) => setSt(s as Status)).catch(() => {}); remote.accountHostStatus?.().then((r) => setAcctOn(!!(r && r.on))).catch(() => {}); };
    load();
    pollRef.current = setInterval(load, 2000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [remote]);
  useEffect(() => { const iv = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(iv); }, []);
  useEffect(() => {
    if (!remote) return;
    remote.currentCode().then((r) => { if (r && r.ok && r.code) { setCode(r.code); if (r.expiresAt) setExpiresAt(r.expiresAt); } }).catch(() => {});
    remote.getIce?.().then((r) => { if (r && r.ok && r.iceServers?.length) setIceText(JSON.stringify(r.iceServers, null, 2)); }).catch(() => {});
  }, [remote]);

  if (!remote) {
    return (
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="text-[12.5px] text-center leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
          远程桌面仅在 HashMM 桌面端可用。<br />（未检测到 hashmmRemote 桥，请用桌面端打开。）
        </div>
      </div>
    );
  }

  const running = !!st?.running;
  const port = st?.port || 17690;
  const addrs = st?.addrs || [];
  const remainSec = expiresAt ? Math.max(0, Math.round((expiresAt - now) / 1000)) : 0;
  const codeAlive = !!code && remainSec > 0;
  const rtcN = st?.webrtcActive || 0;

  const copy = (txt: string, tag: string) => { try { navigator.clipboard.writeText(txt); setCopied(tag); setTimeout(() => setCopied(""), 1500); } catch (_e) {} };

  const start = async () => {
    setBusy(true); setErr("");
    try {
      const r = await remote.start({});
      if (r.ok) {
        if (r.code) setCode(r.code); if (r.expiresAt) setExpiresAt(r.expiresAt);
        setSt({ running: true, port: r.port, addrs: r.addrs, clients: 0, paired: 0 });
        if (token && remote.startAccountHost) { const a = await remote.startAccountHost(token); setAcctOn(!!(a && a.ok)); }
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
  const reissue = async () => { setBusy(true); setErr(""); try { const r = await remote.issueCode(); if (r.ok && r.code) { setCode(r.code); if (r.expiresAt) setExpiresAt(r.expiresAt); } else setErr(r.error || "签发失败"); } catch (e: any) { setErr(e?.message || "签发失败"); } setBusy(false); };
  const toggleAcct = async () => {
    if (!token) { setErr("请先登录账号，才能用同账号跨网络直连"); return; }
    setBusy(true);
    try { if (acctOn) { await remote.stopAccountHost?.(); setAcctOn(false); } else { const a = await remote.startAccountHost?.(token); setAcctOn(!!(a && a.ok)); if (a && !a.ok) setErr(a.error || "开启账号直连失败"); } }
    catch (e: any) { setErr(e?.message || "操作失败"); }
    setBusy(false);
  };
  const connectMine = async () => { if (!token) { setErr("请先登录账号"); return; } try { await remote.openAccountViewer?.({ token }); } catch (e: any) { setErr(e?.message || "打开失败"); } };

  const genOffer = async () => { if (!remote.manualOffer) return; setManMsg("正在生成邀请码（捕获屏幕 + 收集 ICE，约数秒）…"); setOfferCode(""); try { const r = await remote.manualOffer(); if (r.ok && r.code) { setOfferCode(r.code); setManMsg("邀请码已生成 → 发给对方，对方在查看端「手动」粘贴生成应答码回传"); } else setManMsg("生成失败：" + (r.error || "")); } catch (e: any) { setManMsg("生成失败：" + (e?.message || e)); } };
  const applyAnswer = async () => { if (!remote.manualAnswer || !answerIn.trim()) { setManMsg("请先粘贴对方的应答码"); return; } setManMsg("正在应用…"); try { const r = await remote.manualAnswer(answerIn.trim()); setManMsg(r.ok ? "应答已应用，P2P 协商中" : "失败：" + (r.error || "")); } catch (e: any) { setManMsg("失败：" + (e?.message || e)); } };
  const saveIce = async () => { if (!remote.setIce) return; setIceMsg(""); let arr: any[] = []; if (iceText.trim()) { try { arr = JSON.parse(iceText); if (!Array.isArray(arr)) throw new Error(); } catch (_e) { setIceMsg('格式应为 JSON 数组，如 [{"urls":"turn:host:3478","username":"u","credential":"p"}]'); return; } } try { const r = await remote.setIce(arr); setIceMsg(r.ok ? "已保存，新连接生效。" : "保存失败：" + (r.error || "")); } catch (e: any) { setIceMsg("保存失败：" + (e?.message || e)); } };
  const saveViewer = async () => { if (!remote.viewerHtml) return; try { const r = await remote.viewerHtml(); if (r.ok && r.html) { await saveFile("HashMM-远程查看端.html", r.html); setSavedViewer(true); setTimeout(() => setSavedViewer(false), 2500); } } catch (_e) {} };

  // V103.51 远程能力扩展 handlers
  useEffect(() => {
    if (!remote?.listWolTargets) return;
    remote.listWolTargets().then((r) => { if (r.ok) setWolTargets(r.targets || []); }).catch(() => {});
  }, [remote]);
  useEffect(() => {
    if (!remote?.listMonitors) return;
    remote.listMonitors().then((r) => { if (r.ok && r.monitors) setMonitors(r.monitors); }).catch(() => {});
  }, [remote]);
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
    try { const r = await remote.wake(mac); setWolMsg(r.ok ? `已向「${name}」发送开机魔术包（${r.sent} 次）。若该机已配置 WOL 且在同一局域网，将上电开机。` : "发送失败：" + (r.error || "")); }
    catch (e: any) { setWolMsg("发送失败：" + String(e?.message || e)); }
  };
  const delWol = async (id: string) => { if (!remote?.removeWolTarget) return; try { const r = await remote.removeWolTarget(id); if (r.ok) setWolTargets(r.targets || []); } catch (_e) {} };
  const applyQuality = async (nf: number, nj: number) => { setFps(nf); setJpeg(nj); if (remote?.setRemoteQuality) { try { await remote.setRemoteQuality({ fps: nf, jpeg: nj }); } catch (_e) {} } };
  const togglePrivacy = async () => { const next = !privacy; setPrivacy(next); if (remote?.setPrivacy) { try { await remote.setPrivacy(next); } catch (_e) {} } };

  const Card = ({ children }: any) => (<div className="pk-card rounded-2xl p-5 mb-4">{children}</div>);
  const Foldable = ({ open, onClick, icon: Icon, label }: any) => (
    <button onClick={onClick} className="w-full flex items-center gap-1.5 text-[12px] py-1.5 mt-1" style={{ color: "var(--text-secondary)" }}>
      <Icon size={13} /> {label} <ChevronRight size={13} style={{ marginLeft: "auto", transform: open ? "rotate(90deg)" : "none", transition: "transform .15s" }} />
    </button>
  );

  return (
    <PanelShell>
      <PageHeader icon={MonitorSmartphone} title="远程桌面" subtitle="让别人远程这台电脑，或控制你的其他设备 · 配对码 / 账号直连" />
      <div className="remote-cards">
        {/* ① 被控：让别人控制这台电脑 */}
        <Card>
          <div className="flex items-center gap-2 mb-1">
            <MonitorSmartphone size={18} style={{ color: "var(--accent)" }} />
            <div className="text-[15px] font-bold" style={{ color: "var(--text-primary)" }}>让别人远程这台电脑</div>
            <button onClick={running ? stop : start} disabled={busy} className="ml-auto flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-[12.5px] font-medium transition-colors disabled:opacity-50"
              style={running ? { background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)" } : { background: "var(--accent)", color: "#fff" }}>
              <Power size={14} /> {busy ? "…" : running ? "停止" : "开启远程"}
            </button>
          </div>
          <div className="text-[11.5px] mb-3 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
            优先 WebRTC P2P（硬件编码、低延迟、DTLS 加密），不通时自动回退 MJPEG。
          </div>

          {!running ? (
            <div className="text-[11.5px]" style={{ color: "var(--text-tertiary)" }}>未开启。点「开启远程」后，别人可用授权码或同账号直连本机。</div>
          ) : (
            <>
              {/* 账号直连状态 */}
              <div className="flex items-center gap-2 rounded-xl px-3 py-2.5 mb-3" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                <Globe size={15} style={{ color: acctOn ? "var(--success,#16A34A)" : "var(--text-tertiary)" }} />
                <div className="flex-1">
                  <div className="text-[12.5px]" style={{ color: "var(--text-primary)" }}>同账号跨网络直连</div>
                  <div className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{!token ? "登录后可用：别的设备用同账号登录即可直接连本机" : acctOn ? "已开启 · 同账号设备可在任意网络直接连本机" : "未开启"}</div>
                </div>
                <button onClick={toggleAcct} disabled={busy || !token} className="text-[11px] px-2.5 py-1 rounded-lg disabled:opacity-40" style={{ border: "1px solid var(--border)", color: acctOn ? "var(--success,#16A34A)" : "var(--text-secondary)" }}>
                  {acctOn ? "已开启" : "开启"}
                </button>
              </div>

              {/* 授权码（可复制） */}
              <div className="rounded-xl p-4 mb-1" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="flex items-center gap-1 text-[11px]" style={{ color: "var(--text-tertiary)" }}><ShieldCheck size={12} /> 授权码（在查看端输入）</span>
                  <button onClick={reissue} disabled={busy} className="flex items-center gap-1 text-[10.5px] px-1.5 py-0.5 rounded transition-colors hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-secondary)" }}><RefreshCw size={11} /> 重新生成</button>
                </div>
                {codeAlive ? (
                  <div className="flex items-center gap-3">
                    <span className="font-mono font-bold tracking-[0.32em] text-[30px]" style={{ color: "var(--text-primary)" }}>{code}</span>
                    <button onClick={() => copy(code || "", "code")} className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" style={{ border: "1px solid var(--border)", color: copied === "code" ? "var(--success,#16A34A)" : "var(--text-secondary)" }}>
                      {copied === "code" ? <><Check size={12} /> 已复制</> : <><Copy size={12} /> 复制</>}
                    </button>
                    <span className="text-[10.5px] font-mono ml-auto" style={{ color: remainSec < 30 ? "#e0533d" : "var(--text-tertiary)" }}>{Math.floor(remainSec / 60)}:{String(remainSec % 60).padStart(2, "0")} 后失效</span>
                  </div>
                ) : (
                  <div className="flex items-center gap-2"><span className="text-[12px]" style={{ color: "var(--text-tertiary)" }}>授权码已失效</span><button onClick={reissue} className="text-[11.5px] underline" style={{ color: "var(--accent)" }}>重新生成</button></div>
                )}
              </div>

              <div className="flex items-center gap-4 text-[11px] mt-2" style={{ color: "var(--text-secondary)" }}>
                <span>连接 <b className="font-mono" style={{ color: "var(--text-primary)" }}>{st?.clients ?? 0}</b></span>
                <span>已配对 <b className="font-mono" style={{ color: "var(--text-primary)" }}>{st?.paired ?? 0}</b></span>
                <span>P2P <b className="font-mono" style={{ color: "var(--text-primary)" }}>{rtcN}</b></span>
                <span>屏 <b className="font-mono" style={{ color: "var(--text-primary)" }}>{monitors.length || 1}</b></span>
                <span style={{ color: st?.hostConnected ? "var(--success,#16A34A)" : "#D97706" }}>投屏端{st?.hostConnected ? "就绪" : "启动中"}</span>
              </div>

              {/* 局域网网址（折叠） */}
              <Foldable open={showLan} onClick={() => setShowLan(v => !v)} icon={Wifi} label="局域网 / 浏览器连接（同一网络用）" />
              {showLan && (
                <div className="rounded-xl p-3" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                  <div className="text-[11px] mb-2" style={{ color: "var(--text-tertiary)" }}>同一局域网时，另一台设备浏览器打开下面网址即可（免安装）：</div>
                  {addrs.length ? addrs.map((a) => { const url = `http://${a}:${port}/`; return (
                    <div key={a} className="flex items-center justify-between rounded-lg px-3 py-1.5 mb-1.5" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                      <span className="font-mono text-[11.5px] truncate" style={{ color: "var(--text-primary)" }}>{url}</span>
                      <button onClick={() => copy(url, a)} className="text-[10.5px] px-1.5 py-0.5 rounded flex-shrink-0 ml-2" style={{ color: "var(--text-secondary)" }}>{copied === a ? "已复制" : "复制"}</button>
                    </div>); }) : <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>本机自测：http://127.0.0.1:{port}/</div>}
                </div>
              )}

              {/* 跨网络手动（折叠·零服务器兜底） */}
              <Foldable open={showManual} onClick={() => setShowManual(v => !v)} icon={Globe} label="跨网络手动（无账号/无服务器时用）" />
              {showManual && (
                <div className="rounded-xl p-3" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                  <div className="text-[11px] leading-relaxed mb-2" style={{ color: "var(--text-tertiary)" }}>不登录、又跨网络时用：① 把查看端网页发对方；② 互换邀请码/应答码。</div>
                  <button onClick={saveViewer} className="w-full mb-2 px-3 py-1.5 rounded-lg text-[11.5px] flex items-center justify-center gap-1.5" style={{ border: "1px solid var(--border)", color: "var(--text-primary)" }}>{savedViewer ? <><Check size={12} style={{ color: "var(--success,#16A34A)" }} /> 已保存</> : <><Download size={12} /> 1. 保存查看端网页（发对方）</>}</button>
                  <button onClick={genOffer} className="w-full mb-2 px-3 py-1.5 rounded-lg text-[11.5px] font-medium text-white" style={{ background: "var(--accent)" }}>2. 生成邀请码</button>
                  {offerCode && (<div className="mb-2"><textarea readOnly value={offerCode} className="w-full text-[10px] font-mono rounded-lg p-2" style={{ height: 56, background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-secondary)", resize: "vertical" }} /><button onClick={() => copy(offerCode, "offer")} className="mt-1 text-[10.5px] underline" style={{ color: "var(--accent)" }}>{copied === "offer" ? "已复制" : "复制邀请码"}</button></div>)}
                  <textarea value={answerIn} onChange={(e) => setAnswerIn(e.target.value)} placeholder="3. 粘贴对方回传的应答码…" className="w-full text-[10px] font-mono rounded-lg p-2 mb-2" style={{ height: 50, background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)", resize: "vertical" }} />
                  <button onClick={applyAnswer} className="w-full px-3 py-1.5 rounded-lg text-[11.5px] font-medium" style={{ border: "1px solid var(--border)", color: "var(--text-primary)" }}>4. 应用应答码 → 连接</button>
                  {manMsg && <div className="text-[10.5px] mt-2 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>{manMsg}</div>}
                </div>
              )}

              {/* TURN/ICE（折叠） */}
              <Foldable open={showIce} onClick={() => setShowIce(v => !v)} icon={Settings} label="TURN / ICE（对称 NAT 兜底·选填）" />
              {showIce && (
                <div className="rounded-xl p-3" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                  <div className="text-[11px] leading-relaxed mb-2" style={{ color: "var(--text-tertiary)" }}>默认已用免费公共 STUN，多数网络够用。少数对称 NAT 需 TURN 中继（JSON 数组，可填免费/自有）：</div>
                  <textarea value={iceText} onChange={(e) => setIceText(e.target.value)} placeholder='[{"urls":"turn:turn.example:3478","username":"u","credential":"p"}]' className="w-full text-[10px] font-mono rounded-lg p-2 mb-2" style={{ height: 64, background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)", resize: "vertical" }} />
                  <button onClick={saveIce} className="px-3 py-1.5 rounded-lg text-[11.5px] font-medium" style={{ border: "1px solid var(--border)", color: "var(--text-primary)" }}>保存</button>
                  {iceMsg && <div className="text-[10.5px] mt-2" style={{ color: "var(--text-tertiary)" }}>{iceMsg}</div>}
                </div>
              )}
            </>
          )}
          {err && <div className="mt-3 text-[11.5px]" style={{ color: "#e0533d" }}>{err}</div>}
        </Card>

        {/* ② 控制：远程我的其他电脑 */}
        <Card>
          <div className="flex items-center gap-2 mb-1">
            <Monitor size={18} style={{ color: "var(--accent)" }} />
            <div className="text-[15px] font-bold" style={{ color: "var(--text-primary)" }}>控制我的其他电脑</div>
          </div>
          <div className="text-[11.5px] mb-3 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
            另一台电脑用<b>同一账号</b>登录 HashMM、并开启远程后，这里一键即可跨网络连上它。
          </div>
          <button onClick={connectMine} disabled={!token} className="w-full px-3 py-2.5 rounded-xl text-[12.5px] font-medium text-white flex items-center justify-center gap-1.5 disabled:opacity-40" style={{ background: "var(--accent)" }}>
            <Monitor size={14} /> 连接我的设备
          </button>
          {!token && <div className="text-[11px] mt-2 text-center" style={{ color: "var(--text-tertiary)" }}>请先登录账号</div>}
        </Card>

        {/* ③ V103.51 更多远程能力（对标 UU 远程：远程开机 / 画质 / 隐私防护 / 文件传输 / 多屏） */}
        <Card>
          <button onClick={() => setShowMore(v => !v)} className="w-full flex items-center gap-2 mb-1" aria-expanded={showMore}>
            <Power size={18} style={{ color: "var(--accent)" }} />
            <div className="text-[15px] font-bold flex-1 text-left" style={{ color: "var(--text-primary)" }}>更多远程能力</div>
            <ChevronRight size={16} className="transition-transform" style={{ color: "var(--text-tertiary)", transform: showMore ? "rotate(90deg)" : "none" }} />
          </button>
          <div className="text-[11.5px] mb-2 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
            远程开机、真彩画质、隐私防护、文件传输、多屏协作——对标网易 UU 远程的能力集。
          </div>
          {showMore && (
            <div className="flex flex-col gap-4 mt-2">
              {/* 远程开机（WOL） */}
              <div>
                <div className="flex items-center gap-1.5 mb-1.5 text-[12.5px] font-medium" style={{ color: "var(--text-primary)" }}>
                  <Power size={13} /> 远程开机（Wake-on-LAN）
                </div>
                <div className="flex flex-col gap-1.5">
                  {wolTargets.map((t) => (
                    <div key={t.id} className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg" style={{ background: "var(--bg-tertiary)" }}>
                      <span className="flex-1 truncate text-[12px]" style={{ color: "var(--text-secondary)" }}>{t.name} <span className="font-mono text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{t.mac}</span></span>
                      <button onClick={() => wake(t.mac, t.name)} className="px-2 py-1 rounded-md text-[11px] text-white" style={{ background: "var(--accent)" }}>开机</button>
                      <button onClick={() => delWol(t.id)} className="px-1.5 py-1 rounded-md text-[11px]" style={{ color: "var(--text-tertiary)" }}>删除</button>
                    </div>
                  ))}
                  {wolTargets.length === 0 && <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>还没有保存的开机目标。添加目标机器的网卡 MAC 即可一键叫醒。</div>}
                  <div className="flex items-center gap-1.5 mt-1">
                    <input value={wolName} onChange={(e) => setWolName(e.target.value)} placeholder="名称（如 公司主机）" className="w-[34%] px-2 py-1.5 rounded-lg text-[11.5px] outline-none" style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
                    <input value={wolMac} onChange={(e) => setWolMac(e.target.value)} placeholder="AA:BB:CC:DD:EE:FF" className="flex-1 px-2 py-1.5 rounded-lg text-[11.5px] font-mono outline-none" style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
                    <button onClick={addWol} className="px-2.5 py-1.5 rounded-lg text-[11.5px] font-medium" style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>添加</button>
                  </div>
                  {wolMsg && <div className="text-[11px] mt-1 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>{wolMsg}</div>}
                </div>
              </div>

              {/* 画质（真彩 / 流畅） */}
              <div>
                <div className="flex items-center gap-1.5 mb-1.5 text-[12.5px] font-medium" style={{ color: "var(--text-primary)" }}>
                  <Monitor size={13} /> 画质
                </div>
                <div className="flex items-center gap-1.5">
                  {[{ k: "流畅", f: 12, j: 55 }, { k: "均衡", f: 8, j: 70 }, { k: "真彩", f: 6, j: 90 }].map(({ k, f, j }) => (
                    <button key={k} onClick={() => applyQuality(f, j)}
                      className="flex-1 px-2 py-1.5 rounded-lg text-[11.5px] transition-all"
                      style={(fps === f && jpeg === j) ? { background: "var(--accent-light)", color: "var(--accent)", fontWeight: 600 } : { background: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>
                      {k}
                    </button>
                  ))}
                </div>
                <div className="text-[10.5px] mt-1" style={{ color: "var(--text-tertiary)" }}>当前 {fps} fps · JPEG {jpeg}（WebRTC 接通时走硬件编码，此为 MJPEG 兜底画质）</div>
              </div>

              {/* 隐私防护 */}
              <div className="flex items-center gap-2">
                <ShieldCheck size={13} style={{ color: privacy ? "var(--accent)" : "var(--text-tertiary)" }} />
                <div className="flex-1">
                  <div className="text-[12.5px] font-medium" style={{ color: "var(--text-primary)" }}>隐私防护</div>
                  <div className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>被控时本机黑屏遮挡、锁定本地键鼠，守护屏幕隐私</div>
                </div>
                <button onClick={togglePrivacy} role="switch" aria-checked={privacy}
                  className="relative w-10 h-[22px] rounded-full transition-colors flex-shrink-0"
                  style={{ background: privacy ? "var(--accent)" : "var(--border)" }}>
                  <span className="absolute top-[2px] w-[18px] h-[18px] bg-white rounded-full transition-all" style={{ left: privacy ? "20px" : "2px" }} />
                </button>
              </div>

              {/* 多屏 / 本机显示器（来自 listMonitors 真实枚举） */}
              <div>
                <div className="flex items-center gap-1.5 mb-1.5 text-[12.5px] font-medium" style={{ color: "var(--text-primary)" }}>
                  <Monitor size={13} /> 多屏 · 本机显示器
                </div>
                {monitors.length ? (
                  <div className="flex flex-col gap-1.5">
                    {monitors.map((m) => (
                      <div key={m.id} className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg" style={{ background: "var(--bg-tertiary)" }}>
                        <Monitor size={13} style={{ color: m.primary ? "var(--accent)" : "var(--text-tertiary)" }} />
                        <span className="flex-1 truncate text-[12px]" style={{ color: "var(--text-secondary)" }}>{m.label || `显示器 ${m.index + 1}`}</span>
                        <span className="font-mono text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{m.width}×{m.height}</span>
                        {m.primary && <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>主屏</span>}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>检测到单显示器，或需在桌面客户端内运行以枚举多屏。</div>
                )}
                <div className="text-[10.5px] mt-1" style={{ color: "var(--text-tertiary)" }}>被控端连上后，可在远程会话窗口顶部切换要查看的显示器。</div>
              </div>

              <div className="text-[10.5px] leading-relaxed px-0.5" style={{ color: "var(--text-tertiary)" }}>
                文件传输在远程会话窗口内使用：连上后拖拽文件即走 P2P 数据通道直传（大小无限制、不经服务器）。
              </div>
            </div>
          )}
        </Card>

        {/* 说明 */}
        <div className="text-[11px] leading-relaxed px-1" style={{ color: "var(--text-tertiary)" }}>
          跨网络用你已有的公网账号后端做信令碰头点（零额外服务器），视频走 WebRTC P2P 直连、硬件编码、DTLS 加密。
          信令中继、配对、输入注入已在回环端到端自测（共 32 项）；WebRTC 真实媒体协商依赖 Chromium 运行时，需你真机联调，
          不通也有 MJPEG 兜底。账号直连需后端升级到带 /api/remote/ws 的版本。
        </div>
      </div>
    </PanelShell>
  );
}

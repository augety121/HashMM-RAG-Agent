"use client";
/** components/admin/ChannelsTab.tsx — IM 渠道可视化配置（飞书应用机器人 / 微信 iLink）。
 *  用户在客户端里点击开关、填凭证、扫码登录即可启用，无需在 AutoDL 启动命令里写环境变量。
 *  配置经 /api/channels/config 存进后端 DB（settings_store），密钥脱敏回显、占位不覆盖。 */
import { useState, useEffect, useCallback } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Loader2, Save, Check, Copy, QrCode, MessageSquare, RefreshCw } from "lucide-react";

interface MaskedField { configured: boolean; masked: string }
interface FeishuCfg {
  enable: boolean; app_id: string;
  app_secret: MaskedField; encrypt_key: MaskedField; verification_token: MaskedField;
  enabled: boolean; webhook_path: string;
}
interface WechatCfg {
  enable: boolean; channel_version: string;
  enabled: boolean; logged_in: boolean; worker_running: boolean;
}

function Toggle({ on, onChange, disabled }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button onClick={() => !disabled && onChange(!on)} disabled={disabled}
      className="relative inline-flex h-6 w-11 items-center rounded-full transition-colors disabled:opacity-50"
      style={{ background: on ? "var(--accent)" : "var(--border)" }}>
      <span className="inline-block h-5 w-5 transform rounded-full bg-white transition-transform"
        style={{ transform: on ? "translateX(22px)" : "translateX(2px)" }} />
    </button>
  );
}

export function ChannelsTab() {
  const [feishu, setFeishu] = useState<FeishuCfg | null>(null);
  const [wechat, setWechat] = useState<WechatCfg | null>(null);
  const [loading, setLoading] = useState(false);
  const [savingFs, setSavingFs] = useState(false);
  const [savingWx, setSavingWx] = useState(false);
  const [msg, setMsg] = useState("");
  const [copied, setCopied] = useState(false);
  // 飞书可编辑字段（密钥留空=不改）
  const [fsEdit, setFsEdit] = useState<{ app_id?: string; app_secret?: string; encrypt_key?: string; verification_token?: string }>({});
  // 微信扫码
  const [qr, setQr] = useState<{ qrcode: string; img: string; base_url: string } | null>(null);
  const [qrPolling, setQrPolling] = useState(false);

  const headers = useCallback(() => {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    const t = localStorage.getItem("hmm_token");
    if (t) h["Authorization"] = `Bearer ${t}`;
    return h;
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/channels/config", { headers: headers() });
      if (res.ok) {
        const d = await res.json();
        setFeishu(d.feishu); setWechat(d.wechat); setFsEdit({});
      }
    } catch { /* */ } finally { setLoading(false); }
  }, [headers]);

  useEffect(() => { load(); }, [load]);

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 3000); };

  const saveFeishu = async (patch: Partial<{ enable: boolean }>) => {
    setSavingFs(true);
    try {
      const body: any = { feishu: { ...fsEdit, ...patch } };
      const res = await fetch("/api/channels/config", { method: "PUT", headers: headers(), body: JSON.stringify(body) });
      if (res.ok) { flash("飞书配置已保存"); await load(); } else { flash("保存失败"); }
    } catch { flash("保存失败"); } finally { setSavingFs(false); }
  };

  const saveWechat = async (patch: Partial<{ enable: boolean; channel_version: string }>) => {
    setSavingWx(true);
    try {
      const res = await fetch("/api/channels/config", { method: "PUT", headers: headers(), body: JSON.stringify({ wechat: patch }) });
      if (res.ok) { flash("微信配置已保存"); await load(); } else { flash("保存失败"); }
    } catch { flash("保存失败"); } finally { setSavingWx(false); }
  };

  const copyWebhook = () => {
    const path = feishu?.webhook_path || "/api/channels/feishu/webhook";
    const url = `${window.location.origin}${path}`;
    navigator.clipboard?.writeText(url);
    setCopied(true); setTimeout(() => setCopied(false), 1500);
  };

  // 微信扫码登录
  const startLogin = async () => {
    setQr(null);
    try {
      const res = await fetch("/api/channels/wechat/login/start", { method: "POST", headers: headers() });
      if (!res.ok) { flash("获取二维码失败（先开启微信渠道）"); return; }
      const d = await res.json();
      setQr({ qrcode: d.qrcode, img: d.qrcode_img_content, base_url: d.base_url });
      pollLogin(d.qrcode, d.base_url);
    } catch { flash("获取二维码失败"); }
  };

  const pollLogin = async (qrcode: string, base_url: string) => {
    setQrPolling(true);
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      try {
        const res = await fetch("/api/channels/wechat/login/poll", {
          method: "POST", headers: headers(), body: JSON.stringify({ qrcode, base_url }),
        });
        const d = await res.json();
        if (d.status === "confirmed") { setQrPolling(false); setQr(null); flash("微信登录成功，已启动长轮询"); await load(); return; }
      } catch { /* keep polling */ }
    }
    setQrPolling(false); flash("扫码超时，请重试");
  };

  const card: CSSProperties = { border: "1px solid var(--border)", borderRadius: 16, padding: 16 };
  const input = "flex-1 px-3 py-1.5 text-[13px] rounded-lg";
  const inputStyle: CSSProperties = { border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)" };
  const btn = "px-3 py-1.5 text-[13px] rounded-lg flex items-center gap-1 disabled:opacity-50";
  const btnStyle: CSSProperties = { background: "var(--accent)", color: "#fff" };

  return (
    <div className="space-y-4">
      <p className="text-[13px]" style={{ color: "var(--text-tertiary)" }}>
        在这里开启 IM 渠道、填写凭证、扫码登录——无需改 AutoDL 启动命令。配置保存在后端，重启后端后生效。
      </p>
      {msg && <div className="text-[13px] px-3 py-2 rounded-lg" style={{ background: "var(--bg-secondary)" }}>{msg}</div>}

      {loading || !feishu || !wechat ? (
        <div className="flex items-center gap-2 text-[13px]" style={{ color: "var(--text-tertiary)" }}>
          <Loader2 size={16} className="animate-spin" /> 加载中...
        </div>
      ) : (
        <>
          {/* ───── 飞书 ───── */}
          <div style={card}>
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <MessageSquare size={16} style={{ color: "var(--accent)" }} />
                <span className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>飞书（应用机器人）</span>
                {feishu.enabled && <span className="text-[11px] px-2 py-0.5 rounded-full" style={{ background: "#dcfce7", color: "#166534" }}>运行中</span>}
              </div>
              <Toggle on={feishu.enable} disabled={savingFs} onChange={(v) => saveFeishu({ enable: v })} />
            </div>
            <p className="text-[11px] mb-3" style={{ color: "var(--text-tertiary)" }}>
              在飞书开放平台创建企业自建应用、加机器人能力、订阅"接收消息"，把下面的 Webhook 地址填到事件订阅请求地址。
            </p>

            <div className="space-y-2">
              <Field label="App ID">
                <input className={input} style={inputStyle} placeholder={feishu.app_id || "cli_xxx"}
                  value={fsEdit.app_id ?? ""} onChange={(e) => setFsEdit((p) => ({ ...p, app_id: e.target.value }))} />
              </Field>
              <Field label="App Secret">
                <input type="password" className={input} style={inputStyle}
                  placeholder={feishu.app_secret.configured ? `已配置：${feishu.app_secret.masked}` : "未配置"}
                  value={fsEdit.app_secret ?? ""} onChange={(e) => setFsEdit((p) => ({ ...p, app_secret: e.target.value }))} />
              </Field>
              <Field label="Encrypt Key（可选）">
                <input type="password" className={input} style={inputStyle}
                  placeholder={feishu.encrypt_key.configured ? `已配置：${feishu.encrypt_key.masked}` : "未配置则事件明文传输"}
                  value={fsEdit.encrypt_key ?? ""} onChange={(e) => setFsEdit((p) => ({ ...p, encrypt_key: e.target.value }))} />
              </Field>
              <Field label="Verification Token">
                <input type="password" className={input} style={inputStyle}
                  placeholder={feishu.verification_token.configured ? `已配置：${feishu.verification_token.masked}` : "未配置"}
                  value={fsEdit.verification_token ?? ""} onChange={(e) => setFsEdit((p) => ({ ...p, verification_token: e.target.value }))} />
              </Field>
              <Field label="Webhook 地址">
                <div className="flex-1 px-3 py-1.5 text-[12px] rounded-lg truncate" style={{ ...inputStyle, fontFamily: "monospace" }}>
                  {window.location.origin}{feishu.webhook_path}
                </div>
                <button className={btn} style={{ background: "var(--bg-secondary)", color: "var(--text-primary)" }} onClick={copyWebhook}>
                  {copied ? <Check size={14} /> : <Copy size={14} />}{copied ? "已复制" : "复制"}
                </button>
              </Field>
            </div>

            <div className="flex justify-end mt-3">
              <button className={btn} style={btnStyle} disabled={savingFs} onClick={() => saveFeishu({})}>
                {savingFs ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}保存凭证
              </button>
            </div>
          </div>

          {/* ───── 微信 iLink ───── */}
          <div style={card}>
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <MessageSquare size={16} style={{ color: "#07c160" }} />
                <span className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>微信（iLink 机器人）</span>
                {wechat.logged_in && <span className="text-[11px] px-2 py-0.5 rounded-full" style={{ background: "#dcfce7", color: "#166534" }}>已登录</span>}
                {wechat.worker_running && <span className="text-[11px] px-2 py-0.5 rounded-full" style={{ background: "#dbeafe", color: "#1e40af" }}>长轮询中</span>}
              </div>
              <Toggle on={wechat.enable} disabled={savingWx} onChange={(v) => saveWechat({ enable: v })} />
            </div>
            <p className="text-[11px] mb-3" style={{ color: "var(--text-tertiary)" }}>
              基于腾讯官方"微信 ClawBot"协议。开启后点"扫码登录"，用微信扫码绑定即可在微信里问知识库。
              注意：个人号会话约 24h、到期需重新扫码，腾讯不建议用于核心业务（企业级建议用企业微信）。
            </p>

            <div className="flex items-center gap-3">
              <button className={btn} style={btnStyle} disabled={!wechat.enable || qrPolling} onClick={startLogin}>
                {qrPolling ? <Loader2 size={14} className="animate-spin" /> : <QrCode size={14} />}
                {wechat.logged_in ? "重新扫码登录" : "扫码登录"}
              </button>
              <button className={btn} style={{ background: "var(--bg-secondary)", color: "var(--text-primary)" }} onClick={load}>
                <RefreshCw size={14} />刷新状态
              </button>
            </div>

            {qr && (
              <div className="mt-3 p-4 rounded-xl flex flex-col items-center gap-2" style={{ background: "var(--bg-secondary)" }}>
                {qr.img && (qr.img.startsWith("data:") || qr.img.startsWith("http")) ? (
                  <>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={qr.img} alt="微信登录二维码" style={{ width: 200, height: 200, background: "#fff", borderRadius: 8 }} />
                    <p className="text-[12px]" style={{ color: "var(--text-tertiary)" }}>
                      {qrPolling ? "请用微信扫码并确认…" : "二维码已生成"}
                    </p>
                  </>
                ) : (
                  <>
                    <p className="text-[12px]" style={{ color: "var(--text-tertiary)" }}>
                      二维码图片生成失败（后端需安装 opencv：在 AutoDL 运行 pip install opencv-python-headless）。下方为二维码内容串：
                    </p>
                    <code className="text-[11px] break-all" style={{ color: "var(--text-secondary)" }}>{qr.qrcode}</code>
                  </>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <label className="text-[11px] block mb-1" style={{ color: "var(--text-tertiary)" }}>{label}</label>
      <div className="flex gap-2">{children}</div>
    </div>
  );
}

"use client";
/** LoginForm — 登录/注册表单内核（V108）。被两处共用：
 *  LoginPage（web 直链全屏）与 LoginModal（Marvis 式居中遮罩弹窗）。
 *  Supabase 启用后：强制只用 Supabase 账号登录，完全停用 admin / 本地账号入口。 */
import { useState, useEffect } from "react";
import HashMascot from "./HashMascot";
import { login, register } from "@/lib/api";
import { saveAuth } from "@/lib/store";
import { signInWithSupabase, signUpWithSupabase, verifySignupOtp, resendSignupOtp, getSupabaseConfig } from "@/lib/supabase";
import { isDesktop } from "@/lib/desktop";
import { Eye, EyeOff, Loader2, Zap } from "lucide-react";

export function LoginForm({ onSuccess }: { onSuccess?: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [awaitingOtp, setAwaitingOtp] = useState(false);
  const [otp, setOtp] = useState("");
  const isDesktopEnv = isDesktop();
  const [supabaseEnabled, setSupabaseEnabled] = useState(false);
  useEffect(() => { getSupabaseConfig().then((c) => setSupabaseEnabled(!!c.enabled)).catch(() => {}); }, []);

  // Supabase 启用后强制只用 Supabase 登录（admin / 本地账号一律停用）
  const supabaseOnly = supabaseEnabled;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!username.trim() || !password.trim()) {
      setError(supabaseOnly ? "请填写邮箱和密码" : "请填写用户名和密码"); return;
    }
    setError(""); setLoading(true);
    try {
      if (supabaseOnly) {
        // 客户端直连 Supabase 登录/注册：不依赖后端是否配置，本地/离线/AutoDL 都能用。
        if (mode === "register") {
          const up = await signUpWithSupabase(username.trim(), password);
          if (up.session) {
            saveAuth(
              up.session.access_token,
              { username: up.session.email || username, display_name: up.session.email, role: up.session.role } as never,
              up.session.refresh_token,
            );
            onSuccess?.();
          } else {
            // 需邮箱 6 位验证码：进入验证码输入步骤
            setAwaitingOtp(true);
            setError("");
          }
        } else {
          // 角色（admin/user）从返回的 JWT 的 app_metadata.role 解析，管理员可见「管理后台」。
          const r = await signInWithSupabase(username.trim(), password);
          saveAuth(
            r.access_token,
            { username: r.email || username, display_name: r.email, role: r.role } as never,
            r.refresh_token,
          );
          onSuccess?.();
        }
      } else {
        const res = mode === "login"
          ? await login(username, password)
          : await register(username, password, displayName);
        saveAuth(res.token, res.user, res.refresh_token);
        onSuccess?.();
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "操作失败");
    }
    setLoading(false);
  }

  async function resend() {
    setError("");
    try { await resendSignupOtp(username.trim()); setError("验证码已重新发送，请查收邮箱。"); }
    catch (err: unknown) { setError(err instanceof Error ? err.message : "重新发送失败"); }
  }

  async function verifyOtp() {
    if (otp.trim().length < 6) { setError("请输入 6 位验证码"); return; }
    setError(""); setLoading(true);
    try {
      const r = await verifySignupOtp(username.trim(), otp.trim());
      saveAuth(
        r.access_token,
        { username: r.email || username, display_name: r.email, role: r.role } as never,
        r.refresh_token,
      );
      onSuccess?.();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "验证失败");
    }
    setLoading(false);
  }

  const inputCls = "w-full px-3.5 py-2.5 rounded-xl text-[13px] outline-none transition-colors focus:border-[var(--accent)]";
  const inputStyle = { background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" } as const;

  return (
    <div className="w-full max-w-[340px] flex flex-col items-center">
      <div className="mb-4"><HashMascot size={68} /></div>
      <h1 className="text-[22px] font-bold tracking-tight mb-1" style={{ color: "var(--text-primary)" }}>HashMM</h1>
      <p className="text-[12px] mb-7" style={{ color: "var(--text-tertiary)" }}>
        {supabaseOnly ? "用 Supabase 账号登录（与 App 通用）" : "企业级 RAG + 自我进化 Agent · 本地优先"}
      </p>

      {awaitingOtp ? (
        <div className="w-full flex flex-col gap-3">
          <p className="text-[12px] text-center leading-relaxed" style={{ color: "var(--text-secondary)" }}>
            验证码已发到 <b style={{ color: "var(--text-primary)" }}>{username}</b>，请查收邮箱并填写 6 位验证码
          </p>
          <input className={inputCls} style={inputStyle} placeholder="6 位验证码" inputMode="numeric" maxLength={6}
                 value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))} autoFocus />
          {error && <div className="text-[12px] px-1" style={{ color: "#dc2626" }}>{error}</div>}
          <button onClick={verifyOtp} disabled={loading}
                  className="w-full py-2.5 rounded-xl text-[14px] font-semibold text-white flex items-center justify-center gap-2 disabled:opacity-60 transition-opacity hover:opacity-90"
                  style={{ background: "var(--accent)" }}>
            {loading && <Loader2 size={15} className="animate-spin" />}
            验证并登录
          </button>
          <div className="flex items-center justify-center gap-4 text-[12px]">
            <button type="button" onClick={() => { setAwaitingOtp(false); setOtp(""); setError(""); }}
                    className="hover:opacity-75 transition-opacity" style={{ color: "var(--text-secondary)" }}>
              ← 返回修改邮箱
            </button>
            <button type="button" onClick={resend}
                    className="hover:opacity-75 transition-opacity" style={{ color: "var(--accent)" }}>
              没收到？重新发送
            </button>
          </div>
        </div>
      ) : (
      <>
      <form onSubmit={submit} className="w-full flex flex-col gap-3">
        <input className={inputCls} style={inputStyle} placeholder={supabaseOnly ? "邮箱" : "用户名"}
               type={supabaseOnly ? "email" : "text"}
               value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        {!supabaseOnly && mode === "register" && (
          <input className={inputCls} style={inputStyle} placeholder="显示名称（可选）"
                 value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        )}
        <div className="relative">
          <input className={inputCls} style={inputStyle} placeholder="密码"
                 type={showPw ? "text" : "password"}
                 value={password} onChange={(e) => setPassword(e.target.value)} />
          <button type="button" onClick={() => setShowPw(!showPw)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 p-0.5"
                  style={{ color: "var(--text-tertiary)" }}>
            {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
          </button>
        </div>

        {error && <div className="text-[12px] px-1" style={{ color: "#dc2626" }}>{error}</div>}

        <button type="submit" disabled={loading}
                className="w-full py-2.5 rounded-xl text-[14px] font-semibold text-white flex items-center justify-center gap-2 disabled:opacity-60 transition-opacity hover:opacity-90"
                style={{ background: "var(--accent)" }}>
          {loading && <Loader2 size={15} className="animate-spin" />}
          {mode === "login" ? "登录" : "创建账号"}
        </button>
      </form>

      <button onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}
              className="mt-4 text-[12px] hover:opacity-75 transition-opacity"
              style={{ color: "var(--text-secondary)" }}>
        {mode === "login" ? "没有账号？立即注册 →" : "已有账号？返回登录 →"}
      </button>

      {!supabaseOnly && isDesktopEnv && mode === "login" && (
        <div className="mt-6 px-4 py-2.5 rounded-xl text-[11px] text-center w-full"
             style={{ background: "var(--bg-secondary)", color: "var(--text-tertiary)", border: "1px solid var(--border)" }}>
          本地模式首次登录：admin / admin123（进入后请改密码）
        </div>
      )}
      </>
      )}
    </div>
  );
}

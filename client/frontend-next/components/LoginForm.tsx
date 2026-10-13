"use client";
/** LoginForm — 登录/注册表单内核（V108）。被两处共用：
 *  LoginPage（web 直链全屏）与 LoginModal（Marvis 式居中遮罩弹窗）。
 *  Supabase 启用后：强制只用 Supabase 账号登录，完全停用 admin / 本地账号入口。 */
import { useState, useEffect } from "react";
import HashMascot from "./HashMascot";
import { diagnoseBackendSession, login, register } from "@/lib/api";
import { useStore } from "@/lib/store";
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
  const backendOnline = useStore(s => s.backendOnline);
  useEffect(() => { getSupabaseConfig().then((c) => setSupabaseEnabled(!!c.enabled)).catch(() => {}); }, []);

  // Supabase 启用后强制只用 Supabase 登录（admin / 本地账号一律停用）
  const supabaseOnly = supabaseEnabled;

  async function commitSupabaseSession(
    session: { access_token: string; refresh_token: string; email: string; role: string },
    fallbackUsername: string,
  ) {
    const diagnostic = await diagnoseBackendSession(session.access_token);
    if (diagnostic.status === "rejected") {
      const trace = diagnostic.requestId ? `（请求 ${diagnostic.requestId}）` : "";
      if (diagnostic.code === "project-mismatch") {
        throw new Error(`账号所属身份项目与服务器不一致，请管理员统一 Supabase 项目后重试${trace}`);
      }
      if (diagnostic.code === "identity-contract-invalid") {
        throw new Error(`服务器统一身份配置不完整，请管理员检查生产环境配置${trace}`);
      }
      throw new Error(`服务器拒绝了当前会话，请重新登录；若仍失败请把请求编号交给管理员${trace}`);
    }
    saveAuth(
      session.access_token,
      {
        username: session.email || fallbackUsername,
        display_name: session.email,
        role: session.role,
      } as never,
      session.refresh_token,
    );
  }

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
            await commitSupabaseSession(up.session, username);
            onSuccess?.();
          } else {
            // 需邮箱 6 位验证码：进入验证码输入步骤
            setAwaitingOtp(true);
            setError("");
          }
        } else {
          // 角色（admin/user）从返回的 JWT 的 app_metadata.role 解析，管理员可见「管理后台」。
          const r = await signInWithSupabase(username.trim(), password);
          await commitSupabaseSession(r, username);
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
      await commitSupabaseSession(r, username);
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
      <div className="mb-4"><HashMascot size={76} /></div>
      <h1 className="text-[22px] font-bold tracking-tight mb-1" style={{ color: "var(--text-primary)" }}>HashMM</h1>
      <p className="text-[12px] mb-7" style={{ color: "var(--text-tertiary)" }}>
        {supabaseOnly ? "登录账号，随时随地同步你的对话与知识库" : "企业级 RAG + 自我进化 Agent · 本地优先"}
      </p>
      {backendOnline === false && (
        <p className="text-[11.5px] -mt-5 mb-6 px-3 py-1.5 rounded-lg" style={{ background: "#FEF3C7", color: "#92400E" }}>
          后端未连接：<b>Supabase 云端账号可正常登录</b>（历史可离线查看）；本地账号需先启动后端。
        </p>
      )}

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

"use client";
/** components/ProfileModal.tsx — 个人资料（V203 重构）。
 *
 * 修复的根因：旧版改名走 PUT /api/admin/users/{user.id}，而 Supabase 登录的用户
 * ① 没有后端本地 id（user.id 为空 → 请求打到 /users/undefined）
 * ② 也过不了 require_admin —— 所以"名字改了没反应"。
 * 现在按账号体系分流：
 *   · Supabase 账号（JWT sub 是 uuid）→ 直连 profiles 表 PATCH display_name / avatar_url，
 *     与 App 端同一份档案，改完两端都生效；
 *   · 后端本地账号 → 沿用原管理接口（管理员可改自己名字）。
 * 头像支持**上传图片**：本地压缩为 ≤256px 的 JPEG data URL 存 profiles.avatar_url，
 * 同步 localStorage hmm_avatar_{uid} 并广播 hmm-avatar-updated（UserMenu 即时刷新）。
 */
import { useEffect, useRef, useState } from "react";
import { useStore } from "@/lib/store";
import { X, Camera, Check, Loader2, Trash2, ImagePlus } from "lucide-react";
import { userIdFromToken, updateMyAvatar, updateMyProfile, getMyProfile } from "@/lib/supabase";
import { updateMyAccountProfile } from "@/lib/api";

const AVATAR_COLORS = [
  "#2563eb", "#7c3aed", "#059669", "#d97706", "#dc2626",
  "#0891b2", "#db2777", "#4f46e5", "#0d9488", "#ea580c",
];

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** 把图片文件压成 ≤maxEdge 的 JPEG data URL（头像不需要原图，控制在几十 KB 内）。 */
async function fileToAvatarDataUrl(file: File, maxEdge = 256): Promise<string> {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((res, rej) => {
      const el = new Image();
      el.onload = () => res(el);
      el.onerror = () => rej(new Error("图片读取失败"));
      el.src = url;
    });
    const scale = Math.min(1, maxEdge / Math.max(img.width, img.height));
    const w = Math.max(1, Math.round(img.width * scale));
    const h = Math.max(1, Math.round(img.height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas 不可用");
    ctx.drawImage(img, 0, 0, w, h);
    return canvas.toDataURL("image/jpeg", 0.85);
  } finally { URL.revokeObjectURL(url); }
}

export function ProfileModal() {
  const { user } = useStore();
  const set = useStore((s) => s.set);
  const [displayName, setDisplayName] = useState(user?.display_name || "");
  const [avatarColor, setAvatarColor] = useState(() => {
    if (typeof window !== "undefined") return localStorage.getItem("hmm_avatar_color") || "#2563eb";
    return "#2563eb";
  });
  const token = typeof window !== "undefined" ? localStorage.getItem("hmm_token") : null;
  const supaUid = userIdFromToken(token);
  const isSupabaseAccount = UUID_RE.test(supaUid);
  const [avatarImg, setAvatarImg] = useState<string | null>(() => {
    if (typeof window !== "undefined" && supaUid) return localStorage.getItem("hmm_avatar_" + supaUid);
    return null;
  });
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [err, setErr] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  // 打开时从云端同步一次权威档案（display_name 可能在 App 上改过）
  useEffect(() => {
    if (!isSupabaseAccount || !token) return;
    getMyProfile(token).then(p => {
      if (p?.display_name && !user?.display_name) setDisplayName(p.display_name);
      if (p?.avatar_url) {
        setAvatarImg(p.avatar_url);
        localStorage.setItem("hmm_avatar_" + supaUid, p.avatar_url);
      }
    }).catch(() => { /* 离线时用本地缓存 */ });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const initial = (displayName || user?.username || "U").slice(0, 1).toUpperCase();

  /** 头像图片：压缩 → 云端 profiles.avatar_url → 本地缓存 + 广播刷新 */
  async function onPickAvatar(f: File | undefined | null) {
    if (!f || !token) return;
    setErr(""); setAvatarBusy(true);
    try {
      const dataUrl = await fileToAvatarDataUrl(f);
      if (isSupabaseAccount) {
        const ok = await updateMyAvatar(token, dataUrl);
        if (!ok) throw new Error("云端保存失败");
      }
      setAvatarImg(dataUrl);
      if (supaUid) localStorage.setItem("hmm_avatar_" + supaUid, dataUrl);
      window.dispatchEvent(new Event("hmm-avatar-updated"));
    } catch (e) { setErr(e instanceof Error ? e.message : "头像上传失败"); }
    finally { setAvatarBusy(false); }
  }

  async function onRemoveAvatar() {
    if (!token) return;
    setErr(""); setAvatarBusy(true);
    try {
      if (isSupabaseAccount) await updateMyAvatar(token, "");
      setAvatarImg(null);
      if (supaUid) localStorage.removeItem("hmm_avatar_" + supaUid);
      window.dispatchEvent(new Event("hmm-avatar-updated"));
    } finally { setAvatarBusy(false); }
  }

  async function handleSave() {
    setSaving(true); setErr("");
    try {
      const name = displayName.trim();
      let ok = true;
      if (isSupabaseAccount && token) {
        // Supabase 账号：写 profiles.display_name（与 App 同一份档案）
        ok = await updateMyProfile(token, { display_name: name });
        if (!ok) throw new Error("云端保存失败，请检查网络后重试");
      } else if (user?.id) {
        await updateMyAccountProfile(name);
      }
      const newUser = { ...user!, display_name: name };
      useStore.setState({ user: newUser });
      localStorage.setItem("hmm_user", JSON.stringify(newUser));
      localStorage.setItem("hmm_avatar_color", avatarColor);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "保存失败，请重试");
    }
    setSaving(false);
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) set({ profileOpen: false }); }}
    >
      <div
        className="w-[440px] rounded-2xl overflow-hidden anim-fade-up"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4" style={{ borderBottom: "1px solid var(--border)" }}>
          <h3 className="text-[15px] font-semibold" style={{ color: "var(--text-primary)" }}>个人资料</h3>
          <button onClick={() => set({ profileOpen: false })} className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]" aria-label="关闭">
            <X size={18} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>

        {/* Avatar section */}
        <div className="px-6 pt-6 pb-4 flex flex-col items-center">
          <button
            onClick={() => fileRef.current?.click()}
            disabled={avatarBusy}
            className="w-20 h-20 rounded-full flex items-center justify-center text-white text-2xl font-bold mb-3 relative group overflow-hidden"
            style={{ background: avatarImg ? "var(--bg-tertiary)" : avatarColor }}
            title="点击上传头像照片"
            aria-label="上传头像照片"
          >
            {avatarImg
              ? <img src={avatarImg} alt="头像" className="w-full h-full object-cover" />
              : initial}
            <div className="absolute inset-0 rounded-full bg-black/35 opacity-0 group-hover:opacity-100 flex items-center justify-center transition-opacity">
              {avatarBusy ? <Loader2 size={20} className="text-white animate-spin" /> : <Camera size={20} className="text-white" />}
            </div>
          </button>
          <input ref={fileRef} type="file" accept="image/*" className="hidden"
            onChange={(e) => { onPickAvatar(e.target.files?.[0]); e.target.value = ""; }} />

          <div className="flex items-center gap-3 mb-3">
            <button onClick={() => fileRef.current?.click()} disabled={avatarBusy}
              className="inline-flex items-center gap-1 text-[11.5px] px-2.5 py-1 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]"
              style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
              <ImagePlus size={12} /> 上传照片
            </button>
            {avatarImg && (
              <button onClick={onRemoveAvatar} disabled={avatarBusy}
                className="inline-flex items-center gap-1 text-[11.5px] px-2.5 py-1 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]"
                style={{ color: "var(--text-tertiary)", border: "1px solid var(--border)" }}>
                <Trash2 size={12} /> 移除
              </button>
            )}
          </div>

          {/* 无照片时的颜色兜底 */}
          {!avatarImg && (<>
            <div className="flex gap-2 mb-2">
              {AVATAR_COLORS.map((c) => (
                <button
                  key={c}
                  onClick={() => setAvatarColor(c)}
                  aria-label={`头像颜色 ${c}`}
                  className="w-5 h-5 rounded-full transition-transform hover:scale-125"
                  style={{
                    background: c,
                    outline: avatarColor === c ? `2px solid ${c}` : "none",
                    outlineOffset: 2,
                  }}
                />
              ))}
            </div>
            <p className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>或选择一个头像底色</p>
          </>)}
          {isSupabaseAccount && (
            <p className="text-[10px] mt-1" style={{ color: "var(--text-tertiary)" }}>头像与名字云端同步 —— App 与桌面端同时生效</p>
          )}
        </div>

        {/* Form */}
        <div className="px-6 pb-2 space-y-4">
          <div>
            <label className="block text-[12px] font-medium mb-1.5" style={{ color: "var(--text-tertiary)" }}>
              显示名称
            </label>
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="输入你的显示名称"
              maxLength={32}
              className="w-full h-10 px-3 rounded-xl text-[14px] outline-none transition-colors focus:border-[var(--accent)]"
              style={{
                background: "var(--bg-secondary)",
                border: "1px solid var(--border)",
                color: "var(--text-primary)",
              }}
            />
          </div>

          <div>
            <label className="block text-[12px] font-medium mb-1.5" style={{ color: "var(--text-tertiary)" }}>
              用户名
            </label>
            <div
              className="w-full h-10 px-3 rounded-xl text-[14px] flex items-center font-mono"
              style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}
            >
              @{user?.username || "—"}
            </div>
          </div>

          <div>
            <label className="block text-[12px] font-medium mb-1.5" style={{ color: "var(--text-tertiary)" }}>
              角色
            </label>
            <div
              className="w-full h-10 px-3 rounded-xl text-[14px] flex items-center"
              style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}
            >
              {user?.role === "admin" ? "管理员" : user?.role === "viewer" ? "只读" : "用户"}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 flex items-center gap-2" style={{ borderTop: "1px solid var(--border)" }}>
          {err && <span className="text-[11px] flex-1" style={{ color: "var(--error)" }}>{err}</span>}
          {!err && <span className="flex-1" />}
          <button
            onClick={() => set({ profileOpen: false })}
            className="px-5 py-2 rounded-xl text-[13px] font-medium"
            style={{ color: "var(--text-secondary)", background: "var(--bg-tertiary)" }}
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-5 py-2 rounded-xl text-[13px] font-medium text-white flex items-center gap-1.5 disabled:opacity-60"
            style={{ background: saved ? "var(--success)" : "var(--accent)" }}
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : saved ? <Check size={14} /> : null}
            {saving ? "保存中…" : saved ? "已保存" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}

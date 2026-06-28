"use client";
import { useState } from "react";
import { useStore } from "@/lib/store";
import { X, Camera, Check, Loader2 } from "lucide-react";

const AVATAR_COLORS = [
  "#2563eb", "#7c3aed", "#059669", "#d97706", "#dc2626",
  "#0891b2", "#db2777", "#4f46e5", "#0d9488", "#ea580c",
];

export function ProfileModal() {
  const { user } = useStore();
  const set = useStore((s) => s.set);
  const [displayName, setDisplayName] = useState(user?.display_name || "");
  const [avatarColor, setAvatarColor] = useState(() => {
    if (typeof window !== "undefined") return localStorage.getItem("hmm_avatar_color") || "#2563eb";
    return "#2563eb";
  });
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const initial = (displayName || user?.username || "U").slice(0, 1).toUpperCase();

  async function handleSave() {
    setSaving(true);
    try {
      const token = localStorage.getItem("hmm_token");
      await fetch(`/api/admin/users/${user?.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ display_name: displayName }),
      });
      const newUser = { ...user!, display_name: displayName };
      useStore.setState({ user: newUser });
      localStorage.setItem("hmm_user", JSON.stringify(newUser));
      localStorage.setItem("hmm_avatar_color", avatarColor);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (_e) {
      alert("保存失败，请重试");
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
          <button onClick={() => set({ profileOpen: false })} className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]">
            <X size={18} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>

        {/* Avatar section */}
        <div className="px-6 pt-6 pb-4 flex flex-col items-center">
          <div
            className="w-20 h-20 rounded-full flex items-center justify-center text-white text-2xl font-bold mb-4 relative group cursor-pointer"
            style={{ background: avatarColor }}
          >
            {initial}
            <div className="absolute inset-0 rounded-full bg-black/30 opacity-0 group-hover:opacity-100 flex items-center justify-center transition-opacity">
              <Camera size={20} className="text-white" />
            </div>
          </div>
          {/* Color picker */}
          <div className="flex gap-2 mb-2">
            {AVATAR_COLORS.map((c) => (
              <button
                key={c}
                onClick={() => setAvatarColor(c)}
                className="w-5 h-5 rounded-full transition-transform hover:scale-125"
                style={{
                  background: c,
                  outline: avatarColor === c ? `2px solid ${c}` : "none",
                  outlineOffset: 2,
                }}
              />
            ))}
          </div>
          <p className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>选择头像颜色</p>
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
              className="w-full h-10 px-3 rounded-xl text-[14px] outline-none transition-colors"
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
        <div className="px-6 py-4 flex justify-end gap-2" style={{ borderTop: "1px solid var(--border)" }}>
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
            style={{ background: saved ? "#22c55e" : "var(--accent)" }}
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : saved ? <Check size={14} /> : null}
            {saving ? "保存中..." : saved ? "已保存" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}

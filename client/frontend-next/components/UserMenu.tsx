"use client";
import { useState, useRef, useEffect } from "react";
import { useStore } from "@/lib/store";
import {
  Settings, Shield, LogOut, ChevronRight, HelpCircle, User,
  Keyboard, FileText, Bug, Info, ExternalLink, Sparkles, Download,
} from "lucide-react";
import { isDesktop } from "@/lib/desktop";
import { getMyProfile, userIdFromToken } from "@/lib/supabase";

export function UserMenu() {
  const { user } = useStore();
  const set = useStore((s) => s.set);
  const [open, setOpen] = useState(false);
  const [helpExpanded, setHelpExpanded] = useState(false);
  const [, setAvatarBump] = useState(0);
  useEffect(() => {
    const h = () => setAvatarBump((v) => v + 1);
    window.addEventListener("hmm-avatar-updated", h);
    return () => window.removeEventListener("hmm-avatar-updated", h);
  }, []);
  const [downloadOpen, setDownloadOpen] = useState(false);
  const helpCloseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isDesktopEnv = isDesktop();   // V91: 桌面端隐藏 SaaS 痕迹（升级套餐），加「关于」
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false);
        setHelpExpanded(false);
      }
    }
    if (open) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [open]);

  // 后台从 Supabase 同步权威头像 —— 必须放在所有 return 之前，避免「条件调用 Hook」(React #310)。
  useEffect(() => {
    if (typeof window === "undefined") return;
    const uid = userIdFromToken(localStorage.getItem("hmm_token"));
    if (!uid) return;
    (async () => {
      try {
        const tok = localStorage.getItem("hmm_token"); if (!tok) return;
        const p = await getMyProfile(tok);
        if (p?.avatar_url && p.avatar_url !== localStorage.getItem("hmm_avatar_" + uid)) {
          localStorage.setItem("hmm_avatar_" + uid, p.avatar_url);
          window.dispatchEvent(new Event("hmm-avatar-updated"));
        }
      } catch (_e) { /* 离线/未配置：用本地缓存 */ }
    })();
  }, [user]);

  function act(action: string) {
    setOpen(false);
    setHelpExpanded(false);
    switch (action) {
      case "personal-home": set({ setOpen: true, adminOpen: false, desktopView: null, settingsTab: "account" }); break;
      case "upgrade": set({ upgradeOpen: true }); break;
      case "personalize": set({ setOpen: true, adminOpen: false, desktopView: null, settingsTab: "personalize" }); break;
      case "profile": set({ profileOpen: true }); break;
      case "settings": set({ setOpen: true, adminOpen: false, desktopView: null, settingsTab: "general" }); break;
      case "admin": set({ adminOpen: true, setOpen: false, desktopView: null }); break;
      case "help-center": set({ helpOpen: true }); break;
      case "release-notes": set({ releaseNotesOpen: true }); break;
      case "download-app":
        setDownloadOpen(true);
        break;
      case "shortcuts": set({ setOpen: true, settingsTab: "shortcuts" }); break;
      case "terms": window.open("/terms", "_blank"); break;
      case "privacy": window.open("/privacy", "_blank"); break;
      case "bug-report": set({ bugReportOpen: true }); break;
      case "logout": import("@/lib/api").then(m => m.logout()); break;
    }
  }

  // V93: 游客态 —— 底部用户区显示登录入口（点击弹 Marvis 式登录弹窗）
  if (!user) {
    return (
      <button
        onClick={() => set({ loginOpen: true })}
        className="w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl transition-colors hover:bg-[var(--bg-tertiary)]"
        style={{ border: "1px solid var(--border)", background: "var(--bg-secondary)" }}
      >
        <div className="w-8 h-8 rounded-full flex items-center justify-center text-white flex-shrink-0"
             style={{ background: "var(--accent)" }}>
          <User size={15} />
        </div>
        <div className="flex-1 text-left">
          <div className="text-[12.5px] font-medium" style={{ color: "var(--text-primary)" }}>登录 / 注册</div>
          <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>登录后可对话与管理知识库</div>
        </div>
      </button>
    );
  }

  const initial = (user?.display_name || user?.username || "U").slice(0, 1).toUpperCase();
  const displayName = user?.display_name || user?.username || "用户";
  const roleName = user?.role === "admin" ? "管理员" : (isDesktopEnv ? "本地版" : "免费版");
  const avatarColor = typeof window !== "undefined" ? (localStorage.getItem("hmm_avatar_color") || "var(--accent)") : "var(--accent)";
  // 头像 key 用 JWT 里的真实 uuid（Supabase 登录时 user.id 为空）；并在后台从 Supabase 同步权威头像
  const uid = (typeof window !== "undefined") ? userIdFromToken(localStorage.getItem("hmm_token")) : "";
  const avatarUrl = (typeof window !== "undefined" && uid) ? localStorage.getItem("hmm_avatar_" + uid) : null;

  return (
    <div className="relative" ref={menuRef}>
      {downloadOpen && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center" style={{ background: "rgba(0,0,0,.35)" }} onClick={() => setDownloadOpen(false)}>
          <div className="rounded-2xl p-6 w-[420px] anim-fade-up"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}
            onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>下载 / 部署</h2>
              <button onClick={() => setDownloadOpen(false)} className="p-1 rounded-lg hover:opacity-70 text-base leading-none" style={{ color: "var(--text-tertiary)" }}>×</button>
            </div>
            <p className="text-[12.5px] leading-relaxed mb-3" style={{ color: "var(--text-secondary)" }}>
              本项目为自部署版本。桌面客户端、Android App、网页端共用同一套后端与账号，按需选择：
            </p>
            <div className="rounded-xl px-4 py-3 mb-3 text-[12px] font-mono whitespace-pre-line"
              style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
{`# 后端（私有云 / 本机）
git clone <你的仓库>
pip install -r requirements.txt
cd frontend-next && npm i && npm run build
python -m hashmm.api.server`}
            </div>
            <div className="text-[11px] mb-3" style={{ color: "var(--text-tertiary)" }}>
              桌面安装包：用 <span className="font-mono">installer-native\build-all.bat</span> 产出单个 <span className="font-mono">HashMM-Setup.exe</span>。
            </div>
            <button onClick={() => setDownloadOpen(false)}
              className="w-full py-2 rounded-lg text-[12px] font-medium text-white"
              style={{ background: "var(--accent)" }}>知道了</button>
          </div>
        </div>
      )}
      {open && (
        <div
          className="absolute bottom-full left-0 right-0 mb-1 z-50 rounded-xl py-1 anim-fade-up"
          style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)", minWidth: 220 }}
        >
          {/* 同一入口按服务端角色投影：普通用户=个人设置；管理员=完整管理后台。
              高级能力只是从普通用户导航隐藏，仍完整保留在管理员控制面。 */}
          <button onClick={() => act("personal-home")} className="user-menu-item">
            <User size={15} /> <span>账户与偏好</span>
          </button>
          {user?.role === "admin" && (
            <button onClick={() => act("admin")} className="user-menu-item">
              <Shield size={15} /> <span>管理后台</span>
            </button>
          )}

          {/* Web 保留升级入口；桌面版本信息统一归入“设置 → 关于”。 */}
          {!isDesktopEnv && (
            <button onClick={() => act("upgrade")} className="user-menu-item user-menu-accent">
              <Sparkles size={15} /> <span>升级套餐</span>
            </button>
          )}

          <div style={{ borderTop: "1px solid var(--border)", margin: "4px 0" }} />

          {/* Personalize */}
          <button onClick={() => act("personalize")} className="user-menu-item">
            <User size={15} /> <span>个性化</span>
          </button>

          {/* Profile */}
          <button onClick={() => act("profile")} className="user-menu-item">
            <User size={15} /> <span>个人资料</span>
          </button>

          {/* Settings */}
          <button onClick={() => act("settings")} className="user-menu-item">
            <Settings size={15} /> <span>设置</span>
          </button>

          {/* Help — V92: 右侧飞出子菜单（大厂式二级菜单，不再把主菜单往下撑长） */}
          <div
            className="relative"
            onMouseEnter={() => { if (helpCloseTimer.current) clearTimeout(helpCloseTimer.current); setHelpExpanded(true); }}
            onMouseLeave={() => { helpCloseTimer.current = setTimeout(() => setHelpExpanded(false), 150); }}
          >
          <button
            onClick={() => setHelpExpanded(!helpExpanded)}
            className="user-menu-item w-full"
          >
            <HelpCircle size={15} />
            <span className="flex-1 text-left">帮助</span>
            <ChevronRight size={12} style={{ color: "var(--text-tertiary)" }} />
          </button>

          {helpExpanded && (
            <div className="absolute" style={{ left: "100%", bottom: -8, paddingLeft: 8, zIndex: 60 }}>
            {/* V93: 外层 padding 充当 hover 桥（间隙也算锚区，鼠标穿越不再断链） */}
            <div
              className="anim-fade-up rounded-xl py-1"
              style={{ minWidth: 180,
                       background: "var(--bg-primary)", border: "1px solid var(--border)",
                       boxShadow: "var(--shadow-lg)" }}
            >
              <button onClick={() => act("help-center")} className="user-menu-item text-[12px]">
                <Info size={14} /> <span>帮助中心</span>
              </button>
              <button onClick={() => act("release-notes")} className="user-menu-item text-[12px]">
                <FileText size={14} /> <span>发行说明</span>
              </button>
              <button onClick={() => act("download-app")} className="user-menu-item text-[12px]">
                <Download size={14} /> <span>下载应用</span>
              </button>
              <button onClick={() => act("shortcuts")} className="user-menu-item text-[12px]">
                <Keyboard size={14} /> <span>键盘快捷方式</span>
              </button>
              <div style={{ borderTop: "1px solid var(--border)", margin: "4px 8px" }} />
              <button onClick={() => act("terms")} className="user-menu-item text-[12px]">
                <ExternalLink size={14} /> <span>服务条款</span>
              </button>
              <button onClick={() => act("privacy")} className="user-menu-item text-[12px]">
                <ExternalLink size={14} /> <span>隐私政策</span>
              </button>
              <button onClick={() => act("bug-report")} className="user-menu-item text-[12px]">
                <Bug size={14} /> <span>报告错误</span>
              </button>
            </div>
            </div>
          )}
          </div>

          <div style={{ borderTop: "1px solid var(--border)", margin: "4px 0" }} />

          {/* Logout */}
          <button onClick={() => act("logout")} className="user-menu-item user-menu-danger">
            <LogOut size={15} /> <span>退出登录</span>
          </button>
        </div>
      )}

      {/* Trigger */}
      <button
        onClick={() => { setOpen(!open); if (open) setHelpExpanded(false); }}
        className="w-full flex items-center gap-2.5 px-2 py-2 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]"
      >
        {avatarUrl ? (
          <img src={avatarUrl} alt="" className="w-8 h-8 rounded-full object-cover flex-shrink-0" />
        ) : (
          <div className="w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-semibold flex-shrink-0"
            style={{ background: avatarColor }}>
            {initial}
          </div>
        )}
        <div className="flex-1 min-w-0 text-left">
          <div className="text-[12.5px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{displayName}</div>
          <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{roleName}</div>
        </div>
        {!isDesktopEnv && (
          <button
            onClick={(e) => { e.stopPropagation(); set({ upgradeOpen: true }); }}
            className="px-2.5 py-1 rounded-lg text-[10px] font-semibold flex-shrink-0 transition-colors hover:opacity-80"
            style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
          >
            升级
          </button>
        )}
      </button>
    </div>
  );
}

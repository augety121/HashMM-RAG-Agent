"use client";
import { useState, useEffect, useCallback } from "react";
import { useStore } from "@/lib/store";
import { useT } from "@/lib/useT";
import { listWorkspaceFiles } from "@/lib/api";
import { getFiles, getBackendData } from "@/lib/desktop";
import { getMyProfile, updateMyAvatar, userIdFromToken } from "@/lib/supabase";
import * as api from "@/lib/api";
import { COLORS } from "@/lib/types";
import {
  X, Moon, Sun, Monitor, Palette, Keyboard, Database, Bell,
  MessageSquare, FolderOpen, Download, Trash2, Info, Shield, Globe,
  User, ChevronRight, HardDrive, Check, Loader2, Eye, EyeOff,
  Target, BarChart3, Lightbulb,
} from "lucide-react";

type TabId = "general" | "notification" | "personalize" | "data" | "storage" | "security" | "account" | "shortcuts" | "about";

const TABS: { id: TabId; label: string; icon: React.ElementType }[] = [
  { id: "general", label: "常规", icon: Palette },
  { id: "notification", label: "通知", icon: Bell },
  { id: "personalize", label: "个性化", icon: MessageSquare },
  { id: "data", label: "数据管理", icon: Database },
  { id: "storage", label: "存储空间", icon: HardDrive },
  { id: "security", label: "安全", icon: Shield },
  { id: "account", label: "帐户", icon: User },
  { id: "shortcuts", label: "快捷键", icon: Keyboard },
  { id: "about", label: "关于", icon: Info },
];

// 与侧栏一致的信息架构：把设置项按主题分组，配同样的分区标题 + 激活竖条。
const TAB_GROUPS: { title: string; ids: TabId[] }[] = [
  { title: "通用", ids: ["general", "personalize", "notification"] },
  { title: "数据", ids: ["data", "storage"] },
  { title: "账户与安全", ids: ["security", "account"] },
  { title: "其它", ids: ["shortcuts", "about"] },
];

function SettingRow({ label, desc, children, border = true }: { label: string; desc?: string; children: React.ReactNode; border?: boolean }) {
  const t = useT();
  return (
    <div className="flex items-center justify-between py-3.5 gap-4" style={border ? { borderBottom: "1px solid var(--border)" } : {}}>
      <div className="min-w-0">
        <div className="text-[13px]" style={{ color: "var(--text-primary)" }}>{t(label)}</div>
        {desc && <div className="text-[11px] mt-0.5 leading-relaxed max-w-[320px]" style={{ color: "var(--text-tertiary)" }}>{t(desc)}</div>}
      </div>
      <div className="flex-shrink-0">{children}</div>
    </div>
  );
}

function Select({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: { value: string; label: string }[] }) {
  const t = useT();
  return (
    <select value={value} onChange={e => onChange(e.target.value)}
      className="h-8 px-3 pr-7 rounded-lg text-[12px] outline-none appearance-none cursor-pointer"
      style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
      {options.map(o => <option key={o.value} value={o.value}>{t(o.label)}</option>)}
    </select>
  );
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button onClick={() => onChange(!checked)}
      className="w-10 h-[22px] rounded-full transition-colors relative"
      style={{ background: checked ? "var(--accent)" : "var(--bg-tertiary)", border: "1px solid var(--border)" }}>
      <div className="absolute top-[2px] w-4 h-4 rounded-full bg-white shadow transition-transform"
        style={{ left: checked ? 20 : 2 }} />
    </button>
  );
}

function SmallBtn({ children, onClick, danger }: { children: React.ReactNode; onClick: () => void; danger?: boolean }) {
  return (
    <button onClick={onClick}
      className="px-4 py-1.5 rounded-lg text-[12px] font-medium transition-colors hover:opacity-80"
      style={{ border: `1px solid ${danger ? "#fca5a5" : "var(--border)"}`, color: danger ? "#ef4444" : "var(--text-secondary)" }}>
      {children}
    </button>
  );
}

/* ━━━━━━━━━━━━━━━ Tabs ━━━━━━━━━━━━━━━ */

function GeneralTab() {
  const { dark, accent, fontSize } = useStore();
  const set = useStore(s => s.set);
  const locale = useStore(s => s.locale);
  const setLocale = useStore(s => s.setLocale);
  const [themeMode, setThemeMode] = useState<string>(
    typeof window !== "undefined" ? (localStorage.getItem("hmm_theme_mode") || (dark ? "dark" : "light")) : "light"
  );

  function applyTheme(mode: string) {
    setThemeMode(mode);
    let isDark = false;
    if (mode === "system") isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    else isDark = mode === "dark";
    set({ dark: isDark });
    localStorage.setItem("hmm_dark", isDark ? "1" : "");
    localStorage.setItem("hmm_theme_mode", mode);
  }

  function changeFontSize(v: string) {
    const size = parseInt(v, 10);
    set({ fontSize: size });
    localStorage.setItem("hmm_fontsize", v);
    document.documentElement.style.setProperty("--msg-font-size", `${size}px`);
  }

  return (
    <div>
      <SettingRow label="外观">
        <Select value={themeMode} onChange={applyTheme} options={[
          { value: "light", label: "浅色" }, { value: "dark", label: "深色" }, { value: "system", label: "系统" },
        ]} />
      </SettingRow>
      <SettingRow label="重点色">
        <div className="flex gap-2 items-center">
          {COLORS.map(c => (
            <button key={c.v} onClick={() => { set({ accent: c.v }); localStorage.setItem("hmm_accent", c.v); }}
              className="w-6 h-6 rounded-full transition-transform hover:scale-110"
              style={{ background: c.v, outline: accent === c.v ? `2px solid ${c.v}` : "none", outlineOffset: 2 }} title={c.name} />
          ))}
        </div>
      </SettingRow>
      <SettingRow label="语言">
        <Select value={locale} onChange={(v) => setLocale(v as "zh" | "en")} options={[
          { value: "zh", label: "简体中文" }, { value: "en", label: "English" },
        ]} />
      </SettingRow>
      <SettingRow label="字体大小" border={false}>
        <Select value={String(fontSize || 14)} onChange={changeFontSize} options={[
          { value: "12", label: "小" }, { value: "14", label: "默认" }, { value: "16", label: "大" },
        ]} />
      </SettingRow>
    </div>
  );
}

function NotificationTab() {
  const [desktop, setDesktop] = useState(() => typeof window !== "undefined" && localStorage.getItem("hmm_notify") === "1");
  const [sound, setSound] = useState(() => typeof window !== "undefined" && localStorage.getItem("hmm_sound") === "1");

  function toggleDesktop(v: boolean) {
    setDesktop(v);
    localStorage.setItem("hmm_notify", v ? "1" : "");
    if (v && "Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
  }

  return (
    <div>
      <SettingRow label="桌面通知" desc="当收到新消息时显示系统通知">
        <Toggle checked={desktop} onChange={toggleDesktop} />
      </SettingRow>
      <SettingRow label="声音提示" desc="消息完成时播放提示音" border={false}>
        <Toggle checked={sound} onChange={v => { setSound(v); localStorage.setItem("hmm_sound", v ? "1" : ""); }} />
      </SettingRow>
    </div>
  );
}

function PersonalizeTab() {
  const { customPrompt } = useStore();
  const set = useStore(s => s.set);
  const [draft, setDraft] = useState(customPrompt);
  const [saved, setSaved] = useState(false);
  const [answerStyle, setAnswerStyle] = useState(() =>
    typeof window !== "undefined" ? (localStorage.getItem("hmm_answer_style") || "analytical") : "analytical"
  );

  function save() {
    set({ customPrompt: draft });
    localStorage.setItem("hmm_prompt", draft);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  function changeStyle(style: string) {
    setAnswerStyle(style);
    localStorage.setItem("hmm_answer_style", style);
  }

  // V86: emoji 功能图标换 lucide
  const styles = [
    { id: "factual", Icon: Target, color: "#dc2626", label: "精准模式", desc: "数据查询、事实核查" },
    { id: "analytical", Icon: BarChart3, color: "#2563eb", label: "分析模式", desc: "深度分析（推荐）" },
    { id: "creative", Icon: Lightbulb, color: "#d97706", label: "创意模式", desc: "头脑风暴、写作" },
  ];

  return (
    <div>
      <div className="text-[13px] font-medium mb-1" style={{ color: "var(--text-primary)" }}>回答风格</div>
      <p className="text-[11px] mb-3 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>选择 AI 回答的默认风格。精准模式每次回答一致，创意模式每次会有不同视角。</p>
      <div className="grid grid-cols-3 gap-2 mb-6">
        {styles.map(s => (
          <button key={s.id} onClick={() => changeStyle(s.id)}
            className="flex flex-col items-center gap-1 p-3 rounded-lg text-center transition-all"
            style={{
              background: answerStyle === s.id ? "var(--accent-light)" : "var(--bg-secondary)",
              border: `1.5px solid ${answerStyle === s.id ? "var(--accent)" : "var(--border)"}`,
            }}>
            <s.Icon size={18} style={{ color: s.color, flexShrink: 0 }} />
            <span className="text-[12px] font-medium" style={{ color: answerStyle === s.id ? "var(--accent)" : "var(--text-primary)" }}>{s.label}</span>
            <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{s.desc}</span>
          </button>
        ))}
      </div>

      <div className="text-[13px] font-medium mb-1" style={{ color: "var(--text-primary)" }}>自定义指令</div>
      <p className="text-[11px] mb-3 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>设置助手的角色和行为，每次对话都会生效。留空使用默认。</p>
      <textarea value={draft} onChange={e => setDraft(e.target.value)}
        placeholder="例如：你是一个专注于深度学习的学术助手，请用中文回答，注重公式和算法细节..."
        rows={6} className="w-full px-3 py-2.5 rounded-lg text-[13px] outline-none resize-none leading-relaxed"
        style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
      <div className="flex justify-end mt-2">
        <button onClick={save} className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-[12px] font-medium text-white"
          style={{ background: saved ? "#22c55e" : "var(--accent)" }}>
          {saved ? <><Check size={13} /> 已保存</> : "保存"}
        </button>
      </div>
    </div>
  );
}

function DataTab() {
  const [exporting, setExporting] = useState(false);

  function archiveAll() {
    if (confirm("确定要归档所有聊天吗？归档后的聊天可以在「已归档的聊天」中恢复。")) {
      alert("所有对话已归档");
    }
  }

  function clearHistory() {
    if (confirm("确定要删除所有对话历史吗？此操作不可恢复。")) {
      if (typeof window !== "undefined") localStorage.removeItem("hmm_s");
      useStore.setState({ sessions: [], sid: null });
    }
  }

  async function exportData() {
    setExporting(true);
    try {
      const convs = await api.listConversations();
      const blob = new Blob([JSON.stringify(convs, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `hashmm-export-${new Date().toISOString().slice(0,10)}.json`;
      a.click(); URL.revokeObjectURL(url);
    } catch (_e) { alert("导出失败"); }
    setExporting(false);
  }

  return (
    <div>
      <SettingRow label="共享链接" desc="管理你创建的所有共享链接">
        <SmallBtn onClick={() => alert("共享链接管理开发中")}>管理</SmallBtn>
      </SettingRow>
      <SettingRow label="已归档的聊天" desc="查看和恢复已归档的对话">
        <SmallBtn onClick={() => alert("归档管理开发中")}>管理</SmallBtn>
      </SettingRow>
      <SettingRow label="归档所有聊天" desc="将所有对话移入归档">
        <SmallBtn onClick={archiveAll}>全部归档</SmallBtn>
      </SettingRow>
      <SettingRow label="删除所有聊天" desc="永久删除所有对话，此操作不可恢复">
        <SmallBtn onClick={clearHistory} danger>全部删除</SmallBtn>
      </SettingRow>
      <SettingRow label="导出数据" desc="导出你的所有对话数据为 JSON 文件">
        <button onClick={exportData} disabled={exporting}
          className="px-4 py-1.5 rounded-lg text-[12px] font-medium disabled:opacity-50"
          style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          {exporting ? <Loader2 size={13} className="animate-spin inline mr-1" /> : null}
          {exporting ? "导出中..." : "导出"}
        </button>
      </SettingRow>
    </div>
  );
}

function StorageTab() {
  const [files, setFiles] = useState<Array<{ filename: string; size_str: string; download_url: string }>>([]);
  const [saveCfg, setSaveCfg] = useState<{ saveMode: "ask" | "fixed"; saveDir: string } | null>(null);
  const [backendDir, setBackendDir] = useState<{ dataDir: string; isDefault: boolean } | null>(null);
  const [backendMsg, setBackendMsg] = useState("");
  const load = useCallback(async () => { try { const r = await listWorkspaceFiles(); setFiles(r.files || []); } catch (_e) { /* empty */ } }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const f = getFiles();
    if (f) f.getSaveConfig().then(setSaveCfg).catch(() => { /* */ });
    const b = getBackendData();
    if (b) b.getDataDir().then(r => { if (r.ok) setBackendDir({ dataDir: r.dataDir, isDefault: r.isDefault }); }).catch(() => { /* */ });
  }, []);

  const filesApi = getFiles();
  const backendApi = getBackendData();

  return (
    <div>
      {/* V96: 文件保存位置（微信式下载管理，仅桌面端） */}
      {filesApi && saveCfg && (
        <div className="mb-6">
          <div className="text-[13px] font-medium mb-3" style={{ color: "var(--text-primary)" }}>文件保存位置</div>
          <div className="rounded-xl p-3" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
            <div className="flex items-center gap-2 mb-3">
              {(["fixed", "ask"] as const).map(m => (
                <button key={m}
                  onClick={async () => { await filesApi.setSaveMode(m); setSaveCfg({ ...saveCfg, saveMode: m }); }}
                  className="px-3 py-1.5 rounded-lg text-[12px] font-medium transition-colors"
                  style={saveCfg.saveMode === m
                    ? { background: "var(--accent)", color: "#fff" }
                    : { background: "var(--bg-tertiary)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
                  {m === "fixed" ? "保存到固定文件夹" : "每次询问保存位置"}
                </button>
              ))}
            </div>
            {saveCfg.saveMode === "fixed" && (
              <div className="flex items-center gap-2">
                <div className="flex-1 px-3 py-2 rounded-lg text-[11.5px] truncate"
                     style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)" }} title={saveCfg.saveDir}>
                  {saveCfg.saveDir}
                </div>
                <button onClick={async () => { const r = await filesApi.chooseSaveDir(); if (r.ok && r.saveDir) setSaveCfg({ ...saveCfg, saveDir: r.saveDir }); }}
                  className="px-3 py-2 rounded-lg text-[12px] font-medium" style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)" }}>
                  更改
                </button>
                <button onClick={() => filesApi.openPath(saveCfg.saveDir)}
                  className="px-3 py-2 rounded-lg text-[12px] font-medium" style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)" }}>
                  打开
                </button>
              </div>
            )}
            <div className="text-[10.5px] mt-2" style={{ color: "var(--text-tertiary)" }}>
              对话导出、生成的文档、图片等下载内容将保存到这里（同名文件自动加序号，不覆盖）
            </div>
          </div>
        </div>
      )}

      {/* V101: 后端数据位置（解析的文档/知识库落盘位置，默认安装位置，可改） */}
      {backendApi && backendDir && (
        <div className="mb-6">
          <div className="text-[13px] font-medium mb-3" style={{ color: "var(--text-primary)" }}>后端数据位置</div>
          <div className="rounded-xl p-3" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
            <div className="flex items-center gap-2">
              <div className="flex-1 px-3 py-2 rounded-lg text-[11.5px] truncate"
                   style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)" }} title={backendDir.dataDir}>
                {backendDir.dataDir}
              </div>
              <button onClick={async () => {
                  const r = await backendApi.chooseDataDir();
                  if (r.ok && r.dataDir) { setBackendDir({ dataDir: r.dataDir, isDefault: false }); setBackendMsg("已更改，重启本地后端后生效"); }
                  else if (r.error) setBackendMsg(r.error);
                }}
                className="px-3 py-2 rounded-lg text-[12px] font-medium" style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)" }}>
                更改
              </button>
              {!backendDir.isDefault && (
                <button onClick={async () => { const r = await backendApi.resetDataDir(); if (r.ok) { setBackendDir({ dataDir: r.dataDir, isDefault: true }); setBackendMsg("已恢复默认，重启本地后端后生效"); } }}
                  className="px-3 py-2 rounded-lg text-[12px] font-medium" style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)" }}>
                  默认
                </button>
              )}
            </div>
            <div className="text-[10.5px] mt-2" style={{ color: backendMsg ? "var(--accent)" : "var(--text-tertiary)" }}>
              {backendMsg || "解析的文档、知识库、向量索引保存在这里，默认随软件装在安装目录"}
            </div>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>工作区文件</div>
          <div className="text-[11px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{files.length} 个文件</div>
        </div>
      </div>
      {files.length > 0 ? (
        <div className="space-y-1 max-h-[300px] overflow-y-auto">
          {files.map(f => (
            <a key={f.filename} href={api.withToken(f.download_url)} download
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-[12px] transition-colors hover:bg-[var(--bg-tertiary)]">
              <Download size={13} style={{ color: "var(--accent)" }} />
              <span className="flex-1 truncate font-medium" style={{ color: "var(--text-primary)" }}>{f.filename}</span>
              <span style={{ color: "var(--text-tertiary)" }}>{f.size_str}</span>
            </a>
          ))}
        </div>
      ) : (
        <div className="text-center py-12 text-[13px]" style={{ color: "var(--text-tertiary)" }}>暂无文件</div>
      )}
    </div>
  );
}

function SecurityTab() {
  const [showPw, setShowPw] = useState(false);
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);

  async function changePw() {
    if (newPw.length < 4) { setMsg("密码至少 4 个字符"); return; }
    setSaving(true); setMsg("");
    try {
      await fetch("/api/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${localStorage.getItem("hmm_token")}` },
        body: JSON.stringify({ new_password: newPw }),
      });
      setMsg("密码已修改"); setOldPw(""); setNewPw("");
    } catch (_e) { setMsg("修改失败"); }
    setSaving(false);
  }

  return (
    <div>
      <div className="text-[13px] font-medium mb-3" style={{ color: "var(--text-primary)" }}>修改密码</div>
      <div className="space-y-2.5 mb-3">
        <div className="relative">
          <input type={showPw ? "text" : "password"} value={newPw} onChange={e => setNewPw(e.target.value)}
            placeholder="新密码（至少 4 个字符）"
            className="w-full h-9 px-3 pr-9 rounded-lg text-[13px] outline-none"
            style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          <button onClick={() => setShowPw(!showPw)} className="absolute right-2 top-1/2 -translate-y-1/2 p-1" style={{ color: "var(--text-tertiary)" }}>
            {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        </div>
      </div>
      {msg && <div className={`text-[12px] mb-2 ${msg.includes("已修改") ? "text-green-500" : "text-red-500"}`}>{msg}</div>}
      <button onClick={changePw} disabled={saving || !newPw}
        className="px-4 py-1.5 rounded-lg text-[12px] font-medium text-white disabled:opacity-50"
        style={{ background: "var(--accent)" }}>
        {saving ? "修改中..." : "修改密码"}
      </button>
    </div>
  );
}

function AccountTab() {
  const { user } = useStore();
  const [editing, setEditing] = useState(false);
  const [displayName, setDisplayName] = useState(user?.display_name || "");
  const [saved, setSaved] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [localAvatar, setLocalAvatar] = useState<string | null>(null);   // 当前显示的头像（data URL）
  // 头像 key 用 JWT 里的真实 uuid（user.id 在 Supabase 登录时为空），保证唯一且可跨端对齐
  const uid = (typeof window !== "undefined") ? userIdFromToken(localStorage.getItem("hmm_token")) : "";

  // 进入设置：先用本地缓存秒显，再从 Supabase 拉权威头像（始终可达，换设备/后端重启都在）
  useEffect(() => {
    if (!uid) return;
    try { const a = localStorage.getItem("hmm_avatar_" + uid); if (a) setLocalAvatar(a); } catch (_e) { /* */ }
    (async () => {
      try {
        const tok = localStorage.getItem("hmm_token"); if (!tok) return;
        const p = await getMyProfile(tok);
        if (p?.avatar_url) {
          setLocalAvatar(p.avatar_url);
          try { localStorage.setItem("hmm_avatar_" + uid, p.avatar_url); } catch (_e) { /* */ }
          window.dispatchEvent(new Event("hmm-avatar-updated"));
        }
      } catch (_e) { /* 离线/未配置：用本地缓存 */ }
    })();
  }, [uid]);

  // 压缩为 ≤256px 的 JPEG data URL（控制写入 Supabase 的体积，~20–40KB）
  function compressToDataUrl(file: File, max = 256, quality = 0.72): Promise<string> {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        try {
          const scale = Math.min(1, max / Math.max(img.width, img.height));
          const w = Math.max(1, Math.round(img.width * scale));
          const h = Math.max(1, Math.round(img.height * scale));
          const c = document.createElement("canvas"); c.width = w; c.height = h;
          const ctx = c.getContext("2d"); if (!ctx) { reject(new Error("no ctx")); return; }
          ctx.drawImage(img, 0, 0, w, h);
          resolve(c.toDataURL("image/jpeg", quality));
        } catch (err) { reject(err as Error); }
      };
      img.onerror = () => reject(new Error("image load failed"));
      const r = new FileReader();
      r.onload = () => { img.src = r.result as string; };
      r.onerror = () => reject(new Error("read failed"));
      r.readAsDataURL(file);
    });
  }

  async function saveProfile() {
    try {
      await fetch(`/api/admin/users/${user?.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${localStorage.getItem("hmm_token")}` },
        body: JSON.stringify({ display_name: displayName }),
      });
      const newUser = { ...user!, display_name: displayName };
      useStore.setState({ user: newUser });
      localStorage.setItem("hmm_user", JSON.stringify(newUser));
      setSaved(true); setEditing(false);
      setTimeout(() => setSaved(false), 2000);
    } catch (_e) { alert("保存失败"); }
  }

  return (
    <div>
      <SettingRow label="头像" desc="点击更换，桌面与手机通用（云端保存，换设备也在）">
        <div className="flex items-center gap-3">
          <div className="w-12 h-12 rounded-full overflow-hidden flex items-center justify-center shrink-0"
            style={{ background: "var(--accent)" }}>
            {localAvatar ? (
              <img src={localAvatar} alt="头像" className="w-full h-full object-cover" />
            ) : (
              <span className="text-white text-[18px] font-bold">
                {(user?.display_name || user?.username || "#").slice(0, 1).toUpperCase()}
              </span>
            )}
          </div>
          <label className="px-3 py-1 rounded-lg text-[11px] font-medium text-white cursor-pointer inline-flex items-center gap-1.5"
            style={{ background: "var(--accent)", opacity: uploading ? 0.6 : 1 }}>
            {uploading ? <Loader2 size={12} className="animate-spin" /> : null}{uploading ? "保存中…" : "更换头像"}
            <input type="file" accept="image/*" className="hidden" disabled={uploading}
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                setUploading(true);
                try {
                  const dataUrl = await compressToDataUrl(file);
                  // 立即本地显示 + 缓存 + 广播侧栏头像更新
                  setLocalAvatar(dataUrl);
                  if (uid) { try { localStorage.setItem("hmm_avatar_" + uid, dataUrl); } catch (_e) { /* */ } }
                  window.dispatchEvent(new Event("hmm-avatar-updated"));
                  // 权威保存到 Supabase（始终可达；后端宕机/换设备也不丢）
                  const tok = localStorage.getItem("hmm_token");
                  let ok = false;
                  if (tok) ok = await updateMyAvatar(tok, dataUrl);
                  // 同时尝试旧的后端中转接口（向后兼容；失败不影响）
                  try {
                    await fetch("/api/profile/avatar", { method: "POST", headers: { "Authorization": `Bearer ${tok}` }, body: file });
                  } catch (_e) { /* 后端可能未部署，忽略 */ }
                  if (!ok) console.warn("头像未能写入 Supabase（请确认已执行 sql/hashmm-profiles-models.sql）；本地已显示。");
                } catch (_e) {
                  alert("图片处理失败，请换一张试试");
                } finally {
                  setUploading(false);
                }
              }} />
          </label>
        </div>
      </SettingRow>
      <SettingRow label="姓名">
        {editing ? (
          <div className="flex items-center gap-2">
            <input value={displayName} onChange={e => setDisplayName(e.target.value)}
              className="h-8 px-3 rounded-lg text-[13px] outline-none w-[160px]"
              style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            <button onClick={saveProfile} className="px-3 py-1 rounded-lg text-[11px] font-medium text-white" style={{ background: "var(--accent)" }}>保存</button>
            <button onClick={() => setEditing(false)} className="px-3 py-1 rounded-lg text-[11px]" style={{ color: "var(--text-tertiary)" }}>取消</button>
          </div>
        ) : (
          <button onClick={() => setEditing(true)} className="flex items-center gap-1 text-[13px] hover:underline" style={{ color: "var(--text-primary)" }}>
            {saved ? <><Check size={12} className="text-green-500" /> 已保存</> : <>{user?.display_name || user?.username || "—"} <ChevronRight size={12} style={{ color: "var(--text-tertiary)" }} /></>}
          </button>
        )}
      </SettingRow>
      <SettingRow label="用户名">
        <span className="text-[13px] font-mono" style={{ color: "var(--text-primary)" }}>@{user?.username || "—"}</span>
      </SettingRow>
      <SettingRow label="角色">
        <span className="text-[13px]" style={{ color: "var(--text-primary)" }}>{user?.role === "admin" ? "管理员" : "用户"}</span>
      </SettingRow>
      <SettingRow label="删除帐户" desc="永久删除你的帐户和所有数据" border={false}>
        <SmallBtn onClick={() => { if (confirm("确定要删除帐户吗？此操作不可恢复。")) alert("请联系管理员删除帐户。"); }} danger>删除</SmallBtn>
      </SettingRow>
    </div>
  );
}

function ShortcutsTab() {
  const shortcuts = [
    { key: "Enter", desc: "发送消息" }, { key: "Shift + Enter", desc: "换行" },
    { key: "Ctrl + N", desc: "新建对话" }, { key: "Ctrl + K", desc: "命令面板" },
    { key: "Ctrl + V", desc: "粘贴文件" }, { key: "Escape", desc: "停止生成" },
    { key: "Ctrl + Shift + C", desc: "复制最后回答" }, { key: "Ctrl + .", desc: "切换侧边栏" },
  ];
  return (
    <div>
      {shortcuts.map(({ key, desc }, i) => (
        <div key={key} className="flex items-center justify-between py-2.5 px-1"
          style={i < shortcuts.length - 1 ? { borderBottom: "1px solid var(--border)" } : {}}>
          <span className="text-[13px]" style={{ color: "var(--text-secondary)" }}>{desc}</span>
          <kbd className="px-2.5 py-1 rounded-md text-[11px] font-mono"
            style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>{key}</kbd>
        </div>
      ))}
    </div>
  );
}

function AboutTab() {
  const { stats, user } = useStore();
  const info: [string, string][] = [
    ["版本", "HashMM-RAG v29.0"], ["当前模型", stats?.active_model || "—"],
    ["LLM 状态", stats?.llm_ready ? "已连接" : "未配置"],
    ["文档块数", stats?.total_chunks?.toLocaleString() || "—"],
    ["索引大小", stats?.index_size_kb ? `${stats.index_size_kb} KB` : "—"],
    ["当前用户", `${user?.display_name || user?.username} (${user?.role === "admin" ? "管理员" : "用户"})`],
  ];
  return (
    <div>
      {info.map(([label, value], i) => (
        <div key={label} className="flex items-center justify-between py-2.5"
          style={i < info.length - 1 ? { borderBottom: "1px solid var(--border)" } : {}}>
          <span className="text-[13px]" style={{ color: "var(--text-secondary)" }}>{label}</span>
          <span className="text-[12px] font-mono" style={{ color: "var(--text-primary)" }}>{value}</span>
        </div>
      ))}
      <div className="pt-5 flex justify-center gap-4 text-[11px]">
        <a href="/terms" target="_blank" className="underline" style={{ color: "var(--text-tertiary)" }}>服务条款</a>
        <a href="/privacy" target="_blank" className="underline" style={{ color: "var(--text-tertiary)" }}>隐私政策</a>
      </div>
    </div>
  );
}

/* ━━━━━━━━━━━━━━━ Main ━━━━━━━━━━━━━━━ */

export function SettingsModal() {
  const set = useStore(s => s.set);
  const settingsTab = useStore(s => s.settingsTab) as TabId;
  const [tab, setTab] = useState<TabId>(settingsTab || "general");

  // Sync when opened from UserMenu with a specific tab
  useEffect(() => { if (settingsTab) setTab(settingsTab as TabId); }, [settingsTab]);

  const TAB_CONTENT: Record<TabId, React.ReactNode> = {
    general: <GeneralTab />, notification: <NotificationTab />, personalize: <PersonalizeTab />,
    data: <DataTab />, storage: <StorageTab />, security: <SecurityTab />,
    account: <AccountTab />, shortcuts: <ShortcutsTab />, about: <AboutTab />,
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
      onClick={e => { if (e.target === e.currentTarget) set({ setOpen: false }); }}>
      <div className="w-[680px] max-h-[85vh] flex rounded-2xl overflow-hidden anim-fade-up"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
        <div className="w-[170px] flex-shrink-0 py-3 px-2 space-y-0.5 overflow-y-auto"
          style={{ background: "var(--bg-secondary)", borderRight: "1px solid var(--border)" }}>
          {TAB_GROUPS.map(g => (
            <div key={g.title} className="mb-2">
              <div className="px-2 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wide" style={{ color: "var(--text-tertiary)" }}>{g.title}</div>
              {g.ids.map(id => {
                const t = TABS.find(x => x.id === id)!;
                const Icon = t.icon;
                const active = tab === id;
                return (
                  <button key={id} onClick={() => setTab(id)}
                    className="relative w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] font-medium transition-all"
                    style={{ background: active ? "var(--accent-light)" : "transparent", color: active ? "var(--accent)" : "var(--text-secondary)" }}>
                    {active && <span className="absolute left-[3px] top-1/2 -translate-y-1/2 w-[3px] h-[15px] rounded-full" style={{ background: "var(--accent)" }} />}
                    <Icon size={15} /> {t.label}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
        <div className="flex-1 flex flex-col min-w-0">
          <div className="flex items-center justify-between px-6 py-4" style={{ borderBottom: "1px solid var(--border)" }}>
            <h4 className="text-[15px] font-semibold" style={{ color: "var(--text-primary)" }}>
              {TABS.find(t => t.id === tab)?.label}
            </h4>
            <button onClick={() => set({ setOpen: false })} className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]">
              <X size={18} style={{ color: "var(--text-tertiary)" }} />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-6 py-4">{TAB_CONTENT[tab]}</div>
        </div>
      </div>
    </div>
  );
}

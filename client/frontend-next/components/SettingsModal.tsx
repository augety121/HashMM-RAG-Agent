"use client";
import { useState, useEffect, useCallback } from "react";
import { useStore } from "@/lib/store";
import { useT } from "@/lib/useT";
import { listWorkspaceFiles, myModels, addMyModel, delMyModel, preferMyModel, userModelProviders, type MyModel } from "@/lib/api";
import { getBrowser, getCU, getFiles, getBackendData, getDesktop, getLocal, getProjectDesktop, type BackendDataApi, type RepoInspection } from "@/lib/desktop";
import { getMyProfile, updateMyAvatar, userIdFromToken } from "@/lib/supabase";
import * as api from "@/lib/api";
import { COLORS, PROVIDERS, type ModelWireApi, type ProviderSpec } from "@/lib/types";
import {
  X, ArrowLeft, Search, Moon, Sun, Monitor, Palette, Keyboard, Database, Bell,
  MessageSquare, FolderOpen, Download, Trash2, Info, Shield, Globe,
  User, ChevronRight, HardDrive, Check, Loader2, Eye, EyeOff,
  Target, BarChart3, Lightbulb, Cpu, RefreshCw, BookOpen, KeyRound, GitBranch, CircleDollarSign,
} from "lucide-react";
import HashMascot from "./HashMascot";
import { UserSkillsSettings } from "./settings/UserSkillsSettings";
import { ApiAccessSettings } from "./settings/ApiAccessSettings";
import {
  clearAccountWorkspaceCache,
  readAccountProjects,
  readActiveProject,
  readProjectSources,
  writeActiveProject,
  writeProjectSources,
} from "@/lib/accountWorkspaceCache";
import { writeCapabilityPref, type ChatCapability } from "@/lib/capabilityPrefs";
import { WorktreePanel } from "./desktop/WorktreePanel";
import { UsageView } from "./desktop/UsageView";
import ProviderFabricPanel from "./ProviderFabricPanel";

type TabId = "general" | "workspace" | "notification" | "personalize" | "mymodels" | "skills" | "browser" | "computer" | "capabilities" | "worktrees" | "usage" | "data" | "storage" | "security" | "apiaccess" | "account" | "shortcuts" | "about";

const TABS: { id: TabId; label: string; icon: React.ElementType }[] = [
  { id: "worktrees", label: "Worktrees", icon: GitBranch },
  { id: "usage", label: "使用情况", icon: CircleDollarSign },
  { id: "general", label: "常规", icon: Palette },
  { id: "workspace", label: "工作空间", icon: FolderOpen },
  { id: "notification", label: "通知", icon: Bell },
  { id: "personalize", label: "个性化", icon: MessageSquare },
  { id: "mymodels", label: "模型与 API", icon: Cpu },
  { id: "skills", label: "技能", icon: BookOpen },
  { id: "browser", label: "浏览器", icon: Globe },
  { id: "computer", label: "电脑操作", icon: Monitor },
  { id: "capabilities", label: "画布与协作", icon: Target },
  { id: "data", label: "数据管理", icon: Database },
  { id: "storage", label: "存储空间", icon: HardDrive },
  { id: "security", label: "安全", icon: Shield },
  { id: "apiaccess", label: "API 访问", icon: KeyRound },
  { id: "account", label: "帐户", icon: User },
  { id: "shortcuts", label: "快捷键", icon: Keyboard },
  { id: "about", label: "关于", icon: Info },
];

// 与侧栏一致的信息架构：把设置项按主题分组，配同样的分区标题 + 激活竖条。
const TAB_GROUPS: { title: string; ids: TabId[] }[] = [
  { title: "编码与成本", ids: ["worktrees", "usage"] },
  { title: "应用与账号", ids: ["general", "notification", "account", "security"] },
  { title: "工作与智能", ids: ["workspace", "personalize", "mymodels", "skills", "browser", "computer", "capabilities"] },
  { title: "集成与开发", ids: ["apiaccess"] },
  { title: "数据与隐私", ids: ["data", "storage"] },
  { title: "其它", ids: ["shortcuts", "about"] },
];

const TAB_DESCRIPTIONS: Record<TabId, string> = {
  worktrees: "安全创建、保留、恢复和清理隔离工作树",
  usage: "Token、模型费用、工具调用和本机 Agent 用量",
  general: "外观、语言和界面密度",
  workspace: "项目、对话和本地资料边界",
  notification: "桌面通知与声音提醒",
  personalize: "回答风格和对话级默认指令",
  mymodels: "属于当前账号的模型与 API 配置",
  skills: "为 Chat 添加可复用的工作方法",
  browser: "右侧受控浏览器与网页证据",
  computer: "本机电脑操作、确认与安全边界",
  capabilities: "画布与多智能体协作在 Chat 中的入口",
  data: "导出、归档与删除数据",
  storage: "工作区文件和本地数据位置",
  security: "会话安全与登录状态",
  apiaccess: "独立密钥、权限、限流与费用配额",
  account: "头像和账号资料",
  shortcuts: "键盘操作与效率设置",
  about: "版本、许可和产品信息",
};

const TAB_SCOPE: Record<TabId, string> = {
  worktrees: "当前设备与项目", usage: "当前账户与设备",
  general: "此设备", workspace: "当前账号与此设备", notification: "此设备", personalize: "当前账号", mymodels: "当前账号", skills: "当前账号", browser: "此设备", computer: "此设备", capabilities: "当前账号与此设备",
  data: "当前账号", storage: "此设备", security: "当前账号", apiaccess: "当前账号", account: "当前账号",
  shortcuts: "此设备", about: "产品信息",
};

const TAB_SEARCH_TERMS: Record<TabId, string> = {
  worktrees: "Git worktree 分支 隔离 清理 恢复 detached ignored",
  usage: "Token 费用 成本 工具调用 存储 预算",
  general: "主题 深色 浅色 重点色 字体 语言 界面",
  workspace: "项目 对话 本地资料 文件夹 默认工作目录 同步",
  notification: "桌面通知 声音 系统权限",
  personalize: "回答风格 自定义指令 偏好 跨设备",
  mymodels: "模型 API Key Base URL OpenAI DeepSeek 默认模型",
  skills: "技能 工作方法 Codex Claude GitHub ZIP",
  browser: "浏览器 网页 来源 地址栏",
  computer: "电脑操作 只读 严格确认 工作目录",
  capabilities: "画布 多智能体 审批 权限 文件操作",
  data: "导出 归档 删除 共享链接 缓存 隐私",
  storage: "ProjectVault 下载目录 备份 文件 保存位置",
  security: "密码 会话 登录 设备 撤销",
  apiaccess: "API Key Scope IP RPM 并发 配额 过期",
  account: "头像 姓名 用户名 角色 删除账号",
  shortcuts: "快捷键 键盘 Enter Escape",
  about: "版本 更新 许可 模型 索引",
};

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

function SettingSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-7">
      <h2 className="text-[12px] font-semibold mb-2" style={{ color: "var(--text-secondary)" }}>{title}</h2>
      <div className="rounded-2xl px-4" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
        {children}
      </div>
    </section>
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
    <button onClick={() => onChange(!checked)} type="button" role="switch" aria-checked={checked}
      aria-label={checked ? "已启用" : "已停用"}
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

function useAccountSettingControl(keys: string[]) {
  const signature = keys.join("|");
  const [items, setItems] = useState<Record<string, api.AccountSetting>>({});
  const [status, setStatus] = useState("读取账号设置…");

  useEffect(() => {
    let active = true;
    api.listMyAccountSettings().then(result => {
      if (!active) return;
      const wanted = new Set(signature.split("|").filter(Boolean));
      setItems(Object.fromEntries(result.settings.filter(item => wanted.has(item.key)).map(item => [item.key, item])));
      setStatus("");
    }).catch(error => {
      if (active) setStatus((error as Error)?.message || "账号设置暂时无法读取；当前只保留本机偏好。");
    });
    return () => { active = false; };
  }, [signature]);

  const persist = useCallback(async (key: string, value: unknown) => {
    const item = items[key];
    if (!item) {
      setStatus("账号设置尚未载入，本次只应用到当前设备。");
      return;
    }
    setStatus("正在同步…");
    try {
      const result = await api.updateMyAccountSettings([{ key, value, revision: item.revision }]);
      const next = result.settings.find(setting => setting.key === key);
      if (next) setItems(previous => ({ ...previous, [key]: next }));
      setStatus(`已应用 · 回执 ${result.receipt.id}`);
    } catch (error) {
      const conflict = (error as Error & { status?: number }).status === 409;
      setStatus(conflict ? "设置已在其他设备修改，请重新打开本页。" : ((error as Error)?.message || "未取得服务端回执；账号设置未变更。"));
    }
  }, [items]);

  return { items, status, persist };
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
  const { items: accountSettings, status: accountStatus, persist } = useAccountSettingControl([
    "general.language", "general.send_behavior", "general.followup_mode", "general.prevent_sleep",
    "appearance.theme", "appearance.density",
  ]);
  const [sendBehavior, setSendBehavior] = useState("enter");
  const [preventSleep, setPreventSleep] = useState(true);
  const [density, setDensity] = useState("comfortable");

  useEffect(() => {
    const value = (key: string) => accountSettings[key]?.effective_value;
    if (["light", "dark", "system"].includes(String(value("appearance.theme")))) {
      applyTheme(String(value("appearance.theme")), false);
    }
    if (["enter", "ctrl_enter"].includes(String(value("general.send_behavior")))) setSendBehavior(String(value("general.send_behavior")));
    if (typeof value("general.prevent_sleep") === "boolean") setPreventSleep(Boolean(value("general.prevent_sleep")));
    if (["comfortable", "compact"].includes(String(value("appearance.density")))) setDensity(String(value("appearance.density")));
    if (value("general.language") === "zh-CN") setLocale("zh");
    if (value("general.language") === "en-US") setLocale("en");
  }, [accountSettings]);

  function applyTheme(mode: string, sync = true) {
    setThemeMode(mode);
    let isDark = false;
    if (mode === "system") isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    else isDark = mode === "dark";
    set({ dark: isDark });
    localStorage.setItem("hmm_dark", isDark ? "1" : "");
    localStorage.setItem("hmm_theme_mode", mode);
    if (sync) void persist("appearance.theme", mode);
  }

  function changeFontSize(v: string) {
    const size = parseInt(v, 10);
    set({ fontSize: size });
    localStorage.setItem("hmm_fontsize", v);
    document.documentElement.style.setProperty("--msg-font-size", `${size}px`);
  }

  return (
    <div>
      <SettingSection title="外观">
        <SettingRow label="主题" desc="选择浅色、深色或跟随系统的界面主题">
          <Select value={themeMode} onChange={applyTheme} options={[
            { value: "light", label: "浅色" }, { value: "dark", label: "深色" }, { value: "system", label: "系统" },
          ]} />
        </SettingRow>
        <SettingRow label="重点色" desc="用于按钮、选中状态和运行提示">
          <div className="flex gap-2 items-center">
            {COLORS.map(c => (
              <button key={c.v} onClick={() => { set({ accent: c.v }); localStorage.setItem("hmm_accent", c.v); }}
                className="w-6 h-6 rounded-full transition-transform hover:scale-110"
                style={{ background: c.v, outline: accent === c.v ? `2px solid ${c.v}` : "none", outlineOffset: 2 }} title={c.name} />
            ))}
          </div>
        </SettingRow>
      </SettingSection>
      <SettingSection title="语言与排版">
        <SettingRow label="语言">
          <Select value={locale} onChange={(v) => { setLocale(v as "zh" | "en"); void persist("general.language", v === "zh" ? "zh-CN" : "en-US"); }} options={[
            { value: "zh", label: "简体中文" }, { value: "en", label: "English" },
          ]} />
        </SettingRow>
        <SettingRow label="字体大小" border={false}>
          <Select value={String(fontSize || 14)} onChange={changeFontSize} options={[
            { value: "12", label: "小" }, { value: "14", label: "默认" }, { value: "16", label: "大" },
          ]} />
        </SettingRow>
        <SettingRow label="界面密度" desc="在紧凑导航与舒适阅读间切换" border={false}>
          <Select value={density} onChange={value => { setDensity(value); localStorage.setItem("hmm_density", value); void persist("appearance.density", value); }} options={[
            { value: "comfortable", label: "舒适" }, { value: "compact", label: "紧凑" },
          ]} />
        </SettingRow>
      </SettingSection>
      <SettingSection title="对话与任务">
        <SettingRow label="发送快捷键" desc="避免多行输入时误发送">
          <Select value={sendBehavior} onChange={value => { setSendBehavior(value); localStorage.setItem("hmm_send_behavior", value); void persist("general.send_behavior", value); }} options={[
            { value: "enter", label: "Enter 发送" }, { value: "ctrl_enter", label: "Ctrl+Enter 发送" },
          ]} />
        </SettingRow>
        <SettingRow label="生成中追加消息" desc="当前仅支持把新消息作为引导送入正在运行的任务；持久化下一轮队列尚未接入任务状态机">
          <div className="px-3 py-1.5 rounded-lg text-[11.5px]" style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
            引导当前任务
          </div>
        </SettingRow>
        <SettingRow label="任务期间防休眠" desc="保存账号偏好；仅在当前设备支持时生效" border={false}>
          <Toggle checked={preventSleep} onChange={value => { setPreventSleep(value); localStorage.setItem("hmm_prevent_sleep", value ? "1" : "0"); void persist("general.prevent_sleep", value); }} />
        </SettingRow>
      </SettingSection>
      {accountStatus && <div className="text-[10px]" style={{ color: accountStatus.includes("已应用") ? "var(--success)" : "var(--text-tertiary)" }}>{accountStatus}</div>}
    </div>
  );
}

function NotificationTab() {
  const [desktop, setDesktop] = useState(() => typeof window !== "undefined" && localStorage.getItem("hmm_notify") === "1");
  const [sound, setSound] = useState(() => typeof window !== "undefined" && localStorage.getItem("hmm_sound") === "1");
  const [taskComplete, setTaskComplete] = useState(true);
  const [approval, setApproval] = useState(true);
  const [chatContinuation, setChatContinuation] = useState(true);
  const [chatMail, setChatMail] = useState(true);
  const [deviceResume, setDeviceResume] = useState(true);
  const { items, status, persist } = useAccountSettingControl([
    "notifications.desktop", "notifications.sound", "notifications.task_complete",
    "notifications.approval", "notifications.chat_continuation",
    "notifications.chat_mail", "notifications.device_resume",
  ]);

  useEffect(() => {
    const bool = (key: string, fallback: boolean) => typeof items[key]?.effective_value === "boolean" ? Boolean(items[key].effective_value) : fallback;
    setDesktop(bool("notifications.desktop", desktop));
    setSound(bool("notifications.sound", sound));
    setTaskComplete(bool("notifications.task_complete", true));
    setApproval(bool("notifications.approval", true));
    setChatContinuation(bool("notifications.chat_continuation", true));
    setChatMail(bool("notifications.chat_mail", true));
    setDeviceResume(bool("notifications.device_resume", true));
  }, [items]);

  function toggleDesktop(v: boolean) {
    setDesktop(v);
    localStorage.setItem("hmm_notify", v ? "1" : "");
    void persist("notifications.desktop", v);
    if (v && "Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
  }

  return (
    <div>
      <SettingSection title="通知">
      <SettingRow label="桌面通知" desc="当收到新消息时显示系统通知">
        <Toggle checked={desktop} onChange={toggleDesktop} />
      </SettingRow>
      <SettingRow label="声音提示" desc="消息完成时播放提示音" border={false}>
        <Toggle checked={sound} onChange={v => { setSound(v); localStorage.setItem("hmm_sound", v ? "1" : ""); void persist("notifications.sound", v); }} />
      </SettingRow>
      </SettingSection>
      <SettingSection title="任务事件">
        <SettingRow label="任务完成" desc="后台任务完成或带限制完成时通知"><Toggle checked={taskComplete} onChange={v => { setTaskComplete(v); void persist("notifications.task_complete", v); }} /></SettingRow>
        <SettingRow label="等待审批" desc="任务因敏感操作等待你的决定时通知"><Toggle checked={approval} onChange={v => { setApproval(v); void persist("notifications.approval", v); }} /></SettingRow>
        <SettingRow label="新 Chat 继续任务" desc="创建新的 Chat，并收到公开、脱敏、可验证的任务连续性包时通知"><Toggle checked={chatContinuation} onChange={v => { setChatContinuation(v); void persist("notifications.chat_continuation", v); }} /></SettingRow>
        <SettingRow label="Chat 消息" desc="另一个 Chat 向当前 Chat 投递消息时通知；消息不会传递审批或执行权限"><Toggle checked={chatMail} onChange={v => { setChatMail(v); void persist("notifications.chat_mail", v); }} /></SettingRow>
        <SettingRow label="App / 设备接续" desc="同一 Chat 和运行检查点切换到另一台已验证设备时通知；不会创建新 Chat" border={false}><Toggle checked={deviceResume} onChange={v => { setDeviceResume(v); void persist("notifications.device_resume", v); }} /></SettingRow>
      </SettingSection>
      {status && <div className="text-[10px]" style={{ color: status.includes("已应用") ? "var(--success)" : "var(--text-tertiary)" }}>{status}</div>}
    </div>
  );
}

function PersonalizeTab() {
  const { customPrompt } = useStore();
  const set = useStore(s => s.set);
  const [draft, setDraft] = useState(customPrompt);
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [receipt, setReceipt] = useState("");
  const [revisions, setRevisions] = useState<Record<string, number>>({
    "personalization.answer_style": 0,
    "personalization.custom_prompt": 0,
  });
  const [answerStyle, setAnswerStyle] = useState(() =>
    typeof window !== "undefined" ? (localStorage.getItem("hmm_answer_style") || "analytical") : "analytical"
  );

  useEffect(() => {
    let active = true;
    api.listMyAccountSettings().then(result => {
      if (!active) return;
      const byKey = Object.fromEntries(result.settings.map(item => [item.key, item]));
      const style = byKey["personalization.answer_style"];
      const prompt = byKey["personalization.custom_prompt"];
      if (typeof style?.effective_value === "string") setAnswerStyle(style.effective_value);
      if (typeof prompt?.effective_value === "string") {
        setDraft(prompt.effective_value);
        set({ customPrompt: prompt.effective_value });
      }
      setRevisions({
        "personalization.answer_style": style?.revision || 0,
        "personalization.custom_prompt": prompt?.revision || 0,
      });
      setError("");
    }).catch(err => {
      if (active) setError((err as Error)?.message || "账号设置暂时无法读取；本机草稿尚未同步。");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [set]);

  async function save() {
    setError(""); setReceipt("");
    try {
      const result = await api.updateMyAccountSettings([
        { key: "personalization.answer_style", value: answerStyle, revision: revisions["personalization.answer_style"] || 0 },
        { key: "personalization.custom_prompt", value: draft, revision: revisions["personalization.custom_prompt"] || 0 },
      ]);
      const byKey = Object.fromEntries(result.settings.map(item => [item.key, item.revision]));
      setRevisions(byKey);
      set({ customPrompt: draft });
      // Compatibility cache keeps the current device usable during an outage;
      // the server receipt above remains the authority for "saved".
      localStorage.setItem("hmm_prompt", draft);
      localStorage.setItem("hmm_answer_style", answerStyle);
      setReceipt(result.receipt.id);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      const conflict = (err as Error & { status?: number }).status === 409;
      setError(conflict ? "设置已在其他设备修改，请重新打开本页后再保存。" : ((err as Error)?.message || "保存失败，未取得服务端回执。"));
    }
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
          <button key={s.id} onClick={() => setAnswerStyle(s.id)}
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
        <button onClick={() => void save()} disabled={loading} className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-[12px] font-medium text-white disabled:opacity-50"
          style={{ background: saved ? "#22c55e" : "var(--accent)" }}>
          {saved ? <><Check size={13} /> 已同步</> : loading ? "读取中…" : "保存到账号"}
        </button>
      </div>
      {error && <div className="mt-2 text-[11px]" style={{ color: "var(--error)" }}>{error}</div>}
      {receipt && <div className="mt-2 text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>变更回执 {receipt}</div>}
    </div>
  );
}

function DataTab() {
  const user = useStore(s => s.user);
  const [exporting, setExporting] = useState(false);
  const [archOpen, setArchOpen] = useState(false);   // V241 已归档管理弹窗
  const [archList, setArchList] = useState<{ id: string; title: string }[] | null>(null);
  const [archError, setArchError] = useState("");
  const [busy, setBusy] = useState(false);

  async function openArchive() {
    setArchOpen(true); setArchList(null); setArchError("");
    // V247: 双来源合并——本地已归档 sessions（乐观标记，一定有）+ 后端 archived=1（多端同步）。
    // 只依赖后端会因 PATCH 失败而空；只依赖本地则多端不同步。合并去重最稳。
    const local = useStore.getState().sessions.filter(s => s.archived).map(s => ({ id: s.id, title: s.title }));
    let remote: { id: string; title: string }[] = [];
    try { const r = await api.listArchivedConversations(); remote = (r.conversations || []).map(c => ({ id: c.id, title: c.title })); }
    catch { setArchError("服务器归档列表暂时无法读取；当前只展示这台电脑的缓存。未验证为跨设备完整列表。"); }
    const seen = new Set<string>();
    const merged = [...local, ...remote].filter(x => { if (seen.has(x.id)) return false; seen.add(x.id); return true; });
    setArchList(merged);
  }
  async function restore(id: string) {
    useStore.getState().archiveSession(id, false);
    try {
      await api.archiveConversation(id, false);
      setArchList(l => (l || []).filter(x => x.id !== id));
    } catch (error) {
      useStore.getState().archiveSession(id, true);
      setArchError((error as Error)?.message || "还原失败；本机状态已回滚。");
    }
  }
  async function restoreAll() {   // V242: 一键还原全部——清理历史误归档
    const list = archList || [];
    if (list.length === 0) return;
    if (!confirm(`确定还原全部 ${list.length} 个已归档对话到主列表吗？`)) return;
    setBusy(true);
    const failed: string[] = [];
    for (const a of list) {
      useStore.getState().archiveSession(a.id, false);
      try { await api.archiveConversation(a.id, false); }
      catch { failed.push(a.id); useStore.getState().archiveSession(a.id, true); }
    }
    setBusy(false);
    setArchList(list.filter(item => failed.includes(item.id)));
    setArchError(failed.length ? `${failed.length} 个对话未能还原，失败项已保留。` : "");
  }
  async function archiveAll() {
    if (!confirm("确定要归档所有聊天吗？归档后可在「已归档的聊天」中恢复。")) return;
    setBusy(true);
    const ids = useStore.getState().sessions.filter(s => !s.archived).map(s => s.id);
    const failed: string[] = [];
    for (const id of ids) {
      useStore.getState().archiveSession(id, true);
      try { await api.archiveConversation(id, true); }
      catch { failed.push(id); useStore.getState().archiveSession(id, false); }
    }
    setBusy(false);
    if (failed.length) alert(`已归档 ${ids.length - failed.length} 个；${failed.length} 个失败并已回滚。`);
    else alert(`已归档 ${ids.length} 个对话`);
  }

  async function clearHistory() {
    const current = useStore.getState().sessions;
    if (!confirm(`确定永久删除当前账号的 ${current.length} 个对话吗？此操作不可恢复。`)) return;
    setBusy(true);
    const failed: string[] = [];
    for (const session of current) {
      try { await api.deleteSession(session.id); }
      catch { failed.push(session.id); }
    }
    const survivors = current.filter(session => failed.includes(session.id));
    useStore.setState({ sessions: survivors, sid: null, openTabs: [] });
    if (!survivors.length) clearAccountWorkspaceCache(user);
    setBusy(false);
    if (failed.length) alert(`${failed.length} 个对话未能删除，已保留在列表中。`);
  }

  function clearLocalWorkspace() {
    if (!confirm("清除当前账号在这台电脑上的项目与对话缓存吗？云端数据不会删除，联网后可重新同步。")) return;
    clearAccountWorkspaceCache(user);
    useStore.setState({ sessions: [], sid: null, openTabs: [] });
  }

  // ── V250 共享链接管理（原为 alert 占位）：列本人全部画布分享，可复制链接 / 撤销 ──
  const [sharesOpen, setSharesOpen] = useState(false);
  const [shares, setShares] = useState<{ share_id: string; url: string; conv_id: string; filename: string; visibility: string; views: number; comments_count: number }[] | null>(null);
  const [shareBusy, setShareBusy] = useState("");
  async function openShares() {
    setSharesOpen(true); setShares(null);
    try { const r = await api.listMyShares(); setShares(r.items || []); }
    catch { setShares([]); }
  }
  async function revoke(id: string) {
    setShareBusy(id);
    try { await api.revokeShare(id); setShares(ss => (ss || []).filter(x => x.share_id !== id)); }
    catch { alert("撤销失败，请重试"); }
    finally { setShareBusy(""); }
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
        <SmallBtn onClick={openShares}>管理</SmallBtn>
      </SettingRow>
      <SettingRow label="已归档的聊天" desc="查看和恢复已归档的对话">
        <SmallBtn onClick={openArchive}>管理</SmallBtn>
      </SettingRow>
      <SettingRow label="归档所有聊天" desc="将所有对话移入归档">
        <SmallBtn onClick={archiveAll}>{busy ? "归档中…" : "全部归档"}</SmallBtn>
      </SettingRow>
      <SettingRow label="这台电脑上的连续工作" desc="项目和已读对话按账号分别保存；切换账号不会互相看到，切回后可以继续">
        <SmallBtn onClick={clearLocalWorkspace}>清除本机缓存</SmallBtn>
      </SettingRow>
      <SettingRow label="删除所有聊天" desc="永久删除所有对话，此操作不可恢复">
        <SmallBtn onClick={() => void clearHistory()} danger>{busy ? "处理中…" : "全部删除"}</SmallBtn>
      </SettingRow>
      <SettingRow label="导出数据" desc="导出你的所有对话数据为 JSON 文件">
        <button onClick={exportData} disabled={exporting}
          className="px-4 py-1.5 rounded-lg text-[12px] font-medium disabled:opacity-50"
          style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          {exporting ? <Loader2 size={13} className="animate-spin inline mr-1" /> : null}
          {exporting ? "导出中..." : "导出"}
        </button>
      </SettingRow>

      {sharesOpen && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center" style={{ background: "rgba(0,0,0,0.45)" }}
          onClick={() => setSharesOpen(false)}>
          <div onClick={e => e.stopPropagation()}
            className="w-[520px] max-w-[92vw] max-h-[70vh] rounded-2xl overflow-hidden flex flex-col"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
            <div className="flex items-center px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
              <span className="text-[14px] font-semibold flex-1" style={{ color: "var(--text-primary)" }}>我的共享链接</span>
              <button onClick={() => setSharesOpen(false)} className="text-[12px]" style={{ color: "var(--text-tertiary)" }}>关闭</button>
            </div>
            <div className="flex-1 overflow-y-auto p-3 space-y-1.5">
              {shares === null && <div className="text-[12px] py-6 text-center" style={{ color: "var(--text-tertiary)" }}>加载中…</div>}
              {shares !== null && shares.length === 0 && (
                <div className="text-[12px] py-8 text-center" style={{ color: "var(--text-tertiary)" }}>
                  还没有共享链接——在画布右上「发布」后，链接会出现在这里
                </div>
              )}
              {(shares || []).map(sh => (
                <div key={sh.share_id} className="flex items-center gap-3 px-3 py-2.5 rounded-xl"
                  style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                  <div className="flex-1 min-w-0">
                    <div className="text-[12.5px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{sh.filename || "画布"}</div>
                    <div className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
                      {sh.visibility === "link" ? "链接可见" : "组织可见"} · 浏览 {sh.views}{sh.comments_count ? ` · 评论 ${sh.comments_count}` : ""}
                    </div>
                  </div>
                  <button onClick={() => { navigator.clipboard?.writeText(window.location.origin + sh.url); }}
                    className="px-2.5 py-1 rounded-md text-[11px]" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>复制链接</button>
                  <button onClick={() => revoke(sh.share_id)} disabled={shareBusy === sh.share_id}
                    className="px-2.5 py-1 rounded-md text-[11px] disabled:opacity-50"
                    style={{ border: "1px solid var(--error)", color: "var(--error)" }}>
                    {shareBusy === sh.share_id ? "撤销中…" : "撤销"}
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {archOpen && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center" style={{ background: "rgba(0,0,0,0.45)" }}
          onClick={() => setArchOpen(false)}>
          <div onClick={e => e.stopPropagation()}
            className="w-[440px] max-w-[90vw] max-h-[70vh] rounded-2xl overflow-hidden flex flex-col"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
            <div className="flex items-center px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
              <Database size={15} style={{ color: "var(--text-secondary)" }} />
              <span className="ml-2 text-[14px] font-semibold flex-1" style={{ color: "var(--text-primary)" }}>已归档的聊天</span>
              {archList && archList.length > 0 && (
                <button onClick={restoreAll} disabled={busy}
                  className="mr-2 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors disabled:opacity-50"
                  style={{ color: "var(--accent)", border: "1px solid var(--border)" }}>
                  {busy ? "还原中…" : "全部还原"}
                </button>
              )}
              <button onClick={() => setArchOpen(false)} className="p-1 rounded-md hover:bg-[var(--bg-tertiary)]"><X size={15} style={{ color: "var(--text-tertiary)" }} /></button>
            </div>
            <div className="flex-1 overflow-y-auto p-2">
              {archError && <div className="mx-2 mb-2 rounded-lg px-3 py-2 text-[10.5px]" style={{ color: "var(--error)", background: "color-mix(in srgb, var(--error) 8%, transparent)" }}>{archError}</div>}
              {archList === null ? (
                <div className="text-center py-10 text-[12px]" style={{ color: "var(--text-tertiary)" }}>加载中…</div>
              ) : archList.length === 0 ? (
                <div className="text-center py-10 text-[12px] flex flex-col items-center gap-2" style={{ color: "var(--text-tertiary)" }}>
                  <Database size={26} style={{ opacity: 0.35 }} />没有已归档的对话
                </div>
              ) : archList.map(a => (
                <div key={a.id} className="flex items-center px-3 py-2.5 rounded-lg mb-0.5 transition-colors hover:bg-[var(--bg-tertiary)]">
                  <MessageSquare size={13} className="mr-2.5 flex-shrink-0 opacity-40" style={{ color: "var(--text-tertiary)" }} />
                  <span className="text-[12.5px] truncate flex-1" style={{ color: "var(--text-secondary)" }}>{a.title || "新对话"}</span>
                  <button onClick={() => restore(a.id)}
                    className="ml-2 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors shrink-0"
                    style={{ color: "var(--accent)", border: "1px solid var(--border)" }}>还原</button>
                </div>
              ))}
            </div>
            {archList && archList.length > 0 && (
              <div className="px-4 py-2 text-[10.5px]" style={{ borderTop: "1px solid var(--border)", color: "var(--text-tertiary)" }}>
                归档的对话不出现在主列表，随时可还原
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
function StorageTab() {
  const [files, setFiles] = useState<Array<{ filename: string; size_str: string; download_url: string }>>([]);
  const [saveCfg, setSaveCfg] = useState<{ saveMode: "ask" | "fixed"; saveDir: string } | null>(null);
  const [backendDir, setBackendDir] = useState<Awaited<ReturnType<BackendDataApi["getDataDir"]>> | null>(null);
  const [backendMsg, setBackendMsg] = useState("");
  const load = useCallback(async () => { try { const r = await listWorkspaceFiles(); setFiles(r.files || []); } catch (_e) { /* empty */ } }, []);
  const loadVault = useCallback(async () => {
    const b = getBackendData();
    if (!b) return;
    try {
      const status = await b.getDataDir();
      if (status.ok) setBackendDir(status);
    } catch (_e) { /* desktop bridge unavailable */ }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const f = getFiles();
    if (f) f.getSaveConfig().then(setSaveCfg).catch(() => { /* */ });
    void loadVault();
  }, [loadVault]);

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

      {/* ProjectVault：稳定用户数据根，与 Python 运行时和程序更新隔离。 */}
      {backendApi && backendDir && (
        <div className="mb-6">
          <div className="text-[13px] font-medium mb-3" style={{ color: "var(--text-primary)" }}>ProjectVault 用户数据位置</div>
          <div className="rounded-xl p-3" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
            <div className="flex flex-wrap gap-2 mb-3 text-[10.5px]">
              <span className="px-2 py-1 rounded-full" style={{ background: "var(--bg-tertiary)", color: backendDir.availability === "ready" ? "var(--success)" : backendDir.availability === "permission_denied" ? "var(--danger)" : "var(--text-secondary)" }}>
                {backendDir.availability === "ready" ? "目录可用" : backendDir.availability === "permission_denied" ? "目录无读写权限" : "目录尚未初始化"}
              </span>
              <span className="px-2 py-1 rounded-full" style={{ background: "var(--bg-tertiary)", color: backendDir.localBackendRunning ? "var(--success)" : "var(--text-tertiary)" }}>
                {backendDir.localBackendRunning ? "本地后端运行中" : "本地后端未运行"}
              </span>
              <span className="px-2 py-1 rounded-full" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>
                {backendDir.source === "configured" ? "用户指定位置" : "安装目录默认位置"}
              </span>
            </div>
            <div className="text-[10.5px] mb-1" style={{ color: "var(--text-tertiary)" }}>期望位置</div>
            <div className="flex items-center gap-2">
              <div className="flex-1 px-3 py-2 rounded-lg text-[11.5px] truncate"
                   style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)" }} title={backendDir.desiredDir}>
                {backendDir.desiredDir}
              </div>
              <button onClick={async () => {
                  const r = await backendApi.chooseDataDir();
                  if (r.ok && r.dataDir) { await loadVault(); setBackendMsg("期望位置已更改；若本地后端正在运行，请重启后再使用新位置"); }
                  else if (r.error) setBackendMsg(r.error);
                }}
                className="px-3 py-2 rounded-lg text-[12px] font-medium" style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)" }}>
                更改
              </button>
              {!backendDir.isDefault && (
                <button onClick={async () => { const r = await backendApi.resetDataDir(); if (r.ok) { await loadVault(); setBackendMsg("已恢复默认期望位置；若本地后端正在运行，请重启后生效"); } }}
                  className="px-3 py-2 rounded-lg text-[12px] font-medium" style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)" }}>
                  默认
                </button>
              )}
              <button onClick={() => void loadVault()} title="重新读取运行状态"
                className="px-3 py-2 rounded-lg text-[12px] font-medium" style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)" }}>
                刷新
              </button>
            </div>
            {backendDir.localBackendRunning && (
              <div className="mt-3">
                <div className="text-[10.5px] mb-1" style={{ color: "var(--text-tertiary)" }}>当前本地后端实际使用</div>
                <div className="px-3 py-2 rounded-lg text-[11.5px] truncate" style={{ background: "var(--bg-tertiary)", color: backendDir.restartRequired ? "var(--warning)" : "var(--text-secondary)" }} title={backendDir.effectiveDir || ""}>
                  {backendDir.effectiveDir || "未报告实际数据目录"}{backendDir.restartRequired ? "（等待重启切换）" : ""}
                </div>
              </div>
            )}
            <div className="text-[10.5px] mt-2" style={{ color: backendMsg ? "var(--accent)" : "var(--text-tertiary)" }}>
              {backendMsg || (backendDir.localBackendRunning
                ? "这里显示本机 sidecar 的真实数据目录；连接远程服务器时，远程数据不在本机 ProjectVault。"
                : "本地后端未运行时只展示期望位置，不会把尚未创建的目录误报为正在使用。")}
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
  const [newPw, setNewPw] = useState("");
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);

  async function changePw() {
    if (newPw.length < 8 || !/[A-Za-z]/.test(newPw) || !/\d/.test(newPw)) {
      setMsg("密码至少 8 位，并同时包含字母和数字"); return;
    }
    setSaving(true); setMsg("");
    try {
      await api.changeMyPassword(newPw);
      setMsg("密码已修改；其他设备的旧会话已失效，当前设备已接收新会话"); setNewPw("");
    } catch (error) { setMsg((error as Error)?.message || "修改失败，当前会话未变更"); }
    setSaving(false);
  }

  return (
    <div>
      <div className="text-[13px] font-medium mb-3" style={{ color: "var(--text-primary)" }}>修改密码</div>
      <div className="space-y-2.5 mb-3">
        <div className="relative">
          <input type={showPw ? "text" : "password"} value={newPw} onChange={e => setNewPw(e.target.value)}
            placeholder="新密码（至少 8 位，包含字母和数字）"
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
      const name = displayName.trim();
      if (!name) throw new Error("姓名不能为空");
      await api.updateMyAccountProfile(name);
      const newUser = { ...user!, display_name: name };
      useStore.setState({ user: newUser });
      localStorage.setItem("hmm_user", JSON.stringify(newUser));
      setSaved(true); setEditing(false);
      setTimeout(() => setSaved(false), 2000);
    } catch (error) { alert((error as Error)?.message || "保存失败，未取得服务端回执"); }
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
  const [version, setVersion] = useState("");
  const [platform, setPlatform] = useState("");
  const [checking, setChecking] = useState(false);
  const [updateMsg, setUpdateMsg] = useState("");

  useEffect(() => {
    const desktop = getDesktop() as unknown as {
      appVersion?: () => Promise<string>;
      platform?: string;
    } | null;
    setPlatform(desktop?.platform || "");
    desktop?.appVersion?.().then((value) => setVersion(value || "")).catch(() => setVersion(""));
  }, []);

  async function checkUpdate() {
    const desktop = getDesktop() as unknown as {
      checkUpdate?: () => Promise<{ ok: boolean; message: string }>;
    } | null;
    if (!desktop?.checkUpdate) {
      setUpdateMsg("当前环境不支持自动检查更新");
      return;
    }
    setChecking(true);
    setUpdateMsg("");
    try {
      const result = await desktop.checkUpdate();
      setUpdateMsg(result?.message || (result?.ok ? "已是最新版本" : "检查更新失败"));
    } catch (error) {
      setUpdateMsg(`检查更新失败：${(error as Error)?.message || String(error)}`);
    } finally {
      setChecking(false);
    }
  }

  const platformName = platform === "win32" ? "Windows" : platform === "darwin" ? "macOS" : platform || "浏览器";
  const runtimeInfo: [string, string][] = [
    ["当前模型", stats?.active_model || "—"],
    ["模型服务", stats?.llm_ready ? "已连接" : "未配置"],
    ["已索引内容", stats?.total_chunks?.toLocaleString() || "—"],
    ["索引占用", stats?.index_size_kb ? `${stats.index_size_kb} KB` : "—"],
    ["当前账号", `${user?.display_name || user?.username || "—"} · ${user?.role === "admin" ? "管理员" : "成员"}`],
  ];
  return (
    <div className="max-w-[720px]">
      <div className="rounded-2xl p-5 mb-7 flex items-center gap-4"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
        <div className="w-16 h-16 rounded-2xl flex items-center justify-center flex-shrink-0"
          style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
          <HashMascot size={64} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-[18px] font-semibold" style={{ color: "var(--text-primary)" }}>HashMM</div>
          <div className="text-[12px] mt-1" style={{ color: "var(--text-secondary)" }}>面向真实任务的 RAG Agent 工作空间</div>
          <div className="text-[11px] mt-1" style={{ color: "var(--text-tertiary)" }}>对话、长任务、知识检索与本机能力在同一条可审计链路中协作</div>
        </div>
      </div>

      <SettingSection title="版本与更新">
        <SettingRow label="当前版本" desc={`${platformName} 桌面版`}>
          <span className="text-[12px] font-mono" style={{ color: "var(--text-primary)" }}>{version ? `v${version}` : "—"}</span>
        </SettingRow>
        <SettingRow label="检查更新" desc="通过桌面端受控更新通道检查新版本" border={false}>
          <button onClick={checkUpdate} disabled={checking}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-medium disabled:opacity-60"
            style={{ color: "var(--text-primary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
            {checking ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
            {checking ? "正在检查" : "检查更新"}
          </button>
        </SettingRow>
        {updateMsg && <div className="pb-3 text-[11px]" style={{ color: "var(--text-secondary)" }}>{updateMsg}</div>}
      </SettingSection>

      <SettingSection title="当前工作空间">
        {runtimeInfo.map(([label, value], index) => (
          <SettingRow key={label} label={label} border={index < runtimeInfo.length - 1}>
            <span className="text-[12px] font-mono" style={{ color: "var(--text-primary)" }}>{value}</span>
          </SettingRow>
        ))}
      </SettingSection>

      <div className="rounded-xl px-4 py-3 text-[11px] leading-relaxed"
        style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
        HashMM 的本地文件和执行能力保留在当前设备；模型请求发送到你配置的模型服务。涉及文件、命令或电脑操作时，仍受权限确认和审计策略约束。
      </div>
      <div className="pt-5 flex justify-center gap-4 text-[11px]">
        <a href="/terms" target="_blank" className="underline" style={{ color: "var(--text-tertiary)" }}>服务条款</a>
        <a href="/privacy" target="_blank" className="underline" style={{ color: "var(--text-tertiary)" }}>隐私政策</a>
      </div>
    </div>
  );
}

/**
 * 用户能力设置：能力在这里被“配置”，真正执行仍由当前 Chat 的工作方式
 * 按钮触发。这样用户不需要理解 Agent、路由或内部工具名，也不会误以为
 * 打开一个开关就会在后台替他执行任务。
 */
function WorkspaceSettingsTab() {
  const user = useStore(s => s.user);
  const set = useStore(s => s.set);
  const [activeId, setActiveId] = useState(() => readActiveProject(useStore.getState().user));
  const projects = readAccountProjects(user);
  const active = projects.find(project => project.id === activeId) || null;
  const sources = active ? readProjectSources(user, active.id) : [];
  const leaveProject = () => {
    writeActiveProject(user, "");
    setActiveId("");
  };
  return (
    <div>
      <SettingSection title="项目与对话">
        <SettingRow label="当前项目" desc={active ? "新对话会进入这个项目，相关记录仍在左侧项目下继续。" : "当前是普通对话；从左侧选择项目即可进入项目 Chat。"}>
          <div className="flex items-center gap-2">
            <span className="max-w-[170px] truncate text-[11px] font-medium" style={{ color: active ? "var(--text-primary)" : "var(--text-tertiary)" }}>{active?.name || "未选择"}</span>
            <button onClick={() => set({ setOpen: false, desktopView: "gworkspace" })} className="rounded-lg px-2.5 py-1.5 text-[11px]" style={{ color: "var(--text-secondary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>管理项目</button>
          </div>
        </SettingRow>
        <SettingRow label="项目资料文件夹" desc="路径仅保存在这台电脑；第一个文件夹是文件和电脑操作的默认边界。">
          <span className="text-[11px]" style={{ color: sources.length ? "var(--text-primary)" : "var(--text-tertiary)" }}>{sources.length ? `${sources.length} 个本地文件夹` : "尚未绑定"}</span>
        </SettingRow>
        <SettingRow label="离开当前项目" desc="不会删除项目、对话或本地资料；下一条新对话将不再自动归入该项目。" border={false}>
          <button onClick={leaveProject} disabled={!activeId} className="rounded-lg px-2.5 py-1.5 text-[11px] disabled:opacity-40" style={{ color: "var(--text-secondary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>退出项目</button>
        </SettingRow>
      </SettingSection>
      <SettingSection title="本地连续性">
        <SettingRow label="切换账号时隔离" desc="项目目录、最近对话与草稿按账号分别保存；切回账号后可以继续原来的本地工作。">
          <span className="text-[11px]" style={{ color: "var(--success, #15803d)" }}>已启用</span>
        </SettingRow>
        <SettingRow label="按变化更新" desc="看过的项目和对话先从本地恢复，服务器数据发生变化时再更新缓存。" border={false}>
          <span className="text-[11px]" style={{ color: "var(--success, #15803d)" }}>已启用</span>
        </SettingRow>
      </SettingSection>
    </div>
  );
}

function BrowserSettingsTab() {
  const set = useStore(s => s.set);
  const [enabled, setEnabled] = useState(true);
  const [submitConfirmation, setSubmitConfirmation] = useState("always");
  const { items, status, persist } = useAccountSettingControl(["browser.external_submit_confirmation"]);
  const browser = getBrowser();
  useEffect(() => {
    try { setEnabled(localStorage.getItem("hmm_cap_browser") !== "0"); } catch { /* safe default */ }
    const value = items["browser.external_submit_confirmation"]?.effective_value;
    if (value === "always" || value === "sensitive") setSubmitConfirmation(value);
  }, [items]);
  const toggle = (value: boolean) => {
    setEnabled(value);
    writeCapabilityPref("browser", value);
  };
  return (
    <div>
      <SettingSection title="受控浏览器">
        <SettingRow label="浏览器" desc="在右侧打开网页、读取公开内容并把来源带回当前 Chat。">
          <div className="flex items-center gap-2">
            <button onClick={() => set({ setOpen: false, adminOpen: false, desktopView: null, pendingRunMode: "browser" })} disabled={!enabled}
              className="rounded-lg px-2.5 py-1.5 text-[11px]" style={{ color: "var(--accent)", background: "var(--accent-light)" }}>在 Chat 中打开</button>
            <Toggle checked={enabled} onChange={toggle} />
          </div>
        </SettingRow>
        <SettingRow label="浏览器引擎" desc="桌面端使用隔离的内置页面；网页版会回到可用的浏览器能力。">
          <span className="text-[11px]" style={{ color: browser ? "var(--success, #15803d)" : "var(--text-tertiary)" }}>{browser ? "桌面端已接通" : "当前环境不可用"}</span>
        </SettingRow>
        <SettingRow label="外部提交确认" desc="控制表单、发布和外部写入前的确认边界" border={false}>
          <Select value={submitConfirmation} onChange={value => { setSubmitConfirmation(value); void persist("browser.external_submit_confirmation", value); }} options={[
            { value: "always", label: "始终确认" }, { value: "sensitive", label: "敏感提交确认" },
          ]} />
        </SettingRow>
      </SettingSection>
      {status && <div className="mb-3 text-[10px]" style={{ color: status.includes("已应用") ? "var(--success)" : "var(--text-tertiary)" }}>{status}</div>}
      <div className="rounded-xl px-4 py-3 text-[11px] leading-5" style={{ color: "var(--text-secondary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
        浏览器的页面内容是外部数据，不会覆盖 HashMM 的系统规则；每次打开、读取和确认都会留在当前对话的证据链中。
      </div>
    </div>
  );
}

function ComputerSettingsTab() {
  const set = useStore(s => s.set);
  const computer = getCU();
  const [enabled, setEnabled] = useState(true);
  const [safety, setSafety] = useState<"normal" | "readonly" | "strict">("normal");
  const { items, status, persist } = useAccountSettingControl(["computer.mode"]);
  useEffect(() => {
    try { setEnabled(localStorage.getItem("hmm_cap_computer") !== "0"); } catch { /* safe default */ }
    void computer?.getSafety?.().then(result => { if (result?.ok && result.level) setSafety(result.level); }).catch(() => {});
  }, []);
  useEffect(() => {
    const mode = items["computer.mode"]?.effective_value;
    if (mode === "read_only") setSafety("readonly");
    if (mode === "standard") setSafety("normal");
    if (mode === "strict") setSafety("strict");
  }, [items]);
  const toggle = (value: boolean) => {
    setEnabled(value);
    writeCapabilityPref("computer", value);
  };
  const updateSafety = async (value: "normal" | "readonly" | "strict") => {
    setSafety(value);
    await computer?.setSafety?.(value);
    await persist("computer.mode", value === "readonly" ? "read_only" : value === "normal" ? "standard" : "strict");
  };
  return (
    <div>
      <SettingSection title="电脑操作">
        <SettingRow label="电脑操作" desc="在当前设备上读取文件、操作应用或完成重复步骤，执行前按确认策略停下。">
          <div className="flex items-center gap-2">
            <button onClick={() => set({ setOpen: false, adminOpen: false, desktopView: null, pendingRunMode: "computer" })} disabled={!enabled || !computer}
              className="rounded-lg px-2.5 py-1.5 text-[11px]" style={{ color: "var(--accent)", background: "var(--accent-light)" }}>在 Chat 中使用</button>
            <Toggle checked={enabled} onChange={toggle} />
          </div>
        </SettingRow>
        <SettingRow label="当前安全级别" desc="只读不会修改文件；标准模式仍会在有副作用的动作前询问。">
          <Select value={safety} onChange={value => void updateSafety(value as "normal" | "readonly" | "strict")} options={[
            { value: "readonly", label: "只读观察" },
            { value: "normal", label: "标准确认" },
            { value: "strict", label: "严格确认" },
          ]} />
        </SettingRow>
        <SettingRow label="工作目录" desc="项目绑定的第一个本地资料文件夹会成为当前边界；没有项目时使用设备工作区。" border={false}>
          <button onClick={() => set({ setOpen: false, desktopView: "workbench" })} className="rounded-lg px-2.5 py-1.5 text-[11px]" style={{ color: "var(--text-secondary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>查看工作区</button>
        </SettingRow>
      </SettingSection>
      {status && <div className="mb-3 text-[10px]" style={{ color: status.includes("已应用") ? "var(--success)" : "var(--text-tertiary)" }}>{status}</div>}
      <div className="rounded-xl px-4 py-3 text-[11px] leading-5" style={{ color: "var(--text-secondary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
        HashMM 不会把桌面端权限变成服务器权限。文件、命令和电脑操作仍由桌面端窄权限桥接，并写入可回看的操作记录。
      </div>
    </div>
  );
}

function CapabilitiesTab() {
  const set = useStore(s => s.set);
  const [browser, setBrowser] = useState(true);
  const [computer, setComputer] = useState(true);
  const [canvas, setCanvas] = useState(true);
  const [team, setTeam] = useState(true);
  const [approval, setApproval] = useState<"ask" | "workspace">("ask");
  const [approvalRevision, setApprovalRevision] = useState(0);
  const [approvalMessage, setApprovalMessage] = useState("");

  useEffect(() => {
    try {
      setBrowser(localStorage.getItem("hmm_cap_browser") !== "0");
      setComputer(localStorage.getItem("hmm_cap_computer") !== "0");
      setCanvas(localStorage.getItem("hmm_cap_canvas") !== "0");
      setTeam(localStorage.getItem("hmm_cap_team") !== "0");
    } catch { /* 首次启动或受限存储时使用安全默认值 */ }
    api.listMyAccountSettings().then(result => {
      const item = result.settings.find(setting => setting.key === "agent.approval_mode");
      if (item?.effective_value === "ask" || item?.effective_value === "workspace") setApproval(item.effective_value);
      setApprovalRevision(item?.revision || 0);
    }).catch(error => setApprovalMessage((error as Error)?.message || "账号审批偏好暂时无法读取"));
  }, []);

  const toggle = (capability: ChatCapability, value: boolean, update: (next: boolean) => void) => {
    update(value);
    writeCapabilityPref(capability, value);
  };
  const tryInChat = (mode: "browser" | "computer" | "canvas" | "team") => {
    set({ setOpen: false, adminOpen: false, desktopView: null, pendingRunMode: mode });
  };
  const updateApproval = async (value: "ask" | "workspace") => {
    setApprovalMessage("正在保存…");
    try {
      const result = await api.updateMyAccountSettings([{ key: "agent.approval_mode", value, revision: approvalRevision }]);
      const item = result.settings.find(setting => setting.key === "agent.approval_mode");
      setApproval(value); setApprovalRevision(item?.revision || approvalRevision + 1);
      setApprovalMessage(`已应用 · 回执 ${result.receipt.id}`);
    } catch (error) {
      setApprovalMessage((error as Error)?.message || "保存失败；审批策略未变更");
    }
  };

  return (
    <div>
      <SettingSection title="在 Chat 中使用">
        <SettingRow label="受控浏览器" desc="在右侧打开网页、读取公开内容；每次动作都会回到当前对话并保留证据。">
          <div className="flex items-center gap-2">
            <button onClick={() => tryInChat("browser")} disabled={!browser} className="px-2.5 py-1.5 rounded-lg text-[11px]" style={{ color: "var(--accent)", background: "var(--accent-light)" }}>在 Chat 中试用</button>
            <Toggle checked={browser} onChange={value => toggle("browser", value, setBrowser)} />
          </div>
        </SettingRow>
        <SettingRow label="电脑操作" desc="需要桌面端时才会启用；执行前按你的确认策略停下，完成后给出操作记录。">
          <div className="flex items-center gap-2">
            <button onClick={() => tryInChat("computer")} disabled={!computer} className="px-2.5 py-1.5 rounded-lg text-[11px]" style={{ color: "var(--accent)", background: "var(--accent-light)" }}>在 Chat 中试用</button>
            <Toggle checked={computer} onChange={value => toggle("computer", value, setComputer)} />
          </div>
        </SettingRow>
        <SettingRow label="工作画布" desc="把 Chat 的结果变成可编辑成果；版本、来源和后续修改仍属于原会话。">
          <div className="flex items-center gap-2">
            <button onClick={() => tryInChat("canvas")} disabled={!canvas} className="px-2.5 py-1.5 rounded-lg text-[11px]" style={{ color: "var(--accent)", background: "var(--accent-light)" }}>从 Chat 创建</button>
            <Toggle checked={canvas} onChange={value => toggle("canvas", value, setCanvas)} />
          </div>
        </SettingRow>
        <SettingRow label="多智能体协作" desc="只在任务确实适合分工时启用；你可以先审核角色和交接标准，再启动。"
          border={false}>
          <div className="flex items-center gap-2">
            <button onClick={() => tryInChat("team")} disabled={!team} className="px-2.5 py-1.5 rounded-lg text-[11px]" style={{ color: "var(--accent)", background: "var(--accent-light)" }}>在 Chat 中组织</button>
            <Toggle checked={team} onChange={value => toggle("team", value, setTeam)} />
          </div>
        </SettingRow>
      </SettingSection>

      <SettingSection title="执行边界">
        <SettingRow label="默认确认方式" desc="浏览器、电脑和文件操作不会因为打开能力就自动执行。">
          <Select value={approval} onChange={value => void updateApproval(value as "ask" | "workspace")} options={[
            { value: "ask", label: "每次需要时确认" },
            { value: "workspace", label: "仅限当前工作区" },
          ]} />
        </SettingRow>
        {approvalMessage && <div className="pb-3 text-[10px]" style={{ color: approvalMessage.includes("已应用") ? "var(--success)" : "var(--text-tertiary)" }}>{approvalMessage}</div>}
        <SettingRow label="来源与操作记录" desc="对话会显示来源、实际打开页面和已执行步骤；关闭能力不会删除已有记录。" border={false}>
          <span className="text-[11px]" style={{ color: "var(--success, #15803d)" }}>默认保留</span>
        </SettingRow>
      </SettingSection>

      <div className="rounded-xl px-4 py-3 text-[11px] leading-5" style={{ color: "var(--text-secondary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
        能力开关只控制当前设备上的入口，不会绕过服务端权限、账号身份或管理员审核。需要外部插件时，请到“插件”页面完成接通；个人工作方法请到“技能”页面管理。
      </div>
    </div>
  );
}

/* ━━━━━━━━━━━━━━━ Main ━━━━━━━━━━━━━━━ */

function WorktreesSettingsTab() {
  const user = useStore(s => s.user);
  const [projects, setProjects] = useState<api.WorkProject[]>(() => readAccountProjects(user));
  const [projectId, setProjectId] = useState(() => readActiveProject(user));
  const [cwd, setCwd] = useState("");
  const [repo, setRepo] = useState<RepoInspection | null>(null);
  const [state, setState] = useState<"loading" | "no_desktop" | "no_project" | "no_binding" | "invalid" | "ready">("loading");
  const [detail, setDetail] = useState("");
  const [binding, setBinding] = useState(false);
  const { items, status, persist } = useAccountSettingControl(["worktrees.auto_cleanup", "worktrees.keep_limit"]);
  const [autoCleanup, setAutoCleanup] = useState(false);
  const [keepLimit, setKeepLimit] = useState(15);

  useEffect(() => {
    if (typeof items["worktrees.auto_cleanup"]?.effective_value === "boolean") {
      setAutoCleanup(Boolean(items["worktrees.auto_cleanup"].effective_value));
    }
    const nextLimit = Number(items["worktrees.keep_limit"]?.effective_value);
    if (Number.isFinite(nextLimit) && nextLimit >= 1 && nextLimit <= 100) setKeepLimit(nextLimit);
  }, [items]);

  useEffect(() => {
    let active = true;
    api.listWorkProjects().then(rows => {
      if (!active) return;
      const visible = rows.filter(project => !project.archived);
      setProjects(visible);
      setProjectId(current => current && visible.some(project => project.id === current)
        ? current : (visible[0]?.id || ""));
    }).catch(() => { /* account-scoped cache remains a safe navigation fallback */ });
    return () => { active = false; };
  }, [user?.id, user?.username]);

  const inspect = useCallback(async (selectedProjectId: string) => {
    const local = getLocal();
    if (!local?.gitInspect || !getProjectDesktop()) {
      setCwd(""); setRepo(null); setState("no_desktop");
      setDetail("Worktree 需要 HashMM 桌面端的窄权限 Git 桥；网页端不会获得本机路径权限。");
      return;
    }
    if (!selectedProjectId) {
      setCwd(""); setRepo(null); setState("no_project");
      setDetail("先选择一个项目，再为当前设备绑定真实 Git 工作目录。");
      return;
    }
    const source = readProjectSources(user, selectedProjectId)[0] || "";
    setCwd(source);
    if (!source) {
      setRepo(null); setState("no_binding");
      setDetail("这个项目尚未绑定当前设备的工作目录。绝对路径只保存在本设备，不会上传到服务器。");
      return;
    }
    setState("loading"); setDetail("正在验证目录、Git 根和工作树状态…");
    try {
      const result = await local.gitInspect(source);
      setRepo(result);
      if (!result.ok || !result.is_git) {
        setState("invalid");
        setDetail(result.error || "绑定目录存在，但不是可用的 Git 仓库。HashMM 不会自动执行 git init。");
      } else {
        setState("ready");
        setDetail(`已验证 Git 根：${result.root || source}`);
      }
    } catch (error) {
      setRepo(null); setState("invalid");
      setDetail((error as Error)?.message || "无法访问绑定目录；它可能已移动或当前账号没有权限。");
    }
  }, [user]);

  useEffect(() => { void inspect(projectId); }, [inspect, projectId]);
  useEffect(() => {
    const onSelected = (event: Event) => {
      const next = String((event as CustomEvent<{ projectId?: string }>).detail?.projectId || "");
      if (next) setProjectId(next);
    };
    window.addEventListener("hmm-project-selected", onSelected);
    return () => window.removeEventListener("hmm-project-selected", onSelected);
  }, []);

  const selectProject = (next: string) => {
    setProjectId(next); writeActiveProject(user, next);
  };
  const bindFolder = async () => {
    const bridge = getProjectDesktop();
    if (!bridge || !projectId || binding) return;
    setBinding(true); setDetail("正在选择并验证本设备目录…");
    try {
      const result = await bridge.pickSourceFolders();
      const selected = result.paths?.[0] || "";
      if (!result.ok || !selected) {
        if (!result.canceled) setDetail(result.error || "没有选择可用目录。");
        return;
      }
      writeProjectSources(user, projectId, result.paths || [selected]);
      writeActiveProject(user, projectId);
      const activated = await bridge.activateSource(selected);
      if (!activated.ok) throw new Error(activated.error || "目录绑定成功，但无法启用为当前设备工作区。");
      await inspect(projectId);
    } catch (error) {
      setState("invalid"); setDetail((error as Error)?.message || "绑定项目目录失败。");
    } finally { setBinding(false); }
  };

  return <div>
    <SettingSection title="项目与当前设备">
      <SettingRow label="项目" desc="项目归属来自服务器；本机绝对路径按账号与项目隔离，只保存在当前设备。">
        <Select value={projectId} onChange={selectProject} options={projects.length
          ? projects.map(project => ({ value: project.id, label: project.name }))
          : [{ value: "", label: "尚无项目" }]} />
      </SettingRow>
      <SettingRow label="Git 工作目录" desc={cwd || detail} border={false}>
        <div className="flex items-center gap-2">
          {state === "ready" && <span className="text-[10px]" style={{ color: "var(--success,#15803d)" }}>已验证</span>}
          <button onClick={() => void bindFolder()} disabled={!projectId || state === "no_desktop" || binding}
            className="px-3 py-1.5 rounded-lg text-[11px] disabled:opacity-40" style={{ border: "1px solid var(--border)", color: "var(--accent)" }}>
            {binding ? "验证中…" : cwd ? "重新绑定" : "选择文件夹"}
          </button>
          {cwd && <button onClick={() => void inspect(projectId)} className="p-1.5 rounded-lg" title="重新检查"><RefreshCw size={13} /></button>}
        </div>
      </SettingRow>
    </SettingSection>

    <SettingSection title="安全保留策略">
      <SettingRow label="自动清理" desc="默认关闭；启用后也只处理通过 tracked、untracked、ignored、锁定和 detached 提交检查的 HashMM 托管工作树。">
        <Toggle checked={autoCleanup} onChange={value => { setAutoCleanup(value); void persist("worktrees.auto_cleanup", value); }} />
      </SettingRow>
      <SettingRow label="保留数量" desc="超过数量时只回收安全且非活动、非永久的托管工作树。" border={false}>
        <input type="number" min={1} max={100} value={keepLimit} onChange={event => setKeepLimit(Math.max(1, Math.min(100, Number(event.target.value) || 1)))}
          onBlur={() => void persist("worktrees.keep_limit", keepLimit)} className="w-20 h-8 px-2 rounded-lg text-[12px] outline-none"
          style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
      </SettingRow>
    </SettingSection>
    {status && <div className="mb-3 text-[10px]" style={{ color: status.includes("已应用") ? "var(--success)" : "var(--text-tertiary)" }}>{status}</div>}
    {state === "loading" && <div className="flex items-center gap-2 py-10 text-[12px]" style={{ color: "var(--text-tertiary)" }}><Loader2 size={14} className="animate-spin" />{detail || "正在核对当前项目仓库"}</div>}
    {state !== "loading" && state !== "ready" && <div className="rounded-2xl p-5" style={{ border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
      <div className="text-[13px] font-medium">{state === "no_desktop" ? "当前环境不提供本机 Git 能力" : state === "no_project" ? "尚未选择项目" : state === "no_binding" ? "尚未绑定当前设备目录" : "项目目录需要修复"}</div>
      <div className="mt-1 text-[11px] leading-5" style={{ color: "var(--text-tertiary)" }}>{detail}</div>
    </div>}
    {state === "ready" && <WorktreePanel cwd={cwd} repo={repo} onActivated={setCwd} keepLimit={keepLimit} autoCleanup={autoCleanup} />}
  </div>;
}

export function SettingsModal() {
  const set = useStore(s => s.set);
  const settingsTab = useStore(s => s.settingsTab) as TabId;
  const [tab, setTab] = useState<TabId>(settingsTab || "general");
  const [query, setQuery] = useState("");

  // Sync when opened from UserMenu with a specific tab
  useEffect(() => { if (settingsTab) setTab(settingsTab as TabId); }, [settingsTab]);

  const TAB_CONTENT: Record<TabId, React.ReactNode> = {
    general: <GeneralTab />, workspace: <WorkspaceSettingsTab />, notification: <NotificationTab />, personalize: <PersonalizeTab />,
    skills: <UserSkillsSettings />, browser: <BrowserSettingsTab />, computer: <ComputerSettingsTab />, capabilities: <CapabilitiesTab />,
    data: <DataTab />, storage: <StorageTab />, security: <SecurityTab />, apiaccess: <ApiAccessSettings />,
    account: <AccountTab />, shortcuts: <ShortcutsTab />, about: <AboutTab />, mymodels: <MyModelsTab />,
    worktrees: <WorktreesSettingsTab />, usage: <UsageView />,
  };
  const q = query.trim().toLowerCase();
  const visibleGroups = TAB_GROUPS.map(group => ({
    ...group,
    ids: group.ids.filter(id => {
      if (!q) return true;
      const item = TABS.find(x => x.id === id)!;
      return `${item.label} ${TAB_DESCRIPTIONS[id]} ${TAB_SEARCH_TERMS[id]}`.toLowerCase().includes(q);
    }),
  })).filter(group => group.ids.length > 0);
  const activeLabel = TABS.find(t => t.id === tab)?.label || "设置";
  const activeItem = TABS.find(t => t.id === tab) || TABS[0];
  const ActiveIcon = activeItem.icon;

  return (
    <div className="flex-1 min-w-0 min-h-0 flex" style={{ background: "var(--bg-primary)" }}>
        <aside className="w-[268px] flex-shrink-0 flex flex-col min-h-0"
          style={{ background: "var(--bg-secondary)", borderRight: "1px solid var(--border)" }}>
          <div className="px-4 pt-3 pb-2">
            <button onClick={() => set({ setOpen: false })}
              className="flex items-center gap-1.5 px-1.5 py-1.5 mb-3 rounded-lg text-[12px] transition-colors hover:bg-[var(--bg-tertiary)]"
              style={{ color: "var(--text-secondary)" }}>
              <ArrowLeft size={14} /> 返回应用
            </button>
            <div className="px-1 pb-3">
              <div className="text-[17px] font-semibold" style={{ color: "var(--text-primary)" }}>设置</div>
              <div className="text-[11px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>HashMM 桌面与账号偏好</div>
            </div>
            <label className="h-9 px-2.5 flex items-center gap-2 rounded-lg"
              style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
              <Search size={14} style={{ color: "var(--text-tertiary)" }} />
              <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索设置..."
                className="flex-1 min-w-0 bg-transparent outline-none text-[12px]" style={{ color: "var(--text-primary)" }} />
            </label>
          </div>
          <nav className="flex-1 min-h-0 overflow-y-auto px-3 pb-4">
          {visibleGroups.map(g => (
            <div key={g.title} className="mb-2">
              <div className="px-2 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wide" style={{ color: "var(--text-tertiary)" }}>{g.title}</div>
              {g.ids.map(id => {
                const t = TABS.find(x => x.id === id)!;
                const Icon = t.icon;
                const active = tab === id;
                return (
                  <button key={id} onClick={() => { setTab(id); set({ settingsTab: id }); }}
                    className="relative w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-left transition-all"
                    style={{ background: active ? "var(--accent-light)" : "transparent", color: active ? "var(--accent)" : "var(--text-secondary)" }}>
                    {active && <span className="absolute left-[3px] top-1/2 -translate-y-1/2 w-[3px] h-[20px] rounded-full" style={{ background: "var(--accent)" }} />}
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0" style={{ background: active ? "color-mix(in srgb, var(--accent) 11%, var(--bg-primary))" : "var(--bg-primary)", border: "1px solid var(--border)" }}><Icon size={14} /></div>
                    <div className="min-w-0"><div className="text-[12.5px] font-medium">{t.label}</div><div className="text-[10px] mt-0.5 truncate" style={{ color: active ? "color-mix(in srgb, var(--accent) 68%, var(--text-tertiary))" : "var(--text-tertiary)" }}>{TAB_DESCRIPTIONS[id]}</div></div>
                  </button>
                );
              })}
            </div>
          ))}
          {query && visibleGroups.length === 0 && <div className="px-2 py-8 text-center text-[11px]" style={{ color: "var(--text-tertiary)" }}>没有匹配的设置</div>}
          </nav>
        </aside>
        <main className="flex-1 flex flex-col min-w-0 min-h-0" style={{ background: "var(--bg-secondary)" }}>
          <div className="flex-1 overflow-y-auto">
            <div className="w-full max-w-[940px] mx-auto px-8 md:px-12 py-8">
              <div className="mb-7 pb-4 flex items-end gap-4" style={{ borderBottom: "1px solid var(--border)" }}>
                <div className="min-w-0 flex-1">
                  <div className="text-[10.5px] mb-1.5" style={{ color: "var(--text-tertiary)" }}>设置 / {activeLabel}</div>
                  <div className="flex items-center gap-2.5"><ActiveIcon size={17} style={{ color: "var(--accent)" }} /><h1 className="text-[20px] font-semibold tracking-[-0.02em]" style={{ color: "var(--text-primary)" }}>{activeLabel}</h1></div>
                  <p className="mt-1.5 text-[12px] leading-5" style={{ color: "var(--text-tertiary)" }}>{TAB_DESCRIPTIONS[tab]}</p>
                </div>
                <span className="pb-0.5 text-[10.5px] flex-shrink-0" style={{ color: "var(--text-tertiary)" }}>{TAB_SCOPE[tab]}</span>
              </div>
              <div className="settings-page-enter">{TAB_CONTENT[tab]}</div>
            </div>
          </div>
        </main>
    </div>
  );
}


/** 我的模型（V261）：普通用户添加自己的 API 模型并选用——回答用你自己的 Key。 */
function MyModelsTab() {
  const [list, setList] = useState<MyModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [providers, setProviders] = useState<ProviderSpec[]>([]);
  const [form, setForm] = useState({
    name: "", base_url: "https://api.openai.com/v1", api_key: "",
    model_name: "", provider: "openai", wire_api: "responses" as ModelWireApi,
  });
  const selectedProvider = providers.find(p => p.id === form.provider);

  const pull = () => myModels().then(r => { setList(r.models || []); setLoading(false); })
    .catch(() => { setErr("加载失败：后端版本过旧，请升级后端"); setLoading(false); });
  useEffect(() => {
    pull();
    userModelProviders()
      .then(result => setProviders(result.providers || []))
      .catch(() => setProviders(PROVIDERS.map(p => ({
        id: p.id, name: p.name, base_url: p.url,
        wire_apis: p.id === "anthropic" ? ["anthropic_messages"] : ["chat_completions"],
        default_wire_api: p.id === "anthropic" ? "anthropic_messages" : "chat_completions",
        auth: ["ollama", "lmstudio", "vllm"].includes(p.id) ? "optional" : "bearer",
        local: ["ollama", "lmstudio", "vllm"].includes(p.id),
        base_url_required: !p.url, endpoint_note: "", model_hints: [],
        capabilities: { tools: true, streaming: true, vision: true, json_schema: false, model_discovery: true },
      } as ProviderSpec))));
  }, []);

  async function add() {
    if (!form.name.trim() || !form.base_url.trim() || !form.model_name.trim()) {
      setErr("名称、Base URL 和模型 ID 为必填"); return;
    }
    if (selectedProvider?.auth !== "optional" && !form.api_key.trim()) {
      setErr("该服务商需要 API Key"); return;
    }
    setErr(""); setBusy(true);
    try {
      await addMyModel(form);
      setForm({ name: "", base_url: "https://api.openai.com/v1", api_key: "", model_name: "", provider: "openai", wire_api: "responses" });
      pull();
    } catch (e) { setErr((e as Error)?.message || "添加失败"); }
    finally { setBusy(false); }
  }

  async function prefer(id: string, on: boolean) {
    setList(ls => ls.map(m => ({ ...m, is_preferred: on ? m.id === id : false })));
    try { await preferMyModel(on ? id : ""); } catch { pull(); }
  }

  async function del(id: string) {
    if (!confirm("删除这个模型？")) return;
    try { await delMyModel(id); pull(); } catch (e) { setErr((e as Error)?.message || "删除失败"); }
  }

  const inputCls = "w-full px-3 py-2 rounded-xl text-[12.5px] outline-none";
  const inputStyle = { background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" } as React.CSSProperties;

  return (
    <div className="space-y-6">
      <ProviderFabricPanel />
      <details className="rounded-2xl p-4" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
        <summary className="cursor-pointer text-[12.5px] font-semibold" style={{ color: "var(--text-primary)" }}>
          兼容模式：直接添加单一模型
        </summary>
        <div className="mt-4 space-y-4">
      <div className="text-[11.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
        添加你自己的主流模型 API，选用后 Chat、资料问答和长任务会走同一能力契约；
        不选则用系统默认模型。Key 只存服务器、界面脱敏显示。
      </div>
      {/* 添加表单 */}
      <div className="rounded-2xl p-4 space-y-2.5" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
        <div className="text-[12.5px] font-bold" style={{ color: "var(--text-primary)" }}>添加模型</div>
        <div className="grid grid-cols-2 gap-2.5">
          <select value={form.provider} onChange={e => {
            const provider = providers.find(p => p.id === e.target.value);
            setForm(f => ({
              ...f,
              provider: e.target.value,
              base_url: provider?.base_url || "",
              wire_api: provider?.default_wire_api || "chat_completions",
            }));
          }} className={inputCls} style={inputStyle}>
            {(providers.length ? providers : PROVIDERS).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <select value={form.wire_api}
            onChange={e => setForm(f => ({ ...f, wire_api: e.target.value as ModelWireApi }))}
            disabled={(selectedProvider?.wire_apis?.length || 0) <= 1}
            className={inputCls} style={inputStyle}>
            {(selectedProvider?.wire_apis || [form.wire_api]).map(wire => (
              <option key={wire} value={wire}>{wire === "responses" ? "Responses" : wire === "anthropic_messages" ? "Anthropic Messages" : "Chat Completions"}</option>
            ))}
          </select>
        </div>
        <div className="grid grid-cols-2 gap-2.5">
          <input placeholder="显示名称（如：我的 DeepSeek）" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} className={inputCls} style={inputStyle} />
          <input placeholder="模型名（如 deepseek-chat）" value={form.model_name} onChange={e => setForm(f => ({ ...f, model_name: e.target.value }))} className={inputCls} style={inputStyle} />
        </div>
        <input placeholder="Base URL（如 https://api.deepseek.com/v1）" value={form.base_url} onChange={e => setForm(f => ({ ...f, base_url: e.target.value }))} className={inputCls} style={inputStyle} />
        <input placeholder="API Key" type="password" value={form.api_key} onChange={e => setForm(f => ({ ...f, api_key: e.target.value }))} className={inputCls} style={inputStyle} />
        <div className="flex items-center justify-between">
          {err ? <span className="text-[11.5px]" style={{ color: "#b42318" }}>{err}</span> : <span />}
          <button onClick={add} disabled={busy}
            className="px-4 py-2 rounded-xl text-[12.5px] font-semibold text-white disabled:opacity-60"
            style={{ background: "var(--accent)" }}>{busy ? "添加中…" : "添加"}</button>
        </div>
      </div>
      {/* 列表 */}
      {loading ? (
        <div className="text-[12px]" style={{ color: "var(--text-tertiary)" }}>加载中…</div>
      ) : list.length === 0 ? (
        <div className="text-[12px]" style={{ color: "var(--text-tertiary)" }}>还没有添加模型——填上面的表单添加第一个。</div>
      ) : (
        <div className="space-y-2">
          {list.map(m => (
            <div key={m.id} className="rounded-xl px-4 py-3 flex items-center gap-3" style={{ background: "var(--bg-secondary)", border: "1px solid " + (m.is_preferred ? "var(--accent)" : "var(--border)") }}>
              <div className="flex-1 min-w-0">
                <div className="text-[13px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>
                  {m.name} {m.is_preferred && <span className="ml-1 text-[10px] px-1.5 py-0.5 rounded-full" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>使用中</span>}
                </div>
                <div className="text-[10.5px] truncate" style={{ color: "var(--text-tertiary)" }}>{m.model_name} · {m.base_url} · {m.api_key}</div>
              </div>
              <button onClick={() => prefer(m.id, !m.is_preferred)}
                className="px-2.5 py-1 rounded-lg text-[11.5px] font-medium"
                style={{ background: m.is_preferred ? "var(--bg-tertiary)" : "var(--accent-light)", color: m.is_preferred ? "var(--text-secondary)" : "var(--accent)", border: "1px solid var(--border)" }}>
                {m.is_preferred ? "停用" : "选用"}</button>
              <button onClick={() => del(m.id)} className="px-2.5 py-1 rounded-lg text-[11.5px]"
                style={{ color: "#b42318", border: "1px solid var(--border)" }}>删除</button>
            </div>
          ))}
        </div>
      )}
        </div>
      </details>
    </div>
  );
}

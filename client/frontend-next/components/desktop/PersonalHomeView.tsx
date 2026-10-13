"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ArrowRight, Bell, BookOpen, Brain, CheckCircle2, Clock3, Database,
  FolderKanban, Loader2, MonitorSmartphone, Puzzle, RefreshCw, Settings,
  ShieldCheck, UserRound,
} from "lucide-react";
import { getWorkspaceSnapshot, type WorkspaceSnapshotV2 } from "@/lib/api";
import { userIdFromToken } from "@/lib/supabase";
import { useStore } from "@/lib/store";

type PersonalAction = {
  title: string;
  description: string;
  icon: React.ComponentType<{ size?: number }>;
  run: () => void;
};

export function PersonalHomeView() {
  const user = useStore(state => state.user);
  const token = useStore(state => state.token);
  const set = useStore(state => state.set);
  const [snapshot, setSnapshot] = useState<WorkspaceSnapshotV2 | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async (force = false) => {
    if (!token) {
      setSnapshot(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      setSnapshot(await getWorkspaceSnapshot("personal", force));
      setError("");
    } catch (reason) {
      setError((reason as Error)?.message || "个人工作空间暂时无法更新");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { void refresh(); }, [refresh]);

  const uid = typeof window !== "undefined" ? userIdFromToken(localStorage.getItem("hmm_token")) : "";
  const avatar = typeof window !== "undefined" && uid ? localStorage.getItem(`hmm_avatar_${uid}`) : "";
  const name = user?.display_name || user?.username || "用户";
  const initial = name.slice(0, 1).toUpperCase();
  const role = user?.role === "admin" ? "管理员" : "用户";
  const needsUser = snapshot?.today.needs_user.length || 0;
  const inProgress = snapshot?.today.in_progress.length || 0;
  const recentResults = snapshot?.today.recent_results.length || 0;
  const projectCount = snapshot?.projects.length || 0;
  const onlineDevices = snapshot?.devices.online_count || 0;

  const actions: PersonalAction[] = [
    {
      title: "个人资料", description: "修改头像和显示名称，桌面端与 App 同步",
      icon: UserRound, run: () => set({ profileOpen: true }),
    },
    {
      title: "个人 Skills", description: "导入和管理自己的工作方法",
      icon: Puzzle, run: () => set({ desktopView: "plugins" }),
    },
    {
      title: "记忆与偏好", description: "查看和修正 HashMM 长期记住的信息",
      icon: Brain, run: () => set({ desktopView: "memory" }),
    },
    {
      title: "设置与隐私", description: "管理界面、通知、登录和数据选项",
      icon: Settings, run: () => set({ desktopView: null, setOpen: true, adminOpen: false, settingsTab: "general" }),
    },
  ];
  const dataActions: PersonalAction[] = [
    {
      title: "资料与知识包", description: "查看文档、可追溯知识和数据归属",
      icon: Database, run: () => set({ desktopView: "hub-knowledge" }),
    },
    {
      title: "设备接力", description: "在手机和电脑间继续对话、文件与操作",
      icon: MonitorSmartphone, run: () => set({ desktopView: "remote" }),
    },
    {
      title: "通知", description: "只提醒需要确认、完成或异常的工作",
      icon: Bell, run: () => set({ desktopView: null, setOpen: true, settingsTab: "notifications" }),
    },
  ];

  return (
    <div className="flex-1 min-h-0 overflow-y-auto" style={{ background: "var(--bg-secondary)" }}>
      <div className="mx-auto w-full max-w-[1040px] px-7 py-7">
        <header className="flex items-start gap-4">
          <div className="w-12 h-12 rounded-2xl overflow-hidden flex items-center justify-center text-[16px] font-semibold text-white"
            style={{ background: "var(--accent)" }}>
            {avatar ? <img src={avatar} alt="" className="w-full h-full object-cover" /> : initial}
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="text-[22px] font-semibold tracking-[-0.02em] truncate" style={{ color: "var(--text-primary)" }}>{name}</h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
              <span>{role}</span>
              <span>·</span>
              <span className="inline-flex items-center gap-1"><ShieldCheck size={11} /> 账号数据按用户隔离</span>
            </div>
          </div>
          <button onClick={() => void refresh(true)} disabled={loading} className="p-2 rounded-xl hover:bg-[var(--bg-tertiary)]" aria-label="刷新我的空间">
            <RefreshCw size={15} className={loading ? "animate-spin" : ""} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </header>

        {error && (
          <div className="mt-5 rounded-xl px-3.5 py-3 text-[11px]"
            style={{ color: "#b42318", background: "rgba(180,35,24,.06)", border: "1px solid rgba(180,35,24,.16)" }}>
            {error}
          </div>
        )}

        {loading && !snapshot ? (
          <div className="min-h-[320px] flex items-center justify-center gap-2 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
            <Loader2 size={16} className="animate-spin" /> 正在恢复你的工作空间
          </div>
        ) : (
          <>
            <div className="mt-7 mb-2 px-1 text-[10px] font-semibold" style={{ color: "var(--text-tertiary)" }}>我的工作</div>
            <section className="overflow-hidden rounded-2xl" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
              <button onClick={() => set({ desktopView: "work-active" })} className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-[var(--bg-secondary)]">
                <span className="flex h-8 w-8 items-center justify-center rounded-xl" style={{ color: needsUser ? "#b45309" : "#15803d", background: needsUser ? "rgba(245,158,11,.1)" : "rgba(34,197,94,.1)" }}>{needsUser ? <Clock3 size={15} /> : <CheckCircle2 size={15} />}</span>
                <span className="min-w-0 flex-1"><span className="block text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>{needsUser ? `${needsUser} 项工作需要你处理` : "目前没有等待你处理的工作"}</span><span className="mt-0.5 block text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>{inProgress} 项正在推进 · {recentResults} 项最近成果 · {projectCount} 个项目</span></span>
                <ArrowRight size={13} style={{ color: "var(--text-tertiary)" }} />
              </button>
              <div className="grid grid-cols-3" style={{ borderTop: "1px solid var(--border)" }}>
                {[
                  { label: "进行中", value: inProgress, icon: CheckCircle2, view: "work-active" },
                  { label: "成果", value: recentResults, icon: BookOpen, view: "work-results" },
                  { label: "项目", value: projectCount, icon: FolderKanban, view: "gworkspace" },
                ].map(item => <button key={item.label} onClick={() => set({ desktopView: item.view as never })} className="px-4 py-3 text-left hover:bg-[var(--bg-secondary)]" style={{ borderRight: "1px solid var(--border)" }}><span className="text-[15px] font-semibold" style={{ color: "var(--text-primary)" }}>{item.value}</span><span className="ml-1.5 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>{item.label}</span></button>)}
              </div>
            </section>

            <section className="mt-7">
              <div className="mb-2 px-1 text-[10px] font-semibold" style={{ color: "var(--text-tertiary)" }}>个人</div>
              <div className="overflow-hidden rounded-2xl" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                {actions.map(action => (
                  <button key={action.title} onClick={action.run}
                    className="group w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-[var(--bg-secondary)]"
                    style={{ borderBottom: "1px solid var(--border)" }}>
                    <span className="w-8 h-8 rounded-xl flex items-center justify-center" style={{ color: "var(--text-secondary)", background: "var(--bg-secondary)" }}>
                      <action.icon size={14} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>{action.title}</span>
                      <span className="block mt-0.5 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>{action.description}</span>
                    </span>
                    <ArrowRight size={13} className="opacity-35 transition-all group-hover:opacity-100 group-hover:translate-x-0.5" />
                  </button>
                ))}
              </div>
            </section>
            <section className="mt-6">
              <div className="mb-2 px-1 text-[10px] font-semibold" style={{ color: "var(--text-tertiary)" }}>数据与连接</div>
              <div className="overflow-hidden rounded-2xl" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                {dataActions.map(action => <button key={action.title} onClick={action.run} className="group flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-[var(--bg-secondary)]" style={{ borderBottom: "1px solid var(--border)" }}><span className="flex h-8 w-8 items-center justify-center rounded-xl" style={{ color: "var(--text-secondary)", background: "var(--bg-secondary)" }}><action.icon size={14} /></span><span className="min-w-0 flex-1"><span className="block text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>{action.title}</span><span className="mt-0.5 block text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>{action.title === "设备接力" ? `${onlineDevices ? `${onlineDevices} 台电脑在线` : "等待电脑上线"} · ${action.description}` : action.description}</span></span><ArrowRight size={13} className="opacity-35 group-hover:opacity-100" /></button>)}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}

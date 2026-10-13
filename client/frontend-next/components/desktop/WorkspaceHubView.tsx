"use client";

import { useMemo, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Activity, ArrowRight, BarChart3, Brain, BrainCircuit, Clock, Cpu,
  BookOpen, Database, FileText, FlaskConical, HardDrive, KeyRound,
  MonitorSmartphone, Network, Plug, Search, ShieldCheck, Sparkles, Terminal,
  Users,
} from "lucide-react";
import { useStore } from "@/lib/store";
import {
  canSeeProductSurface,
  productSurfaceLabel,
  type ProductAudience,
} from "@/lib/productSurface";

type HubId = "hub-knowledge" | "hub-agents" | "hub-operations" | "hub-device";
type Card = {
  id: string;
  title: string;
  desc: string;
  Icon: LucideIcon;
  view?: string;
  adminTab?: string;
  audience?: ProductAudience;
};
type Section = { title: string; desc: string; cards: Card[] };

const META: Record<HubId, { title: string; desc: string; sections: Section[] }> = {
  "hub-knowledge": {
    title: "资料与创作",
    desc: "让 HashMM 阅读你的资料、记住重要信息，并整理成可以直接使用的成果。",
    sections: [
      { title: "常用", desc: "从当前对话继续处理资料", cards: [
        { id: "docstudio", title: "处理一组文档", desc: "阅读、比较、润色并生成可交付文档", Icon: FileText, view: "docstudio" },
        { id: "memory", title: "我让 HashMM 记住的", desc: "查看和管理跨对话保留的偏好与信息", Icon: Brain, view: "memory" },
      ] },
      { title: "知识管理", desc: "管理员维护的语料与检索资产", cards: [
        { id: "kbs", title: "知识库", desc: "语料库、索引状态与迁移管理", Icon: Database, adminTab: "kbs", audience: "admin" },
        { id: "docs", title: "文档管理", desc: "文档摄取、解析状态与失效处理", Icon: HardDrive, adminTab: "docs", audience: "admin" },
        { id: "kg", title: "知识图谱", desc: "实体关系、图谱构建与检索检查", Icon: Network, adminTab: "kg", audience: "admin" },
        { id: "skills", title: "技能与模板", desc: "复用提示、流程和交付模板", Icon: Sparkles, adminTab: "skills", audience: "admin" },
      ] },
    ],
  },
  "hub-agents": {
    title: "复杂任务",
    desc: "把一件大事拆开并持续推进；你可以随时查看进度、补充要求或接管。",
    sections: [
      { title: "正在做的事", desc: "继续任务或发起多人协作", cards: [
        { id: "gworkspace", title: "查看全部工作", desc: "继续任务、处理待确认事项并查看结果", Icon: BrainCircuit, view: "gworkspace" },
        { id: "agents", title: "组建协作团队", desc: "让多个助手分工并行完成复杂工作", Icon: Users, view: "agents" },
        { id: "routing", title: "选择执行方式", desc: "在效果、速度和花费之间选择", Icon: Cpu, view: "routing", audience: "admin" },
      ] },
      { title: "增强", desc: "受控扩展 Agent 能力", cards: [
        { id: "evolution", title: "经验与技能", desc: "查看技能沉淀和可复用经验", Icon: Sparkles, view: "evolution", audience: "admin" },
        { id: "discovery", title: "主动发现", desc: "检查可执行建议和待确认事项", Icon: Search, view: "discovery", audience: "admin" },
      ] },
    ],
  },
  "hub-operations": {
    title: "任务与进度",
    desc: "查看 HashMM 做到了哪一步、结果是否可靠，以及需要你确认的事项。",
    sections: [
      { title: "进度与结果", desc: "确认任务是否真实完成", cards: [
        { id: "usage", title: "使用概览", desc: "查看完成次数、处理量和估算花费", Icon: BarChart3, view: "usage" },
        { id: "runs", title: "执行过程", desc: "查看每一步做了什么，以及为何暂停或结束", Icon: Activity, view: "runs", audience: "admin" },
        { id: "quality", title: "回答质量", desc: "确认回答是否可靠、有依据并值得采用", Icon: ShieldCheck, view: "quality", audience: "admin" },
        { id: "scheduled", title: "计划任务", desc: "管理定时和后台执行任务", Icon: Clock, view: "scheduled", audience: "admin" },
      ] },
      { title: "安全与设置", desc: "重要操作、连接与高级选项", cards: [
        { id: "audit", title: "安全记录", desc: "查看重要操作、授权与执行结果", Icon: ShieldCheck, view: "audit", audience: "admin" },
        { id: "collab", title: "跨 Agent 协作", desc: "管理协作关系与安全通信", Icon: Network, view: "collab" },
        { id: "selftest", title: "连接诊断", desc: "检查对话、工具和设备是否正常", Icon: FlaskConical, view: "selftest", audience: "diagnostic" },
        { id: "advanced", title: "高级能力", desc: "凭据、派活、隔离和执行策略", Icon: KeyRound, view: "advanced", audience: "admin" },
      ] },
    ],
  },
  "hub-device": {
    title: "电脑与连接",
    desc: "连接你的电脑，让手机上的对话可以继续读取文件、浏览网页并完成工作。",
    sections: [
      { title: "本机", desc: "桌面运行环境", cards: [
        { id: "backend", title: "后端连接", desc: "检查本地或远程后端状态", Icon: Plug, view: "backend", audience: "admin" },
        { id: "terminal", title: "终端", desc: "打开复用的本机终端会话", Icon: Terminal, view: "terminal", audience: "admin" },
        { id: "remote", title: "远程桌面", desc: "管理配对、在线状态和远程访问", Icon: MonitorSmartphone, view: "remote" },
      ] },
    ],
  },
};

const HUB_ICONS: Record<HubId, LucideIcon> = {
  "hub-knowledge": BookOpen,
  "hub-agents": Users,
  "hub-operations": Activity,
  "hub-device": MonitorSmartphone,
};

export function WorkspaceHubView({ hub }: { hub: HubId }) {
  const [query, setQuery] = useState("");
  const set = useStore(s => s.set);
  const role = useStore(s => s.user?.role === "admin" ? "admin" as const : "user" as const);
  const meta = META[hub];
  const sections = useMemo(() => {
    const q = query.trim().toLowerCase();
    return meta.sections.map(section => ({
      ...section,
      cards: section.cards.filter(card => {
        if (!canSeeProductSurface(card.audience || "user", role)) return false;
        return !q || `${card.title} ${card.desc}`.toLowerCase().includes(q);
      }),
    })).filter(section => section.cards.length > 0);
  }, [meta.sections, query, role]);

  const open = (card: Card) => {
    if (card.adminTab) {
      set({ desktopView: null, setOpen: false, adminOpen: true, adminTab: card.adminTab as never });
      return;
    }
    set({ setOpen: false, adminOpen: false, desktopView: card.view as never });
  };

  return (
    <div className="flex-1 min-h-0 overflow-y-auto" style={{ background: "var(--canvas)" }}>
      <div className="w-full max-w-[1120px] mx-auto px-6 md:px-10 py-9 md:py-12">
        <div className="flex items-start gap-3.5 max-w-[820px]">
          <div className="w-11 h-11 rounded-[13px] flex items-center justify-center flex-shrink-0 text-white"
            style={{ background: "var(--accent-grad)", boxShadow: "0 5px 16px color-mix(in srgb, var(--accent) 26%, transparent)" }}>
            {(() => { const Icon = HUB_ICONS[hub]; return <Icon size={21} />; })()}
          </div>
          <div>
            <div className="text-[10.5px] font-semibold tracking-wide uppercase mb-1" style={{ color: "var(--accent)" }}>HashMM 工作区</div>
            <h1 className="text-[26px] md:text-[30px] font-semibold tracking-[-0.02em]" style={{ color: "var(--text-primary)" }}>{meta.title}</h1>
            <p className="mt-1.5 text-[13px] md:text-[14px] leading-6" style={{ color: "var(--text-tertiary)" }}>{meta.desc}</p>
          </div>
        </div>

        <label className="mt-7 max-w-[760px] h-10 px-3 flex items-center gap-2.5 rounded-xl"
          style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
          <Search size={15} style={{ color: "var(--text-tertiary)" }} />
          <input value={query} onChange={e => setQuery(e.target.value)} placeholder={`搜索${meta.title}中的功能`}
            className="flex-1 min-w-0 bg-transparent outline-none text-[13px]" style={{ color: "var(--text-primary)" }} />
        </label>

        <div className="mt-9 space-y-10">
          {sections.map(section => (
            <section key={section.title}>
              <div className="mb-3">
                <h2 className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>{section.title}</h2>
                <p className="text-[11.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{section.desc}</p>
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                {section.cards.map(card => (
                  <button key={card.id} onClick={() => open(card)}
                    className="group flex items-center gap-3.5 p-4 min-h-[78px] rounded-2xl text-left transition-all hover:-translate-y-[1px] hover:shadow-[var(--shadow-sm)] hover:border-[var(--accent)]"
                    style={{ border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                    <span className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
                      style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                      <card.Icon size={17} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>{card.title}</span>
                      {card.audience && card.audience !== "user" && (
                        <span className="inline-block mt-1 mr-1 px-1.5 py-0.5 rounded text-[9px]"
                          style={{ color: "var(--text-tertiary)", background: "var(--bg-tertiary)" }}>
                          {productSurfaceLabel(card.audience)}
                        </span>
                      )}
                      <span className="block mt-0.5 text-[11px] leading-4" style={{ color: "var(--text-tertiary)" }}>{card.desc}</span>
                    </span>
                    <ArrowRight size={14} className="flex-shrink-0 opacity-45 -translate-x-0.5 transition-all group-hover:opacity-100 group-hover:translate-x-0" style={{ color: "var(--text-tertiary)" }} />
                  </button>
                ))}
              </div>
            </section>
          ))}
          {sections.length === 0 && (
            <div className="py-16 text-center text-[12px]" style={{ color: "var(--text-tertiary)" }}>没有匹配的功能</div>
          )}
        </div>
      </div>
    </div>
  );
}

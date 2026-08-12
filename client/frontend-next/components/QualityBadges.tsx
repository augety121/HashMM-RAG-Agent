"use client";
import {
  ShieldCheck, Quote, ListChecks, Zap, RefreshCw, Eye,
} from "lucide-react";

/**
 * QualityBadges —— Loop 工程质量信号的可视徽章（V86 自 AgentRunTimeline 迁出独立）。
 *
 * 为什么独立：V82 的徽章实现埋在 AgentRunTimeline 里，而真实渲染链是
 * AgentLog（ChatArea 流式 + MsgBubble 历史），导致功能"做了但用户看不见"。
 * 现在 MsgBubble 直接挂本组件：折叠条外常显，点徽章展开完整轨迹。
 *
 * 数据来源（后端一直在发，全在 trace steps 里）：
 *   citation  hashmm/agent/loop.py 引用接地校验
 *   dod       完成度自检（Definition of Done）
 *   verify    五语言语法验证链
 *   retrieval_adapt / error_recover  Self-RAG 检索改写 / 错误恢复
 *   vision    V86 截图定向理解（问答栏截屏）
 */

export interface BadgeStep {
  node: string;
  detail: string;
}

export interface QualityBadge {
  key: string;
  Icon: typeof ShieldCheck;
  label: string;
  tone: "ok" | "warn";
  tip: string;
}

/** 纯函数：trace steps → 徽章列表（绿=质量门通过，琥珀=自适应触发/有修正）。 */
export function aggregateQualityBadges(steps: BadgeStep[]): QualityBadge[] {
  const list = steps || [];
  const find = (node: string) => list.find(s => s.node === node);
  const badges: QualityBadge[] = [];

  const cit = find("citation");
  if (cit) {
    const ok = (cit.detail || "").includes("有效");
    badges.push({
      key: "citation", Icon: Quote, tone: ok ? "ok" : "warn",
      label: ok ? "引用校验" : "引用已修正",
      tip: cit.detail || "回答中的引用编号已与检索结果核对",
    });
  }

  const dod = find("dod");
  if (dod) {
    const m = (dod.detail || "").match(/(\d+)\s*项全部完成/);
    badges.push({
      key: "dod", Icon: ListChecks, tone: m ? "ok" : "warn",
      label: m ? `任务清单 ${m[1]}/${m[1]}` : "任务补全",
      tip: dod.detail || "已核对任务清单完成度",
    });
  }

  const verify = find("verify");
  if (verify) {
    const ok = (verify.detail || "").includes("通过");
    badges.push({
      key: "verify", Icon: ShieldCheck, tone: ok ? "ok" : "warn",
      label: "语法验证", tip: verify.detail || "已对生成的代码做语法检查",
    });
  }

  const radapt = list.filter(s => s.node === "retrieval_adapt").length;
  if (radapt) badges.push({
    key: "retrieval_adapt", Icon: Zap, tone: "warn", label: `检索改写 ${radapt}`,
    tip: "检索结果质量低时自动改写查询重试，提升召回",
  });

  const erec = list.filter(s => s.node === "error_recover").length;
  if (erec) badges.push({
    key: "error_recover", Icon: RefreshCw, tone: "warn", label: `错误恢复 ${erec}`,
    tip: "工具执行失败时自动分析原因并换路重试",
  });

  // V86: 截图定向理解——成功为绿，失败/未配置为琥珀（detail 会写明原因）
  const vis = find("vision");
  if (vis) {
    const ok = (vis.detail || "").includes("已分析");
    badges.push({
      key: "vision", Icon: Eye, tone: ok ? "ok" : "warn",
      label: ok ? "图像理解" : "截图未识别",
      tip: vis.detail || "已对所附截图做定向理解",
    });
  }

  return badges;
}

/** 徽章行：常显在 Agent 轨迹折叠条旁；点任一徽章可联动展开完整轨迹。 */
export function QualityBadgesRow({ steps, onBadgeClick }: {
  steps: BadgeStep[];
  onBadgeClick?: () => void;
}) {
  const badges = aggregateQualityBadges(steps);
  if (!badges.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5 mt-1.5">
      {badges.map(b => {
        const Icon = b.Icon;
        const ok = b.tone === "ok";
        return (
          <button key={b.key} type="button" title={b.tip}
            onClick={onBadgeClick}
            className="inline-flex items-center gap-1 px-2 py-[3px] rounded-full text-[10px] font-medium transition-opacity hover:opacity-75"
            style={{
              background: ok ? "rgba(16,163,74,.10)" : "rgba(217,119,6,.10)",
              color: ok ? "#16A34A" : "#D97706",
              border: `1px solid ${ok ? "rgba(16,163,74,.25)" : "rgba(217,119,6,.25)"}`,
              cursor: onBadgeClick ? "pointer" : "default",
            }}>
            <Icon size={11} /> {b.label}
          </button>
        );
      })}
    </div>
  );
}

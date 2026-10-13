"use client";

import { useCallback, useEffect, useState } from "react";
import { BarChart3, BrainCircuit, RefreshCw, Sparkles, Users, Wallet } from "lucide-react";
import { getLocal } from "@/lib/desktop";
import * as api from "@/lib/api";
import { fmtCompact } from "@/lib/usageStats";
import { Badge, Button, Card, CardGrid, CardHeader, PageHeader, PanelShell, SectionTitle, StatCard, StateView } from "./ui/PanelKit";

const money = (value: number, currency: string) => `${currency === "CNY" ? "¥" : "$"}${Number(value || 0).toFixed(4)}`;

export function UsageView() {
  const [days, setDays] = useState(30);
  const [overview, setOverview] = useState<api.UsageOverview | null | undefined>(undefined);
  const [localTools, setLocalTools] = useState<any>(undefined);

  const load = useCallback(async (period = days) => {
    setOverview(undefined);
    try {
      const result = await api.usageOverview(period);
      if (result.contract !== "hashmm.usage-overview.v1") throw new Error("用量接口版本不兼容");
      setOverview(result);
    } catch {
      setOverview(null);
    }
  }, [days]);

  useEffect(() => { load(days); }, [days, load]);
  useEffect(() => {
    const local = getLocal();
    if (!local) { setLocalTools(null); return; }
    local.agentUsage().then(setLocalTools).catch(() => setLocalTools(null));
  }, []);

  return (
    <PanelShell>
      <PageHeader
        icon={BarChart3}
        title="使用概览"
        subtitle="了解 HashMM 在对话和长任务中完成了多少工作，以及大致花费"
        actions={<Button icon={RefreshCw} onClick={() => load(days)}>刷新</Button>}
      />

      <div className="flex items-center gap-2 mb-5">
        {[7, 30, 90].map(period => (
          <button key={period} onClick={() => setDays(period)}
            className="px-3 py-1.5 rounded-full text-[12px] font-medium transition-colors"
            style={{ background: days === period ? "var(--text-primary)" : "var(--bg-secondary)", color: days === period ? "var(--bg-primary)" : "var(--text-secondary)", border: "1px solid var(--border)" }}>
            近 {period} 天
          </button>
        ))}
      </div>

      {overview === undefined ? <StateView kind="loading" message="正在汇总真实使用记录…" /> : overview === null ? (
        <StateView kind="error" message="暂时无法读取使用情况。请确认后端已更新并重新启动。" onRetry={() => load(days)} />
      ) : (<>
        <Card className="mb-4" style={{ background: "linear-gradient(135deg, var(--bg-primary), var(--accent-light))" }}>
          <CardHeader
            icon={overview.scope === "team" ? Users : Sparkles}
            title={overview.scope === "team" ? "团队使用情况" : "我的使用情况"}
            sub="只统计真实对话与任务，不使用演示数据"
            right={<Badge tone="accent">{overview.scope === "team" ? "整个团队" : "仅自己"}</Badge>}
          />
          <div className="text-[21px] font-semibold tracking-[-0.02em] mt-5" style={{ color: "var(--text-primary)" }}>
            {overview.requests > 0 ? `已完成 ${overview.requests} 次模型调用` : "这段时间还没有产生新的模型调用"}
          </div>
          <p className="text-[12.5px] mt-1.5" style={{ color: "var(--text-tertiary)" }}>
            {overview.requests > 0
              ? `共处理 ${fmtCompact(overview.tokens)} 内容单元，估算花费 ${money(overview.cost, overview.currency)}`
              : "开始一次对话、资料整理或长任务后，这里会自动出现真实记录。"}
          </p>
        </Card>

        <CardGrid min={180}>
          <StatCard label="已完成" value={overview.requests} unit="次调用" icon={Sparkles} tone="accent" hint="对话与任务中的模型调用" />
          <StatCard label="处理量" value={fmtCompact(overview.tokens)} icon={BrainCircuit} hint="发送和生成内容的合计" />
          <StatCard label="发送给模型" value={fmtCompact(overview.tokens_in)} icon={BarChart3} hint="资料、上下文和问题" />
          <StatCard label="模型生成" value={fmtCompact(overview.tokens_out)} icon={BarChart3} hint="回答、分析和任务结果" />
          <StatCard label="估算花费" value={money(overview.cost, overview.currency)} icon={Wallet} hint="按服务端当前模型单价估算" />
        </CardGrid>

        {overview.by_user.length > 0 && (<>
          <SectionTitle>团队成员</SectionTitle>
          <Card padding="p-0" className="overflow-hidden">
            {overview.by_user.slice(0, 12).map((row, index) => (
              <div key={`${row.username}-${index}`} className="flex items-center gap-3 px-4 py-3.5" style={{ borderTop: index ? "1px solid var(--hairline)" : undefined }}>
                <div className="w-8 h-8 rounded-full flex items-center justify-center text-[12px] font-semibold" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>
                  {(row.username || "成员").slice(0, 1).toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-[13px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{row.username || "未标注成员"}</div>
                  <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>{row.requests} 次工作 · {fmtCompact(row.tokens)}</div>
                </div>
                <span className="text-[12px] font-mono" style={{ color: "var(--text-secondary)" }}>{money(row.cost, overview.currency)}</span>
              </div>
            ))}
          </Card>
        </>)}

        {overview.by_model.length > 0 && (<>
          <SectionTitle>使用的模型</SectionTitle>
          <CardGrid min={280}>
            {overview.by_model.map((row, index) => (
              <Card key={`${row.model}-${index}`}>
                <CardHeader icon={BrainCircuit} title={row.model || "未标注模型"} sub={`${row.requests} 次调用`} right={<span className="text-[12px] font-mono" style={{ color: "var(--text-secondary)" }}>{money(row.cost, overview.currency)}</span>} />
                <div className="text-[18px] font-semibold" style={{ color: "var(--text-primary)" }}>{fmtCompact(row.tokens)}</div>
                <div className="text-[10.5px] mt-1" style={{ color: "var(--text-tertiary)" }}>处理内容</div>
              </Card>
            ))}
          </CardGrid>
        </>)}
      </>)}

      <details className="mt-8 rounded-2xl" style={{ border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
        <summary className="cursor-pointer select-none px-4 py-3.5 text-[12px] font-medium" style={{ color: "var(--text-secondary)" }}>
          本机开发工具用量
        </summary>
        <div className="px-4 pb-4 text-[12px] leading-5" style={{ color: "var(--text-tertiary)" }}>
          {localTools === undefined ? "正在读取…" : localTools?.claude || localTools?.codex
            ? `已检测到本机开发工具记录。Claude Code 近 7 天 ${fmtCompact(localTools?.claude?.week?.total || 0)}，Codex 当前会话 ${fmtCompact(localTools?.codex?.tokens?.total_tokens || 0)}。`
            : "未检测到本机 Claude Code 或 Codex 的使用记录。此项只用于开发者排查，不影响 HashMM 使用概览。"}
        </div>
      </details>
    </PanelShell>
  );
}

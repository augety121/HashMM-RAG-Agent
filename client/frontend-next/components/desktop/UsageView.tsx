"use client";
/** components/desktop/UsageView.tsx — Agent 用量（V103.90 深做：周期对比条 / 输入输出占比 / 跨 agent 合计）。
 * 本机 ~/.claude 与 ~/.codex 会话的 token 消耗快照，每 30 秒刷新。
 * 由扁平数字升级为可视化：三周期对比条看趋势、Codex 输入/输出占比条、顶部合计指标。 */
import { useEffect, useState } from "react";
import { BarChart3, Wallet, RefreshCw } from "lucide-react";
import { getLocal } from "@/lib/desktop";
import * as api from "@/lib/api";
import { fmtTok } from "./util";
import { PanelShell, PageHeader, Card, CardHeader, CardGrid, StatCard, StateView } from "./ui/PanelKit";
import { relBars, pctSplit, sumTokens, fmtCompact } from "@/lib/usageStats";

export function UsageView() {
  const [u, setU] = useState<any>(undefined);
  const [bal, setBal] = useState<any>(undefined);   // V103.90 API 余额
  const loadBal = () => api.getProviderBalance().then(setBal).catch(() => setBal(null));
  useEffect(() => {
    let iv: ReturnType<typeof setInterval> | undefined;
    const L = getLocal();
    if (!L) { setU(null); } else {
      const load = () => L.agentUsage().then(setU).catch(() => setU(null));
      load();
      iv = setInterval(load, 30000);
    }
    loadBal();
    return () => { if (iv) clearInterval(iv); };
  }, []);

  // 把"X 秒后重置"格式化为可读文案（Codex rate_limits 用）
  const fmtReset = (s?: number): string | null => {
    if (typeof s !== "number" || s <= 0) return null;
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h > 0 ? `${h} 小时 ${m} 分后重置` : `${m} 分后重置`;
  };

  const Bar = ({ label, value, pct, sub, tone = "var(--accent)" }: { label: string; value: string; pct: number; sub?: string; tone?: string }) => (
    <div className="py-1.5">
      <div className="flex justify-between items-baseline mb-1">
        <span className="text-[12px]" style={{ color: "var(--text-secondary)" }}>{label}</span>
        <span className="text-[12.5px] font-mono font-semibold" style={{ color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>
          {value}{sub && <span className="text-[9.5px] font-mono ml-1.5" style={{ color: "var(--text-tertiary)" }}>{sub}</span>}
        </span>
      </div>
      <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg-tertiary)" }}>
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: tone }} />
      </div>
    </div>
  );

  const claude = u && u.claude ? u.claude : null;
  const codex = u && u.codex ? u.codex : null;
  const cl5 = claude?.last5h?.total || 0, clToday = claude?.today?.total || 0, clWeek = claude?.week?.total || 0;
  const [b5, bToday, bWeek] = relBars([cl5, clToday, clWeek]);
  const codexTok = codex?.tokens || {};
  const codexTotal = codexTok.total_tokens || ((codexTok.input_tokens || 0) + (codexTok.output_tokens || 0));
  const io = pctSplit(codexTok.input_tokens || 0, codexTok.output_tokens || 0);
  const combined = sumTokens(clWeek, codexTotal);
  const rl = codex?.rate_limits || {};
  const usedPct = rl?.primary?.used_percent;
  const primReset = fmtReset(rl?.primary?.resets_in_seconds);     // 5h 窗口重置
  const secPct = rl?.secondary?.used_percent;                     // 周窗口已用
  const secReset = fmtReset(rl?.secondary?.resets_in_seconds);    // 周窗口重置

  return (
    <PanelShell>
      <PageHeader icon={BarChart3} title="Agent 用量" subtitle="本机 Claude Code 与 Codex 会话的 token 消耗快照 · 每 30 秒刷新" />

      {/* V103.90 API 余额（当前默认模型所属提供商） */}
      <Card>
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-1.5">
            <Wallet size={14} style={{ color: "var(--accent)" }} />
            <span className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>API 余额</span>
            {bal?.model && <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>· {bal.model}</span>}
          </div>
          <button onClick={loadBal} title="刷新余额" className="p-1 rounded-lg hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-tertiary)" }}>
            <RefreshCw size={13} />
          </button>
        </div>
        {bal === undefined ? (
          <div className="text-[12px] py-1" style={{ color: "var(--text-tertiary)" }}>查询中…</div>
        ) : bal && bal.ok && bal.supported ? (
          <div className="flex items-end gap-6 flex-wrap">
            <div>
              <div className="text-[22px] font-bold font-mono leading-none" style={{ color: "var(--accent)", fontVariantNumeric: "tabular-nums" }}>
                {bal.currency === "CNY" ? "¥" : bal.currency === "USD" ? "$" : ""}{bal.total_balance ?? "—"}
              </div>
              <div className="text-[10.5px] mt-1" style={{ color: "var(--text-tertiary)" }}>总余额{bal.currency ? ` · ${bal.currency}` : ""}{bal.is_available === false ? " · 余额不足" : ""}</div>
            </div>
            <div className="flex gap-5 text-[11.5px] pb-0.5">
              <div>
                <div className="font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{bal.topped_up_balance ?? "—"}</div>
                <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>充值余额</div>
              </div>
              <div>
                <div className="font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{bal.granted_balance ?? "—"}</div>
                <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>赠送余额</div>
              </div>
            </div>
          </div>
        ) : bal && bal.supported === false ? (
          <div className="text-[12px] leading-relaxed py-1" style={{ color: "var(--text-tertiary)" }}>
            {bal.reason || "当前提供商暂不支持余额查询（目前仅支持 DeepSeek）。"}
          </div>
        ) : (
          <div className="text-[12px] leading-relaxed py-1" style={{ color: "var(--warning, #d97706)" }}>
            {(bal && bal.reason) || "暂时取不到余额。若刚更新版本，请确认后端也已重启（此功能依赖后端新接口）；并确认已用管理员登录、默认模型为 DeepSeek。"}
          </div>
        )}
      </Card>

      {u === undefined ? <StateView kind="loading" /> : (<>
        {(claude || codex) && (
          <CardGrid min={170}>
            <StatCard label="近 7 天合计" value={fmtCompact(combined)} unit="tok" icon={BarChart3} tone="accent" hint="Claude Code + Codex" />
            {claude && <StatCard label="Claude 今日消息" value={claude?.today?.msgs ?? 0} hint={`${fmtCompact(clToday)} tok`} />}
            {codex && typeof usedPct === "number" && <StatCard label="Codex 5h 窗口" value={usedPct} unit="%" tone={usedPct >= 80 ? "warning" : "default"} hint="配额已用" />}
          </CardGrid>
        )}

        <CardGrid min={320}>
          <Card>
            <CardHeader title="Claude Code" sub="本机 ~/.claude 会话记录的 token 消耗（条形为三周期相对对比）" />
            {claude ? (<>
              <Bar label="近 5 小时" value={fmtTok(cl5)} sub={`${claude?.last5h?.msgs ?? 0} msgs`} pct={b5} />
              <Bar label="今天" value={fmtTok(clToday)} sub={`${claude?.today?.msgs ?? 0} msgs`} pct={bToday} />
              <Bar label="近 7 天" value={fmtTok(clWeek)} sub={`${claude?.week?.msgs ?? 0} msgs`} pct={bWeek} />
            </>) : <div className="text-[12px] leading-relaxed py-2" style={{ color: "var(--text-tertiary)" }}>未检测到 Claude Code 会话记录。在终端跑过 claude 后这里会显示消耗。</div>}
          </Card>
          <Card>
            <CardHeader title="Codex" sub="本机 ~/.codex 最新会话的 token 与配额快照" />
            {codex ? (<>
              <div className="flex justify-between items-baseline py-1.5">
                <span className="text-[12.5px]" style={{ color: "var(--text-secondary)" }}>累计 tokens</span>
                <span className="text-[14px] font-mono font-semibold" style={{ color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>{fmtTok(codexTotal)}</span>
              </div>
              {io.total > 0 && (<>
                <div className="flex h-2 rounded-full overflow-hidden mt-1 mb-1.5" style={{ background: "var(--bg-tertiary)" }}>
                  <div style={{ width: `${io.aPct}%`, background: "var(--accent)" }} />
                  <div style={{ width: `${io.bPct}%`, background: "color-mix(in srgb, var(--accent) 40%, transparent)" }} />
                </div>
                <div className="flex justify-between text-[10.5px] font-mono" style={{ color: "var(--text-tertiary)" }}>
                  <span>输入 {io.aPct}% · {fmtCompact(codexTok.input_tokens || 0)}</span>
                  <span>输出 {io.bPct}% · {fmtCompact(codexTok.output_tokens || 0)}</span>
                </div>
              </>)}
              {typeof usedPct === "number" && (
                <div className="mt-2.5 pt-2.5" style={{ borderTop: "1px dashed var(--border)" }}>
                  <Bar label="5 小时窗口已用" value={`${usedPct}%`} pct={Math.min(100, Math.max(0, usedPct))} tone={usedPct >= 80 ? "var(--warning)" : "var(--accent)"} />
                  {primReset && <div className="text-[10px] mt-0.5 text-right" style={{ color: "var(--text-tertiary)" }}>{primReset}</div>}
                </div>
              )}
              {typeof secPct === "number" && (
                <div className="mt-2">
                  <Bar label="每周窗口已用" value={`${secPct}%`} pct={Math.min(100, Math.max(0, secPct))} tone={secPct >= 80 ? "var(--warning)" : "var(--accent)"} />
                  {secReset && <div className="text-[10px] mt-0.5 text-right" style={{ color: "var(--text-tertiary)" }}>{secReset}</div>}
                </div>
              )}
            </>) : <div className="text-[12px] py-2" style={{ color: "var(--text-tertiary)" }}>未检测到 Codex 会话记录。</div>}
          </Card>
        </CardGrid>
      </>)}
    </PanelShell>
  );
}

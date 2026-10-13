"use client";
/** components/desktop/ui/PanelKit.tsx — 桌面端面板统一设计系统（V103.52）。
 *
 * 问题：13 个面板各写一套 header / 卡片 / 按钮 / 状态，圆角间距字号都不一致 → 「小作坊感」。
 * 解法（对标 Linear / Vercel / Claude 控制台的做法）：抽一套**单一来源**的原子组件，所有面板
 * 只用它拼装。改一处样式，全站统一。全部基于 globals.css 既有 token（--accent / --bg-* /
 * --border / --text-* / --radius-* / --shadow-* / --space-*），自动暗色适配、含无障碍语义。
 *
 * 设计语言（克制、专业、一致）：
 *   · 卡片：--bg-secondary 面 + 1px --border + --radius-lg(16px) + --shadow-sm，hover 微抬升。
 *   · 字号阶梯：标题 15 / 副标题 11.5 / 正文 12.5 / 标注 11 / 数据 mono。
 *   · 间距：页面 padding 24，区块间距 20，卡片内 16。
 *   · 主色仅用于强调（主按钮 / 激活态 / 图标章），不滥用。
 */
import type { LucideIcon } from "lucide-react";
import { RefreshCw, Loader2, AlertCircle } from "lucide-react";
import type { ReactNode, CSSProperties } from "react";

/* ── 页面外壳：统一外边距 + 纵向滚动 ─────────────────────────── */
export function PanelShell({ children, className = "", style }: { children: ReactNode; className?: string; style?: CSSProperties }) {
  return (
    <div className={`flex-1 overflow-y-auto ${className}`} style={{ background: "var(--bg-secondary)", padding: "28px 32px 64px", ...style }}>
      <div className="mx-auto" style={{ maxWidth: 1040 }}>{children}</div>
    </div>
  );
}

/* ── 页头：图标作为语义标记裸放，不再套彩色底座。 ───────────── */
export function PageHeader({ icon: Icon, title, subtitle, actions, accent = "var(--accent)" }: {
  icon: LucideIcon; title: string; subtitle?: ReactNode; actions?: ReactNode; accent?: string;
}) {
  return (
    <div className="flex items-start gap-3 mb-7">
      <div className="w-6 h-8 flex items-center justify-center flex-shrink-0" style={{ color: accent }}>
        <Icon size={17} strokeWidth={1.8} aria-hidden />
      </div>
      <div className="flex-1 min-w-0">
        <h1 className="text-[22px] font-semibold leading-tight tracking-[-0.025em]" style={{ color: "var(--text-primary)" }}>{title}</h1>
        {subtitle && <div className="text-[12px] mt-1 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>{subtitle}</div>}
      </div>
      {actions && <div className="flex items-center gap-2 flex-shrink-0 pt-1">{actions}</div>}
    </div>
  );
}

/* ── 按钮：primary / secondary / ghost / danger，统一高度与圆角 ─── */
type BtnVariant = "primary" | "secondary" | "ghost" | "danger";
export function Button({ children, onClick, variant = "secondary", icon: Icon, disabled, busy, size = "md", title, type = "button", ariaLabel }: {
  children?: ReactNode; onClick?: () => void; variant?: BtnVariant; icon?: LucideIcon;
  disabled?: boolean; busy?: boolean; size?: "sm" | "md"; title?: string; type?: "button" | "submit"; ariaLabel?: string;
}) {
  const pad = size === "sm" ? "px-2.5 py-1.5 text-[11.5px]" : "px-3.5 py-2 text-[12.5px]";
  const base: CSSProperties = { borderRadius: "var(--radius-md)", transition: "all var(--duration) var(--ease)", fontWeight: 500 };
  const styles: Record<BtnVariant, CSSProperties> = {
    primary: { background: "var(--accent)", color: "#fff", boxShadow: "var(--shadow-sm)" },
    secondary: { background: "var(--bg-secondary)", color: "var(--text-secondary)", border: "1px solid var(--border)" },
    ghost: { background: "transparent", color: "var(--text-secondary)" },
    danger: { background: "transparent", color: "var(--error)", border: "1px solid color-mix(in srgb, var(--error) 30%, transparent)" },
  };
  const hover: Record<BtnVariant, string> = {
    primary: "hover:brightness-110", secondary: "hover:bg-[var(--bg-tertiary)]",
    ghost: "hover:bg-[var(--bg-tertiary)]", danger: "hover:bg-[color-mix(in_srgb,var(--error)_8%,transparent)]",
  };
  return (
    <button type={type} onClick={onClick} disabled={disabled || busy} title={title} aria-label={ariaLabel}
      className={`inline-flex items-center justify-center gap-1.5 ${pad} ${hover[variant]} disabled:opacity-40 disabled:pointer-events-none`}
      style={{ ...base, ...styles[variant] }}>
      {busy ? <Loader2 size={size === "sm" ? 13 : 14} className="animate-spin" /> : Icon && <Icon size={size === "sm" ? 13 : 14} />}
      {children}
    </button>
  );
}

/* ── 卡片：统一面/边/圆角/阴影，可选 hover 抬升与点击 ─────────── */
export function Card({ children, className = "", padding = "p-5", interactive, onClick, style }: {
  children: ReactNode; className?: string; padding?: string; interactive?: boolean; onClick?: () => void; style?: CSSProperties;
}) {
  return (
    <div onClick={onClick}
      className={`pk-card rounded-xl ${padding} ${interactive ? "pk-card-i cursor-pointer" : ""} ${className}`}
      style={style}>
      {children}
    </div>
  );
}

/* ── 卡片头：小标题 + 可选右侧 ─────────────────────────────── */
export function CardHeader({ title, sub, right, icon: Icon }: { title: ReactNode; sub?: ReactNode; right?: ReactNode; icon?: LucideIcon }) {
  return (
    <div className="flex items-start justify-between mb-3">
      <div className="flex items-center gap-2 min-w-0">
        {Icon && <Icon size={15} style={{ color: "var(--text-tertiary)" }} className="flex-shrink-0" />}
        <div className="min-w-0">
          <div className="text-[13.5px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>{title}</div>
          {sub && <div className="text-[11px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{sub}</div>}
        </div>
      </div>
      {right && <div className="flex-shrink-0 ml-2">{right}</div>}
    </div>
  );
}

/* ── 指标卡：大数字 + 标签 + 可选趋势/图标。看板类面板的主砖 ──── */
export function StatCard({ label, value, unit, icon: Icon, tone = "default", hint }: {
  label: string; value: ReactNode; unit?: string; icon?: LucideIcon;
  tone?: "default" | "accent" | "success" | "warning" | "error"; hint?: string;
}) {
  const toneColor = { default: "var(--text-primary)", accent: "var(--accent)", success: "var(--success)", warning: "var(--warning)", error: "var(--error)" }[tone];
  return (
    <div className="pk-card rounded-2xl p-[18px] flex flex-col gap-2.5 min-w-[150px]">
      <div className="flex items-center justify-between">
        <span className="text-[11.5px] font-medium" style={{ color: "var(--text-tertiary)" }}>{label}</span>
        {Icon && (
          <div className="w-5 h-7 flex items-center justify-center flex-shrink-0">
            <Icon size={14} style={{ color: tone === "default" ? "var(--text-tertiary)" : toneColor }} />
          </div>
        )}
      </div>
      <div className="flex items-baseline gap-1">
        <span className="text-[27px] font-bold tracking-[-0.025em] leading-none" style={{ color: toneColor, fontVariantNumeric: "tabular-nums" }}>{value}</span>
        {unit && <span className="text-[12px] font-medium" style={{ color: "var(--text-tertiary)" }}>{unit}</span>}
      </div>
      {hint && <span className="text-[10.5px] leading-snug" style={{ color: "var(--text-tertiary)" }}>{hint}</span>}
    </div>
  );
}

/* ── 区块标题：分隔大段内容 ───────────────────────────────── */
export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex items-center justify-between mt-2 mb-3">
      <h2 className="text-[12px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>{children}</h2>
      {right}
    </div>
  );
}

/* ── 徽章 / pill：状态、计数、标签 ─────────────────────────── */
export function Badge({ children, tone = "neutral", mono }: { children: ReactNode; tone?: "neutral" | "accent" | "success" | "warning" | "error"; mono?: boolean }) {
  const map = {
    neutral: { bg: "var(--bg-tertiary)", fg: "var(--text-secondary)" },
    accent: { bg: "var(--accent-light)", fg: "var(--accent)" },
    success: { bg: "color-mix(in srgb, var(--success) 14%, transparent)", fg: "var(--success)" },
    warning: { bg: "color-mix(in srgb, var(--warning) 16%, transparent)", fg: "var(--warning)" },
    error: { bg: "color-mix(in srgb, var(--error) 14%, transparent)", fg: "var(--error)" },
  }[tone];
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10.5px] font-medium ${mono ? "font-mono" : ""}`}
      style={{ background: map.bg, color: map.fg }}>{children}</span>
  );
}

/* ── 表单字段：标签 + input/控件统一样式 ───────────────────── */
export function Field({ label, children, hint }: { label?: string; children: ReactNode; hint?: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      {label && <label className="text-[11px] font-medium" style={{ color: "var(--text-tertiary)" }}>{label}</label>}
      {children}
      {hint && <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{hint}</span>}
    </div>
  );
}

export const inputClass = "w-full text-[12.5px] px-3 py-2 rounded-[10px] outline-none transition-colors focus:border-[var(--accent)]";
export const inputStyle: CSSProperties = { background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)" };

/* ── 开关 ─────────────────────────────────────────────────── */
export function Toggle({ checked, onChange, ariaLabel }: { checked: boolean; onChange: () => void; ariaLabel?: string }) {
  return (
    <button onClick={onChange} role="switch" aria-checked={checked} aria-label={ariaLabel}
      className="relative w-[38px] h-[22px] rounded-full transition-colors flex-shrink-0"
      style={{ background: checked ? "var(--accent)" : "var(--border)" }}>
      <span className="absolute top-[2px] w-[18px] h-[18px] bg-white rounded-full transition-all"
        style={{ left: checked ? "18px" : "2px", boxShadow: "0 1px 2px rgba(0,0,0,0.2)" }} />
    </button>
  );
}

/* ── 三态：加载 / 空 / 错误（统一收口，含无障碍）──────────── */
export function StateView({ kind, message, title, icon: Icon, onRetry, action }: {
  kind: "loading" | "empty" | "error"; message?: ReactNode; title?: ReactNode; icon?: LucideIcon; onRetry?: () => void; action?: ReactNode;
}) {
  if (kind === "loading") {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-3" role="status" aria-label="加载中">
        <Loader2 className="animate-spin" size={22} style={{ color: "var(--text-tertiary)" }} />
        <span className="text-[12px]" style={{ color: "var(--text-tertiary)" }}>{message || "加载中…"}</span>
      </div>
    );
  }
  if (kind === "error") {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-3" role="alert">
        <div className="w-12 h-12 flex items-center justify-center">
          <AlertCircle size={24} style={{ color: "var(--error)" }} />
        </div>
        <div className="text-[13px] text-center max-w-[380px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>{message || "加载失败"}</div>
        {onRetry && <Button variant="secondary" icon={RefreshCw} onClick={onRetry} size="sm">重试</Button>}
      </div>
    );
  }
  // 空态：放在内容区上方（不再悬在死中央），图标 + 标题 + 说明 + 可选操作，避免"太单调"。
  return (
    <Card className="flex flex-col items-center text-center mx-auto" padding="px-6 pt-12 pb-12" style={{ maxWidth: 560 }}>
      {Icon && (
        <div className="w-16 h-16 flex items-center justify-center mb-4">
          <Icon size={28} style={{ color: "var(--text-tertiary)", opacity: 0.65 }} strokeWidth={1.75} />
        </div>
      )}
      {title && <div className="text-[14.5px] font-semibold mb-1.5" style={{ color: "var(--text-secondary)" }}>{title}</div>}
      <div className="text-[12.5px] max-w-[460px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>{message || "暂无数据"}</div>
      {action && <div className="mt-5 flex items-center gap-2 flex-wrap justify-center">{action}</div>}
    </Card>
  );
}

/* ── 网格：响应式自适应卡片排列 ─────────────────────────── */
export function CardGrid({ children, min = 280 }: { children: ReactNode; min?: number }) {
  return <div className="grid gap-4" style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${min}px, 1fr))` }}>{children}</div>;
}

/* ── 列表行：键值/条目统一行样式，hover 显操作 ──────────── */
export function Row({ children, onClick, className = "" }: { children: ReactNode; onClick?: () => void; className?: string }) {
  return (
    <div onClick={onClick}
      className={`group flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors ${onClick ? "cursor-pointer hover:bg-[var(--bg-tertiary)]" : ""} ${className}`}>
      {children}
    </div>
  );
}

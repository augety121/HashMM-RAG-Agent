"use client";
/** components/desktop/StateBlock.tsx — 面板三态（V103.42 · 方案 P3）。
 *
 * 统一的「加载 / 空 / 错误」展示，替代各面板里裸的「读取中…」文本。
 * 带无障碍语义（role=status/alert + aria）。逻辑仍由各面板自己判断，这里只负责呈现。
 */
import { Loader2, AlertCircle, RefreshCw } from "lucide-react";
import type { LucideIcon } from "lucide-react";

export function StateBlock({ kind, message, icon: Icon, onRetry }: {
  kind: "loading" | "empty" | "error";
  message?: string;
  icon?: LucideIcon;
  onRetry?: () => void;
}) {
  if (kind === "loading") {
    return (
      <div className="flex-1 flex items-center justify-center py-16" role="status" aria-label="加载中">
        <Loader2 className="animate-spin" size={20} style={{ color: "var(--text-tertiary)" }} />
      </div>
    );
  }
  if (kind === "error") {
    return (
      <div className="flex-1 flex flex-col items-center justify-center py-16 gap-3" role="alert">
        <AlertCircle size={26} style={{ color: "#dc2626" }} />
        <div className="text-[13px] text-center max-w-[360px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          {message || "加载失败"}
        </div>
        {onRetry && (
          <button onClick={onRetry} aria-label="重试"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] transition-colors hover:bg-[var(--bg-tertiary)]"
            style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
            <RefreshCw size={13} /> 重试
          </button>
        )}
      </div>
    );
  }
  // empty
  return (
    <div className="flex-1 flex flex-col items-center justify-center py-16 gap-2.5">
      {Icon && <Icon size={30} style={{ color: "var(--text-tertiary)", opacity: 0.4 }} />}
      <div className="text-[12.5px] text-center max-w-[360px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
        {message || "暂无数据"}
      </div>
    </div>
  );
}

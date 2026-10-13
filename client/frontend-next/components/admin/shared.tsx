"use client";
import type { LucideIcon } from "lucide-react";

export function Tab({ active, icon: Icon, label, onClick }: { active: boolean; icon: LucideIcon; label: string; onClick: () => void }) {
  return (
    <button onClick={onClick}
      className="flex items-center gap-2 px-4 py-2.5 text-[13px] font-medium rounded-lg transition-all whitespace-nowrap"
      style={{
        background: active ? "var(--accent-light)" : "transparent",
        color: active ? "var(--accent)" : "var(--text-secondary)",
      }}>
      <Icon size={15} /> {label}
    </button>
  );
}

export function Badge({ children, color = "var(--accent)" }: { children: React.ReactNode; color?: string }) {
  return <span className="px-2 py-0.5 rounded-md text-[10px] font-semibold" style={{ background: color + "18", color }}>{children}</span>;
}

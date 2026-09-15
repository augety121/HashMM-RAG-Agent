"use client";
import { CheckCircle2, Loader2, Circle, ListTodo } from "lucide-react";

export interface TodoItem {
  text: string;
  status: string; // "pending" | "doing" | "done"
}

/**
 * V50: 任务清单卡片（对标 Claude Code TodoWrite 的可视化看板）。
 * Agent 通过 update_todo 工具全量更新，前端原地替换渲染：
 * done=绿勾+删除线，doing=旋转+高亮，pending=空圈灰字。
 */
export function TodoCard({ items }: { items: TodoItem[] }) {
  if (!items || items.length === 0) return null;
  const nDone = items.filter(i => i.status === "done").length;
  const allDone = nDone === items.length;

  return (
    <div className="todo-card">
      <div className="todo-head">
        <ListTodo size={13} style={{ color: allDone ? "#22c55e" : "var(--accent)" }} />
        <span className="todo-title">任务清单</span>
        <span className="todo-progress">{nDone}/{items.length}</span>
        <div className="todo-bar">
          <div className="todo-bar-fill" style={{ width: `${(nDone / items.length) * 100}%` }} />
        </div>
      </div>
      <div className="todo-items">
        {items.map((it, i) => (
          <div key={i} className={`todo-item todo-${it.status}`}>
            {it.status === "done" ? <CheckCircle2 size={13} className="todo-ico" style={{ color: "#22c55e" }} />
              : it.status === "doing" ? <Loader2 size={13} className="todo-ico animate-spin" style={{ color: "var(--accent)" }} />
              : <Circle size={13} className="todo-ico" style={{ color: "var(--text-tertiary)" }} />}
            <span className="todo-text">{it.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

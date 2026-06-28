"use client";
/** components/CuReplayPanel.tsx — Computer Use 操作回放审计面板（V99）。
 *
 *  让"AI 在我电脑上做过什么"对用户可见——大厂级 GUI 自动化的合规闭环。
 *  数据来自主进程 ActionRecorder（modules/cu-actions.js），经 cu:replay 桥拉取：
 *  每条记录含时间、动作摘要（中文）、成功/失败、归一化坐标。
 *
 *  设计：右侧抽屉浮层，打开时每 1.5s 轮询一次（电脑操作进行中会持续刷新），
 *  关闭即停轮询。可一键清空（清主进程缓冲）、可导出文本。纯展示，不触发任何动作。
 *  桌面端 only —— web 无 hashmmCU 桥时整个入口不渲染（调用方已 isDesktopEnv 守卫）。
 */
import { useEffect, useRef, useState, useCallback } from "react";
import { getCU } from "@/lib/desktop";
import { X, MousePointerClick, Trash2, ClipboardCopy, ShieldCheck } from "lucide-react";

type ReplayEvent = { ts: number; type: string; summary: string; ok: boolean; error: string };

export function CuReplayPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [events, setEvents] = useState<ReplayEvent[]>([]);
  const [text, setText] = useState("");
  const [copied, setCopied] = useState(false);
  const [safety, setSafety] = useState<"normal" | "readonly" | "strict">("normal");
  const [fileDir, setFileDir] = useState("");          // V103.90 CU 默认文件保存目录
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    const cu = getCU();
    if (!cu || !cu.replay) return;
    try {
      const r = await cu.replay(200);
      if (r && r.ok) { setEvents(r.events || []); setText(r.text || ""); }
    } catch { /* 桥异常静默——审计面板不该打断主流程 */ }
  }, []);

  useEffect(() => {
    if (!open) { if (timer.current) { clearInterval(timer.current); timer.current = null; } return; }
    load();
    const cu = getCU();
    if (cu && cu.getSafety) cu.getSafety().then(r => { if (r && r.ok) setSafety(r.level); }).catch(() => {});
    if (cu && cu.getFileDir) cu.getFileDir().then(r => { if (r && r.ok) setFileDir(r.dir || ""); }).catch(() => {});
    timer.current = setInterval(load, 1500);   // 进行中持续刷新
    return () => { if (timer.current) { clearInterval(timer.current); timer.current = null; } };
  }, [open, load]);

  if (!open) return null;

  const clear = async () => {
    const cu = getCU();
    if (cu && cu.replayClear) { try { await cu.replayClear(); } catch { /* */ } }
    setEvents([]); setText("");
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(text || "（暂无记录）"); setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { /* 剪贴板不可用就算了 */ }
  };
  const changeSafety = async (lv: "normal" | "readonly" | "strict") => {
    setSafety(lv);
    const cu = getCU();
    if (cu && cu.setSafety) { try { await cu.setSafety(lv); } catch { /* */ } }
  };
  const pickDir = async () => {
    const cu = getCU();
    if (cu && cu.pickFileDir) {
      try { const r = await cu.pickFileDir(); if (r && r.ok && r.dir) setFileDir(r.dir); } catch { /* */ }
    }
  };
  const resetDir = async () => {
    const cu = getCU();
    if (cu && cu.setFileDir) {
      try { const r = await cu.setFileDir(""); if (r && r.ok) setFileDir(r.dir || ""); } catch { /* */ }
    }
  };

  const okCount = events.filter(e => e.ok).length;
  const failCount = events.length - okCount;

  return (
    <div className="fixed inset-0 z-[60] flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/20" />
      <div className="relative w-[380px] max-w-[90vw] h-full flex flex-col shadow-2xl"
        style={{ background: "var(--bg-primary)", borderLeft: "1px solid var(--border)" }}
        onClick={e => e.stopPropagation()}>
        {/* 头部 */}
        <div className="flex items-center gap-2 px-4 py-3 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <ShieldCheck size={15} style={{ color: "var(--accent)" }} />
          <span className="text-[13px] font-bold" style={{ color: "var(--text-primary)" }}>电脑操作记录</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>审计</span>
          <button onClick={onClose} className="ml-auto p-1 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]">
            <X size={15} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>

        {/* 安全级别选择器 */}
        <div className="px-4 py-2.5 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="text-[10.5px] mb-1.5" style={{ color: "var(--text-tertiary)" }}>安全级别</div>
          <div className="flex gap-1">
            {([
              ["normal", "普通", "仅危险操作确认"],
              ["readonly", "只读", "禁止一切修改"],
              ["strict", "高安全", "每步操作都确认"],
            ] as const).map(([lv, label, desc]) => (
              <button key={lv} onClick={() => changeSafety(lv)} title={desc}
                className="flex-1 px-2 py-1.5 rounded-lg text-[11px] font-medium transition-colors"
                style={{
                  background: safety === lv ? "var(--accent)" : "var(--bg-secondary)",
                  color: safety === lv ? "#fff" : "var(--text-secondary)",
                  border: "1px solid " + (safety === lv ? "var(--accent)" : "var(--border)"),
                }}>
                {label}
              </button>
            ))}
          </div>
          <div className="text-[10px] mt-1.5" style={{ color: "var(--text-tertiary)" }}>
            {safety === "normal" ? "危险命令、危险快捷键、敏感区点击会请求确认" :
             safety === "readonly" ? "AI 只能看和读，不能点击、输入或修改任何东西" :
             "AI 的每一次写入/点击/输入都会弹出确认框"}
          </div>
        </div>

        {/* V103.90 默认文件保存位置 */}
        <div className="px-4 py-2.5 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="text-[10.5px] mb-1.5" style={{ color: "var(--text-tertiary)" }}>默认文件保存位置</div>
          <div className="flex items-center gap-1.5">
            <div className="flex-1 px-2 py-1.5 rounded-lg text-[11px] truncate"
              style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
              title={fileDir || "（系统下载文件夹）"}>
              {fileDir || "（系统下载文件夹）"}
            </div>
            <button onClick={pickDir}
              className="px-2.5 py-1.5 rounded-lg text-[11px] font-medium transition-colors"
              style={{ background: "var(--accent)", color: "#fff", border: "1px solid var(--accent)" }}>
              选择…
            </button>
            <button onClick={resetDir} title="恢复为系统下载文件夹"
              className="px-2 py-1.5 rounded-lg text-[11px] transition-colors hover:bg-[var(--bg-tertiary)]"
              style={{ background: "var(--bg-secondary)", color: "var(--text-tertiary)", border: "1px solid var(--border)" }}>
              默认
            </button>
          </div>
          <div className="text-[10px] mt-1.5" style={{ color: "var(--text-tertiary)" }}>
            AI 写文件时，只给文件名或相对路径会存到这里；确认框里也可临时「选择位置…」
          </div>
        </div>

        {/* 统计条 */}
        <div className="flex items-center gap-3 px-4 py-2 text-[11px] flex-shrink-0" style={{ borderBottom: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          <span>共 <b style={{ color: "var(--text-primary)" }}>{events.length}</b> 步</span>
          <span style={{ color: "var(--success, #16A34A)" }}>成功 {okCount}</span>
          {failCount > 0 && <span style={{ color: "var(--danger, #DC2626)" }}>失败 {failCount}</span>}
          <div className="flex-1" />
          <button onClick={copy} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md transition-colors hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-tertiary)" }} title="复制全部记录">
            <ClipboardCopy size={11} /> {copied ? "已复制" : "复制"}
          </button>
          <button onClick={clear} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md transition-colors hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-tertiary)" }} title="清空记录">
            <Trash2 size={11} /> 清空
          </button>
        </div>

        {/* 记录列表（最新在上） */}
        <div className="flex-1 overflow-y-auto px-2 py-2">
          {!events.length && (
            <div className="h-full flex flex-col items-center justify-center gap-2 px-6 text-center" style={{ color: "var(--text-tertiary)" }}>
              <MousePointerClick size={26} strokeWidth={1.5} />
              <div className="text-[12px]">暂无操作记录</div>
              <div className="text-[11px] leading-relaxed">开启「电脑操作」后，AI 在你电脑上的每一次点击、输入、按键都会记录在这里，供你随时核查。</div>
            </div>
          )}
          {events.slice().reverse().map((e, i) => (
            <div key={events.length - i} className="flex items-start gap-2 px-2.5 py-2 rounded-lg mb-1" style={{ background: "var(--bg-secondary)" }}>
              <span className="mt-0.5 w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: e.ok ? "var(--success, #16A34A)" : "var(--danger, #DC2626)" }} />
              <div className="flex-1 min-w-0">
                <div className="text-[12px]" style={{ color: "var(--text-primary)" }}>{e.summary}</div>
                {!e.ok && e.error && <div className="text-[10.5px] mt-0.5" style={{ color: "var(--danger, #DC2626)" }}>{e.error}</div>}
              </div>
              <span className="text-[9.5px] font-mono flex-shrink-0 mt-0.5" style={{ color: "var(--text-tertiary)" }}>
                {new Date(e.ts).toLocaleTimeString("zh-CN", { hour12: false })}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

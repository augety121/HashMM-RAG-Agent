"use client";
/** components/desktop/GitGraphView.tsx — Git 提交图（借鉴 Ridge 的 Git Graph）。
 *
 *  左侧 Canvas 画泳道/节点/连线（布局来自 lib/gitGraph 的纯逻辑算法，已单测），
 *  右侧 HTML 列表显示分支标签 + 提交信息（可选中、与 Canvas 共用行高对齐、同一滚动容器）。
 *  数据来自 git:log IPC（只读跑 git log --all）。需要本机装了 git、且 cwd 是个 git 仓库。 */
import { useEffect, useRef, useState, useCallback } from "react";
import { getLocal } from "@/lib/desktop";
import { computeGraphLayout, maxLanes, type LaidOutCommit } from "@/lib/gitGraph";
import { GitBranch, RefreshCw, Loader2 } from "lucide-react";
import { StateBlock } from "./StateBlock";

const ROW_H = 30;
const LANE_W = 18;
const PAD_X = 14;
const DOT_R = 5;
const COLORS = ["#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899", "#14B8A6", "#F97316"];

export function GitGraphView() {
  const [cwd, setCwd] = useState("");
  const [rows, setRows] = useState<LaidOutCommit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const load = useCallback(async () => {
    const L = getLocal();
    if (!L || !(L as any).gitLog) { setError("Git 接口不可用（需重装含本版的桌面端）"); return; }
    setLoading(true); setError("");
    try {
      const r: any = await (L as any).gitLog(cwd || undefined, 400);
      if (!r || !r.ok) {
        const raw = (r && r.error) || "git log 失败";
        setError(/not a git repository/i.test(raw)
          ? "这个目录不是 Git 仓库。请在上方“仓库路径”里填一个 Git 仓库的目录（含 .git 的那个），再点刷新。"
          : (/git[:\s]/i.test(raw) && /not found|无法找到|不是内部/i.test(raw))
            ? "未找到 git 命令。请先在本机安装 Git 并加入 PATH。"
            : raw);
        setRows([]);
      }
      else {
        setRows(computeGraphLayout(r.commits || []));
        if (!r.commits || r.commits.length === 0) setError("该目录没有提交记录（或不是 git 仓库）");
      }
    } catch (e: any) { setError(String(e?.message || e)); setRows([]); }
    finally { setLoading(false); }
  }, [cwd]);

  useEffect(() => { load(); }, []);   // 首次加载（默认 cwd = 后端进程目录）

  // 绘制 Canvas（泳道 + 连线 + 节点）
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const lanes = maxLanes(rows);
    const cssW = PAD_X * 2 + lanes * LANE_W;
    const cssH = Math.max(rows.length * ROW_H, ROW_H);
    const dpr = window.devicePixelRatio || 1;
    canvas.width = cssW * dpr; canvas.height = cssH * dpr;
    canvas.style.width = cssW + "px"; canvas.style.height = cssH + "px";
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    ctx.lineWidth = 2;

    const idx: Record<string, number> = {};
    rows.forEach((r, i) => { idx[r.hash] = i; });
    const laneX = (l: number) => PAD_X + l * LANE_W + LANE_W / 2;
    const rowY = (i: number) => i * ROW_H + ROW_H / 2;

    // 先画连线（在节点下层）
    rows.forEach((r, i) => {
      const y1 = rowY(i);
      for (const pl of r.parentLanes) {
        const pIdx = idx[pl.parent];
        if (pIdx === undefined) continue;            // 父不在已加载集合内
        const x1 = laneX(r.lane), x2 = laneX(pl.lane), y2 = rowY(pIdx);
        ctx.strokeStyle = COLORS[(r.color + (x1 === x2 ? 0 : 0)) % COLORS.length];
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        if (x1 === x2) { ctx.lineTo(x2, y2); }
        else {
          const my = (y1 + y2) / 2;
          ctx.bezierCurveTo(x1, my, x2, my, x2, y2);   // 平滑 S 形连线
        }
        ctx.stroke();
      }
    });

    // 再画节点
    rows.forEach((r, i) => {
      const x = laneX(r.lane), y = rowY(i);
      ctx.beginPath();
      ctx.arc(x, y, DOT_R, 0, Math.PI * 2);
      ctx.fillStyle = COLORS[r.color % COLORS.length];
      ctx.fill();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = "rgba(255,255,255,0.85)";
      ctx.stroke();
      ctx.lineWidth = 2;
    });
  }, [rows]);

  const graphW = PAD_X * 2 + maxLanes(rows) * LANE_W;

  return (
    <div className="flex flex-1 min-h-0 flex-col">
      <div className="flex items-center gap-2 px-4 py-2 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <GitBranch size={14} style={{ color: "var(--text-secondary)" }} />
        <span className="text-[12px] font-medium" style={{ color: "var(--text-secondary)" }}>Git 提交图</span>
        <input value={cwd} onChange={(e) => setCwd(e.target.value)} placeholder="仓库路径（留空＝后端目录）"
          onKeyDown={(e) => { if (e.key === "Enter") load(); }}
          className="ml-2 flex-1 max-w-md px-2.5 py-1 rounded-lg text-[12px] bg-transparent outline-none"
          style={{ border: "1px solid var(--border)", color: "var(--text-primary)" }} />
        <button onClick={load} disabled={loading} title="刷新"
          className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-[12px] transition-colors hover:bg-[var(--bg-tertiary)]"
          style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} 刷新
        </button>
        {rows.length > 0 && <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>{rows.length} 提交</span>}
      </div>

      {error && <div className="px-4 py-2 text-[12px]" style={{ color: "var(--danger,#DC2626)" }}>{error}</div>}

      <div className="flex-1 min-h-0 overflow-auto">
        <div className="flex min-w-max">
          {/* 左：Canvas 泳道图 */}
          <canvas ref={canvasRef} className="flex-shrink-0" style={{ width: graphW }} />
          {/* 右：提交信息列表（行高与 Canvas 对齐） */}
          <div className="flex-1 min-w-0">
            {rows.map((r) => (
              <div key={r.hash} className="flex items-center gap-2 px-3 overflow-hidden" style={{ height: ROW_H, borderBottom: "1px solid color-mix(in srgb, var(--border) 40%, transparent)" }}>
                {r.refs.map((ref) => (
                  <span key={ref} className="flex-shrink-0 text-[10px] px-1.5 py-0.5 rounded-md font-mono whitespace-nowrap"
                    style={{ background: "color-mix(in srgb, var(--accent) 18%, transparent)", color: "var(--accent)" }}>{ref}</span>
                ))}
                <span className="text-[12px] truncate" style={{ color: "var(--text-primary)" }}>{r.subject}</span>
                <span className="ml-auto flex-shrink-0 text-[10.5px] font-mono" style={{ color: "var(--text-tertiary)" }}>{r.hash.slice(0, 7)}</span>
                <span className="flex-shrink-0 text-[10.5px] truncate max-w-[110px]" style={{ color: "var(--text-tertiary)" }}>{r.author}</span>
                <span className="flex-shrink-0 text-[10.5px] font-mono" style={{ color: "var(--text-tertiary)" }}>{r.date}</span>
              </div>
            ))}
          </div>
        </div>
        {!loading && rows.length === 0 && !error && (
          <StateBlock kind="loading" />
        )}
      </div>
    </div>
  );
}

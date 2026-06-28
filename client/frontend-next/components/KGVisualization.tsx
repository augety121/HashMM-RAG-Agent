"use client";
import { useState, useEffect, useRef, useCallback } from "react";
import { X, Search, RefreshCw, ZoomIn, ZoomOut, Maximize2, Network } from "lucide-react";

interface KGNode {
  id: string;
  label: string;
  type: string;
  color: string;
  size: number;
  description: string;
  community: number;
}

interface KGEdge {
  from: string;
  to: string;
  label: string;
  weight: number;
}

interface KGStats {
  entities: number;
  relations: number;
  density: number;
  communities: number;
  type_distribution: Record<string, number>;
  top_entities: Array<{ name: string; degree: number; type: string }>;
}

interface Props {
  onClose: () => void;
}

export function KGVisualization({ onClose }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [nodes, setNodes] = useState<KGNode[]>([]);
  const [edges, setEdges] = useState<KGEdge[]>([]);
  const [stats, setStats] = useState<KGStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedNode, setSelectedNode] = useState<KGNode | null>(null);
  const [hoveredNode, setHoveredNode] = useState<KGNode | null>(null);

  // Canvas transform state
  const transformRef = useRef({ zoom: 1, panX: 0, panY: 0 });
  const [, forceRender] = useState(0);
  const nodePositionsRef = useRef<Record<string, { x: number; y: number; vx: number; vy: number }>>({});
  const isDragging = useRef(false);
  const dragStart = useRef({ x: 0, y: 0, panX: 0, panY: 0 });
  const animFrameRef = useRef<number>(0);

  const fetchGraph = useCallback(async () => {
    setLoading(true);
    try {
      const token = typeof localStorage !== "undefined" ? localStorage.getItem("hmm_token") : null;
      const headers: Record<string, string> = {};
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const res = await fetch("/api/kg/graph?max_nodes=200", { headers });
      const data = await res.json();
      setNodes(data.nodes || []);
      setEdges(data.edges || []);
      setStats(data.stats || null);

      if (data.nodes?.length > 0) {
        const positions = computeLayout(data.nodes, data.edges);
        nodePositionsRef.current = positions;
        forceRender(n => n + 1);
      }
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
    setLoading(false);
  }, []);

  useEffect(() => { fetchGraph(); }, [fetchGraph]);

  // ── Canvas rendering ──
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || nodes.length === 0) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const cw = canvas.clientWidth;
    const ch = canvas.clientHeight;
    canvas.width = cw * dpr;
    canvas.height = ch * dpr;
    ctx.scale(dpr, dpr);

    const { zoom, panX, panY } = transformRef.current;
    const positions = nodePositionsRef.current;
    const isDark = document.documentElement.classList.contains("dark");

    ctx.clearRect(0, 0, cw, ch);
    ctx.save();
    ctx.translate(cw / 2 + panX, ch / 2 + panY);
    ctx.scale(zoom, zoom);

    // Draw edges
    for (const edge of edges) {
      const from = positions[edge.from];
      const to = positions[edge.to];
      if (!from || !to) continue;

      ctx.beginPath();
      ctx.moveTo(from.x, from.y);
      ctx.lineTo(to.x, to.y);
      ctx.strokeStyle = isDark ? "rgba(100,100,130,0.25)" : "rgba(160,160,180,0.25)";
      ctx.lineWidth = Math.max(0.5, edge.weight * 0.8);
      ctx.stroke();

      if (zoom > 0.9 && edge.label) {
        const mx = (from.x + to.x) / 2;
        const my = (from.y + to.y) / 2;
        ctx.fillStyle = isDark ? "rgba(140,140,170,0.5)" : "rgba(100,100,120,0.5)";
        ctx.font = "9px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(edge.label, mx, my - 3);
      }
    }

    // Draw nodes
    const hovId = hoveredNode?.id;
    const selId = selectedNode?.id;
    for (const node of nodes) {
      const pos = positions[node.id];
      if (!pos) continue;

      const isSelected = node.id === selId;
      const isHovered = node.id === hovId;
      const isSearchMatch = searchQuery && node.label.toLowerCase().includes(searchQuery.toLowerCase());
      const r = (node.size || 8) / 2;
      const effectiveR = r + (isSelected ? 4 : isHovered ? 2.5 : 0);

      // Glow for hovered/selected
      if (isSelected || isHovered) {
        ctx.beginPath();
        ctx.arc(pos.x, pos.y, effectiveR + 6, 0, Math.PI * 2);
        ctx.fillStyle = isSelected
          ? (node.color + "30")
          : (node.color + "20");
        ctx.fill();
      }

      // Node circle
      ctx.beginPath();
      ctx.arc(pos.x, pos.y, effectiveR, 0, Math.PI * 2);
      ctx.fillStyle = isSearchMatch ? "#facc15" : node.color;
      ctx.globalAlpha = isSelected || isHovered || isSearchMatch ? 1.0 : 0.7;
      ctx.fill();

      if (isSelected || isHovered) {
        ctx.strokeStyle = isDark ? "#e4e4e7" : "#27272a";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
      ctx.globalAlpha = 1.0;

      // Label
      if (zoom > 0.4 || isSelected || isHovered || isSearchMatch) {
        const fontSize = Math.max(9, Math.min(12, 11 / zoom));
        ctx.fillStyle = isDark ? "#e4e4e7" : "#1a1a2e";
        ctx.font = `${isSelected || isHovered ? "600 " : ""}${fontSize}px system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.fillText(node.label, pos.x, pos.y + effectiveR + fontSize + 2);
      }
    }

    ctx.restore();
  }, [nodes, edges, selectedNode, hoveredNode, searchQuery, forceRender]);

  // ── Hit test helper ──
  function hitTest(clientX: number, clientY: number): KGNode | null {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const { zoom, panX, panY } = transformRef.current;
    const mx = (clientX - rect.left - rect.width / 2 - panX) / zoom;
    const my = (clientY - rect.top - rect.height / 2 - panY) / zoom;
    const positions = nodePositionsRef.current;

    for (const node of nodes) {
      const pos = positions[node.id];
      if (!pos) continue;
      const r = (node.size || 8) / 2 + 5;
      const dx = pos.x - mx;
      const dy = pos.y - my;
      if (dx * dx + dy * dy < r * r) return node;
    }
    return null;
  }

  // ── Mouse interactions ──
  function handleCanvasClick(e: React.MouseEvent<HTMLCanvasElement>) {
    if (isDragging.current) return;
    const hit = hitTest(e.clientX, e.clientY);
    setSelectedNode(hit);
  }

  function handleMouseMove(e: React.MouseEvent<HTMLCanvasElement>) {
    if (isDragging.current) {
      const dx = e.clientX - dragStart.current.x;
      const dy = e.clientY - dragStart.current.y;
      transformRef.current.panX = dragStart.current.panX + dx;
      transformRef.current.panY = dragStart.current.panY + dy;
      forceRender(n => n + 1);
      return;
    }
    const hit = hitTest(e.clientX, e.clientY);
    if (hit !== hoveredNode) {
      setHoveredNode(hit);
      const canvas = canvasRef.current;
      if (canvas) canvas.style.cursor = hit ? "pointer" : "grab";
    }
  }

  function handleMouseDown(e: React.MouseEvent<HTMLCanvasElement>) {
    const hit = hitTest(e.clientX, e.clientY);
    if (!hit) {
      isDragging.current = false;
      dragStart.current = {
        x: e.clientX,
        y: e.clientY,
        panX: transformRef.current.panX,
        panY: transformRef.current.panY,
      };
      const canvas = canvasRef.current;
      if (canvas) canvas.style.cursor = "grabbing";

      const onMove = (ev: MouseEvent) => {
        const dx = ev.clientX - dragStart.current.x;
        const dy = ev.clientY - dragStart.current.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) isDragging.current = true;
        transformRef.current.panX = dragStart.current.panX + dx;
        transformRef.current.panY = dragStart.current.panY + dy;
        forceRender(n => n + 1);
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        if (canvas) canvas.style.cursor = "grab";
        setTimeout(() => { isDragging.current = false; }, 50);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    }
  }

  function handleWheel(e: React.WheelEvent<HTMLCanvasElement>) {
    e.preventDefault();
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    const newZoom = Math.min(8, Math.max(0.1, transformRef.current.zoom * delta));

    // Zoom toward cursor position
    const canvas = canvasRef.current;
    if (canvas) {
      const rect = canvas.getBoundingClientRect();
      const cx = e.clientX - rect.left - rect.width / 2;
      const cy = e.clientY - rect.top - rect.height / 2;
      const scale = 1 - newZoom / transformRef.current.zoom;
      transformRef.current.panX += (cx - transformRef.current.panX) * scale;
      transformRef.current.panY += (cy - transformRef.current.panY) * scale;
    }

    transformRef.current.zoom = newZoom;
    forceRender(n => n + 1);
  }

  function resetView() {
    transformRef.current = { zoom: 1, panX: 0, panY: 0 };
    forceRender(n => n + 1);
  }

  const filteredNodes = searchQuery
    ? nodes.filter(n => n.label.toLowerCase().includes(searchQuery.toLowerCase()))
    : [];

  // ── Type distribution bar chart ──
  function TypeDistChart({ dist }: { dist: Record<string, number> }) {
    const entries = Object.entries(dist).sort((a, b) => b[1] - a[1]);
    const maxVal = Math.max(...entries.map(e => e[1]), 1);
    return (
      <div className="space-y-1.5">
        {entries.map(([type, count]) => (
          <div key={type} className="flex items-center gap-2">
            <span className="text-[11px] w-16 truncate text-right" style={{ color: "var(--text-secondary)" }}>{type}</span>
            <div className="flex-1 h-4 rounded-sm overflow-hidden" style={{ background: "var(--bg-tertiary)" }}>
              <div className="h-full rounded-sm transition-all duration-500"
                style={{ width: `${(count / maxVal) * 100}%`, background: "var(--accent)" }} />
            </div>
            <span className="text-[10px] font-mono w-6 text-right" style={{ color: "var(--text-tertiary)" }}>{count}</span>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex" style={{ background: "var(--bg-primary)" }}>
      {/* Main canvas */}
      <div className="flex-1 flex flex-col" ref={containerRef}>
        {/* Toolbar */}
        <div className="flex items-center gap-2 px-4 py-2" style={{ borderBottom: "1px solid var(--border)" }}>
          <h3 className="text-[14px] font-semibold mr-2" style={{ color: "var(--text-primary)" }}>
            知识图谱 {nodes.length > 0 && <span className="font-normal text-[12px]" style={{ color: "var(--text-tertiary)" }}>({nodes.length} 实体)</span>}
          </h3>
          <div className="flex-1 relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-tertiary)" }} />
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索实体..."
              className="w-full max-w-[300px] h-8 pl-9 pr-3 rounded-lg text-[13px] outline-none"
              style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            />
          </div>
          <button onClick={() => { transformRef.current.zoom = Math.min(transformRef.current.zoom * 1.3, 8); forceRender(n => n + 1); }}
            className="p-1.5 rounded hover:bg-[var(--bg-tertiary)]" title="放大">
            <ZoomIn size={16} style={{ color: "var(--text-tertiary)" }} />
          </button>
          <button onClick={() => { transformRef.current.zoom = Math.max(transformRef.current.zoom / 1.3, 0.1); forceRender(n => n + 1); }}
            className="p-1.5 rounded hover:bg-[var(--bg-tertiary)]" title="缩小">
            <ZoomOut size={16} style={{ color: "var(--text-tertiary)" }} />
          </button>
          <button onClick={resetView} className="p-1.5 rounded hover:bg-[var(--bg-tertiary)]" title="重置视图">
            <Maximize2 size={16} style={{ color: "var(--text-tertiary)" }} />
          </button>
          <button onClick={fetchGraph} className="p-1.5 rounded hover:bg-[var(--bg-tertiary)]" title="刷新">
            <RefreshCw size={16} style={{ color: "var(--text-tertiary)" }} />
          </button>
          <button onClick={onClose} className="p-1.5 rounded hover:bg-[var(--bg-tertiary)]" title="关闭">
            <X size={18} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>

        {/* Canvas */}
        {loading ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="flex items-center gap-2 text-[14px]" style={{ color: "var(--text-tertiary)" }}>
              <RefreshCw size={16} className="animate-spin" /> 加载知识图谱...
            </div>
          </div>
        ) : error ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-[14px] text-red-500">{error}</div>
          </div>
        ) : nodes.length === 0 ? (
          <div className="flex-1 flex flex-col items-center justify-center gap-3">
            <Network size={44} style={{ color: "var(--text-tertiary)", opacity: 0.4 }} />
            <div className="text-[14px]" style={{ color: "var(--text-tertiary)" }}>知识图谱为空</div>
            <div className="text-[12px]" style={{ color: "var(--text-tertiary)" }}>上传文档并构建 KG 后，图谱将在此显示</div>
          </div>
        ) : (
          <canvas
            ref={canvasRef}
            onClick={handleCanvasClick}
            onMouseMove={handleMouseMove}
            onMouseDown={handleMouseDown}
            onWheel={handleWheel}
            className="flex-1"
            style={{ background: "var(--bg-primary)", cursor: "grab" }}
          />
        )}

        {/* Hover tooltip */}
        {hoveredNode && !isDragging.current && (
          <div className="absolute bottom-4 left-4 px-3 py-2 rounded-lg text-[12px] pointer-events-none anim-fade-up"
            style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-md)" }}>
            <span className="font-semibold" style={{ color: "var(--text-primary)" }}>{hoveredNode.label}</span>
            <span className="ml-2" style={{ color: "var(--accent)" }}>{hoveredNode.type}</span>
            {hoveredNode.description && <div className="mt-1" style={{ color: "var(--text-tertiary)" }}>{hoveredNode.description.slice(0, 80)}</div>}
          </div>
        )}

        {/* Search results dropdown */}
        {searchQuery && filteredNodes.length > 0 && (
          <div className="absolute top-14 left-4 max-h-[200px] w-[280px] overflow-y-auto rounded-lg z-10"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
            {filteredNodes.slice(0, 10).map(n => (
              <button key={n.id} onClick={() => { setSelectedNode(n); setSearchQuery(""); }}
                className="w-full text-left px-3 py-1.5 text-[12px] hover:bg-[var(--bg-tertiary)] flex items-center gap-2">
                <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: n.color }} />
                <span style={{ color: "var(--text-primary)" }}>{n.label}</span>
                <span className="text-[10px] ml-auto" style={{ color: "var(--text-tertiary)" }}>{n.type}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Right panel — stats + selected entity */}
      <div className="w-[280px] flex-shrink-0 overflow-y-auto" style={{ borderLeft: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
        {/* Stats */}
        {stats && (
          <div className="p-4" style={{ borderBottom: "1px solid var(--border)" }}>
            <h4 className="text-[12px] font-semibold mb-2" style={{ color: "var(--text-tertiary)" }}>图谱统计</h4>
            <div className="grid grid-cols-2 gap-2">
              {([
                ["实体", stats.entities], ["关系", stats.relations],
                ["社区", stats.communities], ["密度", typeof stats.density === "number" ? stats.density.toFixed(4) : stats.density],
              ] as [string, string | number][]).map(([label, value]) => (
                <div key={label} className="p-2 rounded-lg" style={{ background: "var(--bg-tertiary)" }}>
                  <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{label}</div>
                  <div className="text-[16px] font-bold" style={{ color: "var(--text-primary)" }}>{String(value)}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Type distribution chart */}
        {stats?.type_distribution && Object.keys(stats.type_distribution).length > 0 && (
          <div className="p-4" style={{ borderBottom: "1px solid var(--border)" }}>
            <h4 className="text-[12px] font-semibold mb-3" style={{ color: "var(--text-tertiary)" }}>实体类型分布</h4>
            <TypeDistChart dist={stats.type_distribution} />
          </div>
        )}

        {/* Selected entity detail */}
        {selectedNode && (
          <div className="p-4" style={{ borderBottom: "1px solid var(--border)" }}>
            <h4 className="text-[12px] font-semibold mb-2" style={{ color: "var(--text-tertiary)" }}>选中实体</h4>
            <div className="flex items-center gap-2 mb-2">
              <span className="w-3 h-3 rounded-full" style={{ background: selectedNode.color }} />
              <span className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>{selectedNode.label}</span>
            </div>
            <div className="text-[11px] mb-1" style={{ color: "var(--accent)" }}>{selectedNode.type}</div>
            {selectedNode.description && (
              <div className="text-[12px] leading-relaxed mb-3" style={{ color: "var(--text-secondary)" }}>
                {selectedNode.description}
              </div>
            )}
            {selectedNode.community >= 0 && (
              <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                社区 #{selectedNode.community}
              </div>
            )}
            {/* Related edges */}
            {edges.filter(e => e.from === selectedNode.id || e.to === selectedNode.id).length > 0 && (
              <div className="mt-3">
                <div className="text-[10px] font-semibold mb-1" style={{ color: "var(--text-tertiary)" }}>关联</div>
                {edges.filter(e => e.from === selectedNode.id || e.to === selectedNode.id).slice(0, 8).map((e, i) => {
                  const other = e.from === selectedNode.id ? e.to : e.from;
                  const otherNode = nodes.find(n => n.id === other);
                  return (
                    <div key={i} className="flex items-center gap-1.5 py-0.5 text-[11px] cursor-pointer hover:text-[var(--accent)]"
                      onClick={() => { if (otherNode) setSelectedNode(otherNode); }}
                      style={{ color: "var(--text-secondary)" }}>
                      <span>→</span>
                      <span className="w-1.5 h-1.5 rounded-full" style={{ background: otherNode?.color || "#888" }} />
                      <span>{otherNode?.label || other}</span>
                      {e.label && <span className="text-[9px]" style={{ color: "var(--text-tertiary)" }}>({e.label})</span>}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* Top entities */}
        {stats?.top_entities && stats.top_entities.length > 0 && (
          <div className="p-4">
            <h4 className="text-[12px] font-semibold mb-2" style={{ color: "var(--text-tertiary)" }}>高连接实体 Top 15</h4>
            {stats.top_entities.slice(0, 15).map((e, i) => (
              <div key={i} className="flex items-center gap-2 py-1 cursor-pointer hover:bg-[var(--bg-tertiary)] px-1 rounded"
                onClick={() => {
                  const n = nodes.find(n => n.label === e.name);
                  if (n) setSelectedNode(n);
                }}>
                <span className="text-[11px] font-mono w-5 text-right" style={{ color: "var(--text-tertiary)" }}>{e.degree}</span>
                <span className="text-[12px] truncate flex-1" style={{ color: "var(--text-primary)" }}>{e.name}</span>
                <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{e.type}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}


// ── Force-directed layout (Barnes-Hut optimized) ──

function computeLayout(
  nodes: KGNode[],
  edges: KGEdge[]
): Record<string, { x: number; y: number; vx: number; vy: number }> {
  const positions: Record<string, { x: number; y: number; vx: number; vy: number }> = {};
  const n = nodes.length;

  // Initialize in a spiral (better than circle for large graphs)
  for (let i = 0; i < n; i++) {
    const angle = (2 * Math.PI * i) / n;
    const radius = Math.min(400, 60 + n * 2.5);
    const spiralR = radius * (0.3 + 0.7 * (i / n));
    positions[nodes[i].id] = {
      x: Math.cos(angle) * spiralR,
      y: Math.sin(angle) * spiralR,
      vx: 0,
      vy: 0,
    };
  }

  // Build adjacency for attraction
  const adj = new Map<string, Set<string>>();
  for (const node of nodes) adj.set(node.id, new Set());
  for (const edge of edges) {
    adj.get(edge.from)?.add(edge.to);
    adj.get(edge.to)?.add(edge.from);
  }

  // Force-directed simulation (80 iterations with cooling)
  const ITERATIONS = 80;
  const REPULSION = 8000;
  const ATTRACTION = 0.005;
  const DAMPING = 0.92;
  const MAX_VELOCITY = 30;

  for (let iter = 0; iter < ITERATIONS; iter++) {
    const temp = 1 - iter / ITERATIONS; // cooling

    // Repulsion (all pairs — O(n²), acceptable for n < 500)
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const a = positions[nodes[i].id];
        const b = positions[nodes[j].id];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const dist = Math.sqrt(dx * dx + dy * dy) + 1;
        const force = (REPULSION * temp) / (dist * dist);
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      }
    }

    // Attraction (along edges)
    for (const edge of edges) {
      const a = positions[edge.from];
      const b = positions[edge.to];
      if (!a || !b) continue;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) + 1;
      const force = dist * ATTRACTION * temp;
      a.vx += dx * force;
      a.vy += dy * force;
      b.vx -= dx * force;
      b.vy -= dy * force;
    }

    // Center gravity (prevent drift)
    for (const node of nodes) {
      const p = positions[node.id];
      p.vx -= p.x * 0.001 * temp;
      p.vy -= p.y * 0.001 * temp;
    }

    // Apply velocities with damping + max velocity
    for (const node of nodes) {
      const p = positions[node.id];
      p.vx *= DAMPING;
      p.vy *= DAMPING;
      const speed = Math.sqrt(p.vx * p.vx + p.vy * p.vy);
      if (speed > MAX_VELOCITY) {
        p.vx = (p.vx / speed) * MAX_VELOCITY;
        p.vy = (p.vy / speed) * MAX_VELOCITY;
      }
      p.x += p.vx;
      p.y += p.vy;
    }
  }

  return positions;
}

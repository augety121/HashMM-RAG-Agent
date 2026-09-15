"use client";
/** DocStudioView — 文档工坊（V255）：文件深度理解与生成（对标 Marvis 同名能力）。
 *
 * 流程：① 选来源（上传文件 或 粘贴文本）→ ② 点动作卡（深度解读 / 优化润色 /
 * 图表生成 / 格式转换 / 一页提要）→ ③ 产出直接写成**会话画布**——
 * 天然获得就地编辑 / 版本历史 / 划选提问 / 发布分享全家桶。
 * 数据源：/api/docstudio/actions | run（routes/doc_studio.py）；上传写入当前会话工作区。
 */
import { useEffect, useRef, useState } from "react";
import { FileText, FileCheck2, Upload, Loader2, Eye, Wand2, ClipboardType, Brain, Sparkles, BarChart3, Repeat2, TerminalSquare, Mic, Sigma, Figma, Palette, LayoutTemplate, Presentation } from "lucide-react";
import { docActions, docRun, createConversation, figmaImport, type DocAction } from "@/lib/api";
import { useStore } from "@/lib/store";
import { openArtifact } from "@/lib/artifact";

const ACTION_ICON: Record<string, typeof Brain> = {
  office_audit: FileCheck2,
  deep_read: Brain, polish: Sparkles, chart: BarChart3, convert: Repeat2, brief: FileText, custom: TerminalSquare, audio_minutes: Mic, data_qa: Sigma,
  design_icon: Palette, design_poster: LayoutTemplate, design_slides: Presentation, design_infographic: BarChart3,
};

export function DocStudioView() {
  const sid = useStore(s => s.sid);
  const set = useStore(s => s.set);
  const token = useStore(s => s.token);
  const [actions, setActions] = useState<DocAction[]>([]);
  const [src, setSrc] = useState<"file" | "text">("file");
  const [fileName, setFileName] = useState("");
  const [fileConv, setFileConv] = useState("");   // 上传落在哪个会话（产出画布同会话）
  const [text, setText] = useState("");
  const [target, setTarget] = useState("md");
  const [busy, setBusy] = useState("");           // 正在跑的 action id
  const [err, setErr] = useState("");
  const [out, setOut] = useState<{ file: string; content: string; note: string; conv: string } | null>(null);
  const [customIns, setCustomIns] = useState("");
  // V279 Figma 导入（后端 /api/figma/import；token 仅本次请求使用、不落库）
  const [figmaUrl, setFigmaUrl] = useState("");
  const [figmaToken, setFigmaToken] = useState("");
  const [figmaBusy, setFigmaBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => { docActions().then(r => setActions(r.actions || [])).catch(() => setErr("后端版本过旧（缺 /api/docstudio），请升级后端")); }, []);

  async function ensureConv(): Promise<string> {
    if (sid) return sid;
    const cid = "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
    await createConversation(cid, "文档工坊");
    set({ sid: cid });
    return cid;
  }

  async function onPickFile(f: File) {
    setErr(""); setOut(null);
    try {
      const cid = await ensureConv();
      const fd = new FormData();
      fd.append("file", f);
      // analyze=0：工坊只要文件落盘，不需要上传时的泛解析等待（动作执行时才按需抽文本）
      const res = await fetch(`/api/conversations/${encodeURIComponent(cid)}/upload`, {
        method: "POST", headers: token ? { Authorization: `Bearer ${token}` } : undefined, body: fd,
      });
      if (!res.ok) {
        let message = `HTTP ${res.status}`;
        try { const body = await res.json(); message = body?.detail || body?.error || message; } catch { /* non-JSON */ }
        throw new Error(message);
      }
      const j = await res.json() as { filename?: string };
      setFileName(j.filename || f.name);
      setFileConv(cid);
    } catch (e) { setErr("上传失败：" + ((e as Error)?.message || e)); }
  }

  async function run(action: string) {
    if (busy) return;
    setErr(""); setOut(null);
    const isDesign = action.startsWith("design_");
    if (isDesign) {
      if (!customIns.trim()) { setErr("先在下方输入框写清你要设计什么（例：给一个哈希检索项目设计一枚图标）"); return; }
    } else {
      if (src === "file" && !fileName) { setErr("先上传一个文件"); return; }
      if (src === "text" && !text.trim()) { setErr("先粘贴要处理的文本"); return; }
      if (action === "custom" && !customIns.trim()) { setErr("先在下方写清你的处理指令"); return; }
    }
    // V269 数据问答：问题写在同一输入框；留空则做基础分析（后端有兜底问题）

    setBusy(action);
    try {
      const cid = (isDesign || src !== "file") ? await ensureConv() : fileConv;
      const r = await docRun({
        conv_id: cid,
        filename: (!isDesign && src === "file") ? fileName : undefined,
        text: (!isDesign && src === "text") ? text : undefined,
        action, target,
        instruction: (isDesign || action === "custom" || action === "data_qa") ? (customIns.trim() || undefined) : undefined,
      });
      const resultConv = r.conv_id || cid;
      setOut({ file: r.file, content: r.content, note: r.note, conv: resultConv });
      if (r.artifact?.filename) {
        openArtifact(resultConv, {
          filename: r.artifact.filename,
          download_url: r.artifact.download_url,
        });
      }
    } catch (e) { setErr("执行失败：" + ((e as Error)?.message || e)); }
    finally { setBusy(""); }
  }

  async function runFigma() {
    if (figmaBusy) return;
    setErr(""); setOut(null);
    if (!figmaUrl.trim()) { setErr("先粘贴 Figma 文件链接（figma.com/file/... 或 /design/...）"); return; }
    if (!figmaToken.trim()) { setErr("需要你的 Figma Personal Access Token（Figma 账号设置里生成；仅本次使用、不会保存）"); return; }
    setFigmaBusy(true);
    try {
      const cid = await ensureConv();
      const r = await figmaImport(figmaUrl.trim(), figmaToken.trim(), { conv_id: cid });
      setOut({ file: r.file, content: "（Figma 设计稿已转成画布 HTML —— 点右上「在画布打开」查看还原效果，可就地编辑 / 版本 / 发布）", note: r.note, conv: cid });
      setFigmaToken("");   // 用完即清，不留在输入框
    } catch (e) { setErr("Figma 导入失败：" + ((e as Error)?.message || e)); }
    finally { setFigmaBusy(false); }
  }

  return (
    <div className="flex-1 overflow-y-auto px-6 py-5" style={{ background: "var(--bg-primary)" }}>
      <div className="max-w-[980px] mx-auto">
        <div className="flex items-center gap-2.5 mb-1">
          <div className="w-8 h-8 rounded-xl flex items-center justify-center" style={{ background: "var(--accent-light)" }}>
            <FileText size={16} style={{ color: "var(--accent)" }} />
          </div>
          <div>
            <div className="text-[15px] font-bold" style={{ color: "var(--text-primary)" }}>文档 & 设计工坊</div>
            <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>文件深度理解 · 一句话出设计稿 · 产出直接进画布</div>
          </div>
        </div>
        <div className="text-[11.5px] mb-4 mt-1" style={{ color: "var(--text-tertiary)" }}>
          文档类：上传文件/粘贴文本，深度解读 / 润色 / 图表 / 转换 / 音频转纪要。
          设计类（对标 Claude Design）：在下方输入框写一句需求，一键生成图标 / 海报 / 幻灯片 / 信息图，产出可在画布编辑发布。
        </div>

        {/* 来源 */}
        <div className="rounded-2xl px-5 py-4 mb-4" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
          <div className="flex items-center gap-2 mb-3">
            <button onClick={() => setSrc("file")}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-[12px] font-semibold"
              style={{ background: src === "file" ? "var(--accent)" : "var(--bg-primary)", color: src === "file" ? "#fff" : "var(--text-secondary)", border: "1px solid " + (src === "file" ? "var(--accent)" : "var(--border)") }}>
              <Upload size={12} /> 上传文件</button>
            <button onClick={() => setSrc("text")}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-[12px] font-semibold"
              style={{ background: src === "text" ? "var(--accent)" : "var(--bg-primary)", color: src === "text" ? "#fff" : "var(--text-secondary)", border: "1px solid " + (src === "text" ? "var(--accent)" : "var(--border)") }}>
              <ClipboardType size={12} /> 粘贴文本</button>
            <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>支持 pdf / docx / xlsx / csv / md / txt / 代码 / 音频（mp3·wav·m4a→纪要）等</span>
          </div>
          {src === "file" ? (
            <div className="flex items-center gap-2.5">
              <input ref={fileRef} type="file" className="hidden" aria-label="选择文件"
                onChange={e => { const f = e.target.files?.[0]; if (f) onPickFile(f); e.currentTarget.value = ""; }} />
              <button onClick={() => fileRef.current?.click()}
                className="px-3.5 py-2 rounded-xl text-[12.5px] font-semibold"
                style={{ background: "var(--bg-primary)", border: "1.5px dashed var(--border)", color: "var(--text-secondary)" }}>
                选择文件…</button>
              {fileName && <span className="text-[12.5px] font-medium truncate" style={{ color: "var(--accent)" }}>{fileName}</span>}
            </div>
          ) : (
            <textarea value={text} onChange={e => setText(e.target.value)} rows={5}
              placeholder="把要处理的文档内容粘贴到这里…"
              className="w-full px-3 py-2.5 rounded-xl text-[12.5px] resize-y outline-none"
              style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          )}
        </div>

        {/* 动作卡 */}
        <div className="text-[12.5px] font-bold mb-2" style={{ color: "var(--text-primary)" }}>选择动作</div>
        <div className="grid gap-2.5 mb-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))" }}>
          {actions.map(a => (
            <button key={a.id} onClick={() => run(a.id)} disabled={!!busy}
              className="text-left rounded-xl px-3.5 py-3 transition-all hover:-translate-y-0.5 disabled:opacity-60"
              style={{ background: "var(--bg-secondary)", border: "1px solid " + (busy === a.id ? "var(--accent)" : "var(--border)") }}>
              <div className="flex items-center gap-2">
                {(() => { const Ic = ACTION_ICON[a.id] || FileText; return <Ic size={16} style={{ color: "var(--accent)" }} />; })()}
                <span className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{a.name}</span>
                {busy === a.id && <Loader2 size={13} className="animate-spin ml-auto" style={{ color: "var(--accent)" }} />}
              </div>
              <div className="text-[11px] mt-1 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>{a.desc}</div>
              {a.id === "convert" && (
                <div className="mt-2 flex gap-1" onClick={e => e.stopPropagation()}>
                  {(["md", "html", "txt"] as const).map(x => (
                    <span key={x} role="button" onClick={() => setTarget(x)}
                      className="px-2 py-0.5 rounded-full text-[10px] font-semibold cursor-pointer"
                      style={{ background: target === x ? "var(--accent)" : "var(--bg-primary)", color: target === x ? "#fff" : "var(--text-tertiary)", border: "1px solid var(--border)" }}>{x}</span>
                  ))}
                </div>
              )}
            </button>
          ))}
          {!actions.length && <div className="text-[12px] py-4" style={{ color: "var(--text-tertiary)" }}>动作加载中…</div>}
        </div>
        {/* 指令 / 设计需求输入（配合"自定义指令"与"设计·*"动作卡） */}
        <div className="rounded-xl px-4 py-3 mb-3 flex items-center gap-2.5" style={{ background: "var(--bg-secondary)", border: "1px dashed var(--border)" }}>
          <TerminalSquare size={13} style={{ color: "var(--text-tertiary)" }} />
          <input value={customIns} onChange={e => setCustomIns(e.target.value)}
            placeholder='指令 / 数据问题 / 设计需求：如"抽取所有金额并按时间排序"、"哪个月销售额最高"、或"给哈希检索项目设计一枚科技感图标"'
            className="flex-1 bg-transparent outline-none text-[12px]" style={{ color: "var(--text-primary)" }} />
        </div>
        {/* Figma 导入（V279）：设计稿 → 画布 HTML，进画布全家桶 */}
        <div className="rounded-xl px-4 py-3 mb-3" style={{ background: "var(--bg-secondary)", border: "1px dashed var(--border)" }}>
          <div className="flex items-center gap-2 mb-2">
            <Figma size={13} style={{ color: "var(--text-tertiary)" }} />
            <span className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>Figma 设计稿导入</span>
            <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>转成画布 HTML · Token 仅本次使用不保存</span>
          </div>
          <div className="flex items-center gap-2">
            <input value={figmaUrl} onChange={e => setFigmaUrl(e.target.value)}
              placeholder="Figma 文件链接：https://www.figma.com/file/…"
              className="flex-1 px-3 py-2 rounded-lg text-[12px] outline-none"
              style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            <input value={figmaToken} onChange={e => setFigmaToken(e.target.value)} type="password"
              placeholder="Personal Access Token"
              className="w-[210px] px-3 py-2 rounded-lg text-[12px] outline-none"
              style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            <button onClick={() => runFigma()} disabled={figmaBusy}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-[12px] font-semibold disabled:opacity-60"
              style={{ background: "var(--accent)", color: "#fff" }}>
              {figmaBusy ? <Loader2 size={12} className="animate-spin" /> : <Figma size={12} />} 导入</button>
          </div>
        </div>

        {busy && <div className="text-[11.5px] mb-3 inline-flex items-center gap-1.5" style={{ color: "var(--text-tertiary)" }}><Wand2 size={12} /> 正在深度处理，长文档约需 20~60 秒…</div>}
        {err && <div className="text-[12px] mb-3" style={{ color: "#b42318" }}>{err}</div>}

        {/* 产出 */}
        {out && (
          <div className="rounded-2xl px-5 py-4" style={{ background: "var(--bg-secondary)", borderLeft: "3px solid var(--accent)", border: "1px solid var(--border)" }}>
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[12px] font-semibold" style={{ color: "var(--accent)" }}>产出 · {out.note}</span>
              {out.file && (
                <button onClick={() => openArtifact(out.conv, { filename: out.file, download_url: `/api/conversations/${out.conv}/download/${out.file}` })}
                  className="ml-auto inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11.5px] hover:bg-[var(--bg-tertiary)]"
                  style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}><Eye size={12} /> 在画布打开</button>
              )}
            </div>
            {out.file ? (
              <div className="rounded-xl px-3.5 py-3 text-[12px] leading-relaxed"
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                产物已经保存并在右侧画布打开。HTML、SVG 等设计稿在画布中按最终效果预览，源代码不会再作为正文铺满页面。
              </div>
            ) : (
              <pre className="text-[12px] whitespace-pre-wrap leading-relaxed max-h-[380px] overflow-y-auto" style={{ color: "var(--text-primary)", fontFamily: "inherit" }}>{out.content}</pre>
            )}
            <div className="mt-3 pt-3 flex items-center justify-between" style={{ borderTop: "1px solid var(--border)" }}>
              <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>结果已写回当前对话，手机端同步后也能继续处理。</span>
              <button onClick={() => set({ desktopView: null, sid: out.conv })}
                className="px-3 py-1.5 rounded-lg text-[11.5px] font-medium text-white" style={{ background: "var(--accent)" }}>回到对话继续</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

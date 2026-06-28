"use client";
/** components/desktop/EditorPane.tsx — 借鉴 Ridge「pane = xterm 或 Monaco 双模式」。
 *
 *  一个可入驻分屏格/预览区的代码编辑器：优先加载 Monaco（CDN，AMD loader + 跨域 worker 代理），
 *  加载失败（网络/CSP/worker）则**优雅降级**为轻量 textarea 编辑器——绝不出现"坏掉的空格子"。
 *  读用 local:read，存盘用 local:write（原地写回已存在文件，≤1MB）。Ctrl+S / 按钮保存，dirty 标记。 */
import { useEffect, useRef, useState } from "react";
import { getLocal } from "@/lib/desktop";
import { loadScript } from "./util";
import { Save, FileCode, Loader2 } from "lucide-react";

const MONACO_VER = "0.52.2";
const MONACO_BASE = `https://cdn.jsdelivr.net/npm/monaco-editor@${MONACO_VER}/min`;

function langFromExt(ext: string): string {
  const e = (ext || "").replace(/^\./, "").toLowerCase();
  const map: Record<string, string> = {
    ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript", py: "python",
    json: "json", md: "markdown", html: "html", css: "css", scss: "scss", c: "c", h: "c",
    cpp: "cpp", hpp: "cpp", cc: "cpp", java: "java", go: "go", rs: "rust", rb: "ruby",
    php: "php", sh: "shell", bash: "shell", yml: "yaml", yaml: "yaml", xml: "xml", sql: "sql",
    toml: "ini", ini: "ini", txt: "plaintext",
  };
  return map[e] || "plaintext";
}

let _monacoLoading: Promise<any> | null = null;
function loadMonaco(): Promise<any> {
  const W = window as any;
  if (W.monaco) return Promise.resolve(W.monaco);
  if (_monacoLoading) return _monacoLoading;
  _monacoLoading = (async () => {
    // 跨域 worker 代理：用 data: worker importScripts CDN 的 workerMain，避免 cross-origin worker 报错
    W.MonacoEnvironment = {
      getWorkerUrl: () =>
        "data:text/javascript;charset=utf-8," +
        encodeURIComponent(
          `self.MonacoEnvironment={baseUrl:'${MONACO_BASE}/'};importScripts('${MONACO_BASE}/vs/base/worker/workerMain.js');`
        ),
    };
    await loadScript(`${MONACO_BASE}/vs/loader.js`);
    await new Promise<void>((resolve, reject) => {
      try {
        W.require.config({ paths: { vs: `${MONACO_BASE}/vs` } });
        W.require(["vs/editor/editor.main"], () => resolve(), reject);
      } catch (e) { reject(e); }
    });
    return W.monaco;
  })();
  return _monacoLoading;
}

export function EditorPane({ path, onClose }: { path: string; onClose?: () => void }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const edRef = useRef<any>(null);
  const baseRef = useRef("");                 // 原始内容（判 dirty）
  const [mode, setMode] = useState<"loading" | "monaco" | "textarea">("loading");
  const [text, setText] = useState("");
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState("");

  // 读文件 → 尝试 Monaco，失败退 textarea
  useEffect(() => {
    let alive = true;
    const L = getLocal();
    if (!L || !L.read) { setMode("textarea"); setStatus("文件接口不可用"); return; }
    L.read(path).then((r: any) => {
      if (!alive) return;
      if (!r || !r.ok || r.kind !== "text") { setMode("textarea"); setStatus((r && r.error) || "无法读取为文本"); return; }
      baseRef.current = r.text || "";
      setText(r.text || "");
      loadMonaco().then((monaco) => {
        if (!alive || !hostRef.current) return;
        const ed = monaco.editor.create(hostRef.current, {
          value: r.text || "", language: langFromExt(r.ext || ""),
          theme: "vs-dark", fontSize: 13, minimap: { enabled: false },
          automaticLayout: true, scrollBeyondLastLine: false, tabSize: 2,
        });
        ed.onDidChangeModelContent(() => setDirty(ed.getValue() !== baseRef.current));
        edRef.current = ed;
        setMode("monaco");
      }).catch(() => { if (alive) setMode("textarea"); });   // Monaco 失败 → 文本框兜底
    }).catch((e: any) => { if (alive) { setMode("textarea"); setStatus(String(e?.message || e)); } });
    return () => { alive = false; try { edRef.current?.dispose?.(); } catch (_e) {} };
  }, [path]);

  const save = async () => {
    const L = getLocal();
    if (!L || !(L as any).write) { setStatus("写接口不可用（需重装含本版的桌面端）"); return; }
    const val = mode === "monaco" ? (edRef.current?.getValue() ?? "") : (taRef.current?.value ?? text);
    setStatus("保存中…");
    const r: any = await (L as any).write(path, val);
    if (r && r.ok) { baseRef.current = val; setDirty(false); setStatus("已保存 ✓"); setTimeout(() => setStatus(""), 1800); }
    else { setStatus("保存失败：" + ((r && r.error) || "未知")); }
  };
  const saveRef = useRef(save); saveRef.current = save;

  // Ctrl/Cmd+S 保存
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); saveRef.current(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const fname = path.split(/[\\/]/).pop() || path;
  const canSave = dirty || mode === "textarea";
  return (
    <div className="flex flex-1 min-h-0 min-w-0 flex-col">
      <div className="flex items-center gap-2 px-4 py-2" style={{ borderBottom: "1px solid var(--border)" }}>
        <FileCode size={13} style={{ color: "var(--text-tertiary)" }} />
        <span className="text-[12px] font-mono truncate" style={{ color: "var(--text-secondary)" }}>{fname}{dirty ? " ●" : ""}</span>
        {mode === "textarea" && <span className="text-[10px] flex-shrink-0" style={{ color: "var(--text-tertiary)" }}>（轻量编辑器）</span>}
        <span className="text-[10px] ml-auto truncate" style={{ color: /失败/.test(status) ? "var(--danger,#DC2626)" : "var(--text-tertiary)" }}>{status}</span>
        <button onClick={save} disabled={!canSave}
          className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-[12px] font-medium text-white flex-shrink-0"
          style={{ background: "var(--accent)", opacity: canSave ? 1 : 0.5 }}><Save size={11} /> 保存</button>
        {onClose && <button onClick={onClose} className="px-2.5 py-1.5 rounded-lg text-[12px] flex-shrink-0" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>关闭</button>}
      </div>
      <div className="flex-1 min-h-0 relative" style={{ background: "#1E1E1E" }}>
        {mode === "loading" && (
          <div className="absolute inset-0 flex items-center justify-center text-[12px] gap-2" style={{ color: "var(--text-tertiary)" }}>
            <Loader2 size={14} className="animate-spin" /> 加载编辑器…
          </div>
        )}
        <div ref={hostRef} className="w-full h-full" style={{ display: mode === "monaco" ? "block" : "none" }} />
        {mode === "textarea" && (
          <textarea ref={taRef} defaultValue={text} onChange={(e) => setDirty(e.target.value !== baseRef.current)} spellCheck={false}
            className="w-full h-full p-3 outline-none resize-none font-mono text-[13px]"
            style={{ background: "#1E1E1E", color: "#E4E4E7", border: "none", lineHeight: 1.5 }} />
        )}
      </div>
    </div>
  );
}

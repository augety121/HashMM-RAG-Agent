"use client";

import { useEffect, useState } from "react";
import { ChevronDown, Code2, FileText, Globe2, LayoutTemplate, ShieldCheck, X } from "lucide-react";
import { useStore } from "@/lib/store";
import { isDesktop } from "@/lib/desktop";
import { ArtifactPanel, type ArtifactItem } from "./ArtifactPanel";
import { RightContextPanel } from "./RightContextPanel";
import { BrowserInspector } from "./BrowserInspector";

function artifactMeta(type?: string) {
  if (type === "html") return { label: "画布", Icon: LayoutTemplate };
  if (type === "code") return { label: "代码", Icon: Code2 };
  return { label: "文件", Icon: FileText };
}

/**
 * V345 统一右侧检查器：会话证据、Agent、运行、文件以及代码/画布共享同一个
 * 可拖拽容器。打开产物只切换检查器页签，不再用另一套右栏替换上下文面板。
 */
export function WorkspaceInspector() {
  const set = useStore(s => s.set);
  const artifact = useStore(s => s.artifactPanel);
  const artifactTabs = useStore(s => s.artifactTabs);
  const browser = useStore(s => s.browserPanel);
  const tab = useStore(s => s.inspectorTab);
  const [artifactMenu, setArtifactMenu] = useState(false);
  const [isDesktopEnv, setIsDesktopEnv] = useState(false);
  const selectedArtifact = artifact || artifactTabs[artifactTabs.length - 1];
  const meta = artifactMeta(selectedArtifact?.type);
  const artifactKey = artifact ? `${artifact.convId || ""}|${artifact.filename}|${artifact.download_url}` : "";

  useEffect(() => {
    if (artifactKey) set({ rightPanelOpen: true, inspectorTab: "artifact" });
  }, [artifactKey, set]);

  useEffect(() => { setIsDesktopEnv(isDesktop()); }, []);

  const close = () => {
    try { localStorage.setItem("hmm_right_panel", "0"); } catch { /* */ }
    set({ rightPanelOpen: false });
  };

  const activeTab = browser && tab === "browser" ? "browser" : artifact && tab === "artifact" ? "artifact" : "context";

  return (
    <aside className="flex flex-col h-full w-full min-w-0" style={{ background: "var(--bg-primary)", borderLeft: "1px solid var(--border)" }}>
      <div className={`h-11 px-2.5 flex items-center gap-1 shrink-0 ${isDesktopEnv ? "titlebar-safe" : ""}`} style={{ borderBottom: "1px solid var(--border)" }}>
        <button onClick={() => set({ inspectorTab: "context", rightPanelOpen: true })}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11.5px] font-medium transition-colors"
          style={{ background: activeTab === "context" ? "var(--bg-tertiary)" : "transparent", color: activeTab === "context" ? "var(--text-primary)" : "var(--text-tertiary)" }}>
          <ShieldCheck size={13} /> 上下文
        </button>
        {browser && (
          <button onClick={() => set({ inspectorTab: "browser", rightPanelOpen: true })}
            className="inline-flex min-w-0 items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11.5px] font-medium transition-colors"
            style={{ background: activeTab === "browser" ? "var(--bg-tertiary)" : "transparent", color: activeTab === "browser" ? "var(--text-primary)" : "var(--text-tertiary)" }}
            title={browser.title || browser.url}>
            <Globe2 size={13} className="flex-shrink-0" />
            <span className="truncate max-w-[82px]">浏览器</span>
          </button>
        )}
        {(artifact || artifactTabs.length > 0) && (
          <div className="relative flex min-w-0 items-center">
          <button onClick={() => {
              const next = artifact || artifactTabs[artifactTabs.length - 1];
              set({ artifactPanel: next || null, inspectorTab: "artifact", rightPanelOpen: true });
            }}
            className="inline-flex min-w-0 items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11.5px] font-medium transition-colors"
            style={{ background: activeTab === "artifact" ? "var(--bg-tertiary)" : "transparent", color: activeTab === "artifact" ? "var(--text-primary)" : "var(--text-tertiary)" }}
            title={selectedArtifact?.filename || "产物"}>
            <meta.Icon size={13} className="flex-shrink-0" />
            <span className="truncate max-w-[72px]">{meta.label}</span>
          </button>
          {artifactTabs.length > 1 && <button onClick={() => setArtifactMenu(v => !v)} className="p-1 rounded-md hover:bg-[var(--bg-tertiary)]" aria-label="切换产物"><ChevronDown size={12} /></button>}
          {artifactMenu && <div className="absolute left-0 top-9 z-40 w-[250px] py-1 rounded-xl shadow-lg" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
            <div className="px-3 py-1 text-[9.5px] font-semibold" style={{ color: "var(--text-tertiary)" }}>本会话产物</div>
            {[...artifactTabs].reverse().map(item => {
              const itemMeta = artifactMeta(item.type);
              return <button key={`${item.convId || ""}|${item.filename}`} onClick={() => { set({ artifactPanel: item, inspectorTab: "artifact", rightPanelOpen: true }); setArtifactMenu(false); }}
                className="w-full px-3 py-2 flex items-center gap-2 text-left hover:bg-[var(--bg-secondary)]">
                <itemMeta.Icon size={13} style={{ color: "var(--text-tertiary)" }} />
                <span className="min-w-0 flex-1 truncate text-[11px]" style={{ color: "var(--text-primary)" }}>{item.filename}</span>
              </button>;
            })}
          </div>}
          </div>
        )}
        <button onClick={close} className="ml-auto p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" title="关闭检查器" aria-label="关闭检查器">
          <X size={15} style={{ color: "var(--text-tertiary)" }} />
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-hidden">
        {activeTab === "browser" && browser ? (
          <BrowserInspector />
        ) : activeTab === "artifact" && artifact ? (
          <ArtifactPanel artifact={artifact as ArtifactItem} embedded onClose={() => set({ artifactPanel: null, inspectorTab: "context" })} />
        ) : (
          <RightContextPanel embedded onClose={close} />
        )}
      </div>
    </aside>
  );
}

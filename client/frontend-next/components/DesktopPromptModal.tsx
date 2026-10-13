"use client";

/**
 * First-party desktop approval surface.
 *
 * Main process owns request ids, available decisions and fail-closed timeout.
 * This component only renders the bounded protocol and returns one advertised
 * decision. Backdrop/Escape only defer the prompt; they neither approve nor
 * deny the long-running task. The explicit cancel/deny option remains visible
 * in the prompt and the request stays recoverable from the Run inspector.
 */
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Globe2, RefreshCw, RotateCcw, ShieldCheck, X } from "lucide-react";
import { getDesktop } from "@/lib/desktop";
import type { DesktopPrompt } from "@/lib/desktop";

function PromptIcon({ kind }: { kind: DesktopPrompt["kind"] }) {
  if (kind === "danger") return <RotateCcw size={19} />;
  if (kind === "warning") return <AlertTriangle size={19} />;
  if (kind === "update") return <RefreshCw size={19} />;
  if (kind === "privacy") return <ShieldCheck size={19} />;
  return <Globe2 size={19} />;
}

export function DesktopPromptModal() {
  const [prompt, setPrompt] = useState<DesktopPrompt | null>(null);
  const defaultButton = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const desktop = getDesktop();
    if (!desktop?.onDesktopPrompt) return;
    const off = desktop.onDesktopPrompt((next) => setPrompt(next));
    desktop.desktopPromptReady?.();
    return off;
  }, []);

  const decide = (decision?: string) => {
    if (!prompt) return;
    const selected = decision || prompt.cancelId;
    try { getDesktop()?.resolveDesktopPrompt?.(prompt.id, selected); } catch { /* main process times out fail-closed */ }
    setPrompt(null);
  };

  const defer = () => setPrompt(null);

  useEffect(() => {
    if (!prompt) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); defer(); }
    };
    window.addEventListener("keydown", onKeyDown);
    const frame = requestAnimationFrame(() => defaultButton.current?.focus());
    return () => { window.removeEventListener("keydown", onKeyDown); cancelAnimationFrame(frame); };
    // The request id is the lifecycle boundary; hiding does not resolve it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prompt?.id]);

  if (!prompt) return null;
  const dangerous = prompt.kind === "danger";

  return (
    <div
      className="fixed inset-0 z-[180] flex items-center justify-center px-4"
      style={{ background: "rgba(15, 23, 42, .34)", backdropFilter: "blur(8px)" }}
      onMouseDown={(event) => { if (event.target === event.currentTarget) defer(); }}
      role="presentation"
    >
      <section
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="desktop-prompt-title"
        aria-describedby="desktop-prompt-description"
        className="relative w-full max-w-[520px] overflow-hidden rounded-[22px]"
        style={{
          background: "var(--bg-primary)",
          border: "1px solid var(--border)",
          boxShadow: "0 28px 80px rgba(15, 23, 42, .22), 0 3px 14px rgba(15, 23, 42, .08)",
        }}
      >
        <div className="px-6 pt-6 pb-5">
          <div className="flex items-start gap-3.5 pr-8">
            <div
              className="mt-0.5 flex h-10 w-10 flex-none items-center justify-center rounded-2xl"
              style={{
                background: dangerous ? "rgba(239, 68, 68, .09)" : "color-mix(in srgb, var(--accent) 10%, transparent)",
                color: dangerous ? "#dc2626" : "var(--accent)",
                border: dangerous ? "1px solid rgba(239, 68, 68, .16)" : "1px solid color-mix(in srgb, var(--accent) 17%, transparent)",
              }}
            >
              <PromptIcon kind={prompt.kind} />
            </div>
            <div className="min-w-0 flex-1">
              <p className="mb-1 text-[11px] font-semibold tracking-[.08em]" style={{ color: dangerous ? "#dc2626" : "var(--accent)" }}>
                {prompt.eyebrow}
              </p>
              <h2 id="desktop-prompt-title" className="text-[17px] font-semibold leading-6" style={{ color: "var(--text-primary)" }}>
                {prompt.title}
              </h2>
            </div>
          </div>
          <button
            type="button"
            onClick={defer}
            className="absolute right-4 top-4 flex h-8 w-8 items-center justify-center rounded-xl transition-colors hover:bg-[var(--bg-tertiary)]"
            style={{ color: "var(--text-tertiary)" }}
            aria-label="稍后处理"
          >
            <X size={16} />
          </button>

          <div id="desktop-prompt-description" className="mt-5 space-y-3">
            {prompt.message && <p className="text-[13px] leading-5" style={{ color: "var(--text-secondary)" }}>{prompt.message}</p>}
            {prompt.target && (
              <div className="rounded-xl px-3.5 py-3" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                <p className="mb-1 text-[10.5px] font-medium" style={{ color: "var(--text-tertiary)" }}>目标</p>
                <p className="break-all text-[12px] leading-[18px]" style={{ color: "var(--text-primary)" }}>{prompt.target}</p>
              </div>
            )}
            {prompt.detail && <p className="whitespace-pre-wrap text-[12px] leading-[19px]" style={{ color: "var(--text-secondary)" }}>{prompt.detail}</p>}
            {prompt.boundary && (
              <div className="flex gap-2.5 rounded-xl px-3.5 py-3" style={{ background: dangerous ? "rgba(239, 68, 68, .055)" : "color-mix(in srgb, var(--accent) 5%, transparent)", border: dangerous ? "1px solid rgba(239, 68, 68, .13)" : "1px solid color-mix(in srgb, var(--accent) 12%, transparent)" }}>
                <ShieldCheck size={15} className="mt-0.5 flex-none" style={{ color: dangerous ? "#dc2626" : "var(--accent)" }} />
                <p className="text-[11.5px] leading-[18px]" style={{ color: "var(--text-secondary)" }}>{prompt.boundary}</p>
              </div>
            )}
          </div>
        </div>

        <footer className="flex flex-wrap justify-end gap-2 px-6 py-4" style={{ background: "var(--bg-secondary)", borderTop: "1px solid var(--border)" }}>
          {prompt.buttons.map((button) => {
            const primary = button.tone === "primary";
            const danger = button.tone === "danger";
            return (
              <button
                key={button.id}
                ref={button.id === prompt.defaultId ? defaultButton : undefined}
                type="button"
                onClick={() => decide(button.id)}
                className="min-h-9 rounded-xl px-4 text-[12.5px] font-medium outline-none transition-all focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2"
                style={primary ? {
                  background: "var(--accent)", color: "white", border: "1px solid transparent",
                } : danger ? {
                  background: "transparent", color: "#dc2626", border: "1px solid rgba(239, 68, 68, .24)",
                } : {
                  background: "var(--bg-primary)", color: "var(--text-primary)", border: "1px solid var(--border)",
                }}
              >
                {button.label}
              </button>
            );
          })}
        </footer>
      </section>
    </div>
  );
}

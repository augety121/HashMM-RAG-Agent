"use client";
import { useEffect, useRef, useState } from "react";

interface Props { code: string; }

export function MermaidBlock({ code }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const mermaid = (window as any).mermaid;
    if (!mermaid) {
      setError("Mermaid.js 未加载");
      return;
    }
    try {
      const id = `mermaid_${Math.random().toString(36).slice(2, 8)}`;
      mermaid.render(id, code).then((result: { svg: string }) => {
        if (ref.current) ref.current.innerHTML = result.svg;
      }).catch((e: unknown) => setError(String(e)));
    } catch (e) {
      setError(String(e));
    }
  }, [code]);

  if (error) return <pre className="text-red-400 text-[12px] p-3">{error}</pre>;
  return <div ref={ref} className="mermaid-block" />;
}

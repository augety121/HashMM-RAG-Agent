"use client";

import { useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, Link2 } from "lucide-react";
import type { GroundingClaim, GroundingLedger } from "@/lib/types";

const STATUS: Record<GroundingClaim["status"], { label: string; color: string }> = {
  supported: { label: "证据已关联", color: "#059669" },
  inferred: { label: "无引用·推断", color: "#d97706" },
  unsupported: { label: "证据不支持", color: "#dc2626" },
  invalid_citation: { label: "引用不存在", color: "#dc2626" },
};

export function GroundingAudit({ ledger }: { ledger?: GroundingLedger }) {
  const [open, setOpen] = useState(false);
  if (!ledger || ledger.status === "not_evaluable" || !ledger.total_factual_claims) return null;

  const visibleClaims = ledger.claims.filter((claim, index, claims) => {
    const key = claim.claim_span.text.replace(/\s+/g, " ").trim().toLocaleLowerCase();
    return claims.findIndex(candidate =>
      candidate.claim_span.text.replace(/\s+/g, " ").trim().toLocaleLowerCase() === key
    ) === index;
  });
  const ok = ledger.status === "passed";
  const accent = ok ? "#059669" : "#d97706";
  const pct = ledger.coverage_ratio == null ? "—" : `${Math.round(ledger.coverage_ratio * 100)}%`;

  return (
    <div className="mt-3 rounded-xl overflow-hidden"
      style={{ background: "var(--bg-secondary)", border: `1px solid ${ok ? "rgba(5,150,105,.28)" : "rgba(217,119,6,.32)"}` }}>
      <button type="button" onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-2.5 px-3 py-2.5 text-left">
        {ok ? <CheckCircle2 size={15} style={{ color: accent }} /> : <AlertTriangle size={15} style={{ color: accent }} />}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>逐主张证据审计</span>
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded"
              style={{ color: accent, background: `${accent}12` }}>
              {ledger.supported_claims}/{ledger.total_factual_claims} · {pct}
            </span>
            {ledger.review_required && (
              <span className="text-[10px]" style={{ color: "#dc2626" }}>需要复核</span>
            )}
          </div>
          <div className="text-[10px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
            {ledger.inferred_claims ? `${ledger.inferred_claims} 条无明确引用` : "所有检测到的事实主张均有证据关联"}
            {ledger.unsupported_claims ? ` · ${ledger.unsupported_claims} 条证据不足` : ""}
            {ledger.invalid_citation_claims ? ` · ${ledger.invalid_citation_claims} 条引用无效` : ""}
          </div>
        </div>
        <ChevronDown size={14} className={`transition-transform ${open ? "rotate-180" : ""}`}
          style={{ color: "var(--text-tertiary)" }} />
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-2" style={{ borderTop: "1px solid var(--border)" }}>
          <div className="pt-2 text-[10px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
            {ledger.disclaimer}
          </div>
          {visibleClaims.map((claim) => {
            const state = STATUS[claim.status];
            return (
              <div key={claim.id} className="rounded-lg p-2.5"
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                <div className="flex items-start gap-2">
                  <span className="text-[9px] px-1.5 py-0.5 rounded whitespace-nowrap mt-0.5"
                    style={{ color: state.color, background: `${state.color}12` }}>{state.label}</span>
                  <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                    {claim.claim_span.text}
                  </p>
                </div>
                {claim.evidence.length > 0 && (
                  <div className="mt-1.5 ml-[76px] flex flex-wrap gap-1.5">
                    {claim.evidence.filter((e, index, all) =>
                      all.findIndex(candidate =>
                        (candidate.filename || candidate.doc_id || candidate.chunk_id)
                          === (e.filename || e.doc_id || e.chunk_id)
                        && candidate.page === e.page
                      ) === index
                    ).map((e, i) => (
                      <span key={`${claim.id}-${e.source_index}-${i}`}
                        className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded"
                        style={{ color: "var(--text-tertiary)", background: "var(--bg-tertiary)" }}>
                        <Link2 size={9} />[{e.source_index}] {e.filename || e.doc_id || e.chunk_id || "来源"}
                        {e.page && e.page > 0 ? ` p.${e.page}` : ""}
                        {typeof e.overlap === "number" ? ` · 关联${Math.round(e.overlap * 100)}%` : ""}
                      </span>
                    ))}
                  </div>
                )}
                {!!claim.invalid_citations?.length && (
                  <div className="mt-1 text-[9px] ml-[76px]" style={{ color: "#dc2626" }}>
                    不存在的引用：{claim.invalid_citations.map(n => `[${n}]`).join("、")}
                  </div>
                )}
              </div>
            );
          })}
          {ledger.truncated && (
            <div className="text-[9px]" style={{ color: "var(--text-tertiary)" }}>主张过多，仅展示前 80 条。</div>
          )}
        </div>
      )}
    </div>
  );
}

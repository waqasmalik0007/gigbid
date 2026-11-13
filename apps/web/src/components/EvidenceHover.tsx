"use client";
import { useId, useState } from "react";

export type Evidence = { evidence_id: string; type: string; title: string; snippet: string; outcome?: string | null };
export type Claim = { text: string; evidence_ids: string[]; status: string; note?: string | null };

const TONE: Record<string, string> = { supported: "good", partial: "warn", unsupported: "bad", unchecked: "muted" };

/** A claim sentence; hover or keyboard focus shows the evidence behind it. */
export function EvidenceHover({ claim, evidence }: { claim: Claim; evidence: Record<string, Evidence> }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const items = claim.evidence_ids.map((e) => evidence[e]).filter(Boolean);
  return (
    <span
      className={`claim ${TONE[claim.status] ?? "muted"}`}
      tabIndex={0}
      aria-describedby={id}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      data-testid="claim"
    >
      {claim.text}
      <span role="tooltip" id={id} className={`tip ${open ? "open" : ""}`}>
        <strong>{claim.status}</strong>
        {claim.note && <em> — {claim.note}</em>}
        {items.length === 0 && <span className="muted"> No evidence linked.</span>}
        {items.map((e) => (
          <span key={e.evidence_id} className="tip-item">
            <code>{e.evidence_id}</code> · {e.type}
            {e.outcome ? ` · ${e.outcome}` : ""}
            <br />
            {e.title}: {e.snippet.slice(0, 220)}…
          </span>
        ))}
      </span>
    </span>
  );
}

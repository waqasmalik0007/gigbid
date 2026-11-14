"use client";
import { useMemo, useState } from "react";
import { call } from "@/lib/api/client";
import { Badge } from "./States";
import { Claim, Evidence, EvidenceHover } from "./EvidenceHover";

type Payload = Record<string, any>;

function annotate(letter: string, claims: Claim[], evidence: Record<string, Evidence>) {
  // split the letter into sentences; wrap the ones that are verified claims
  const sentences = letter.split(/(?<=[.!?])\s+/);
  const clean = (s: string) => s.replace(/\[[a-z]+_[\w-]+\]/g, "").trim();
  return sentences.map((s, i) => {
    const c = claims.find((x) => clean(s).startsWith(x.text.slice(0, 40)) || x.text.startsWith(clean(s).slice(0, 40)));
    return c ? (
      <span key={i}>
        <EvidenceHover claim={c} evidence={evidence} />{" "}
      </span>
    ) : (
      <span key={i}>{s} </span>
    );
  });
}

export function ApprovalPanel({ threadId, payload, onDone }: { threadId: string; payload: Payload; onDone: (s: string) => void }) {
  const [mode, setMode] = useState<"view" | "edit" | "reject">("view");
  const [text, setText] = useState<string>(payload.cover_letter ?? payload.reply?.body ?? "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [paraBusy, setParaBusy] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [gone, setGone] = useState<string[]>([]);
  const isProposal = payload.kind === "proposal";
  const evidence = useMemo(() => {
    const m: Record<string, Evidence> = {};
    for (const e of payload.evidence ?? []) m[e.evidence_id] = e;
    return m;
  }, [payload]);
  const claims: Claim[] = payload.claims ?? [];
  const lastCritique = (payload.critiques ?? []).at(-1);

  async function decide(action: "approve" | "edit" | "reject") {
    setBusy(true);
    setErr(null);
    try {
      await call(`/approvals/${encodeURIComponent(threadId)}`, {
        method: "POST",
        body: JSON.stringify({
          action,
          edited_text: action === "edit" ? text : undefined,
          reason: action === "reject" ? reason : undefined,
          slot: payload.reply?.proposed_slots?.[0],
        }),
      });
      onDone(action);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function regenerate(i: number) {
    setParaBusy(i);
    try {
      const paras = text.split(/\n\s*\n/);
      const r = await call<{ text: string; removed?: string[] }>(`/studio/regenerate`, {
        method: "POST",
        body: JSON.stringify({ thread_id: threadId, paragraph: paras[i], instruction: "make it more specific to the client" }),
      });
      paras[i] = r.text;
      setText(paras.join("\n\n"));
      setNote(r.removed?.length
        ? `Claim verifier removed ${r.removed.length} unsupported sentence(s): ${r.removed.join(" | ")}`
        : "Regenerated; every claim is supported by current evidence.");
      setMode("edit");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setParaBusy(null);
    }
  }

  async function removeEvidence(id: string) {
    try {
      await call(`/portfolio/${encodeURIComponent(id)}`, { method: "DELETE" });
      setGone((g) => [...g, id]);
      setNote(`${id} removed from your portfolio. Regenerate a paragraph: claims that relied on it are now unsupported.`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <section className="card approval" aria-label="Approval" data-testid="approval-panel">
      <header className="row between">
        <h2>{isProposal ? "Proposal awaiting your approval" : "Reply awaiting your approval"}</h2>
        {payload.dry_run && (
          <Badge tone="info">
            dry run: {payload.dry_run.connects_cost ?? 0} Connects
            {payload.dry_run.balance != null ? ` · balance ${payload.dry_run.balance}` : ""}
          </Badge>
        )}
      </header>

      {mode === "view" && (
        <div className="letter" data-testid="letter">
          {isProposal
            ? (text || "").split(/\n\s*\n/).map((p, i) => (
                <p key={i}>
                  {annotate(p, claims, evidence)}
                  <button className="btn link small" onClick={() => regenerate(i)} disabled={paraBusy !== null}
                          aria-label={`Regenerate paragraph ${i + 1}`}>
                    {paraBusy === i ? "regenerating…" : "↻ regenerate"}
                  </button>
                </p>
              ))
            : <p>{text}</p>}
        </div>
      )}
      {mode === "edit" && (
        <label className="field">
          <span>Edit the text (your edit is diffed, re-checked by Compliance, and may teach GigBid your style)</span>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={12} data-testid="edit-box" />
        </label>
      )}
      {mode === "reject" && (
        <label className="field">
          <span>Why are you rejecting it? (required; GigBid learns from this)</span>
          <input value={reason} onChange={(e) => setReason(e.target.value)} data-testid="reject-reason" />
        </label>
      )}

      {note && <p className="muted" role="status" data-testid="regen-note">{note}</p>}

      {isProposal && (payload.evidence ?? []).length > 0 && (
        <details className="sub" data-testid="evidence-list">
          <summary>Evidence used ({payload.evidence.length})</summary>
          <ul>
            {payload.evidence.map((e: Evidence) => (
              <li key={e.evidence_id}>
                <code>{e.evidence_id}</code> · {e.type} · {e.title}{" "}
                {gone.includes(e.evidence_id)
                  ? <Badge tone="warn">removed</Badge>
                  : <button className="btn link small" onClick={() => removeEvidence(e.evidence_id)}
                            aria-label={`Remove ${e.evidence_id} from portfolio`}>remove from portfolio</button>}
              </li>
            ))}
          </ul>
        </details>
      )}

      {isProposal && payload.screening_answers?.length > 0 && (
        <div className="sub">
          <h3>Screening answers</h3>
          <ol>{payload.screening_answers.map((a: string, i: number) => <li key={i}>{a}</li>)}</ol>
        </div>
      )}

      <div className="grid3">
        {isProposal && (
          <div className="sub">
            <h3>Critic</h3>
            {lastCritique ? (
              <>
                <Badge tone={lastCritique.passed ? "good" : "warn"}>{lastCritique.passed ? "passed" : "concerns"}</Badge>{" "}
                grounding {lastCritique.grounding} · personalisation {lastCritique.personalisation}
                <ul>{(lastCritique.issues ?? []).map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
                <p className="muted">{payload.critiques.length} critique round(s)</p>
              </>
            ) : <p className="muted">No critique.</p>}
          </div>
        )}
        <div className="sub">
          <h3>Compliance</h3>
          {payload.compliance?.allowed ? <Badge tone="good">allowed</Badge> : <Badge tone="bad">blocked</Badge>}
          {payload.compliance?.redactions?.length > 0 && <p>Removed: {payload.compliance.redactions.join(", ")}</p>}
          {payload.verification && (
            <p>Unsupported claims: {payload.verification.unsupported_before} → {payload.verification.unsupported_after}
              {payload.verification.removed?.length ? ` (${payload.verification.removed.length} removed)` : ""}</p>
          )}
        </div>
        {isProposal && (
          <div className="sub">
            <h3>Price &amp; risk</h3>
            <p><strong>${payload.price?.amount}</strong> {payload.price?.budget_type} · fair band {payload.price?.fair_band?.join("–")}</p>
            <p className="small">{payload.price?.rationale}</p>
            <p>Risk {payload.risk?.risk_score} {(payload.risk?.flags ?? []).map((f: string) => <Badge key={f} tone="warn">{f}</Badge>)}</p>
          </div>
        )}
      </div>

      {err && <p className="bad" role="alert">{err}</p>}
      <footer className="row gap">
        {mode === "view" && (
          <>
            <button className="btn primary" disabled={busy} onClick={() => decide("approve")} data-testid="approve">Approve &amp; send draft</button>
            <button className="btn" disabled={busy} onClick={() => setMode("edit")} data-testid="edit">Edit</button>
            <button className="btn danger" disabled={busy} onClick={() => setMode("reject")} data-testid="reject">Reject</button>
          </>
        )}
        {mode === "edit" && (
          <>
            <button className="btn primary" disabled={busy || !text.trim()} onClick={() => decide("edit")} data-testid="save-edit">Save edit &amp; approve</button>
            <button className="btn ghost" onClick={() => setMode("view")}>Cancel</button>
          </>
        )}
        {mode === "reject" && (
          <>
            <button className="btn danger" disabled={busy || !reason.trim()} onClick={() => decide("reject")} data-testid="confirm-reject">Reject with reason</button>
            <button className="btn ghost" onClick={() => setMode("view")}>Cancel</button>
          </>
        )}
      </footer>
      <p className="muted small">Nothing is sent until you approve. In live mode, Upwork asks you to confirm the draft on upwork.com.</p>
    </section>
  );
}

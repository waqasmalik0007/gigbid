"use client";
import { useEffect, useState } from "react";
import { NodeEvent, streamRun } from "@/lib/api/client";

const LABEL: Record<string, string> = {
  intake: "Intake",
  job_analyst: "Job Analyst",
  risk_assessor: "Risk Assessor",
  fit_matcher: "Fit Matcher",
  decide: "Bid or skip",
  pricing: "Pricing Strategist",
  writer: "Proposal Writer",
  verifier: "Claim verification",
  critic: "Critic",
  compliance: "Compliance Guard",
  preview: "Dry-run preview",
  approval: "Waiting for your approval",
  submit: "Submitted",
  read_thread: "Read thread",
  classify_intent: "Classify intent",
  find_slots: "Find interview slots",
  draft_reply: "Draft reply",
  send_reply: "Sent",
  _run: "Run",
  _error: "Error",
};

function summary(e: NodeEvent): string {
  const d = e.data as Record<string, any>;
  if (e.node === "_run") return `status: ${d.status}`;
  if (d.decision) return `${d.decision.action} — ${(d.decision.reasons ?? []).join("; ")}`;
  if (d.fit_report) return `fit ${d.fit_report.fit_score}`;
  if (d.risk_report) return `risk ${d.risk_report.risk_score}${d.risk_report.is_scam ? " · SCAM" : ""} ${(d.risk_report.flags ?? []).join(", ")}`;
  if (d.price) return `$${d.price.amount} (${d.price.budget_type}) · ${d.price.connects_cost} Connects · EV ${d.price.expected_value}`;
  if (d.verification) return `claims ${d.verification.claims}, unsupported ${d.verification.unsupported_before} → ${d.verification.unsupported_after}`;
  if (d.critiques) return `critic: ${d.critiques.at(-1)?.passed ? "passed" : "revise — " + (d.critiques.at(-1)?.issues ?? []).join("; ")}`;
  if (d.compliance) return d.compliance.allowed ? `allowed${d.compliance.redactions?.length ? " · redacted " + d.compliance.redactions.length : ""}` : `BLOCKED: ${d.compliance.blocked_reason}`;
  if (d.status) return `status: ${d.status}`;
  if (d.errors) return String(d.errors.at(-1));
  return "";
}

/** Live node-by-node progress streamed from LangGraph (via the API's SSE endpoint). */
export function RunTimeline({ threadId, onStatus }: { threadId: string; onStatus?: (s: string) => void }) {
  const [events, setEvents] = useState<NodeEvent[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    setEvents([]);
    streamRun(
      threadId,
      (e) => {
        setEvents((xs) => [...xs, e]);
        const st = (e.data as any)?.status;
        if (e.node === "_run" && st && onStatus) onStatus(st);
      },
      ctl.signal,
    ).catch((e) => {
      if (!ctl.signal.aborted) setErr(String(e));
    });
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);
  return (
    <ol className="timeline" aria-live="polite" data-testid="run-timeline">
      {events.length === 0 && !err && <li className="muted">Waiting for the agents to start…</li>}
      {err && <li className="bad">Stream interrupted: {err}</li>}
      {events.map((e, i) => (
        <li key={i} className={e.node === "_error" ? "bad" : ""}>
          <span className="t-node">{LABEL[e.node] ?? e.node}</span>
          <span className="t-sum">{summary(e)}</span>
          <time>{new Date(e.ts * 1000).toLocaleTimeString()}</time>
        </li>
      ))}
    </ol>
  );
}

"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { call } from "@/lib/api/client";
import { useApi } from "@/lib/useApi";
import { Badge, ErrorBox, Loading } from "@/components/States";
import { RunTimeline } from "@/components/RunTimeline";

type Detail = Record<string, any>;

export default function JobDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: d, error, loading, reload } = useApi<Detail>(`/jobs/${id}`);
  if (loading && !d) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!d) return null;
  const spec = d.job_spec, risk = d.risk_report, fit = d.fit_report, dec = d.decision, price = d.price;
  return (
    <>
      <p><Link href="/inbox">← Inbox</Link></p>
      <h1>{d.job?.title}</h1>
      <p className="muted">{d.job?.category} · {d.job?.budget_type} {d.job?.budget_min}–{d.job?.budget_max} · {d.job?.connects_cost} Connects · source {d.job?.source}</p>
      <div className="row gap">
        {dec && <Badge tone={dec.action === "bid" ? "good" : dec.action === "watch" ? "warn" : "muted"}>{dec.action}</Badge>}
        {d.status && <Badge tone="info">{d.status}</Badge>}
        {d.awaiting_approval && <Link className="btn primary small" href={`/studio/${id}`}>Open proposal studio</Link>}
        {!d.row?.thread_id && <button className="btn small" onClick={async () => { await call(`/jobs/${id}/analyze`, { method: "POST" }); reload(); }}>Analyse</button>}
        {d.row?.thread_id && <Link className="btn small" href={`/runs/${encodeURIComponent(d.row.thread_id)}`}>Run &amp; trace</Link>}
      </div>

      {dec && (
        <section className="card"><h2>Bid or skip</h2>
          <ul>{dec.reasons.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
        </section>
      )}

      <div className="grid2">
        <section className="card"><h2>Job spec</h2>
          {spec ? (
            <>
              <p><strong>Requirements</strong></p><ul>{spec.requirements.map((r: string) => <li key={r}>{r}</li>)}</ul>
              <p><strong>Deliverables:</strong> {spec.deliverables.join("; ") || "–"}</p>
              <p><strong>Hidden needs:</strong> {spec.hidden_needs.join("; ") || "–"}</p>
              <p><strong>Implied skills:</strong> {spec.implied_skills.join(", ") || "–"}</p>
            </>
          ) : <p className="muted">Not analysed yet.</p>}
        </section>
        <section className="card"><h2>Risk report</h2>
          {risk ? (
            <>
              <p>Score <strong>{risk.risk_score}</strong> · client quality {risk.client_quality} {risk.is_scam && <Badge tone="bad">scam</Badge>}</p>
              <p>{risk.flags.map((f: string) => <Badge key={f} tone="warn">{f}</Badge>)}</p>
              {risk.injection_spans?.length > 0 && <><p><strong>Injected instructions found (ignored):</strong></p>
                <ul>{risk.injection_spans.map((s: string, i: number) => <li key={i}><code>{s}</code></li>)}</ul></>}
              <p className="small">{risk.explanation}</p>
            </>
          ) : <p className="muted">–</p>}
        </section>
      </div>

      <section className="card"><h2>Requirement → evidence</h2>
        {fit ? (
          <table className="matrix"><thead><tr><th>Requirement</th><th>Strength</th><th>Portfolio evidence</th></tr></thead>
            <tbody>{fit.matrix.map((m: any) => (
              <tr key={m.requirement}><td>{m.requirement}</td>
                <td><Badge tone={m.strength === "strong" ? "good" : m.strength === "partial" ? "warn" : "bad"}>{m.strength}</Badge></td>
                <td>{m.evidence.length === 0 ? <span className="muted">none</span> : m.evidence.map((e: any) => (
                  <div key={e.evidence_id} className="small"><code>{e.evidence_id}</code> {e.title}</div>))}</td></tr>))}
            </tbody></table>
        ) : <p className="muted">–</p>}
        {fit && <p>Fit score <strong>{fit.fit_score}</strong></p>}
      </section>

      {price && (
        <section className="card"><h2>Price</h2>
          <p><strong>${price.amount}</strong> ({price.budget_type}) · fair band {price.fair_band.join("–")} · {price.connects_cost} Connects ·
            {price.connects_worth_it ? <Badge tone="good">worth it</Badge> : <Badge tone="bad">not worth the Connects</Badge>}</p>
          <p className="small">{price.rationale}</p>
        </section>
      )}

      {d.row?.thread_id && <section className="card"><h2>Agent run</h2><RunTimeline threadId={d.row.thread_id} onStatus={() => reload()} /></section>}

      <section className="card"><h2>Job post (untrusted)</h2><p className="small" style={{ whiteSpace: "pre-wrap" }}>{d.job?.description}</p></section>
    </>
  );
}

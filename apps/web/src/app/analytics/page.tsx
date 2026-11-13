"use client";
import { useApi } from "@/lib/useApi";
import { Empty, ErrorBox, Loading } from "@/components/States";

type A = {
  by_category: Record<string, { sent: number; replied: number; hired: number; connects: number; reply_rate: number | null; hire_rate: number | null }>;
  connects_spent: number; jobs_analysed: number;
  cost_usd_per_job: { mean: number | null; p95: number | null };
  latency_s_per_job: { p50: number | null; p95: number | null };
  top_skip_reasons: [string, number][];
};

const pct = (x: number | null) => (x == null ? "–" : `${Math.round(x * 100)}%`);

export default function Analytics() {
  const { data: a, error, loading, reload } = useApi<A>("/analytics");
  if (loading && !a) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!a) return null;
  const cats = Object.entries(a.by_category);
  return (
    <>
      <h1>Analytics</h1>
      <div className="grid3">
        <div className="card"><h3>Jobs analysed</h3><p style={{ fontSize: "1.6rem" }}>{a.jobs_analysed}</p></div>
        <div className="card"><h3>Cost per job (mean / p95)</h3><p style={{ fontSize: "1.6rem" }}>${a.cost_usd_per_job.mean ?? "–"} / ${a.cost_usd_per_job.p95 ?? "–"}</p></div>
        <div className="card"><h3>Latency per job (p50 / p95)</h3><p style={{ fontSize: "1.6rem" }}>{a.latency_s_per_job.p50 ?? "–"}s / {a.latency_s_per_job.p95 ?? "–"}s</p></div>
      </div>
      <section className="card"><h2>Reply and hire rate by category</h2>
        {cats.length === 0 ? <Empty title="No proposals sent yet" /> : (
          <table><thead><tr><th>Category</th><th>Sent</th><th>Reply rate</th><th>Hire rate</th><th>Connects spent</th></tr></thead>
            <tbody>{cats.map(([c, v]) => <tr key={c}><td>{c}</td><td>{v.sent}</td><td>{pct(v.reply_rate)}</td><td>{pct(v.hire_rate)}</td><td>{v.connects}</td></tr>)}</tbody></table>)}
        <p className="muted">Connects spent in total: {a.connects_spent}</p>
      </section>
      <section className="card"><h2>Top reasons GigBid skipped jobs</h2>
        {a.top_skip_reasons.length === 0 ? <p className="muted">None yet.</p> :
          <ol>{a.top_skip_reasons.map(([r, n]) => <li key={r}>{r} <span className="muted">({n})</span></li>)}</ol>}
      </section>
    </>
  );
}

"use client";
import Link from "next/link";
import { useState } from "react";
import { call } from "@/lib/api/client";
import { useApi } from "@/lib/useApi";
import { Badge, Empty, ErrorBox, Loading } from "@/components/States";

type Job = {
  job_id: string; title: string; category?: string; status?: string; action?: string; fit?: number; risk?: number;
  price?: number; connects?: number; budget?: string; flags: string[]; is_scam: boolean; reasons: string[]; thread_id?: string;
};

const ACTION_TONE: Record<string, "good" | "warn" | "bad" | "muted"> = { bid: "good", watch: "warn", skip: "muted" };

/** Job inbox: ranked by recommended action and fit, with risk badges, price and Connects cost. */
export default function Inbox() {
  const [filter, setFilter] = useState<string>("");
  const { data, error, loading, reload } = useApi<Job[]>(`/jobs${filter ? `?action=${filter}` : ""}`, [filter]);
  const [scouting, setScouting] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function scout() {
    setScouting(true);
    try {
      const r = await call<{ new_jobs: number; queued: number }>("/jobs/scout", { method: "POST" });
      setMsg(`${r.new_jobs} new jobs found; analysing ${r.queued}. Results appear as agents finish.`);
      await reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setScouting(false);
    }
  }

  async function analyze(id: string) {
    await call(`/jobs/${id}/analyze`, { method: "POST" });
    await reload();
  }

  return (
    <>
      <div className="row between gap">
        <h1>Job inbox</h1>
        <div className="row gap">
          <label className="sr-only" htmlFor="flt">Filter</label>
          <select id="flt" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ width: "auto" }}>
            <option value="">All</option><option value="bid">Bid</option><option value="watch">Watch</option><option value="skip">Skip</option>
          </select>
          <button className="btn" onClick={reload}>Refresh</button>
          <button className="btn primary" onClick={scout} disabled={scouting} data-testid="scout">{scouting ? "Scouting…" : "Find new jobs"}</button>
        </div>
      </div>
      {msg && <p className="muted" role="status">{msg}</p>}
      {loading && !data && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}
      {data && data.length === 0 && <Empty title="No jobs yet" hint="Click “Find new jobs” to run your saved searches." />}
      {data && data.length > 0 && (
        <div className="card" style={{ padding: 0, overflowX: "auto" }}>
          <table data-testid="inbox-table">
            <thead><tr><th>Job</th><th>Action</th><th>Fit</th><th className="hide-sm">Risk</th><th>Price</th><th className="hide-sm">Connects</th><th /></tr></thead>
            <tbody>
              {data.map((j) => (
                <tr key={j.job_id}>
                  <td>
                    <Link href={`/jobs/${j.job_id}`}>{j.title}</Link>
                    <div className="small muted">{j.category} · {j.budget}</div>
                    {j.is_scam && <Badge tone="bad">scam</Badge>}
                    {j.flags.filter((f) => !j.is_scam || f !== "off_platform_contact_or_payment").map((f) => <Badge key={f} tone="warn">{f.replaceAll("_", " ")}</Badge>)}
                  </td>
                  <td>{j.action ? <Badge tone={ACTION_TONE[j.action] ?? "muted"}>{j.action}</Badge> : <span className="muted small">{j.status ?? "new"}</span>}</td>
                  <td>{j.fit != null ? j.fit.toFixed(2) : "–"}</td>
                  <td className="hide-sm">{j.risk != null ? j.risk.toFixed(2) : "–"}</td>
                  <td>{j.price != null ? `$${j.price}` : "–"}</td>
                  <td className="hide-sm">{j.connects ?? "–"}</td>
                  <td>
                    {!j.thread_id && j.status !== "duplicate" && <button className="btn small" onClick={() => analyze(j.job_id)}>Analyse</button>}
                    {j.status === "awaiting_approval" && <Link className="btn small primary" href={`/studio/${j.job_id}`}>Review</Link>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

"use client";
import Link from "next/link";
import { useApi } from "@/lib/useApi";
import { Badge, Empty, ErrorBox, Loading } from "@/components/States";

type Run = { thread_id: string; status: string; kind?: string; job_id?: string; trace_id?: string; usage: Record<string, number>; updated?: number };

export default function Runs() {
  const { data, error, loading, reload } = useApi<Run[]>("/runs");
  return (
    <>
      <div className="row between"><h1>Agent runs</h1><button className="btn" onClick={reload}>Refresh</button></div>
      {loading && !data && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}
      {data?.length === 0 && <Empty title="No runs yet" />}
      {data && data.length > 0 && (
        <div className="card" style={{ padding: 0, overflowX: "auto" }}>
          <table><thead><tr><th>Thread</th><th>Status</th><th className="hide-sm">Tokens</th><th className="hide-sm">Cost</th><th>Updated</th></tr></thead>
            <tbody>{data.map((r) => (
              <tr key={r.thread_id}>
                <td><Link href={`/runs/${encodeURIComponent(r.thread_id)}`}>{r.kind}: {r.job_id ?? r.thread_id.split(":").at(-1)}</Link></td>
                <td><Badge tone={r.status === "submitted" ? "good" : r.status === "awaiting_approval" ? "info" : r.status === "dead_letter" || r.status === "needs_attention" ? "bad" : "muted"}>{r.status}</Badge></td>
                <td className="hide-sm">{(r.usage?.tokens_in ?? 0) + (r.usage?.tokens_out ?? 0)}</td>
                <td className="hide-sm">${(r.usage?.usd ?? 0).toFixed(4)}</td>
                <td>{r.updated ? new Date(r.updated * 1000).toLocaleString() : ""}</td>
              </tr>))}</tbody></table>
        </div>
      )}
    </>
  );
}

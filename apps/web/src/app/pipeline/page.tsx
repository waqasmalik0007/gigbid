"use client";
import Link from "next/link";
import { useEffect } from "react";
import { call } from "@/lib/api/client";
import { useApi } from "@/lib/useApi";
import { ErrorBox, Loading } from "@/components/States";

type Board = Record<string, { proposal_id?: string; job_id?: string; title?: string; amount?: number; connects?: number; thread_id?: string }[]>;
const COLS = ["drafted", "submitted", "viewed", "replied", "interview", "hired", "lost"];

/** Kanban that updates from marketplace events (synced by the worker every 2 minutes, or on demand). */
export default function Pipeline() {
  const { data, error, loading, reload } = useApi<Board>("/pipeline");
  useEffect(() => {
    const id = setInterval(async () => { await call("/pipeline/sync", { method: "POST" }).catch(() => null); reload(); }, 20000);
    return () => clearInterval(id);
  }, [reload]);
  return (
    <>
      <div className="row between"><h1>Pipeline</h1>
        <button className="btn" onClick={async () => { await call("/pipeline/sync", { method: "POST" }); reload(); }}>Sync now</button></div>
      {loading && !data && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}
      {data && (
        <div className="kanban" data-testid="kanban">
          {COLS.map((c) => (
            <section className="col" key={c} aria-label={c}>
              <h3>{c} <span className="muted">({data[c]?.length ?? 0})</span></h3>
              {(data[c] ?? []).map((k, i) => (
                <div className="kcard" key={k.proposal_id ?? k.thread_id ?? i}>
                  <Link href={`/jobs/${k.job_id}`}>{k.title ?? k.job_id}</Link>
                  {k.amount != null && <div className="muted">${k.amount} · {k.connects ?? 0} Connects</div>}
                </div>
              ))}
            </section>
          ))}
        </div>
      )}
    </>
  );
}

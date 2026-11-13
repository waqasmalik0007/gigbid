"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useApi } from "@/lib/useApi";
import { RunTimeline } from "@/components/RunTimeline";

type Run = { thread_id: string; trace_id?: string; usage: Record<string, number>; status: string };

/** Live node-by-node view of one LangGraph thread, with a link to the full trace. */
export default function RunViewer() {
  const { id } = useParams<{ id: string }>();
  const thread = decodeURIComponent(id);
  const runs = useApi<Run[]>("/runs");
  const run = runs.data?.find((r) => r.thread_id === thread);
  const traceBase = process.env.NEXT_PUBLIC_TRACE_URL;
  return (
    <>
      <p><Link href="/runs">← Runs</Link></p>
      <h1>Run</h1>
      <p className="muted"><code>{thread}</code></p>
      {run && (
        <p>
          Status <strong>{run.status}</strong> · trace id <code>{run.trace_id}</code>
          {traceBase && run.trace_id && <> · <a href={`${traceBase}${run.trace_id}`} target="_blank" rel="noreferrer">open full trace</a></>}
          {" "}· {(run.usage?.tokens_in ?? 0) + (run.usage?.tokens_out ?? 0)} tokens · ${(run.usage?.usd ?? 0).toFixed(4)}
        </p>
      )}
      <section className="card"><RunTimeline threadId={thread} /></section>
    </>
  );
}

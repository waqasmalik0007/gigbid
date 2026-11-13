"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { call } from "@/lib/api/client";
import { useApi } from "@/lib/useApi";
import { ApprovalPanel } from "@/components/ApprovalPanel";
import { Empty, ErrorBox, Loading } from "@/components/States";
import { RunTimeline } from "@/components/RunTimeline";

/** Proposal studio for one job: the pending approval is re-read from the graph checkpoint on every load,
 * so a page refresh never loses it. */
export default function Studio() {
  const { id } = useParams<{ id: string }>();
  const { data: d, error, loading, reload } = useApi<Record<string, any>>(`/jobs/${id}`);
  const thread = d?.row?.thread_id as string | undefined;
  const [payload, setPayload] = useState<Record<string, any> | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    if (!thread || !d?.awaiting_approval) return;
    call<Record<string, any>>(`/approvals/${encodeURIComponent(thread)}/payload`).then(setPayload).catch(() => setPayload(null));
  }, [thread, d?.awaiting_approval]);

  if (loading && !d) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <>
      <p><Link href={`/jobs/${id}`}>← Job detail</Link></p>
      <h1>Proposal studio</h1>
      <p className="muted">{d?.job?.title}</p>
      {done && <p className="good" role="status" data-testid="decision-status">Decision recorded: {done}. The agents are finishing the run below.</p>}
      {payload && !done && thread && <ApprovalPanel threadId={thread} payload={payload} onDone={(s) => { setDone(s); setTimeout(reload, 1500); }} />}
      {!payload && !done && <Empty title="Nothing awaiting approval" hint={d?.status ? `Run status: ${d.status}` : "Analyse the job first."} />}
      {thread && <section className="card"><h2>Live run</h2><RunTimeline threadId={thread} onStatus={() => reload()} /></section>}
      {d?.submission && <section className="card"><h2>Submitted</h2><pre className="small">{JSON.stringify(d.submission, null, 2)}</pre></section>}
    </>
  );
}

"use client";
import Link from "next/link";
import { useApi } from "@/lib/useApi";
import { ApprovalPanel } from "@/components/ApprovalPanel";
import { Empty, ErrorBox, Loading } from "@/components/States";

type Pending = { thread_id: string; kind: string; payload: Record<string, any> };

export default function Approvals() {
  const { data, error, loading, reload } = useApi<Pending[]>("/approvals");
  return (
    <>
      <div className="row between"><h1>Approvals</h1><button className="btn" onClick={reload}>Refresh</button></div>
      {loading && !data && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}
      {data?.length === 0 && <Empty title="Nothing waiting for you" hint="Proposals and replies appear here before anything is sent." />}
      {data?.map((p) => (
        <div key={p.thread_id}>
          <p className="small muted">
            {p.kind === "proposal" ? <Link href={`/studio/${p.payload.job?.job_id}`}>{p.payload.job?.title}</Link> : `Client thread ${p.thread_id.split(":")[2]}`}
          </p>
          <ApprovalPanel threadId={p.thread_id} payload={p.payload} onDone={() => setTimeout(reload, 1200)} />
        </div>
      ))}
    </>
  );
}

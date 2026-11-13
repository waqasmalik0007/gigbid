"use client";
import Link from "next/link";
import { useState } from "react";
import { call } from "@/lib/api/client";
import { useApi } from "@/lib/useApi";
import { Empty, ErrorBox, Loading } from "@/components/States";

type Thread = { thread_id: string; last: string; n: number };
type Msg = { id: string; sender: string; body: string; sim_time: string };

/** Client threads; GigBid drafts replies and interview slots, which wait in Approvals. */
export default function Messages() {
  const threads = useApi<Thread[]>("/messages");
  const [open, setOpen] = useState<string | null>(null);
  const msgs = useApi<Msg[]>(open ? `/messages/${open}` : null, [open]);
  const [note, setNote] = useState<string | null>(null);
  return (
    <>
      <h1>Messages</h1>
      {threads.loading && !threads.data && <Loading />}
      {threads.error && <ErrorBox error={threads.error} onRetry={threads.reload} />}
      {threads.data?.length === 0 && <Empty title="No client messages yet" hint="Replies to your proposals appear here." />}
      <div className="grid2">
        <ul className="card" style={{ listStyle: "none" }}>
          {threads.data?.map((t) => (
            <li key={t.thread_id}>
              <button className="btn link" onClick={() => setOpen(t.thread_id)} aria-pressed={open === t.thread_id}>
                {t.thread_id}</button> <span className="muted small">{t.n} msg · {t.last?.slice(0, 16)}</span>
            </li>
          ))}
        </ul>
        {open && (
          <section className="card" aria-label="Conversation">
            {msgs.loading && <Loading />}
            {msgs.data?.map((m) => <div key={m.id} className={`bubble ${m.sender}`}><strong>{m.sender}</strong><br />{m.body}</div>)}
            <button className="btn primary" onClick={async () => {
              await call(`/messages/${open}/draft`, { method: "POST" });
              setNote("Drafting a reply… it will appear in Approvals for you to approve, edit or reject.");
            }}>Draft a reply</button>
            {note && <p className="muted" role="status">{note} <Link href="/approvals">Go to approvals</Link></p>}
          </section>
        )}
      </div>
    </>
  );
}

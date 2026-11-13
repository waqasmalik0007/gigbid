"use client";
import { useEffect, useState } from "react";
import { call } from "@/lib/api/client";
import { useApi } from "@/lib/useApi";
import { Badge, ErrorBox, Loading } from "@/components/States";

type User = { id: string; email: string; persona_id: string; settings: Record<string, any> };
type Mem = { style: { key: string; text: string; rule: string; evidence_edits: string[] }[]; outcome: { key: string; text: string; rule: string }[] };

/** Autonomy, budgets, saved searches, connected accounts, portfolio upload and the memory viewer. */
export default function Settings() {
  const me = useApi<User>("/auth/me");
  const mem = useApi<Mem>("/memory");
  const [s, setS] = useState<Record<string, any>>({});
  const [saved, setSaved] = useState<string | null>(null);
  const [upload, setUpload] = useState<string | null>(null);
  useEffect(() => { if (me.data) setS(me.data.settings); }, [me.data]);

  async function save() {
    await call("/auth/settings", { method: "PUT", body: JSON.stringify({
      floor_rate: Number(s.floor_rate), weekly_connects_budget: Number(s.weekly_connects_budget), autonomy: s.autonomy,
      saved_searches: s.saved_searches, marketplace: s.marketplace ?? "gigsim" }) });
    setSaved("Saved.");
    me.reload();
  }

  async function onUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    const { API_URL, getToken } = await import("@/lib/api/client");
    const r = await fetch(`${API_URL}/api/jobs/portfolio/upload`, { method: "POST", body: fd, headers: { Authorization: `Bearer ${getToken()}` } });
    setUpload(r.ok ? `Added ${(await r.json()).evidence_id}` : `Upload failed (${r.status})`);
  }

  if (me.loading && !me.data) return <Loading />;
  if (me.error) return <ErrorBox error={me.error} onRetry={me.reload} />;
  const search = (s.saved_searches ?? [{}])[0] ?? {};
  return (
    <>
      <h1>Settings</h1>
      <div className="grid2">
        <section className="card"><h2>Rates, budget, autonomy</h2>
          <label className="field"><span>Floor rate (USD/h)</span><input type="number" value={s.floor_rate ?? ""} onChange={(e) => setS({ ...s, floor_rate: e.target.value })} /></label>
          <label className="field"><span>Weekly Connects budget</span><input type="number" value={s.weekly_connects_budget ?? ""} onChange={(e) => setS({ ...s, weekly_connects_budget: e.target.value })} /></label>
          <label className="field"><span>Autonomy</span>
            <select value={s.autonomy ?? "suggest"} onChange={(e) => setS({ ...s, autonomy: e.target.value })}>
              <option value="suggest">Suggest only (everything needs my approval)</option>
              <option value="auto_skip_low_fit">Auto-skip low-fit jobs (still never auto-send)</option>
            </select></label>
          <label className="field"><span>Saved search keywords</span>
            <input value={search.query ?? ""} onChange={(e) => setS({ ...s, saved_searches: [{ ...search, query: e.target.value }] })} placeholder="e.g. RAG, dashboard" /></label>
          <button className="btn primary" onClick={save}>Save</button> {saved && <span className="good">{saved}</span>}
        </section>
        <section className="card"><h2>Accounts &amp; portfolio</h2>
          <p>Persona: <strong>{me.data?.persona_id}</strong></p>
          <p>Marketplace: <Badge tone="info">{s.marketplace ?? "gigsim"}</Badge></p>
          <button className="btn" onClick={async () => {
            try { const r = await call<{ authorize_url: string }>("/auth/upwork/start"); window.location.href = r.authorize_url; }
            catch (e) { setSaved(e instanceof Error ? e.message : String(e)); }
          }}>Connect Upwork (OAuth)</button>
          <label className="field"><span>Upload a case study or CV (.md, .txt, .pdf)</span><input type="file" accept=".md,.txt,.pdf" onChange={onUpload} /></label>
          {upload && <p role="status">{upload}</p>}
        </section>
      </div>
      <section className="card" data-testid="memory"><h2>What GigBid has learned</h2>
        <p className="muted small">Memory only changes through explicit promotion rules: a style rule needs the same edit in 2 of your last 5 edits; an outcome insight needs 5+ proposals per group and a 10-point difference in reply rate.</p>
        <h3>Style memory</h3>
        {mem.data?.style.length === 0 && <p className="muted">Nothing learned yet.</p>}
        <ul>{mem.data?.style.map((m) => (
          <li key={m.key}>{m.text} <span className="muted small">({m.rule})</span>{" "}
            <button className="btn link small" onClick={async () => { await call(`/memory/style/${encodeURIComponent(m.key)}`, { method: "DELETE" }); mem.reload(); }}>delete</button></li>))}</ul>
        <h3>Outcome memory</h3>
        {mem.data?.outcome.length === 0 && <p className="muted">Not enough outcomes yet.</p>}
        <ul>{mem.data?.outcome.map((m) => (
          <li key={m.key}>{m.text}{" "}
            <button className="btn link small" onClick={async () => { await call(`/memory/outcome/${encodeURIComponent(m.key)}`, { method: "DELETE" }); mem.reload(); }}>delete</button></li>))}</ul>
      </section>
    </>
  );
}

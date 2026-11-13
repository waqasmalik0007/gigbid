"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { call, setToken } from "@/lib/api/client";

const PERSONAS = [
  ["ai_ml_engineer", "AI/ML engineer"],
  ["fullstack_web", "Full-stack web developer"],
  ["data_analyst", "Data analyst"],
] as const;

/** Sign-in and onboarding: account, persona (or own profile), rates, Connects budget. */
export default function Login() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [persona, setPersona] = useState<string>("ai_ml_engineer");
  const [floor, setFloor] = useState(35);
  const [budget, setBudget] = useState(80);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await call<{ token: string }>(`/auth/${mode}`, {
        method: "POST",
        body: JSON.stringify(mode === "signup" ? { email, password, persona_id: persona } : { email, password }),
      });
      setToken(r.token);
      if (mode === "signup") {
        await call("/auth/settings", { method: "PUT", body: JSON.stringify({ floor_rate: floor, weekly_connects_budget: budget, marketplace: "gigsim" }) });
      }
      router.push(mode === "signup" ? "/settings?welcome=1" : "/inbox");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ maxWidth: 460, margin: "40px auto" }}>
      <h1>GigBid</h1>
      <p className="muted">A team of agents finds jobs, flags scams and drafts grounded proposals. You approve everything.</p>
      <div className="row gap" role="tablist">
        <button role="tab" aria-selected={mode === "login"} className={`btn ${mode === "login" ? "primary" : ""}`} onClick={() => setMode("login")}>Sign in</button>
        <button role="tab" aria-selected={mode === "signup"} className={`btn ${mode === "signup" ? "primary" : ""}`} onClick={() => setMode("signup")}>Create account</button>
      </div>
      <form onSubmit={submit}>
        <label className="field"><span>Email</span>
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" data-testid="email" />
        </label>
        <label className="field"><span>Password</span>
          <input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)}
                 autoComplete={mode === "login" ? "current-password" : "new-password"} data-testid="password" />
        </label>
        {mode === "signup" && (
          <>
            <label className="field"><span>Portfolio</span>
              <select value={persona} onChange={(e) => setPersona(e.target.value)} data-testid="persona">
                {PERSONAS.map(([v, l]) => <option key={v} value={v}>{l} (synthetic persona)</option>)}
              </select>
            </label>
            <p className="small muted">You can upload your own CV and case studies in Settings after signing up.</p>
            <div className="grid2">
              <label className="field"><span>Floor rate (USD/h)</span>
                <input type="number" min={1} value={floor} onChange={(e) => setFloor(+e.target.value)} />
              </label>
              <label className="field"><span>Weekly Connects budget</span>
                <input type="number" min={0} value={budget} onChange={(e) => setBudget(+e.target.value)} />
              </label>
            </div>
          </>
        )}
        {err && <p className="bad" role="alert">{err}</p>}
        <button className="btn primary" type="submit" disabled={busy} data-testid="submit">
          {busy ? "…" : mode === "login" ? "Sign in" : "Create account"}
        </button>
      </form>
    </div>
  );
}

import { Page, Route } from "@playwright/test";

export const PAYLOAD = {
  kind: "proposal",
  job: { job_id: "job_1", title: "RAG chatbot over PDFs", budget_type: "fixed", budget_min: 800, budget_max: 1500 },
  cover_letter: "You need answers grounded in your PDFs. I built a RAG assistant that answered 300 staff questions a week [case_proj_1].",
  screening_answers: ["I would evaluate with a labelled question set."],
  claims: [{ text: "I built a RAG assistant that answered 300 staff questions a week.", evidence_ids: ["case_proj_1"], status: "supported" }],
  evidence: [{ evidence_id: "case_proj_1", type: "case_study", title: "Internal-docs RAG assistant", snippet: "Using LangChain, RAG, Qdrant…" }],
  risk: { risk_score: 0.1, flags: [], is_scam: false },
  fit: { fit_score: 0.8 },
  price: { amount: 1200, budget_type: "fixed", fair_band: [800, 1500], rationale: "median…", connects_cost: 8 },
  critiques: [{ passed: true, grounding: 0.9, personalisation: 0.8, issues: [] }],
  compliance: { allowed: true, redactions: [], violations: [] },
  verification: { claims: 1, unsupported_before: 0.0, unsupported_after: 0.0, removed: [] },
  dry_run: { dry_run: true, connects_cost: 8, balance: 120 },
};

export type Calls = { approvals: any[]; removed: string[] };

/** Mock the GigBid API so UI behaviour is tested without a backend. */
export async function mockApi(page: Page, opts: { marketplaceDown?: boolean } = {}): Promise<Calls> {
  const calls: Calls = { approvals: [], removed: [] };
  await page.addInitScript(() => window.localStorage.setItem("gigbid_token", "test-token"));
  await page.route("**/api/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const p = url.pathname.replace(/^\/api/, "");
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (p === "/auth/signup" || p === "/auth/login") return json({ token: "t", user: { id: "u1", email: "a@b.co", persona_id: "ai_ml_engineer", settings: {} } });
    if (p === "/auth/settings") return json({ id: "u1", email: "a@b.co", persona_id: "ai_ml_engineer", settings: { floor_rate: 40 } });
    if (p === "/auth/me") return json({ id: "u1", email: "a@b.co", persona_id: "ai_ml_engineer", settings: { floor_rate: 40, weekly_connects_budget: 80 } });
    if (p === "/health/marketplace") return json(opts.marketplaceDown ? { ok: false, message: "Marketplace unavailable: new work is queued." } : { ok: true, connects: 120 });
    if (p === "/memory") return json({ style: [], outcome: [] });
    if (p === "/jobs") return json([{ job_id: "job_1", title: "RAG chatbot over PDFs", category: "ml_engineering", action: "bid", fit: 0.8, risk: 0.1, price: 1200, connects: 8, budget: "fixed 800-1500", flags: [], is_scam: false, reasons: [], thread_id: "bid:u1:job_1", status: "awaiting_approval" }]);
    if (p === "/approvals" && route.request().method() === "GET") return json([{ thread_id: "bid:u1:job_1", kind: "proposal", payload: PAYLOAD }]);
    if (p.startsWith("/approvals/") && route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      calls.approvals.push(body);
      if (body.action === "reject" && !body.reason) return json({ detail: "a reason is required to reject" }, 422);
      return json({ thread_id: "bid:u1:job_1", dispatch: "queued" });
    }
    if (p.startsWith("/portfolio/") && route.request().method() === "DELETE") {
      calls.removed.push(decodeURIComponent(p.split("/").pop() ?? ""));
      return json({ removed: calls.removed.at(-1) });
    }
    if (p === "/studio/regenerate") {
      // after the evidence is gone, the verifier strips the claim that relied on it
      return calls.removed.length
        ? json({ text: "You need answers grounded in your PDFs.", removed: ["I built a RAG assistant that answered 300 staff questions a week."] })
        : json({ text: PAYLOAD.cover_letter, removed: [] });
    }
    if (p.endsWith("/stream")) return route.fulfill({ status: 200, contentType: "text/event-stream", body: 'data: {"node":"_run","data":{"status":"awaiting_approval"},"ts":1}\n\n' });
    return json({});
  });
  return calls;
}

// Typed client generated from FastAPI's OpenAPI schema (src/lib/api/schema.d.ts, `npm run gen:api`).
import createClient from "openapi-fetch";
import type { paths } from "./schema";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function getToken(): string | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage.getItem("gigbid_token");
  } catch {
    return null;
  }
}

export function setToken(t: string | null) {
  try {
    if (t) window.localStorage.setItem("gigbid_token", t);
    else window.localStorage.removeItem("gigbid_token");
  } catch {
    /* storage unavailable: session-only */
  }
}

export const api = createClient<paths>({ baseUrl: API_URL });
api.use({
  onRequest({ request }) {
    const t = getToken();
    if (t) request.headers.set("Authorization", `Bearer ${t}`);
    return request;
  },
});

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Untyped helper for endpoints that return free-form JSON (agent state). */
export async function call<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const t = getToken();
  const res = await fetch(`${API_URL}/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(t ? { Authorization: `Bearer ${t}` } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/auth")) {
    setToken(null);
    window.location.href = "/login";
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export type NodeEvent = { node: string; data: Record<string, unknown>; ts: number };

/** Server-Sent Events over fetch (EventSource cannot send the Authorization header). */
export async function streamRun(threadId: string, onEvent: (e: NodeEvent) => void, signal: AbortSignal) {
  const t = getToken();
  const res = await fetch(`${API_URL}/api/runs/${encodeURIComponent(threadId)}/stream`, {
    headers: t ? { Authorization: `Bearer ${t}` } : {},
    signal,
  });
  if (!res.ok || !res.body) throw new ApiError(res.status, "stream unavailable");
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const data = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (data) onEvent(JSON.parse(data.slice(6)) as NodeEvent);
    }
  }
}

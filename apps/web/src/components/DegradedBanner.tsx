"use client";
import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { call, getToken } from "@/lib/api/client";

type Health = { ok: boolean; message?: string; connects?: number };

/** Polls marketplace health; shows a banner when the Marketplace MCP server / GigSim is down. */
export function DegradedBanner() {
  const [h, setH] = useState<Health | null>(null);
  const path = usePathname();
  useEffect(() => {
    if (path === "/login" || !getToken()) return;
    let alive = true;
    const tick = async () => {
      try {
        const r = await call<Health>("/health/marketplace");
        if (alive) setH(r);
      } catch {
        if (alive) setH({ ok: false, message: "GigBid API unreachable. Retrying…" });
      }
    };
    void tick();
    const id = setInterval(tick, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [path]);
  if (!h || h.ok) return null;
  return (
    <div className="banner" role="alert" data-testid="degraded-banner">
      <strong>Marketplace unavailable.</strong>{" "}
      {h.message ?? "New work is queued and will resume automatically. Your approvals are saved."}
    </div>
  );
}

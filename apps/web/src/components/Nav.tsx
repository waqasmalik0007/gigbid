"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { setToken } from "@/lib/api/client";

const LINKS = [
  ["/inbox", "Inbox"],
  ["/approvals", "Approvals"],
  ["/pipeline", "Pipeline"],
  ["/messages", "Messages"],
  ["/runs", "Runs"],
  ["/analytics", "Analytics"],
  ["/settings", "Settings"],
] as const;

export function Nav() {
  const path = usePathname();
  const router = useRouter();
  if (path === "/login") return null;
  return (
    <nav className="nav" aria-label="Main">
      <Link href="/inbox" className="brand">
        GigBid
      </Link>
      <ul>
        {LINKS.map(([href, label]) => (
          <li key={href}>
            <Link href={href} aria-current={path.startsWith(href) ? "page" : undefined}>
              {label}
            </Link>
          </li>
        ))}
      </ul>
      <button
        className="btn ghost small"
        onClick={() => {
          setToken(null);
          router.push("/login");
        }}
      >
        Sign out
      </button>
    </nav>
  );
}

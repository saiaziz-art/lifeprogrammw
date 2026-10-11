"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";
import { ago, api } from "@/lib/api";
import type { Run } from "@/lib/types";

const NAV = [
  { href: "/", label: "Dashboard", ico: "◐" },
  { href: "/map", label: "Land Map", ico: "◎" },
  { href: "/deals", label: "Deal Explorer", ico: "≡" },
  { href: "/design", label: "Design Studio", ico: "⌂" },
  { href: "/pipeline", label: "Acquisition Pipeline", ico: "➝" },
  { href: "/import", label: "Add & Inbox", ico: "+" },
  { href: "/settings", label: "Preferences", ico: "⚙" },
  { href: "/admin", label: "Admin", ico: "▤" },
];

export function Shell({ children, title }: { children: ReactNode; title?: string }) {
  const path = usePathname();
  const router = useRouter();
  const [run, setRun] = useState<Run | null>(null);
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    api<{ authenticated: boolean; setup_required: boolean; email: string | null }>("/auth/state")
      .then((s) => {
        if (!s.authenticated) router.replace("/login");
        else setEmail(s.email);
      })
      .catch(() => router.replace("/login"));
    api<Run[]>("/admin/runs?limit=1")
      .then((r) => setRun(r[0] ?? null))
      .catch(() => undefined);
  }, [router]);

  async function logout() {
    await api("/auth/logout", { method: "POST" });
    router.replace("/login");
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">PLOT</div>
        <div className="brand-sub">Land &amp; small-home sourcing</div>
        <nav className="nav">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className={(n.href === "/" ? path === "/" : path.startsWith(n.href)) ? "active" : ""}>
              <span className="ico">{n.ico}</span>
              {n.label}
            </Link>
          ))}
        </nav>
        <div className="sidebar-foot">
          Screening tool. Planning, engineering and cost outputs are estimates with stated evidence — not professional advice.
        </div>
      </aside>
      <div className="main">
        <div className="topbar">
          <div className="crumbs">{title ?? ""}</div>
          <div className="row small">
            {run ? (
              <Link href="/admin" title="Most recent sourcing run">
                <span className={`chip ${run.status}`}>Last run: {run.status}</span> <span className="muted">{ago(run.finished_at ?? run.started_at)}</span>
              </Link>
            ) : (
              <span className="chip unknown">No runs yet</span>
            )}
            {email ? <span className="muted">{email}</span> : null}
            <button className="btn sm" onClick={logout}>
              Sign out
            </button>
          </div>
        </div>
        <div className="page">{children}</div>
      </div>
    </div>
  );
}

"use client";

import Link from "next/link";
import { Shell } from "@/components/Shell";
import { Chip, Empty, ErrorBox, Loading, OppRow } from "@/components/ui";
import { ago, gbp, useApi, when } from "@/lib/api";
import type { Run, SiteRow } from "@/lib/types";

type Dash = {
  budget: number;
  last_run: Run | null;
  last_successful_run: Run | null;
  last_completed_run: Run | null;
  last_data_refresh: string | null;
  next_run: string | null;
  counts: Record<string, number>;
  strongest: SiteRow[];
  new_today: SiteRow[];
  price_reductions: SiteRow[];
  off_market: SiteRow[];
  affordability: { median_residual: number | null; best_residual: number | null; within_budget: number; assessed: number };
  alerts: { level: string; title: string; detail: string | null; link: string }[];
  digest: { date: string; items: { site_id: number; headline: string; score: number; reason: string; budget_fit: string; next_action: string; stale: boolean }[]; delivery_status: string; delivery_error: string | null } | null;
};

export default function Dashboard() {
  const { data, error, loading } = useApi<Dash>("/dashboard");
  return (
    <Shell title="Dashboard">
      <div className="page-head">
        <div>
          <h1>Today&apos;s land intelligence</h1>
          <p>Ranked by planning, financial, physical, location and discount potential against your all-in budget of {data ? gbp(data.budget) : "…"} (land + build + every cost — not a land budget).</p>
        </div>
        <div className="row">
          <Link className="btn" href="/deals">
            All opportunities
          </Link>
          <Link className="btn primary" href="/map">
            Open map
          </Link>
        </div>
      </div>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : null}
      {data ? (
        <div className="stack" style={{ gap: 16 }}>
          <RunStrip d={data} />
          {data.alerts.length ? (
            <div className="card">
              <div className="card-head">
                <h3>Critical alerts &amp; notices</h3>
              </div>
              <ul className="list-plain">
                {data.alerts.map((a, i) => (
                  <li key={i} className="row between">
                    <div>
                      <Chip tone={a.level === "error" ? "bad" : a.level === "warning" ? "warn" : "info"}>{a.level}</Chip> <strong>{a.title}</strong>
                      {a.detail ? <div className="small" style={{ marginTop: 2 }}>{a.detail}</div> : null}
                    </div>
                    <Link href={a.link} className="small">
                      Review →
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="grid g4">
            <Kpi label="Active opportunities" value={data.counts.active} sub={`${data.counts.on_market} on market · ${data.counts.off_market} off-market/register`} />
            <Kpi label="Priced & within budget" value={data.counts.within_budget} sub={`of ${data.counts.priced} with an asking price · ${data.counts.unpriced_with_capacity} unpriced with land headroom`} />
            <Kpi label="Best max land value" value={gbp(data.affordability.best_residual, { compact: true })} sub={`median ${gbp(data.affordability.median_residual, { compact: true })} after all other costs`} />
            <Kpi label="Fatal-flagged" value={data.counts.fatal} sub={`${data.counts.stale} from historic records`} />
          </div>
          <div className="grid g-main">
            <div className="card">
              <div className="card-head">
                <h3>Strongest opportunities</h3>
                <Link href="/deals" className="small">
                  Explore →
                </Link>
              </div>
              {data.strongest.length ? data.strongest.map((r) => <OppRow key={r.id} r={r} />) : <Empty>No assessed opportunities without fatal flags yet.</Empty>}
            </div>
            <div className="stack" style={{ gap: 16 }}>
              <div className="card">
                <div className="card-head">
                  <h3>Daily digest</h3>
                  {data.digest ? <span className={`chip ${data.digest.delivery_status === "sent" ? "ok" : "unknown"}`}>{data.digest.delivery_status.replace(/_/g, " ")}</span> : null}
                </div>
                {data.digest ? (
                  data.digest.items.length ? (
                    <ol style={{ paddingLeft: 18, margin: 0 }}>
                      {data.digest.items.map((it) => (
                        <li key={it.site_id} style={{ marginBottom: 8 }}>
                          <Link href={`/sites/${it.site_id}`}>{it.headline}</Link> <span className="small">({it.reason}{it.stale ? ", historic record" : ""})</span>
                          <div className="small">{it.budget_fit}</div>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <Empty>No genuinely new or materially improved opportunities in the last run.</Empty>
                  )
                ) : (
                  <Empty>No digest yet — it is produced by the daily run.</Empty>
                )}
              </div>
              <div className="card">
                <div className="card-head">
                  <h3>Off-market prospects</h3>
                  <Link href="/pipeline?tab=leads" className="small">
                    Leads →
                  </Link>
                </div>
                {data.off_market.length ? data.off_market.slice(0, 4).map((r) => <OppRow key={r.id} r={r} />) : <Empty>No off-market leads yet.</Empty>}
                <div className="disclaimer">Off-market and register sites are not known to be for sale. Availability is only recorded once an owner confirms it.</div>
              </div>
            </div>
          </div>
          <div className="grid g2">
            <div className="card">
              <div className="card-head">
                <h3>New today</h3>
              </div>
              {data.new_today.length ? data.new_today.map((r) => <OppRow key={r.id} r={r} />) : <Empty>Nothing new since midnight.</Empty>}
            </div>
            <div className="card">
              <div className="card-head">
                <h3>Recent price reductions</h3>
              </div>
              {data.price_reductions.length ? data.price_reductions.map((r) => <OppRow key={r.id} r={r} />) : <Empty>No price reductions detected. Reductions are tracked for listings PLOT has seen more than once.</Empty>}
            </div>
          </div>
        </div>
      ) : null}
    </Shell>
  );
}

function Kpi({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="card kpi">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub ? <div className="sub">{sub}</div> : null}
    </div>
  );
}

function RunStrip({ d }: { d: Dash }) {
  const r = d.last_run;
  return (
    <div className="card flat" style={{ padding: "12px 16px" }}>
      <div className="row between">
        <div className="row" style={{ gap: 18 }}>
          <div>
            <div className="small">Last sourcing run</div>
            {r ? (
              <div>
                <span className={`chip ${r.status}`}>{r.status}</span> {when(r.started_at)} <span className="small">({r.trigger})</span>
              </div>
            ) : (
              <div className="muted">never</div>
            )}
          </div>
          <div>
            <div className="small">Last fully successful run</div>
            <div>{d.last_successful_run ? when(d.last_successful_run.started_at) : <span className="muted">none yet — see failing sources</span>}</div>
          </div>
          <div>
            <div className="small">Last actual data refresh</div>
            <div>{d.last_data_refresh ? `${when(d.last_data_refresh)} (${ago(d.last_data_refresh)})` : <span className="muted">never</span>}</div>
          </div>
          <div>
            <div className="small">Next scheduled run</div>
            <div>{d.next_run ? `${when(d.next_run)} (${ago(d.next_run)})` : <span className="muted">schedule disabled</span>}</div>
          </div>
        </div>
        {r && r.status !== "succeeded" && Array.isArray(r.summary?.failing_sources) ? (
          <div className="small" style={{ maxWidth: 380 }}>
            Failing sources: {(r.summary.failing_sources as string[]).join(", ") || "none"}
          </div>
        ) : null}
      </div>
    </div>
  );
}

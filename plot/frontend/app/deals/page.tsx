"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Shell } from "@/components/Shell";
import { Confidence, ErrorBox, FatalFlags, Glyph, Loading, OriginChip, ScoreBadge, ScoreStack } from "@/components/ui";
import { area, gbp, useApi } from "@/lib/api";
import type { SiteRow } from "@/lib/types";

type SortKey = "score" | "asking" | "max_offer" | "headroom" | "total" | "area" | "confidence" | "newest";

const SHORT: Record<string, [string, string]> = {
  permission_verified: ["Permission verified", "good"],
  approval_exists_review: ["Approval — review", "good"],
  plausible_route: ["Plausible route", "warn"],
  speculative_high_risk: ["Speculative", "unknown"],
  significant_obstacle: ["Obstacle", "bad"],
};

const PLANNING = [
  ["", "Any planning status"],
  ["permission_verified", "Permission verified"],
  ["approval_exists_review", "Approval exists — review"],
  ["plausible_route", "Plausible route"],
  ["speculative_high_risk", "Speculative / high risk"],
  ["significant_obstacle", "Significant obstacle"],
];

export default function Deals() {
  const [showExcluded, setShowExcluded] = useState(false);
  const { data, error, loading } = useApi<{ total: number; items: SiteRow[] }>(`/opportunities?limit=1000&include_excluded=${showExcluded}`, [showExcluded]);
  const [q, setQ] = useState("");
  const [origin, setOrigin] = useState("");
  const [planning, setPlanning] = useState("");
  const [fatal, setFatal] = useState(true);
  const [stale, setStale] = useState(true);
  const [budgetOnly, setBudgetOnly] = useState(false);
  const [sort, setSort] = useState<{ k: SortKey; dir: 1 | -1 }>({ k: "score", dir: -1 });

  const rows = useMemo(() => {
    let r = data?.items ?? [];
    const ql = q.toLowerCase();
    r = r.filter(
      (x) =>
        (!ql || `${x.headline} ${x.address} ${x.lpa} ${x.postcode}`.toLowerCase().includes(ql)) &&
        (!origin || x.origin === origin) &&
        (!planning || x.planning_class === planning) &&
        (fatal || !x.score.fatal) &&
        (stale || !x.stale) &&
        (!budgetOnly || x.within_budget || (x.asking_price === null && (x.residual_land_value ?? -1) > 0)),
    );
    const val = (x: SiteRow): number => {
      switch (sort.k) {
        case "score":
          return x.score.fatal ? -1000 + (x.score.total ?? 0) : (x.score.total ?? -1);
        case "asking":
          return x.asking_price ?? Number.MAX_SAFE_INTEGER * (sort.dir === 1 ? 1 : -1);
        case "max_offer":
          return x.max_offer ?? -1;
        case "headroom":
          return x.headroom ?? -1e9;
        case "total":
          return x.non_land_cost_base ?? 1e12;
        case "area":
          return x.area_m2 ?? -1;
        case "confidence":
          return x.confidence.score;
        case "newest":
          return new Date(x.first_seen_at).getTime();
      }
    };
    return [...r].sort((a, b) => (val(a) - val(b)) * sort.dir);
  }, [data, q, origin, planning, fatal, stale, budgetOnly, sort]);

  const th = (k: SortKey, label: string, num = false) => (
    <th className={`sortable ${num ? "num" : ""}`} onClick={() => setSort((s) => ({ k, dir: s.k === k ? ((-s.dir) as 1 | -1) : -1 }))}>
      {label}
      {sort.k === k ? (sort.dir === -1 ? " ↓" : " ↑") : ""}
    </th>
  );

  return (
    <Shell title="Deal Explorer">
      <div className="page-head">
        <div>
          <h1>Deal Explorer</h1>
          <p>Every opportunity with its original source, price, maximum viable land offer and feasibility score. Click a column to sort.</p>
        </div>
        <Link href="/import" className="btn primary">
          + Add listing / site
        </Link>
      </div>
      <div className="card flat row" style={{ marginBottom: 12, gap: 12 }}>
        <input placeholder="Search name, address, LPA, postcode…" value={q} onChange={(e) => setQ(e.target.value)} style={{ minWidth: 260 }} />
        <select value={origin} onChange={(e) => setOrigin(e.target.value)}>
          <option value="">All origins</option>
          <option value="on_market">On market</option>
          <option value="off_market">Off-market leads</option>
          <option value="register">Public registers</option>
        </select>
        <select value={planning} onChange={(e) => setPlanning(e.target.value)}>
          {PLANNING.map(([k, l]) => (
            <option key={k} value={k}>
              {l}
            </option>
          ))}
        </select>
        <label className="row" style={{ gap: 5 }}>
          <input type="checkbox" checked={budgetOnly} onChange={(e) => setBudgetOnly(e.target.checked)} /> Fits budget
        </label>
        <label className="row" style={{ gap: 5 }}>
          <input type="checkbox" checked={fatal} onChange={(e) => setFatal(e.target.checked)} /> Fatal-flagged
        </label>
        <label className="row" style={{ gap: 5 }}>
          <input type="checkbox" checked={stale} onChange={(e) => setStale(e.target.checked)} /> Historic records
        </label>
        <label className="row" style={{ gap: 5 }}>
          <input type="checkbox" checked={showExcluded} onChange={(e) => setShowExcluded(e.target.checked)} /> Excluded
        </label>
        <span className="muted small">{rows.length} shown</span>
      </div>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : null}
      <div className="table-wrap">
        <table className="t">
          <thead>
            <tr>
              <th style={{ width: 64 }} />
              <th>Opportunity</th>
              {th("area", "Area", true)}
              {th("asking", "Asking", true)}
              {th("max_offer", "Max land offer", true)}
              {th("headroom", "Headroom", true)}
              {th("total", "Costs excl. land", true)}
              <th>Planning</th>
              {th("score", "Score")}
              {th("confidence", "Evidence")}
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} style={r.excluded ? { opacity: 0.55 } : undefined}>
                <td>
                  <Glyph g={r.glyph} />
                </td>
                <td style={{ minWidth: 240, maxWidth: 380 }}>
                  <Link href={`/sites/${r.id}`} style={{ fontWeight: 500 }}>
                    {r.headline}
                  </Link>
                  <div className="row small" style={{ gap: 6, marginTop: 3 }}>
                    <OriginChip o={r.origin} stale={r.stale} />
                    <span>{r.lpa ?? ""}</span>
                  </div>
                  <FatalFlags flags={r.score.fatal_flags} />
                  {r.excluded ? <div className="small err">Excluded: {r.exclusion_reason}</div> : null}
                  {r.next_action ? <div className="small" style={{ marginTop: 3 }}>Next: {r.next_action}</div> : null}
                </td>
                <td className="num">{area(r.area_m2)}</td>
                <td className="num">
                  {gbp(r.asking_price)}
                  {r.price_reductions ? <div className="small" style={{ color: "var(--good)" }}>↓ {r.price_reductions} reduction(s)</div> : null}
                </td>
                <td className="num">{gbp(r.max_offer)}</td>
                <td className="num" style={{ color: r.headroom !== null ? (r.headroom >= 0 ? "var(--good)" : "var(--bad)") : undefined }}>
                  {r.headroom !== null ? gbp(r.headroom) : "—"}
                </td>
                <td className="num">
                  {gbp(r.non_land_cost_base, { compact: true })}
                  {r.within_budget === false ? <div className="small err">over budget</div> : null}
                </td>
                <td title={r.planning_label ?? ""}>
                  {r.planning_class ? <span className={`chip ${SHORT[r.planning_class]?.[1] ?? ""}`}>{SHORT[r.planning_class]?.[0] ?? r.planning_class}</span> : "—"}
                </td>
                <td style={{ minWidth: 90 }}>
                  <div className="row" style={{ gap: 8 }}>
                    <ScoreBadge score={r.score.total} fatal={r.score.fatal} />
                  </div>
                  <div style={{ marginTop: 6 }}>
                    <ScoreStack components={r.score.components} />
                  </div>
                </td>
                <td>
                  <Confidence c={r.confidence} />
                </td>
                <td className="small" style={{ minWidth: 100 }}>
                  {r.listing_url ? (
                    <a href={r.listing_url} target="_blank" rel="noreferrer noopener">
                      Original listing ↗
                    </a>
                  ) : r.provenance?.[0]?.url ? (
                    <a href={r.provenance[0].url} target="_blank" rel="noreferrer noopener">
                      Source record ↗
                    </a>
                  ) : (
                    "—"
                  )}
                  <div className="muted">{r.listing_source ?? r.provenance?.[0]?.source_id}</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Shell>
  );
}

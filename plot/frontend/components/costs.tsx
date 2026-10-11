"use client";

import { Fragment, useState } from "react";
import { gbp } from "@/lib/api";
import type { CostPlan, Residual } from "@/lib/types";

export function ResidualSummary({ r }: { r: Residual }) {
  const asking = r.asking_price;
  return (
    <div className="grid g4">
      <div className="card kpi">
        <div className="label">Max recommended land offer</div>
        <div className="value">{gbp(r.max_recommended_offer)}</div>
        <div className="sub">residual {gbp(r.residual_land_value)} · prudent {gbp(r.prudent_offer)}</div>
      </div>
      <div className="card kpi">
        <div className="label">Asking price</div>
        <div className="value">{gbp(asking)}</div>
        <div className="sub" style={{ color: r.headroom_vs_asking !== null ? (r.headroom_vs_asking >= 0 ? "var(--good)" : "var(--bad)") : undefined }}>
          {r.headroom_vs_asking !== null ? `${r.headroom_vs_asking >= 0 ? "headroom" : "shortfall"} ${gbp(Math.abs(r.headroom_vs_asking))}` : "no price — off-market or unpriced"}
        </div>
      </div>
      <div className="card kpi">
        <div className="label">{asking === null ? "All costs except land (base)" : "Total project cost (base)"}</div>
        <div className="value">{gbp(asking === null ? r.non_land_cost?.base : r.total_project_cost.base)}</div>
        <div className="sub">
          range {gbp(r.total_project_cost.low_rss, { compact: true })}–{gbp(r.total_project_cost.high_rss, { compact: true })} · stress {gbp(r.total_project_cost.high, { compact: true })}
        </div>
      </div>
      <div className="card kpi">
        <div className="label">vs budget {gbp(r.budget, { compact: true })}</div>
        {asking === null ? (
          <div className="value" style={{ color: r.residual_land_value < 0 ? "var(--bad)" : "var(--good)" }}>
            {r.residual_land_value < 0 ? `+${gbp(-r.residual_land_value)}` : `${gbp(r.residual_land_value, { compact: true })} left`}
          </div>
        ) : (
          <div className="value" style={{ color: r.over_under_budget_base > 0 ? "var(--bad)" : "var(--good)" }}>
            {r.over_under_budget_base > 0 ? "+" : ""}
            {gbp(r.over_under_budget_base)}
          </div>
        )}
        <div className="sub">
          {gbp(r.cost_per_m2)}/m² all-in · contingency {gbp(r.contingency_base, { compact: true })} · land basis: {r.land_price_basis}
        </div>
      </div>
    </div>
  );
}

export function CostTable({ plan }: { plan: CostPlan }) {
  const [open, setOpen] = useState<Record<number, boolean>>({});
  return (
    <div className="table-wrap">
      <table className="t">
        <thead>
          <tr>
            <th style={{ width: 30 }}>#</th>
            <th>Category / line</th>
            <th className="num">Low</th>
            <th className="num">Base</th>
            <th className="num">High</th>
            <th>Basis & source</th>
          </tr>
        </thead>
        <tbody>
          {plan.categories.map((c) => (
            <Fragment key={c.no}>
              <tr onClick={() => setOpen({ ...open, [c.no]: !open[c.no] })} style={{ cursor: c.lines ? "pointer" : "default", background: "var(--surface-2)" }}>
                <td className="mono">{c.no}</td>
                <td style={{ fontWeight: 600 }}>
                  {c.lines ? (open[c.no] ? "▾ " : "▸ ") : "  "}
                  {c.name}
                </td>
                <td className="num">{gbp(c.low)}</td>
                <td className="num" style={{ fontWeight: 600 }}>
                  {gbp(c.base)}
                </td>
                <td className="num">{gbp(c.high)}</td>
                <td className="small">{c.lines ? `${c.lines} line(s)` : "none applicable"}</td>
              </tr>
              {open[c.no]
                ? plan.lines
                    .filter((l) => l.cat === c.no)
                    .map((l) => (
                      <tr key={`${c.no}-${l.key}-${l.label}`}>
                        <td />
                        <td style={{ paddingLeft: 24 }}>
                          {l.label}
                          {l.qty !== 1 ? <span className="small"> · {l.qty.toLocaleString("en-GB")} {l.unit}</span> : null}
                          {l.user_override ? <span className="chip info" style={{ marginLeft: 6 }}>your rate</span> : null}
                          {l.note ? <div className="small">{l.note}</div> : null}
                        </td>
                        <td className="num">{gbp(l.low)}</td>
                        <td className="num">{gbp(l.base)}</td>
                        <td className="num">{gbp(l.high)}</td>
                        <td className="small" style={{ maxWidth: 360 }}>
                          {l.basis}
                          {l.source ? <div className="muted">{l.source}{l.source_date ? ` (${l.source_date})` : ""}</div> : null}
                        </td>
                      </tr>
                    ))
                : null}
            </Fragment>
          ))}
          <tr>
            <td />
            <td style={{ fontWeight: 700 }}>Total project cost</td>
            <td className="num" style={{ fontWeight: 700 }}>
              {gbp(plan.totals.low)}
            </td>
            <td className="num" style={{ fontWeight: 700 }}>
              {gbp(plan.totals.base)}
            </td>
            <td className="num" style={{ fontWeight: 700 }}>
              {gbp(plan.totals.high)}
            </td>
            <td className="small">Realistic range (root-sum-square): {gbp(plan.totals.low_rss)} – {gbp(plan.totals.high_rss)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

export function Tornado({ items, base }: { items: { factor: string; delta: number; residual: number }[]; base: number }) {
  const max = Math.max(1, ...items.map((i) => Math.abs(i.delta)));
  return (
    <div className="stack" style={{ gap: 6 }}>
      {items.map((i) => (
        <div key={i.factor} className="compbar" style={{ gridTemplateColumns: "230px 1fr 90px" }}>
          <span className="small">{i.factor}</span>
          <div style={{ position: "relative", height: 12, background: "var(--line-2)", borderRadius: 4 }}>
            <span style={{ position: "absolute", right: "50%", width: `${(Math.abs(Math.min(0, i.delta)) / max) * 50}%`, top: 0, bottom: 0, background: "#c46a3c", borderRadius: 4 }} />
            <span style={{ position: "absolute", left: "50%", width: `${(Math.max(0, i.delta) / max) * 50}%`, top: 0, bottom: 0, background: "var(--good)", borderRadius: 4 }} />
          </div>
          <span className="num mono" style={{ textAlign: "right" }}>
            {i.delta >= 0 ? "+" : ""}
            {gbp(i.delta, { compact: true })}
          </span>
        </div>
      ))}
      <div className="small">Change in maximum viable land value (base residual {gbp(base)}) when one driver is stressed.</div>
    </div>
  );
}

export function Scenarios({ r }: { r: Residual }) {
  const names: Record<string, string> = { buy_now: "Buy unconditionally now", subject_to_planning: "Buy subject to planning", option: "Option agreement" };
  return (
    <div className="grid g3">
      {Object.entries(r.scenarios).map(([k, s]) => (
        <div className="card flat" key={k}>
          <h3>{names[k] ?? k}</h3>
          <div className="dl" style={{ gridTemplateColumns: "140px 1fr" }}>
            <dt>Max viable land value</dt>
            <dd>{gbp(s.residual_land_value)}</dd>
            <dt>Total cost (base)</dt>
            <dd>{gbp(s.total_base)}</dd>
            <dt>Upper estimate</dt>
            <dd>{gbp(s.total_high_rss)}</dd>
          </div>
          <p className="small" style={{ marginTop: 8 }}>
            {s.notes}
          </p>
        </div>
      ))}
    </div>
  );
}

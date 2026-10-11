"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Fragment, useCallback, useMemo, useState } from "react";
import { CostTable, ResidualSummary, Scenarios, Tornado } from "@/components/costs";
import { FloorPlan, Massing, SitePlan } from "@/components/drawings";
import { Shell } from "@/components/Shell";
import { Chip, Confidence, Empty, ErrorBox, FatalFlags, Loading, OriginChip, ScoreBadge, ScoreBreakdown, SourceLinks, Status } from "@/components/ui";
import { api, area, gbp, label, useApi, when } from "@/lib/api";
import type { CostPlan, DesignOption, Finding, Fit, Residual, RiskItem, SiteRow } from "@/lib/types";

const MapView = dynamic(() => import("@/components/MapView").then((m) => m.MapView), { ssr: false });

type Dossier = {
  site: SiteRow;
  boundary: { type: "Feature"; geometry: unknown; properties: Record<string, unknown> } | null;
  listings: { id: number; source_id: string; url: string | null; title: string | null; description: string | null; asking_price: number | null; price_qualifier: string | null; tenure: string | null; status: string; agent: string | null; first_seen_at: string; last_seen_at: string; provenance: Record<string, unknown>; events: { event: string; old: Record<string, unknown>; new: Record<string, unknown>; at: string }[] }[];
  evidence: { id: number; topic: string; kind: string; title: string; summary: string | null; data: Record<string, unknown>; status: string; confidence: number; source_id: string | null; source_url: string | null; data_date: string | null; retrieved_at: string; licence: string | null; attribution: string | null }[];
  assessment: null | {
    created_at: string;
    engine_version: string;
    planning: { classification: string; classification_label: string; score: number; max_score: number; findings: Finding[]; routes: { code: string; name: string; plausibility: string; detail: string }[]; obligations: { code: string; name: string; applies: string; detail: string }[]; lpa: string | null; confidence: number; checks: Record<string, string>; actions: string[]; disclaimer: string };
    site_risk: { items: RiskItem[]; drivers: Record<string, unknown>; score: number; max_score: number; confidence: number };
    design: { option: DesignOption; fit: Fit; tried: { concept: string; storeys: number; fits: boolean | null; method: string; reason: string | null }[] };
    costs: CostPlan;
    residual: Residual;
    score: { total: number; components: Record<string, { score: number; max: number; notes?: string[]; why?: string[]; parts?: Record<string, { score: number; max: number; detail: string; status: string }> }>; fatal_flags: { code: string; title: string }[]; dismissed_flags: { code: string; title: string }[]; feedback_adjustment: number };
    explanation: { why_interesting: string[]; undervalued: string[]; how_home_works: string; budget_fit: string; top_risks: { title: string; severity: string; status: string }[]; next_action: string; confidence: { score: number; label: string; components: Record<string, number> } };
  };
  score_history: { at: string; score: number; fatal: boolean }[];
  lead: null | { id: number; stage: string; signals: string[]; owner: Record<string, unknown>; availability_evidence: Record<string, unknown>[]; notes: string | null; events: { from: string | null; to: string; at: string; actor: string; note: string | null }[] };
  pipeline: null | { id: number; stage: string };
  overrides: { id: number; field: string; value: { value: unknown }; reason: string; active: boolean; by: string; at: string }[];
  feedback: { signal: number; reason: string | null; note: string | null; at: string }[];
};

const TABS = ["Overview", "Planning", "Site & engineering", "Design", "Costs & valuation", "Evidence", "Acquisition"] as const;

export default function SitePage() {
  const { id } = useParams<{ id: string }>();
  const { data, error, loading, reload } = useApi<Dossier>(`/sites/${id}`, [id]);
  const [tab, setTab] = useState<(typeof TABS)[number]>("Overview");
  const [msg, setMsg] = useState<string | null>(null);
  const [drawing, setDrawing] = useState(false);

  const act = useCallback(
    async (fn: () => Promise<unknown>, ok: string) => {
      setMsg(null);
      try {
        await fn();
        setMsg(ok);
        reload();
      } catch (e) {
        setMsg(`Failed: ${(e as Error).message}`);
      }
    },
    [reload],
  );

  const points = useMemo(
    () => ({ type: "FeatureCollection" as const, features: data ? [{ type: "Feature" as const, geometry: { type: "Point", coordinates: [data.site.lon, data.site.lat] }, properties: { id: data.site.id, score: data.site.score.total, fatal: data.site.score.fatal, origin: data.site.origin, stale: data.site.stale } }] : [] }),
    [data],
  );
  const boundaries = useMemo(() => ({ type: "FeatureCollection" as const, features: data?.boundary ? [data.boundary] : [] }), [data]);
  const onDraw = useCallback(
    (coords: [number, number][]) => {
      setDrawing(false);
      act(() => api(`/sites/${id}/boundary`, { method: "POST", json: { geometry: { type: "Polygon", coordinates: [coords] } } }), "Boundary saved — site re-assessed.");
    },
    [act, id],
  );

  if (loading && !data) return <Shell title="Site dossier"><Loading /></Shell>;
  if (error || !data) return <Shell title="Site dossier"><ErrorBox error={error} /></Shell>;
  const s = data.site;
  const a = data.assessment;

  return (
    <Shell title={`Site dossier #${s.id}`}>
      <div className="page-head">
        <div style={{ maxWidth: 900 }}>
          <div className="row" style={{ marginBottom: 6 }}>
            <OriginChip o={s.origin} stale={s.stale} />
            <Confidence c={s.confidence} />
            {s.lpa ? <Chip>{s.lpa}</Chip> : null}
            {s.excluded ? <Chip tone="bad">excluded: {s.exclusion_reason}</Chip> : null}
          </div>
          <h1>{s.headline}</h1>
          <p>
            {s.address ?? ""} {s.postcode ? `· ${s.postcode}` : ""} · {area(s.area_m2)} {s.area_source ? <span className="small">({s.area_source})</span> : null} · boundary: {label(s.boundary_source)}
          </p>
        </div>
        <div className="row">
          <ScoreBadge score={s.score.total} fatal={s.score.fatal} size="lg" />
          <div className="stack" style={{ gap: 6 }}>
            {data.pipeline ? (
              <Link className="btn sm" href="/pipeline">
                In pipeline: {label(data.pipeline.stage)}
              </Link>
            ) : (
              <button className="btn sm primary" onClick={() => act(() => api("/pipeline", { method: "POST", json: { site_id: s.id } }), "Added to shortlist.")}>
                Shortlist
              </button>
            )}
            <div className="row" style={{ gap: 4 }}>
              <button className="btn sm" title="More like this" onClick={() => act(() => api(`/sites/${s.id}/feedback`, { method: "POST", json: { signal: 1 } }), "Feedback recorded (+).")}>
                👍
              </button>
              <button className="btn sm" title="Less like this" onClick={() => act(() => api(`/sites/${s.id}/feedback`, { method: "POST", json: { signal: -1 } }), "Feedback recorded (−).")}>
                👎
              </button>
              <button className="btn sm" onClick={() => act(() => api(`/sites/${s.id}/refresh`, { method: "POST" }), "Data refresh queued — the worker will re-query sources.")}>
                Refresh data
              </button>
            </div>
          </div>
        </div>
      </div>
      {msg ? <div className={`notice ${msg.startsWith("Failed") ? "bad" : "info"}`} style={{ marginBottom: 12 }}>{msg}</div> : null}
      <FatalFlags flags={s.score.fatal_flags} />
      <div className="tabs" style={{ marginTop: 12 }}>
        {TABS.map((t) => (
          <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>

      {!a ? <Empty>Not assessed yet.</Empty> : null}

      {a && tab === "Overview" ? (
        <div className="grid g-main">
          <div className="stack" style={{ gap: 16 }}>
            <div className="card">
              <div className="grid g2">
                <div>
                  <h4>Why this site is interesting</h4>
                  <ul>{a.explanation.why_interesting.map((x, i) => <li key={i}>{x}</li>)}</ul>
                  <h4>What may make it undervalued</h4>
                  <ul>{a.explanation.undervalued.map((x, i) => <li key={i}>{x}</li>)}</ul>
                </div>
                <div>
                  <h4>How a small home could work</h4>
                  <p>{a.explanation.how_home_works}</p>
                  <h4>Budget fit</h4>
                  <p>{a.explanation.budget_fit}</p>
                </div>
              </div>
              <hr />
              <div className="grid g2">
                <div>
                  <h4>Top risks</h4>
                  {a.explanation.top_risks.map((r, i) => (
                    <div key={i} className={`finding sev-${r.severity}`}>
                      <span className="ttl">{r.title}</span> <Status s={r.status} />
                    </div>
                  ))}
                </div>
                <div>
                  <h4>Next due-diligence action</h4>
                  <div className="notice info">{a.explanation.next_action}</div>
                  {a.score.dismissed_flags?.length ? <div className="small" style={{ marginTop: 8 }}>Dismissed by you: {a.score.dismissed_flags.map((f) => f.title).join("; ")}</div> : null}
                </div>
              </div>
            </div>
            <div style={{ position: "relative" }}>
              <MapView points={points} boundaries={boundaries} filters={{ minScore: 0, origins: ["on_market", "off_market", "register"], includeFatal: true, includeStale: true }} focus={{ lon: s.lon, lat: s.lat, zoom: 16 }} height="380px" drawMode={drawing} onDraw={onDraw} />
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn sm" onClick={() => setDrawing(!drawing)}>
                  {drawing ? "Cancel drawing" : "Draw / correct boundary"}
                </button>
                <span className="small">Location quality: {label(String(s.provenance?.[0]?.location_quality ?? "exact"))}. INSPIRE extents are indicative, not legal boundaries.</span>
              </div>
            </div>
            <ListingsCard d={data} />
          </div>
          <div className="stack" style={{ gap: 16 }}>
            <div className="card">
              <div className="card-head">
                <h3>Score</h3>
                <span className="small">feedback adj. {a.score.feedback_adjustment >= 0 ? "+" : ""}{a.score.feedback_adjustment}</span>
              </div>
              <ScoreBreakdown components={a.score.components} />
              <div className="small" style={{ marginTop: 8 }}>
                Evidence confidence {(a.explanation.confidence.score * 100).toFixed(0)}% ({a.explanation.confidence.label}) — reported separately from attractiveness.
              </div>
              {a.score.components.location?.parts ? (
                <div style={{ marginTop: 10 }}>
                  {Object.entries(a.score.components.location.parts).map(([k, p]) => (
                    <div key={k} className="small">
                      {label(k)}: {p.score}/{p.max} — {p.detail} <Status s={p.status} />
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
            <div className="card">
              <h3>Valuation snapshot</h3>
              <dl className="dl">
                <dt>Max land offer</dt>
                <dd>{gbp(a.residual.max_recommended_offer)}</dd>
                <dt>Prudent offer</dt>
                <dd>{gbp(a.residual.prudent_offer)}</dd>
                <dt>Asking</dt>
                <dd>{gbp(a.residual.asking_price)}</dd>
                <dt>Total project (base)</dt>
                <dd>{gbp(a.residual.total_project_cost.base)}</dd>
                <dt>Cost per m² (all-in)</dt>
                <dd>{gbp(a.residual.cost_per_m2)}</dd>
                <dt>Finished value</dt>
                <dd>{a.residual.market_value.available ? `${gbp(a.residual.market_value.comparables_median)} median of ${a.residual.market_value.n} local sales` : "insufficient evidence"}</dd>
              </dl>
            </div>
            <ProvenanceCard d={data} />
            <OverridesCard d={data} act={act} />
          </div>
        </div>
      ) : null}

      {a && tab === "Planning" ? <PlanningTab a={a} /> : null}
      {a && tab === "Site & engineering" ? <RiskTab a={a} /> : null}
      {a && tab === "Design" ? <DesignTab a={a} siteId={s.id} /> : null}
      {a && tab === "Costs & valuation" ? (
        <div className="stack" style={{ gap: 16 }}>
          <ResidualSummary r={a.residual} />
          <div className="card">
            <h3>Acquisition scenarios</h3>
            <Scenarios r={a.residual} />
          </div>
          <div className="grid g2">
            <div className="card">
              <h3>Sensitivity</h3>
              <Tornado items={a.residual.sensitivity} base={a.residual.residual_land_value} />
            </div>
            <div className="card">
              <h3>Programme & finance</h3>
              <dl className="dl">
                <dt>Purchase</dt>
                <dd>{a.costs.programme.purchase_months} months</dd>
                <dt>Planning</dt>
                <dd>{a.costs.programme.planning_months.base}–{a.costs.programme.planning_months.high} months</dd>
                <dt>Build</dt>
                <dd>{a.costs.programme.build_months.low}–{a.costs.programme.build_months.high} months (base {a.costs.programme.build_months.base})</dd>
                <dt>Borrowing (base)</dt>
                <dd>{gbp(a.costs.borrowing_base)}</dd>
              </dl>
              {a.costs.warnings.map((w, i) => <div key={i} className="notice warn" style={{ marginTop: 8 }}>{w}</div>)}
              <h4 style={{ marginTop: 12 }}>Assumptions</h4>
              <ul className="small">{a.costs.assumptions.map((x, i) => <li key={i}>{x}</li>)}</ul>
              <div className="small">{a.residual.market_value.note}</div>
            </div>
          </div>
          <div className="card">
            <h3>Cost plan (16 categories)</h3>
            <CostTable plan={a.costs} />
          </div>
        </div>
      ) : null}
      {tab === "Evidence" ? <EvidenceTab d={data} /> : null}
      {tab === "Acquisition" ? <AcquisitionTab d={data} act={act} /> : null}
    </Shell>
  );
}

function ListingsCard({ d }: { d: Dossier }) {
  if (!d.listings.length) return null;
  return (
    <div className="card">
      <h3>Listing & price history</h3>
      {d.listings.map((l) => (
        <div key={l.id} className="finding">
          <div className="row between">
            <div>
              <span className="ttl">{l.title}</span> <Chip>{l.status}</Chip> <span className="small">{l.source_id}</span>
            </div>
            <strong>
              {gbp(l.asking_price)} {l.price_qualifier ? <span className="small">{l.price_qualifier}</span> : null}
            </strong>
          </div>
          {l.url ? (
            <a className="small" href={l.url} target="_blank" rel="noreferrer noopener">
              Original listing ↗
            </a>
          ) : null}
          <div className="small">
            First seen {when(l.first_seen_at)} · last seen {when(l.last_seen_at)} {l.tenure ? `· ${l.tenure}` : ""}
          </div>
          <div className="small" style={{ marginTop: 4 }}>
            {l.events.map((e, i) => (
              <div key={i}>
                {when(e.at, false)} — {label(e.event)} {e.event === "price_change" ? `${gbp(e.old.asking_price as number)} → ${gbp(e.new.asking_price as number)}` : ""}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function ProvenanceCard({ d }: { d: Dossier }) {
  return (
    <div className="card">
      <h3>Provenance</h3>
      {d.site.provenance.map((p, i) => (
        <div key={i} className="small finding">
          <div>
            <strong>{p.source_id}</strong> {p.ref ? <span className="mono">· {p.ref}</span> : null}
          </div>
          {p.url ? (
            <a href={p.url} target="_blank" rel="noreferrer noopener">
              Source record ↗
            </a>
          ) : null}
          <div>
            Retrieved {when(p.retrieved_at)} · data dated {p.data_date ?? "unknown"}
          </div>
          {p.licence ? <div className="muted">{p.licence}</div> : null}
          {p.attribution ? <div className="muted">{p.attribution}</div> : null}
        </div>
      ))}
      <div className="small">Last assessed {when(d.assessment?.created_at)} · engine {d.assessment?.engine_version}</div>
    </div>
  );
}

function OverridesCard({ d, act }: { d: Dossier; act: (fn: () => Promise<unknown>, ok: string) => void }) {
  const [field, setField] = useState("planning.class");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  return (
    <div className="card">
      <h3>Manual overrides</h3>
      {d.overrides.filter((o) => o.active).map((o) => (
        <div key={o.id} className="small row between">
          <span>
            <span className="mono">{o.field}</span> = {String(o.value.value)} — {o.reason}
          </span>
          <button className="btn sm" onClick={() => act(() => api(`/overrides/${o.id}`, { method: "DELETE" }), "Override removed.")}>
            Remove
          </button>
        </div>
      ))}
      <div className="stack" style={{ marginTop: 8, gap: 6 }}>
        <select value={field} onChange={(e) => setField(e.target.value)}>
          <option value="planning.class">Planning class</option>
          <option value="planning.verified">Mark permission/lawful use verified</option>
          <option value="listing.asking_price">Asking price (£)</option>
          {(d.assessment?.score.fatal_flags ?? []).map((f) => (
            <option key={f.code} value={`flag.${f.code}`}>
              Dismiss flag: {f.title}
            </option>
          ))}
          {["planning", "financial", "physical", "location", "discount"].map((k) => (
            <option key={k} value={`score.${k}`}>
              Set score component: {k}
            </option>
          ))}
        </select>
        {field === "planning.class" ? (
          <select value={value} onChange={(e) => setValue(e.target.value)}>
            <option value="">choose…</option>
            <option value="permission_verified">Permission verified</option>
            <option value="approval_exists_review">Approval exists — review</option>
            <option value="plausible_route">Plausible route</option>
            <option value="speculative_high_risk">Speculative / high risk</option>
            <option value="significant_obstacle">Significant obstacle</option>
          </select>
        ) : field.startsWith("flag.") || field === "planning.verified" ? null : (
          <input placeholder="value" value={value} onChange={(e) => setValue(e.target.value)} />
        )}
        <input placeholder="reason & evidence (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
        <button
          className="btn sm"
          disabled={reason.length < 3}
          onClick={() => {
            const v = field.startsWith("flag.") || field === "planning.verified" ? true : field.startsWith("score.") || field === "listing.asking_price" ? Number(value) : value;
            act(() => api(`/sites/${d.site.id}/overrides`, { method: "POST", json: { field, value: v, reason } }), "Override saved; site re-assessed.");
          }}
        >
          Save override
        </button>
      </div>
    </div>
  );
}

function PlanningTab({ a }: { a: NonNullable<Dossier["assessment"]> }) {
  const p = a.planning;
  return (
    <div className="grid g-main">
      <div className="card">
        <div className="card-head">
          <h3>{p.classification_label}</h3>
          <span className="small">
            score {p.score}/{p.max_score} · confidence {(p.confidence * 100).toFixed(0)}% · LPA {p.lpa ?? "unknown"}
          </span>
        </div>
        {p.findings.map((f) => (
          <div key={f.code} className={`finding sev-${f.effect}`}>
            <div className="row between">
              <span className="ttl">{f.title}</span>
              <span className="row" style={{ gap: 4 }}>
                <Chip tone={f.effect === "positive" ? "good" : f.effect === "fatal" ? "bad" : f.effect === "major" ? "bad" : f.effect === "moderate" ? "warn" : ""}>{f.effect}</Chip>
                <Status s={f.status} />
              </span>
            </div>
            <div className="ink2">{f.detail}</div>
            {f.policy ? <div className="small">Policy basis: {f.policy}</div> : null}
            <SourceLinks sources={f.sources} />
            {f.action ? <div className="small">→ {f.action}</div> : null}
          </div>
        ))}
        <div className="disclaimer">{p.disclaimer}</div>
      </div>
      <div className="stack" style={{ gap: 16 }}>
        <div className="card">
          <h3>Possible planning routes</h3>
          {p.routes.length ? p.routes.map((r) => (
            <div key={r.code + r.name} className="finding">
              <div className="row between">
                <span className="ttl">{r.name}</span>
                <Chip tone={r.plausibility === "strong" ? "good" : r.plausibility === "possible" ? "warn" : r.plausibility === "excluded" ? "bad" : ""}>{r.plausibility}</Chip>
              </div>
              <div className="small">{r.detail}</div>
            </div>
          )) : <Empty>No route identified from available evidence.</Empty>}
        </div>
        <div className="card">
          <h3>Obligations & levies</h3>
          {p.obligations.map((o) => (
            <div key={o.code + o.name} className="finding small">
              <div className="row between">
                <strong>{o.name}</strong>
                <Chip tone={o.applies === "yes" ? "warn" : o.applies === "no" ? "good" : ""}>{o.applies}</Chip>
              </div>
              {o.detail}
            </div>
          ))}
        </div>
        <div className="card">
          <h3>What was checked</h3>
          {Object.entries(p.checks).map(([k, v]) => (
            <div key={k} className="small">
              {label(k)}: <span className={v.startsWith("checked") ? "" : "err"}>{v}</span>
            </div>
          ))}
          <h4 style={{ marginTop: 12 }}>Actions</h4>
          <ol className="small">{p.actions.map((x, i) => <li key={i}>{x}</li>)}</ol>
        </div>
      </div>
    </div>
  );
}

function RiskTab({ a }: { a: NonNullable<Dossier["assessment"]> }) {
  const topics = ["flood", "ground", "utilities", "access", "site_prep"];
  return (
    <div className="grid g2">
      {topics.map((t) => (
        <div className="card" key={t}>
          <h3>{{ flood: "Flooding & water", ground: "Ground conditions", utilities: "Wastewater, water & utilities", access: "Access", site_prep: "Site preparation & ecology" }[t]}</h3>
          {a.site_risk.items.filter((i) => i.topic === t).map((i) => (
            <div key={i.code} className={`finding sev-${i.severity}`}>
              <div className="row between">
                <span className="ttl">{i.title}</span>
                <Status s={i.status} />
              </div>
              <div className="small ink2">{i.detail}</div>
              <SourceLinks sources={i.sources} />
              {i.action ? <div className="small">→ {i.action}</div> : null}
            </div>
          ))}
        </div>
      ))}
      <div className="card">
        <h3>Cost drivers used</h3>
        <dl className="dl">
          {Object.entries(a.site_risk.drivers).map(([k, v]) => (
            <Fragment key={k}>
              <dt>{label(k)}</dt>
              <dd>{String(v)}</dd>
            </Fragment>
          ))}
        </dl>
        <div className="disclaimer">Screening-level. Foundations, drainage and structure must be designed by qualified engineers after site investigation.</div>
      </div>
    </div>
  );
}

function DesignTab({ a, siteId }: { a: NonNullable<Dossier["assessment"]>; siteId: number }) {
  const o = a.design.option;
  return (
    <div className="grid g2">
      <div className="card">
        <div className="card-head">
          <h3>{o.concept.name} — {o.method.name}</h3>
          <Link className="btn sm" href={`/design?site=${siteId}`}>
            Open in Design Studio
          </Link>
        </div>
        <div className="row" style={{ alignItems: "flex-start" }}>
          <Massing option={o} />
          <dl className="dl" style={{ flex: 1 }}>
            <dt>GIA</dt>
            <dd>{o.gia_m2} m² · {o.storeys} storey(s)</dd>
            <dt>Footprint</dt>
            <dd>{o.dims.external_length_m} × {o.dims.external_width_m} m ({o.dims.footprint_m2} m²)</dd>
            <dt>Programme on site</dt>
            <dd>{o.programme_months.low}–{o.programme_months.high} months</dd>
            {o.modules ? (
              <>
                <dt>Modules</dt>
                <dd>{o.modules}</dd>
              </>
            ) : null}
          </dl>
        </div>
        <FloorPlan option={o} />
      </div>
      <div className="stack" style={{ gap: 16 }}>
        <div className="card">
          <h3>Site layout test</h3>
          <div className="row" style={{ marginBottom: 8 }}>
            <Chip tone={a.design.fit.fits ? "good" : a.design.fit.fits === false ? "bad" : ""}>{a.design.fit.fits ? "fits" : a.design.fit.fits === false ? "does not fit" : "not testable"}</Chip>
            <span className="small">{a.design.fit.method?.replace("_", " ")}{a.design.fit.garden_m2 !== undefined ? ` · garden ≈ ${a.design.fit.garden_m2} m²` : ""}{a.design.fit.main_aspect ? ` · aspect ${a.design.fit.main_aspect}` : ""}</span>
          </div>
          <SitePlan fit={a.design.fit} />
          {a.design.fit.reason ? <div className="notice warn" style={{ marginTop: 8 }}>{a.design.fit.reason}</div> : null}
          {a.design.fit.note ? <div className="small">{a.design.fit.note}</div> : null}
          <h4 style={{ marginTop: 12 }}>Concepts tested</h4>
          {a.design.tried.map((t, i) => (
            <div key={i} className="small">
              {t.concept} · {t.storeys} storey: {t.fits === null ? "untestable" : t.fits ? "fits" : `no — ${t.reason}`}
            </div>
          ))}
        </div>
        <div className="card">
          <h3>Design-for-cost principles</h3>
          <ul className="small">{o.method.design_for_cost.map((x, i) => <li key={i}>{x}</li>)}</ul>
          <h4>Professionals required</h4>
          <ul className="small">{o.professionals_required.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </div>
      </div>
    </div>
  );
}

function EvidenceTab({ d }: { d: Dossier }) {
  if (!d.evidence.length) return <Empty>No evidence retrieved yet. Enrichment sources may be unreachable or not configured — see Admin.</Empty>;
  return (
    <div className="table-wrap">
      <table className="t">
        <thead>
          <tr>
            <th>Topic</th>
            <th>Finding</th>
            <th>Status</th>
            <th>Data date</th>
            <th>Retrieved</th>
            <th>Source & licence</th>
          </tr>
        </thead>
        <tbody>
          {d.evidence.map((e) => (
            <tr key={e.id}>
              <td className="small">{e.topic}</td>
              <td>
                <div>{e.title}</div>
                {e.summary ? <div className="small">{e.summary.slice(0, 300)}</div> : null}
              </td>
              <td>
                <Status s={e.status} />
              </td>
              <td className="small">{e.data_date ?? "—"}</td>
              <td className="small">{when(e.retrieved_at)}</td>
              <td className="small" style={{ maxWidth: 320 }}>
                {e.source_url ? (
                  <a href={e.source_url} target="_blank" rel="noreferrer noopener">
                    {e.source_id ?? "source"} ↗
                  </a>
                ) : (
                  e.source_id
                )}
                {e.licence ? <div className="muted">{e.licence}</div> : null}
                {e.attribution ? <div className="muted">{e.attribution}</div> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AcquisitionTab({ d, act }: { d: Dossier; act: (fn: () => Promise<unknown>, ok: string) => void }) {
  const l = d.lead;
  return (
    <div className="grid g2">
      <div className="card">
        <h3>Off-market lead</h3>
        {l ? (
          <>
            <div className="row">
              <Chip tone="info">{label(l.stage)}</Chip>
              <Link href="/pipeline?tab=leads" className="small">
                Manage in pipeline →
              </Link>
            </div>
            <div className="small" style={{ marginTop: 6 }}>Signals: {l.signals.join(", ") || "—"}</div>
            <h4 style={{ marginTop: 12 }}>History</h4>
            {l.events.map((e, i) => (
              <div key={i} className="small">
                {when(e.at)} — {label(e.from ?? "start")} → <strong>{label(e.to)}</strong> ({e.actor}){e.note ? `: ${e.note}` : ""}
              </div>
            ))}
            <div className="disclaimer">PLOT never treats an unadvertised parcel as available. Owner identity comes only from records you obtain (e.g. HM Land Registry official copies).</div>
          </>
        ) : (
          <Empty>Not an off-market lead (listed or user-entered on-market site).</Empty>
        )}
      </div>
      <div className="card">
        <h3>Exclusion</h3>
        <p className="small">Exclude sites you never want to see again; the reason is kept and the site stops being ranked.</p>
        {d.site.excluded ? (
          <button className="btn" onClick={() => act(() => api(`/sites/${d.site.id}/exclude`, { method: "POST", json: { excluded: false } }), "Site re-included.")}>
            Re-include site
          </button>
        ) : (
          <button className="btn danger" onClick={() => act(() => api(`/sites/${d.site.id}/exclude`, { method: "POST", json: { excluded: true, reason: "not for me" } }), "Site excluded.")}>
            Exclude site
          </button>
        )}
        <h4 style={{ marginTop: 14 }}>Score history</h4>
        {d.score_history.map((h, i) => (
          <div key={i} className="small">
            {when(h.at)} — {h.fatal ? "fatal-flagged" : h.score.toFixed(1)}
          </div>
        ))}
      </div>
    </div>
  );
}

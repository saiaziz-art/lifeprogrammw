"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { ErrorBox, Loading } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Prefs = any;

function Num({ label, value, onChange, step = 1, suffix }: { label: string; value: number | null; onChange: (v: number | null) => void; step?: number; suffix?: string }) {
  return (
    <label className="f">
      {label}
      <div className="row" style={{ gap: 6 }}>
        <input type="number" step={step} value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} style={{ width: "100%" }} />
        {suffix ? <span className="small">{suffix}</span> : null}
      </div>
    </label>
  );
}

function Check({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="row" style={{ gap: 6 }}>
      <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} /> {label}
    </label>
  );
}

export default function Settings() {
  const { data, error, loading } = useApi<{ version: number; preferences: Prefs }>("/preferences");
  const [p, setP] = useState<Prefs | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    if (data) setP(structuredClone(data.preferences));
  }, [data]);
  if (loading && !p) return <Shell title="Preferences"><Loading /></Shell>;
  if (!p) return <Shell title="Preferences"><ErrorBox error={error} /></Shell>;

  const set = (path: string, v: unknown) => {
    const next = structuredClone(p);
    const keys = path.split(".");
    let o = next;
    for (const k of keys.slice(0, -1)) o = o[k];
    o[keys[keys.length - 1]] = v;
    setP(next);
  };
  const setBuyer = (k: string, v: unknown) => setP({ ...p, buyer: { ...p.buyer, [k]: v, facts_confirmed: false, confirmed_at: null } });
  const wsum = ["planning", "financial", "physical", "location", "discount"].reduce((a, k) => a + Number(p.weights[k] || 0), 0);

  async function save() {
    setMsg(null);
    try {
      const r = await api<{ version: number; reassess_job: number | null }>("/preferences", { method: "PUT", json: p });
      setMsg(`Saved as version ${r.version}. Every site is being re-assessed (job #${r.reassess_job}).`);
    } catch (e) {
      const d = (e as ApiError).detail;
      setMsg(`Not saved: ${Array.isArray(d) ? d.map((x: any) => `${x.loc?.slice(1).join(".")}: ${x.msg}`).join("; ") : (e as Error).message}`);
    }
  }

  return (
    <Shell title="Preferences">
      <div className="page-head">
        <div>
          <h1>Acquisition strategy</h1>
          <p>Everything here is editable and versioned. Changing it re-runs the planning, cost and scoring engines for every site.</p>
        </div>
        <button className="btn primary" onClick={save} disabled={Math.abs(wsum - 100) > 0.01}>
          Save preferences (v{data?.version})
        </button>
      </div>
      {msg ? <div className={`notice ${msg.startsWith("Not") ? "bad" : "info"}`} style={{ marginBottom: 12 }}>{msg}</div> : null}
      <div className="grid g2">
        <div className="card stack">
          <h3>Budget</h3>
          <div className="notice warn small">This is the ALL-IN budget for land, build, fees, taxes, finance and contingency — not a land budget. The land figure is always derived by residual valuation.</div>
          <Num label="All-in budget (£)" value={p.budget.total_all_in_gbp} onChange={(v) => set("budget.total_all_in_gbp", v)} step={5000} />
          <div className="grid g2">
            <Num label="Over-budget tolerance" value={p.budget.over_budget_tolerance_pct} onChange={(v) => set("budget.over_budget_tolerance_pct", v)} suffix="%" />
            <Num label="New-build contingency" value={p.budget.build_contingency_pct} onChange={(v) => set("budget.build_contingency_pct", v)} suffix="%" />
            <Num label="Conversion contingency" value={p.budget.conversion_contingency_pct} onChange={(v) => set("budget.conversion_contingency_pct", v)} suffix="%" />
            <Num label="Location cost factor" value={p.region_cost_factor} onChange={(v) => set("region_cost_factor", v)} step={0.01} />
          </div>
          <h3 style={{ marginTop: 10 }}>Finance</h3>
          <div className="grid g2">
            <Num label="Cash available (£)" value={p.finance.cash_available_gbp} onChange={(v) => set("finance.cash_available_gbp", v)} step={5000} />
            <Num label="Mortgage rate" value={p.finance.mortgage_rate_pct} onChange={(v) => set("finance.mortgage_rate_pct", v)} step={0.05} suffix="%" />
            <Num label="Arrangement fee (£)" value={p.finance.mortgage_arrangement_fee_gbp} onChange={(v) => set("finance.mortgage_arrangement_fee_gbp", v)} />
            <Num label="Max LTV on land" value={p.finance.max_ltv_land_pct} onChange={(v) => set("finance.max_ltv_land_pct", v)} suffix="%" />
          </div>
          <Check label="Use a self-build (stage-release) mortgage" value={p.finance.use_self_build_mortgage} onChange={(v) => set("finance.use_self_build_mortgage", v)} />
          <Check label="I already own another residential property (SDLT higher rates)" value={p.finance.buyer_owns_other_residential_property} onChange={(v) => set("finance.buyer_owns_other_residential_property", v)} />
          <Check label="First-time buyer" value={p.finance.first_time_buyer} onChange={(v) => set("finance.first_time_buyer", v)} />
          <Check label="Non-UK resident (SDLT surcharge)" value={p.finance.buyer_is_non_uk_resident} onChange={(v) => set("finance.buyer_is_non_uk_resident", v)} />
          <h3 style={{ marginTop: 10 }}>Programme</h3>
          <div className="grid g2">
            <Num label="Purchase (months)" value={p.programme.purchase_months} onChange={(v) => set("programme.purchase_months", v)} />
            <Num label="Construction inflation" value={p.programme.construction_inflation_pct_pa} onChange={(v) => set("programme.construction_inflation_pct_pa", v)} step={0.1} suffix="%/yr" />
            <Num label="Planning — base (months)" value={p.programme.planning_months_base} onChange={(v) => set("programme.planning_months_base", v)} />
            <Num label="Planning — high (months)" value={p.programme.planning_months_high} onChange={(v) => set("programme.planning_months_high", v)} />
          </div>
        </div>
        <div className="card stack">
          <h3>House brief</h3>
          <label className="f">
            Default concept
            <select value={p.house.default_concept} onChange={(e) => set("house.default_concept", e.target.value)}>
              <option value="cabin_40">Compact cabin (~40 m²)</option>
              <option value="small_65">Practical small home (~65 m²)</option>
              <option value="larger_90">Larger permanent home (~90 m²)</option>
            </select>
          </label>
          <label className="f">
            Specification
            <select value={p.house.specification} onChange={(e) => set("house.specification", e.target.value)}>
              <option value="essential">Essential</option>
              <option value="good">Good</option>
              <option value="high">High</option>
            </select>
          </label>
          <div className="grid g3">
            <Num label="Min bedrooms" value={p.house.bedrooms_min} onChange={(v) => set("house.bedrooms_min", v)} />
            <Num label="Min garden (m²)" value={p.house.min_private_garden_m2} onChange={(v) => set("house.min_private_garden_m2", v)} />
            <Num label="Parking spaces" value={p.house.parking_spaces} onChange={(v) => set("house.parking_spaces", v)} />
          </div>
          <div className="small">Construction methods to consider</div>
          <div className="row">
            {["timber_frame", "sips", "modular", "masonry", "conversion"].map((m) => (
              <Check key={m} label={m.replace("_", " ")} value={p.house.preferred_construction.includes(m)} onChange={(v) => set("house.preferred_construction", v ? [...p.house.preferred_construction, m] : p.house.preferred_construction.filter((x: string) => x !== m))} />
            ))}
          </div>
          <Check label="Solar PV" value={p.house.want_solar_pv} onChange={(v) => set("house.want_solar_pv", v)} />
          <Check label="Rainwater harvesting (non-potable)" value={p.house.want_rainwater_harvesting} onChange={(v) => set("house.want_rainwater_harvesting", v)} />
          <h3 style={{ marginTop: 10 }}>Location priorities</h3>
          <Check label="Prefer rural settings (privacy, landscape)" value={p.location.prefer_rural} onChange={(v) => set("location.prefer_rural", v)} />
          <Check label="Bonus for stations with direct London trains" value={p.location.london_rail_station_bonus} onChange={(v) => set("location.london_rail_station_bonus", v)} />
          <div className="grid g2">
            <Num label="Max distance to rail station" value={p.location.max_distance_to_rail_station_km} onChange={(v) => set("location.max_distance_to_rail_station_km", v)} suffix="km" />
            <Num label="Max distance to a town" value={p.location.max_distance_to_town_km} onChange={(v) => set("location.max_distance_to_town_km", v)} suffix="km" />
          </div>
          <h3 style={{ marginTop: 10 }}>Score weights (must total 100 — now {wsum})</h3>
          <div className="grid g3">
            {["planning", "financial", "physical", "location", "discount"].map((k) => (
              <Num key={k} label={k} value={p.weights[k]} onChange={(v) => set(`weights.${k}`, v)} />
            ))}
          </div>
        </div>
        <div className="card stack">
          <h3>Search areas</h3>
          {p.search_areas.map((a: any, i: number) => (
            <div key={i} className="row" style={{ alignItems: "flex-end" }}>
              <Check label="" value={a.enabled} onChange={(v) => set(`search_areas.${i}.enabled`, v)} />
              <label className="f" style={{ flex: 1 }}>
                {a.name}
                <input value={a.bbox.join(", ")} onChange={(e) => set(`search_areas.${i}.bbox`, e.target.value.split(",").map((x) => Number(x.trim())))} title="min lon, min lat, max lon, max lat" />
              </label>
              <button className="btn sm danger" onClick={() => set("search_areas", p.search_areas.filter((_: unknown, j: number) => j !== i))}>
                Remove
              </button>
            </div>
          ))}
          <button className="btn sm" onClick={() => set("search_areas", [...p.search_areas, { name: "New area", bbox: [-2.0, 50.7, -1.5, 51.0], enabled: true }])}>
            + Add area (edit bbox)
          </button>
          <div className="small">Bounding boxes in WGS84: min lon, min lat, max lon, max lat.</div>
        </div>
        <div className="card stack" id="offmarket">
          <h3>Off-market: who the letters come from</h3>
          <div className="small">Drafts use only what you confirm here. Changing any detail asks you to confirm again.</div>
          <label className="f">
            Write as
            <select value={p.buyer.persona} onChange={(e) => setBuyer("persona", e.target.value)}>
              <option value="couple">Me and my partner (e.g. “My wife and I…”)</option>
              <option value="private">A private buyer (just me)</option>
              <option value="represented">Professionally represented (an agent writes on my behalf)</option>
            </select>
          </label>
          <div className="grid g3">
            <label className="f">
              First name (sign-off)
              <input value={p.buyer.first_name ?? ""} onChange={(e) => setBuyer("first_name", e.target.value || null)} />
            </label>
            <label className="f">
              Surname
              <input value={p.buyer.last_name ?? ""} onChange={(e) => setBuyer("last_name", e.target.value || null)} />
            </label>
            <label className="f">
              Partner
              <select value={p.buyer.partner_term ?? ""} onChange={(e) => setBuyer("partner_term", e.target.value || null)}>
                <option value="wife">wife</option>
                <option value="husband">husband</option>
                <option value="partner">partner</option>
                <option value="">— (not mentioned)</option>
              </select>
            </label>
          </div>
          <Check label="We are moving back to the UK (mentioned in letters)" value={!!p.buyer.returning_to_uk} onChange={(v) => setBuyer("returning_to_uk", v)} />
          <label className="f">
            Local connection — only if true (e.g. “My parents live in Ringwood”)
            <input value={p.buyer.local_connection ?? ""} onChange={(e) => setBuyer("local_connection", e.target.value || null)} />
          </label>
          <label className="f">
            Funding statement — only if confirmed (e.g. “We have a self-build mortgage agreed in principle”)
            <input value={p.buyer.funding_statement ?? ""} onChange={(e) => setBuyer("funding_statement", e.target.value || null)} />
          </label>
          {p.buyer.persona === "represented" ? (
            <div className="grid g2">
              <label className="f">
                Agent name
                <input value={p.buyer.agent_name ?? ""} onChange={(e) => setBuyer("agent_name", e.target.value || null)} />
              </label>
              <label className="f">
                Agent company
                <input value={p.buyer.agent_company ?? ""} onChange={(e) => setBuyer("agent_company", e.target.value || null)} />
              </label>
            </div>
          ) : null}
          <label className="f">
            Reply postal address (printed on letters)
            <input value={p.buyer.reply_postal_address ?? ""} onChange={(e) => setBuyer("reply_postal_address", e.target.value || null)} />
          </label>
          <div className="grid g2">
            <label className="f">
              Reply email
              <input type="email" value={p.buyer.reply_email ?? ""} onChange={(e) => setBuyer("reply_email", e.target.value || null)} />
            </label>
            <label className="f">
              Reply phone (optional)
              <input value={p.buyer.reply_phone ?? ""} onChange={(e) => setBuyer("reply_phone", e.target.value || null)} />
            </label>
          </div>
          <div className="notice small">Letters never claim you are a cash buyer, locally resident, fully financed or able to complete quickly unless one of the confirmed statements above says so.</div>
          <label className="checkline">
            <input type="checkbox" checked={!!p.buyer.facts_confirmed} onChange={(e) => setP({ ...p, buyer: { ...p.buyer, facts_confirmed: e.target.checked, confirmed_at: e.target.checked ? new Date().toISOString() : null } })} />
            <span>
              <b>I confirm these details are true and may be used in letters and emails.</b>
              {p.buyer.confirmed_at && p.buyer.facts_confirmed ? <span className="small"> Confirmed {new Date(p.buyer.confirmed_at).toLocaleDateString("en-GB")}.</span> : null}
            </span>
          </label>
        </div>
        <div className="card stack">
          <h3>Off-market: outreach rules</h3>
          <div className="grid g3">
            <Num label="Drafts prepared per day (max)" value={p.outreach.daily_draft_limit} onChange={(v) => set("outreach.daily_draft_limit", v)} />
            <Num label="Optional follow-up after" value={p.outreach.follow_up_after_days} onChange={(v) => set("outreach.follow_up_after_days", v)} suffix="days" />
            <Num label="Follow-ups (0 or 1)" value={p.outreach.max_follow_ups} onChange={(v) => set("outreach.max_follow_ups", v)} />
            <Num label="Min days between contacts" value={p.outreach.min_days_between_contacts} onChange={(v) => set("outreach.min_days_between_contacts", v)} />
            <Num label="Max contacts per owner / year" value={p.outreach.max_contacts_per_year} onChange={(v) => set("outreach.max_contacts_per_year", v)} />
            <Num label="Keep personal data for" value={p.outreach.retention_months} onChange={(v) => set("outreach.retention_months", v)} suffix="months" />
          </div>
          <div className="grid g3">
            <label className="f">
              Postal route
              <select value={p.outreach.postal_provider} onChange={(e) => set("outreach.postal_provider", e.target.value)}>
                <option value="manual">I print and post letters myself</option>
                <option value="stannp">Stannp print &amp; post (API key in Admin)</option>
              </select>
            </label>
            <Num label="Postage estimate (£)" value={p.outreach.postage_estimate_gbp} onChange={(v) => set("outreach.postage_estimate_gbp", v)} step={0.01} />
            <div style={{ alignSelf: "end" }}>
              <Check label="Postal API in test mode" value={p.outreach.postal_provider_test_mode} onChange={(v) => set("outreach.postal_provider_test_mode", v)} />
            </div>
          </div>
          <label className="f">
            Legitimate interests assessment (recorded with every letter to an individual)
            <textarea value={p.outreach.lia_summary} onChange={(e) => set("outreach.lia_summary", e.target.value)} style={{ minHeight: 110 }} />
          </label>
        </div>
        <div className="card stack">
          <h3>Off-market: prospecting areas</h3>
          <div className="small">Each morning PLOT analyses these circles for garden plots, edge-of-settlement paddocks, outbuildings, replacement dwellings, derelict buildings and past residential applications. Discovery never contacts anyone.</div>
          {p.prospecting_areas.map((a: any, i: number) => (
            <div key={i} className="row" style={{ alignItems: "flex-end" }}>
              <Check label="" value={a.enabled} onChange={(v) => set(`prospecting_areas.${i}.enabled`, v)} />
              <label className="f" style={{ flex: 2 }}>
                Name
                <input value={a.name} onChange={(e) => set(`prospecting_areas.${i}.name`, e.target.value)} />
              </label>
              <label className="f" style={{ flex: 2 }}>
                Centre (lon, lat)
                <input value={`${a.lon}, ${a.lat}`} onChange={(e) => { const [lo, la] = e.target.value.split(",").map((x) => Number(x.trim())); const next = structuredClone(p); next.prospecting_areas[i].lon = lo; next.prospecting_areas[i].lat = la; setP(next); }} />
              </label>
              <label className="f" style={{ flex: 1 }}>
                Radius (m)
                <input type="number" value={a.radius_m} onChange={(e) => set(`prospecting_areas.${i}.radius_m`, Number(e.target.value))} />
              </label>
              <button className="btn sm danger" onClick={() => set("prospecting_areas", p.prospecting_areas.filter((_: unknown, j: number) => j !== i))}>
                Remove
              </button>
            </div>
          ))}
          <button className="btn sm" onClick={() => set("prospecting_areas", [...p.prospecting_areas, { name: "New area", lon: -1.79, lat: 50.85, radius_m: 800, enabled: true, types: ["garden_subdivision", "edge_paddock", "outbuilding", "replacement", "derelict", "planning_history"] }])}>
            + Add area
          </button>
          <div className="small">Keep areas small (100–2,000 m radius): the analysis reads every registered title extent and building inside the circle.</div>
        </div>
        <div className="card stack">
          <h3>Exclusions</h3>
          <Check label="Exclude sites in Flood Zone 3" value={p.exclusions.exclude_flood_zone_3} onChange={(v) => set("exclusions.exclude_flood_zone_3", v)} />
          <div className="grid g3">
            <Num label="Min site area (m²)" value={p.exclusions.min_site_area_m2} onChange={(v) => set("exclusions.min_site_area_m2", v)} />
            <Num label="Max site area (m²)" value={p.exclusions.max_site_area_m2} onChange={(v) => set("exclusions.max_site_area_m2", v)} />
            <Num label="Max asking price (£)" value={p.exclusions.max_asking_price_gbp} onChange={(v) => set("exclusions.max_asking_price_gbp", v)} />
          </div>
          <label className="f">
            Excluded keywords (comma-separated)
            <input value={p.exclusions.excluded_keywords.join(", ")} onChange={(e) => set("exclusions.excluded_keywords", e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} />
          </label>
          <label className="f">
            Excluded LPAs (comma-separated, as named)
            <input value={p.exclusions.excluded_lpas.join(", ")} onChange={(e) => set("exclusions.excluded_lpas", e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} />
          </label>
          <h3 style={{ marginTop: 10 }}>Notifications</h3>
          <label className="f">
            Digest email address
            <input type="email" value={p.notifications.email_to ?? ""} onChange={(e) => set("notifications.email_to", e.target.value || null)} />
          </label>
          <Num label="Digest size" value={p.notifications.digest_size} onChange={(v) => set("notifications.digest_size", v)} />
          <Check label="Send critical alerts (failed runs, failing sources)" value={p.notifications.notify_on_critical_alerts} onChange={(v) => set("notifications.notify_on_critical_alerts", v)} />
          <div className="small">Delivery also needs SMTP credentials or a webhook in Admin → Integrations.</div>
        </div>
      </div>
    </Shell>
  );
}

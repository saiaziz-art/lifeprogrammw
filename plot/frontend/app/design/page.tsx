"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { CostTable, ResidualSummary } from "@/components/costs";
import { FloorPlan, Massing, SitePlan } from "@/components/drawings";
import { Shell } from "@/components/Shell";
import { Chip, ErrorBox, FatalFlags, Loading } from "@/components/ui";
import { api, gbp, useApi } from "@/lib/api";
import type { CostPlan, DesignOption, Fit, Residual, SiteRow } from "@/lib/types";

type Catalogue = { concepts: { key: string; name: string; gia_m2: number; bedrooms: number; storeys: number; description: string }[]; methods: { key: string; name: string; notes: string }[]; specifications: string[] };
type Result = { design: { option: DesignOption; fit: Fit; tried: unknown[] }; residual: Residual; costs: CostPlan; comparison: { method: string; name: string; total_base: number; non_land_base: number; residual: number; programme_months: { low: number; base: number; high: number }; cost_per_m2: number }[]; planning_class: string; fatal_flags: { code: string; title: string }[] };

function Studio() {
  const params = useSearchParams();
  const { data: cat } = useApi<Catalogue>("/design/catalogue");
  const { data: sites } = useApi<{ items: SiteRow[] }>("/opportunities?limit=1000");
  const [siteId, setSiteId] = useState<string>(params.get("site") ?? "");
  const [concept, setConcept] = useState("small_65");
  const [method, setMethod] = useState("timber_frame");
  const [gia, setGia] = useState<number>(65);
  const [storeys, setStoreys] = useState<number>(1);
  const [spec, setSpec] = useState("good");
  const [solar, setSolar] = useState(true);
  const [rain, setRain] = useState(true);
  const [land, setLand] = useState<string>("");
  const [genericArea, setGenericArea] = useState<string>("600");
  const [res, setRes] = useState<Result | null>(null);
  const [err, setErr] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const c = cat?.concepts.find((x) => x.key === concept);
    if (c) {
      setGia(c.gia_m2);
      setStoreys(c.storeys);
    }
  }, [concept, cat]);

  const body = useMemo(
    () => ({ site_id: siteId ? Number(siteId) : null, concept, method, gia_m2: gia, storeys, specification: spec, include_solar: solar, include_rainwater: rain, land_price: land === "" ? null : Number(land), site_area_m2: !siteId && genericArea ? Number(genericArea) : null }),
    [siteId, concept, method, gia, storeys, spec, solar, rain, land, genericArea],
  );

  useEffect(() => {
    const t = setTimeout(async () => {
      setBusy(true);
      try {
        setRes(await api<Result>("/design/evaluate", { method: "POST", json: body }));
        setErr(null);
      } catch (e) {
        setErr(e as Error);
      } finally {
        setBusy(false);
      }
    }, 350);
    return () => clearTimeout(t);
  }, [body]);

  const site = sites?.items.find((s) => String(s.id) === siteId);

  return (
    <Shell title="Design Studio">
      <div className="page-head">
        <div>
          <h1>Design Studio</h1>
          <p>Test small-home concepts and construction methods against a site and your budget. Changes recalculate the full cost plan and maximum land offer immediately.</p>
        </div>
        {busy ? <span className="small"><span className="spinner" /> recalculating…</span> : null}
      </div>
      <div className="grid" style={{ gridTemplateColumns: "300px minmax(0,1fr)" }}>
        <div className="card stack" style={{ alignSelf: "start", position: "sticky", top: 70 }}>
          <label className="f">
            Site
            <select value={siteId} onChange={(e) => setSiteId(e.target.value)}>
              <option value="">Generic plot (no site constraints)</option>
              {(sites?.items ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  #{s.id} {s.headline?.slice(0, 48)}
                </option>
              ))}
            </select>
          </label>
          {!siteId ? (
            <label className="f">
              Generic plot area (m²)
              <input inputMode="numeric" value={genericArea} onChange={(e) => setGenericArea(e.target.value.replace(/[^0-9]/g, ""))} />
            </label>
          ) : null}
          <label className="f">
            Concept
            <select value={concept} onChange={(e) => setConcept(e.target.value)}>
              {(cat?.concepts ?? []).map((c) => (
                <option key={c.key} value={c.key}>
                  {c.name} (~{c.gia_m2} m², {c.bedrooms} bed)
                </option>
              ))}
            </select>
          </label>
          <label className="f">
            Gross internal area: {gia} m²
            <input type="range" min={30} max={150} step={5} value={gia} onChange={(e) => setGia(Number(e.target.value))} />
          </label>
          <label className="f">
            Storeys
            <select value={storeys} onChange={(e) => setStoreys(Number(e.target.value))}>
              <option value={1}>Single storey</option>
              <option value={1.5}>1½ storey (rooms in roof)</option>
              <option value={2}>Two storey</option>
            </select>
          </label>
          <label className="f">
            Construction
            <select value={method} onChange={(e) => setMethod(e.target.value)}>
              {(cat?.methods ?? []).map((m) => (
                <option key={m.key} value={m.key}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
          <label className="f">
            Specification
            <select value={spec} onChange={(e) => setSpec(e.target.value)}>
              <option value="essential">Essential</option>
              <option value="good">Good</option>
              <option value="high">High (adds MVHR)</option>
            </select>
          </label>
          <label className="row" style={{ gap: 6 }}>
            <input type="checkbox" checked={solar} onChange={(e) => setSolar(e.target.checked)} /> Solar PV
          </label>
          <label className="row" style={{ gap: 6 }}>
            <input type="checkbox" checked={rain} onChange={(e) => setRain(e.target.checked)} /> Rainwater harvesting (non-potable)
          </label>
          <label className="f">
            Land price to test (£){site?.asking_price ? ` — asking ${gbp(site.asking_price)}` : ""}
            <input inputMode="numeric" placeholder={site?.asking_price ? String(site.asking_price) : "leave blank = solve for max"} value={land} onChange={(e) => setLand(e.target.value.replace(/[^0-9]/g, ""))} />
          </label>
          <div className="disclaimer">Concepts are feasibility studies, not architectural drawings, structural designs or Building Regulations submissions.</div>
        </div>
        <div className="stack" style={{ gap: 16, minWidth: 0 }}>
          {err ? <ErrorBox error={err} /> : null}
          {!res ? <Loading what="Calculating" /> : (
            <>
              <FatalFlags flags={res.fatal_flags} />
              <ResidualSummary r={res.residual} />
              <div className="grid g2">
                <div className="card">
                  <div className="card-head">
                    <h3>{res.design.option.concept.name}</h3>
                    <Chip tone={res.design.fit.fits ? "good" : res.design.fit.fits === false ? "bad" : ""}>{res.design.fit.fits ? "fits site" : res.design.fit.fits === false ? "does not fit" : "fit untestable"}</Chip>
                  </div>
                  <div className="row" style={{ alignItems: "flex-start" }}>
                    <Massing option={res.design.option} />
                    <div className="small" style={{ flex: 1 }}>
                      Footprint {res.design.option.dims.external_length_m} × {res.design.option.dims.external_width_m} m · GEA {res.design.option.dims.gea_m2} m²
                      {res.design.option.modules ? ` · ${res.design.option.modules} modules` : ""}
                      <br />
                      On-site programme {res.design.option.programme_months.low}–{res.design.option.programme_months.high} months
                      <br />
                      Planning status: {res.planning_class}
                    </div>
                  </div>
                  <FloorPlan option={res.design.option} />
                </div>
                <div className="stack" style={{ gap: 16 }}>
                  <div className="card">
                    <h3>Construction comparison</h3>
                    <table className="t">
                      <thead>
                        <tr>
                          <th>Method</th>
                          <th className="num">Excl. land</th>
                          <th className="num">£/m² all-in</th>
                          <th className="num">Max land</th>
                          <th className="num">Months</th>
                        </tr>
                      </thead>
                      <tbody>
                        {res.comparison.map((c) => (
                          <tr key={c.method} style={c.method === method ? { background: "var(--accent-soft)" } : undefined} onClick={() => setMethod(c.method)}>
                            <td>{c.name}</td>
                            <td className="num">{gbp(c.non_land_base, { compact: true })}</td>
                            <td className="num">{gbp(c.cost_per_m2)}</td>
                            <td className="num">{gbp(c.residual, { compact: true })}</td>
                            <td className="num">{c.programme_months.base}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="card">
                    <h3>Site layout</h3>
                    <SitePlan fit={res.design.fit} width={380} height={280} />
                    {res.design.fit.reason ? <div className="small err">{res.design.fit.reason}</div> : null}
                  </div>
                </div>
              </div>
              <div className="card">
                <h3>Cost plan</h3>
                <CostTable plan={res.costs} />
              </div>
            </>
          )}
        </div>
      </div>
    </Shell>
  );
}

export default function DesignPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Studio />
    </Suspense>
  );
}

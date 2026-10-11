"""Assessment orchestrator: facts → planning → design fit → site risk → costs/residual → score.

`compute()` is pure (no DB writes) so the Design Studio can recalculate live; `assess_site()`
persists a versioned Assessment only when the inputs fingerprint changed.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from plot.engine import scoring
from plot.engine.costs.model import CostInputs, build_cost_plan
from plot.engine.costs.rates import load_rates, rates_version
from plot.engine.costs.residual import evaluate
from plot.engine.design.concepts import CONCEPTS, METHODS, design_option
from plot.engine.design.fit import test_fit
from plot.engine.facts import SiteFacts, load_facts
from plot.engine.planning import base as planning_base
from plot.engine.planning import england  # noqa: F401  (registers England)
from plot.engine.planning.england import AGRI_BUILDING, EXISTING_DWELLING
from plot.engine.site_risk import assess_site_risk
from plot.models import Assessment, Feedback, Site, utcnow
from plot.preferences import PreferencesModel

ENGINE_VERSION = "2026.10.3"
CONCEPT_FALLBACK = ["larger_90", "small_65", "cabin_40"]


def _planning_months(cls: str, prefs: PreferencesModel) -> tuple[float, float]:
    base, high = prefs.programme.planning_months_base, prefs.programme.planning_months_high
    if cls == "permission_verified":
        return (1.0, 3.0)
    if cls == "approval_exists_review":
        return (2.0, 6.0)
    if cls == "speculative_high_risk":
        return (base + 3, high + 6)
    return (float(base), float(high))


def cost_inputs(f: SiteFacts, prefs: PreferencesModel, opt: dict[str, Any], drivers, obligations, planning_months, scenario: str = "buy_now") -> CostInputs:
    perimeter = None
    if f.boundary_wkt:
        from plot import geo

        g = geo.parse_wkt(f.boundary_wkt)
        if g is not None:
            perimeter = geo.to_bng(g).length
    existing_dwelling = f.category in ("renovation", "house") or bool(EXISTING_DWELLING.search(f.listing_text))
    return CostInputs(
        gia_m2=opt["gia_m2"],
        method_key=opt["method"]["key"],
        storeys=opt["storeys"],
        dims=opt["dims"],
        modules=opt["modules"],
        build_months=opt["programme_months"],
        spec=prefs.house.specification,
        bathrooms=opt["concept"]["bathrooms"],
        include_solar=prefs.house.want_solar_pv,
        include_rainwater=prefs.house.want_rainwater_harvesting,
        drivers=drivers,
        obligations=obligations,
        site_area_m2=f.area_m2,
        perimeter_m=perimeter,
        existing_dwelling=existing_dwelling,
        planning_months=planning_months,
        purchase_months=prefs.programme.purchase_months,
        budget=prefs.budget.total_all_in_gbp,
        cash_available=prefs.finance.cash_available_gbp,
        use_mortgage=prefs.finance.use_self_build_mortgage,
        mortgage_rate=prefs.finance.mortgage_rate_pct,
        arrangement_fee=prefs.finance.mortgage_arrangement_fee_gbp,
        inflation_pa=prefs.programme.construction_inflation_pct_pa,
        build_contingency_pct=prefs.budget.build_contingency_pct,
        conversion_contingency_pct=prefs.budget.conversion_contingency_pct,
        region_factor=prefs.region_cost_factor,
        sdlt_additional_dwelling=prefs.finance.buyer_owns_other_residential_property,
        sdlt_non_resident=prefs.finance.buyer_is_non_uk_resident,
        sdlt_first_time_buyer=prefs.finance.first_time_buyer,
        scenario=scenario,
        overrides={k: v["value"] for k, v in f.overrides.items() if k.startswith("cost.")},
    )


def choose_design(f: SiteFacts, prefs: PreferencesModel, drivers, obligations, planning_months, rates, meta, concept_key: str | None = None, method_key: str | None = None, gia: float | None = None, storeys: float | None = None) -> tuple[dict, dict, list[dict]]:
    """Pick the concept that fits (falling back to smaller ones) and the cheapest preferred method."""
    methods = [m for m in prefs.house.preferred_construction if m in METHODS]
    has_building = f.category in ("barn", "renovation", "building") or bool(AGRI_BUILDING.search(f.listing_text))
    if not has_building:
        methods = [m for m in methods if m != "conversion"] or ["timber_frame"]
    if method_key:
        methods = [method_key]
    start = concept_key or prefs.house.default_concept
    order = [start] + [c for c in CONCEPT_FALLBACK if c != start and CONCEPTS[c].gia_m2 < CONCEPTS[start].gia_m2]
    tried: list[dict] = []
    chosen_fit, chosen_opt = None, None
    for ck in order:
        storey_opts = [storeys] if storeys else ([CONCEPTS[ck].storeys] + ([2.0] if CONCEPTS[ck].storeys < 2 and ck != "cabin_40" else []))
        for st in storey_opts:
            opt0 = design_option(ck, methods[0], gia if ck == start else None, st)
            fit = test_fit(f.boundary_wkt, f.area_m2, opt0["dims"], parking_spaces=prefs.house.parking_spaces, min_garden_m2=prefs.house.min_private_garden_m2)
            tried.append({"concept": ck, "storeys": st, "fits": fit.get("fits"), "method": fit.get("method"), "reason": fit.get("reason")})
            if fit.get("fits") is not False:
                chosen_fit, chosen_opt = fit, opt0
                break
        if chosen_opt:
            break
    if chosen_opt is None:
        chosen_opt = design_option(order[-1], methods[0], None, None)
        chosen_fit = test_fit(f.boundary_wkt, f.area_m2, chosen_opt["dims"], parking_spaces=prefs.house.parking_spaces, min_garden_m2=prefs.house.min_private_garden_m2)
    # cheapest method at zero land cost
    best = None
    for mk in methods:
        opt = design_option(chosen_opt["concept"]["key"], mk, chosen_opt["gia_m2"], chosen_opt["storeys"])
        plan = build_cost_plan(cost_inputs(f, prefs, opt, drivers, obligations, planning_months), rates, meta, 0.0)
        if best is None or plan["totals"]["base"] < best[0]:
            best = (plan["totals"]["base"], opt)
    return best[1], chosen_fit, tried


def compute(f: SiteFacts, prefs: PreferencesModel, rates, meta, design: dict[str, Any] | None = None, feedback: int = 0, light: bool = False) -> dict[str, Any]:
    w = prefs.weights
    planning = planning_base.get("england").assess(f, prefs, w.planning)
    obligations = [o.__dict__ for o in planning.obligations]
    pm = _planning_months(planning.classification, prefs)

    # preliminary drivers for design choice; recomputed with the fit result afterwards
    risk0 = assess_site_risk(f, w.physical)
    d = design or {}
    opt, fit, tried = choose_design(f, prefs, risk0.drivers, obligations, pm, rates, meta, d.get("concept"), d.get("method"), d.get("gia_m2"), d.get("storeys"))
    risk = assess_site_risk(f, w.physical, fit)
    if fit.get("fits") is False:
        risk = replace(risk, score=round(max(0.0, risk.score - 8 * w.physical / 20), 1))

    asking = f.asking_price
    market = None
    comp = f.first("comparables")
    if comp:
        market = dict(comp.data.get("summary", {}))
        epc = f.first("epc-floor-areas")
        if epc and epc.data.get("records"):
            areas = sorted(r["floor_area_m2"] for r in epc.data["records"])
            market["median_floor_area_m2"] = areas[len(areas) // 2]
    inp = cost_inputs(f, prefs, opt, risk.drivers, obligations, pm)
    res = evaluate(inp, rates, meta, prefs.budget.total_all_in_gbp, asking, market, prefs.budget.over_budget_tolerance_pct, light=light)

    fin, fin_notes = scoring.financial_score(res, prefs.budget.total_all_in_gbp, w.financial)
    loc, loc_parts = scoring.location_score(f, prefs, w.location)
    disc, disc_why = scoring.discount_score(f, res, w.discount, f.today)
    components = {
        "planning": {"score": planning.score, "max": w.planning},
        "financial": {"score": fin, "max": w.financial, "notes": fin_notes},
        "physical": {"score": risk.score, "max": w.physical},
        "location": {"score": loc, "max": w.location, "parts": loc_parts},
        "discount": {"score": disc, "max": w.discount, "why": disc_why},
    }
    for k in list(components):
        ov = f.overrides.get(f"score.{k}")
        if ov is not None:
            components[k]["overridden_from"] = components[k]["score"]
            components[k]["score"] = float(ov["value"])
            components[k]["override_reason"] = ov.get("reason")
    total = round(sum(c["score"] for c in components.values()), 1)

    # fatal flags (override numeric score in ranking)
    flags = []
    for fd in planning.findings:
        if fd.effect == "fatal" and fd.status in ("confirmed", "estimated"):
            flags.append({"code": fd.code, "title": fd.title, "source": "planning"})
    for it in risk.items:
        if it.severity == "fatal":
            flags.append({"code": it.code, "title": it.title, "source": "site_risk"})
    if res["residual_land_value"] < 0:
        flags.append({"code": "unaffordable", "title": f"Over budget by £{-res['residual_land_value']:,.0f} even with free land", "source": "financial"})
    if fit.get("fits") is False and all(t["fits"] is False for t in tried):
        how = "inside the boundary with setbacks" if fit.get("method") == "boundary" else "on the reported site area (area-based estimate; boundary unknown)"
        flags.append({"code": "too_small", "title": f"No house concept fits {how}", "source": "design"})
    dismissed = [fl for fl in flags if f"flag.{fl['code']}" in f.overrides]
    flags = [fl for fl in flags if f"flag.{fl['code']}" not in f.overrides]
    conf = scoring.data_confidence(f, planning.confidence, risk.confidence, asking is not None)

    explanation = explain(f, prefs, planning, risk, opt, fit, res, components, flags)
    return {
        "planning": planning.to_dict(),
        "site_risk": risk.to_dict(),
        "design": {"option": opt, "fit": fit, "tried": tried},
        "residual": {k: v for k, v in res.items() if k != "plan"},
        "costs": res["plan"],
        "score": {"total": total, "components": components, "fatal_flags": flags, "dismissed_flags": dismissed, "feedback_adjustment": feedback * 3, "rank_score": (-100 if flags else 0) + total + feedback * 3},
        "fatal": bool(flags),
        "confidence": conf,
        "explanation": explanation,
    }


def explain(f, prefs, planning, risk, opt, fit, res, components, flags) -> dict[str, Any]:
    pos = [x.title for x in planning.findings if x.effect == "positive"]
    loc = components["location"]["parts"]
    interesting = pos[:2] + [p["detail"] for p in loc.values() if p["score"] >= 0.75 * p["max"] and p["status"] != "unknown"][:2]
    if not interesting:
        interesting = [f.headline or "Candidate site in your search area"]
    under = list(components["discount"]["why"])
    if res.get("headroom_vs_asking") is not None and res["headroom_vs_asking"] > 0:
        under.insert(0, f"Residual land value exceeds asking price by £{res['headroom_vs_asking']:,.0f}")
    c = opt["concept"]
    home = f"{c['name']} (~{opt['gia_m2']:.0f} m², {c['bedrooms']} bed, {opt['storeys']:g}-storey) in {opt['method']['name'].lower()}; footprint {opt['dims']['external_length_m']:.1f} × {opt['dims']['external_width_m']:.1f} m."
    if fit.get("fits") is True:
        home += f" Fits ({fit['method'].replace('_', ' ')})" + (f", ~{fit.get('garden_m2'):,} m² garden" if fit.get("garden_m2") else "") + (f", main aspect {fit.get('main_aspect')}" if fit.get("main_aspect") else "") + "."
    elif fit.get("fits") is False:
        home += f" Does not fit: {fit.get('reason')}"
    else:
        home += " Fit not testable (no boundary or area)."
    t = res["total_project_cost"]
    b = prefs.budget.total_all_in_gbp
    if res["asking_price"] is not None:
        budget_fit = f"Total at asking price: £{t['base']:,} base (£{t['low']:,}–£{t['high_rss']:,}); {'within' if res['within_budget'] else 'over'} your £{b:,} budget. Max recommended offer £{res['max_recommended_offer']:,}."
    elif res["residual_land_value"] >= 0:
        budget_fit = f"No asking price. Everything except land costs ≈£{t['base'] - res['land_price_used']:,}; you could pay up to £{res['max_recommended_offer']:,} for the land within £{b:,}."
    else:
        budget_fit = f"Exceeds £{b:,} budget by £{-res['residual_land_value']:,} even with free land."
    risks = []
    for fl in flags:
        risks.append({"title": fl["title"], "severity": "fatal", "status": "confirmed"})
    sev_rank = {"fatal": 0, "major": 1, "high": 1, "moderate": 2, "medium": 2}
    cand = [{"title": x.title, "severity": x.effect, "status": x.status} for x in planning.findings if x.effect in ("major", "moderate")]
    cand += [{"title": i.title, "severity": i.severity, "status": i.status} for i in risk.items if i.severity in ("high", "medium")]
    cand.sort(key=lambda r: sev_rank.get(r["severity"], 9))
    for r in cand:
        if len(risks) >= 3:
            break
        if r["title"] not in [x["title"] for x in risks]:
            risks.append(r)
    if flags:
        nxt = f"Deprioritise unless you can disprove: {flags[0]['title']}"
    elif f.stale:
        nxt = "Confirm the site's current status with the LPA — the source record is historic"
    elif planning.classification == "approval_exists_review":
        nxt = "Verify the permission on the LPA register (reference, conditions, expiry, S106) before viewing"
    elif f.origin != "on_market" and f.asking_price is None:
        nxt = "Check title ownership (HM Land Registry official copy, ~£7) before any approach"
    else:
        nxt = planning.actions[0] if planning.actions else "Arrange a site visit"
    return {"why_interesting": interesting[:4], "undervalued": under[:4] or ["No discount evidence yet"], "how_home_works": home, "budget_fit": budget_fit, "top_risks": risks[:3], "next_action": nxt}


def feedback_signal(db: Session, site_id: int) -> int:
    vals = db.scalars(select(Feedback.signal).where(Feedback.site_id == site_id)).all()
    s = sum(vals)
    return max(-1, min(1, s))


def assess_site(db: Session, site: Site, prefs: PreferencesModel, prefs_version: int, run_id: int | None = None, force: bool = False, rates_cache=None) -> Assessment | None:
    rates, meta = rates_cache or load_rates(db)
    rv = rates_version(rates)
    facts = load_facts(db, site)
    fp = facts.fingerprint(prefs_version, rv, ENGINE_VERSION)
    fb = feedback_signal(db, site.id)
    fp = f"{fp[:56]}{fb + 1:08d}"
    cur = db.scalar(select(Assessment).where(Assessment.site_id == site.id, Assessment.is_current.is_(True)))
    if cur is not None and cur.inputs_fingerprint == fp and not force:
        return None
    out = compute(facts, prefs, rates, meta, feedback=fb)
    db.execute(update(Assessment).where(Assessment.site_id == site.id, Assessment.is_current.is_(True)).values(is_current=False))
    a = Assessment(
        site_id=site.id,
        run_id=run_id,
        is_current=True,
        inputs_fingerprint=fp,
        preferences_version=prefs_version,
        engine_version=ENGINE_VERSION,
        planning=out["planning"],
        site_risk=out["site_risk"],
        design=out["design"],
        costs=out["costs"],
        residual=out["residual"],
        score=out["score"],
        total_score=out["score"]["total"],
        fatal=out["fatal"],
        data_confidence=out["confidence"]["score"],
        explanation=out["explanation"] | {"confidence": out["confidence"]},
        created_at=utcnow(),
    )
    db.add(a)
    db.flush()
    return a


def rerank(db: Session) -> None:
    rows = db.scalars(select(Assessment).where(Assessment.is_current.is_(True))).all()
    rows.sort(key=lambda a: a.score.get("rank_score", a.total_score), reverse=True)
    for i, a in enumerate(rows, start=1):
        a.rank = i
    db.flush()


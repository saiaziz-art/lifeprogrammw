"""Unit tests for the deterministic engines (no database, no network)."""

from __future__ import annotations

import datetime as dt

import pytest

from plot.ai.extract import ListingFields, verify
from plot.engine.assess import compute
from plot.engine.costs.model import CATEGORIES, CostInputs, build_cost_plan
from plot.engine.costs.rates import load_rates
from plot.engine.costs.residual import evaluate, solve_residual, total_at
from plot.engine.costs.sdlt import sdlt
from plot.engine.design.concepts import design_option, footprint_dims
from plot.engine.design.fit import test_fit as fit_test
from plot.engine.planning import base as planning_base
from plot.engine.planning import england  # noqa: F401
from plot.engine.site_risk import CostDrivers, assess_site_risk
from plot.preferences import PreferencesModel, Weights, default_preferences
from tests.helpers import SQUARE_30M, ev, facts, listing, near

RATES, META = load_rates(None)
PREFS = default_preferences()


# ----------------------------------------------------------------------------- SDLT


@pytest.mark.parametrize(
    "price,kw,expected",
    [
        (200_000, {"residential": False}, 1_000),  # 2% of 50k
        (400_000, {"residential": False}, 9_500),  # 2%×100k + 5%×150k
        (300_000, {"residential": True}, 5_000),  # 2%×125k + 5%×50k
        (300_000, {"residential": True, "additional_dwelling": True}, 20_000),
        (300_000, {"residential": True, "first_time_buyer": True}, 0),
        (450_000, {"residential": True, "first_time_buyer": True}, 7_500),
        (600_000, {"residential": True, "first_time_buyer": True}, 20_000),  # relief unavailable above £500k
        (30_000, {"residential": True, "additional_dwelling": True}, 0),  # higher rates not below £40k
    ],
)
def test_sdlt_bands(price, kw, expected):
    assert sdlt(price, **kw).tax == expected


# ----------------------------------------------------------------------------- cost model


def _inputs(**over) -> CostInputs:
    opt = design_option("small_65", over.pop("method", "timber_frame"))
    base = dict(
        gia_m2=opt["gia_m2"], method_key=opt["method"]["key"], storeys=opt["storeys"], dims=opt["dims"], modules=opt["modules"],
        build_months=opt["programme_months"], spec="good", bathrooms=1, include_solar=True, include_rainwater=True, drivers=CostDrivers(),
        obligations=[], site_area_m2=900, perimeter_m=120, existing_dwelling=False, planning_months=(6, 14), purchase_months=3,
        budget=400_000, cash_available=150_000, use_mortgage=True, mortgage_rate=5.75, arrangement_fee=1500, inflation_pa=3.5,
        build_contingency_pct=10, conversion_contingency_pct=17.5, region_factor=1.03,
    )
    base.update(over)
    return CostInputs(**base)


def test_cost_plan_structure_and_totals():
    plan = build_cost_plan(_inputs(), RATES, META, 100_000)
    assert [c["no"] for c in plan["categories"]] == list(CATEGORIES)
    assert plan["totals"]["base"] == pytest.approx(sum(c["base"] for c in plan["categories"]), abs=16)
    for line in plan["lines"]:
        assert line["low"] <= line["base"] + 1 <= line["high"] + 2, line["key"]
    assert plan["totals"]["low_rss"] <= plan["totals"]["base"] <= plan["totals"]["high_rss"] <= plan["totals"]["high"]
    land = next(l for l in plan["lines"] if l["key"] == "land")
    assert land["base"] == 100_000
    sd = next(l for l in plan["lines"] if l["key"] == "sdlt")
    assert sd["base"] == 0  # non-residential, below £150k


def test_modular_transport_and_conversion_vat():
    mod = build_cost_plan(_inputs(method="modular"), RATES, META, 0)
    assert any(l["key"] == "transport.module" for l in mod["lines"])
    conv = build_cost_plan(_inputs(method="conversion"), RATES, META, 0)
    assert any(l["key"] == "vat.conversion" for l in conv["lines"])
    assert any(l["key"] == "survey.structural" for l in conv["lines"])


def test_cost_override_and_ground_case():
    standard = build_cost_plan(_inputs(), RATES, META, 0)["totals"]["base"]
    d = CostDrivers(foundation_case="engineered", foundation_known=True)
    worse = build_cost_plan(_inputs(drivers=d), RATES, META, 0)["totals"]["base"]
    assert worse > standard + 10_000
    ov = build_cost_plan(_inputs(overrides={"cost.pkg.kitchen": 20_000}), RATES, META, 0)
    k = next(l for l in ov["lines"] if l["key"] == "pkg.kitchen")
    assert k["base"] == 20_000 and k["user_override"]


def test_residual_solves_budget_exactly():
    inp = _inputs()
    r = solve_residual(inp, RATES, META, 400_000)
    assert r > 0
    assert total_at(inp, RATES, META, r) == pytest.approx(400_000, abs=100)
    assert total_at(inp, RATES, META, r + 5_000) > 400_000


def test_residual_negative_when_unaffordable():
    r = solve_residual(_inputs(), RATES, META, 150_000)
    assert r < 0  # shortfall even with free land


def test_cheap_unserviced_plot_does_not_beat_serviced_plot():
    """A £60k plot needing long service runs and engineered foundations must not outrank a
    £100k serviced plot on cost grounds alone."""
    cheap = _inputs(drivers=CostDrivers(services_remote=True, drainage="off_mains", infiltration_doubt=True, foundation_case="engineered", foundation_known=True, long_access_m=120))
    serviced = _inputs(drivers=CostDrivers(services_remote=False, drainage="mains", foundation_case="standard", foundation_known=True))
    t_cheap = build_cost_plan(cheap, RATES, META, 60_000)["totals"]["base"]
    t_serv = build_cost_plan(serviced, RATES, META, 100_000)["totals"]["base"]
    assert t_cheap > t_serv


def test_evaluate_scenarios_and_sensitivity():
    res = evaluate(_inputs(), RATES, META, 400_000, asking=50_000, market=None, tolerance_pct=5)
    assert set(res["scenarios"]) == {"buy_now", "subject_to_planning", "option"}
    assert len(res["sensitivity"]) == 5 and all(s["delta"] <= 0 for s in res["sensitivity"])
    assert res["headroom_vs_asking"] == res["residual_land_value"] - 50_000
    assert res["market_value"]["available"] is False
    assert evaluate(_inputs(), RATES, META, 400_000, asking=None, market=None, tolerance_pct=5)["within_budget"] is None


# ----------------------------------------------------------------------------- planning


def plan(f):
    return planning_base.get("england").assess(f, PREFS, 30)


def test_sssi_on_site_is_fatal():
    p = plan(facts(evidence=[ev("pd:site-of-special-scientific-interest", {"name": "Test SSSI"})]))
    assert p.classification == "significant_obstacle"
    assert any(x.code == "sssi_onsite" and x.effect == "fatal" for x in p.findings)


def test_dorset_heaths_400m_rule():
    p = plan(facts(evidence=[near("special-protection-area", ("Dorset Heathlands", 300.0))]))
    assert p.classification == "significant_obstacle"
    p2 = plan(facts(evidence=[near("special-protection-area", ("Dorset Heathlands", 2500.0))]))
    assert any(o.code == "habitats" and o.applies == "yes" for o in p2.obligations)


def test_claimed_permission_needs_verification():
    p = plan(facts(listings=[listing(desc="Building plot with full planning permission for a detached house")], evidence=[ev("pd:built-up-area", {"name": "Sway"}, topic="location")]))
    assert p.classification == "approval_exists_review"
    claim = next(x for x in p.findings if x.code == "claimed_permission")
    assert claim.status == "requires_investigation"


def test_isolated_bare_land_is_speculative_and_infill_is_plausible():
    iso = plan(facts(listings=[listing(desc="Paddock")], evidence=[near("built-up-area", ("Village", 2500.0))]))
    assert iso.classification == "speculative_high_risk"
    infill = plan(facts(listings=[listing(desc="Garden plot")], evidence=[ev("pd:built-up-area", {"name": "Town"}, topic="location")]))
    assert infill.classification == "plausible_route"


def test_class_q_excluded_in_national_landscape():
    p = plan(facts(category="barn", listings=[listing(desc="Steel portal frame barn")], evidence=[ev("pd:area-of-outstanding-natural-beauty", {"name": "Cranborne Chase"}), near("built-up-area", ("Village", 1200.0))]))
    cq = next(r for r in p.routes if r.code == "class_q")
    assert cq.plausibility == "excluded" and "National Landscape" in cq.detail


def test_bng_small_site_exemption_after_aug_2026():
    small = plan(facts(area=1500))
    assert next(o for o in small.obligations if o.code == "bng").applies == "no"
    big = plan(facts(area=3000))
    assert next(o for o in big.obligations if o.code == "bng").applies == "yes"
    before = plan(facts(area=1500, today=dt.date(2026, 6, 1)))
    assert next(o for o in before.obligations if o.code == "bng").applies == "possible"


def test_flood_zone_3_is_major_and_3b_fatal():
    p = plan(facts(evidence=[ev("ea:flood_zone", {"zone": "3"}, topic="flood")]))
    assert any(x.code == "flood_zone_3" and x.effect == "major" for x in p.findings)
    p = plan(facts(evidence=[ev("ea:flood_zone", {"zone": "3b"}, topic="flood")]))
    assert p.classification == "significant_obstacle"


def test_unchecked_constraints_lower_confidence():
    checked = plan(facts(evidence=[near("built-up-area", ("Town", 50.0)), ev("ea:flood_zone", {"zone": "1"}, topic="flood")]))
    unchecked = plan(facts(checked=False))
    assert unchecked.confidence < checked.confidence
    assert unchecked.actions[0].startswith("Constraint data not yet retrieved")


# ----------------------------------------------------------------------------- site risk


def test_geology_drives_foundation_case():
    clay = assess_site_risk(facts(evidence=[ev("bgs:bedrock", {"units": [{"LEX_D": "LONDON CLAY FORMATION", "RCS_D": "CLAY, SILT AND SAND"}]}, topic="ground")]))
    assert clay.drivers.foundation_case == "deep" and clay.drivers.infiltration_doubt
    peat = assess_site_risk(facts(evidence=[ev("bgs:superficial", {"units": [{"LEX_D": "PEAT", "RCS_D": "PEAT"}]}, topic="ground")]))
    assert peat.drivers.foundation_case == "engineered"
    gravel = assess_site_risk(facts(evidence=[ev("bgs:superficial", {"units": [{"LEX_D": "RIVER TERRACE DEPOSITS", "RCS_D": "SAND AND GRAVEL"}]}, topic="ground")]))
    assert gravel.drivers.foundation_case == "standard"


def test_unknown_data_does_not_score_better_than_surveyed_low_risk():
    surveyed = assess_site_risk(facts(evidence=[ev("ea:flood_zone", {"zone": "1"}, topic="flood"), ev("ea:rofsw", {"hits": []}, topic="flood"), ev("ea:recorded_outlines", {"hits": []}, topic="flood"), ev("bgs:superficial", {"units": [{"LEX_D": "RIVER TERRACE DEPOSITS"}]}, topic="ground"), ev("pd:built-up-area", {"name": "Town"}, topic="location")]))
    unknown = assess_site_risk(facts(checked=False, evidence=[]))
    assert unknown.score < surveyed.score


def test_off_mains_when_remote():
    r = assess_site_risk(facts(evidence=[near("built-up-area", ("Village", 900.0))]))
    assert r.drivers.drainage == "off_mains" and r.drivers.services_remote is True


# ----------------------------------------------------------------------------- design fit


def test_fit_with_boundary_and_area_only():
    dims = footprint_dims(65, 1)
    ok = fit_test(SQUARE_30M, None, dims, min_garden_m2=80)
    assert ok["fits"] is True and ok["method"] == "boundary" and ok["garden_m2"] > 80
    tiny = fit_test(None, 180, dims)
    assert tiny["fits"] is False and tiny["method"] == "area_only"
    unknown = fit_test(None, None, dims)
    assert unknown["fits"] is None


def test_modular_respects_road_width():
    o = design_option("small_65", "modular")
    assert o["modules"] >= 2
    assert o["dims"]["internal_width_m"] <= 2 * 4.2


# ----------------------------------------------------------------------------- full compute & scoring


def test_compute_end_to_end_scores_and_explanations():
    f = facts(listings=[listing(price=40_000, desc="Plot with outline planning permission", events=[{"event": "price_change", "old": {"asking_price": 55_000}, "new": {"asking_price": 40_000}, "at": "2026-10-01"}])], boundary=SQUARE_30M, evidence=[ev("pd:built-up-area", {"name": "Sway"}, topic="location"), ev("ea:flood_zone", {"zone": "1"}, topic="flood")], rail={"name": "Sway", "distance_m": 1500, "london_direct": True})
    out = compute(f, PREFS, RATES, META)
    comps = out["score"]["components"]
    assert set(comps) == {"planning", "financial", "physical", "location", "discount"}
    assert all(0 <= c["score"] <= c["max"] for c in comps.values())
    assert out["score"]["total"] == pytest.approx(sum(c["score"] for c in comps.values()), abs=0.2)
    e = out["explanation"]
    assert e["why_interesting"] and e["next_action"] and e["budget_fit"] and len(e["top_risks"]) <= 3
    assert "price reduction" in " ".join(comps["discount"]["why"])


def test_fatal_flag_overrides_rank_and_can_be_dismissed():
    f = facts(evidence=[ev("pd:scheduled-monument", {"name": "Barrow"})])
    out = compute(f, PREFS, RATES, META)
    assert out["fatal"] and out["score"]["rank_score"] < 0
    f.overrides["flag.scheduled_monument_onsite"] = {"value": True, "reason": "monument is on adjoining land per HE map"}
    out2 = compute(f, PREFS, RATES, META)
    assert not any(fl["code"] == "scheduled_monument_onsite" for fl in out2["score"]["fatal_flags"])


def test_weights_must_sum_to_100():
    with pytest.raises(ValueError):
        Weights(planning=50, financial=25, physical=20, location=15, discount=10)
    PreferencesModel.model_validate(default_preferences().model_dump())


# ----------------------------------------------------------------------------- AI verification


def test_ai_verification_rejects_unquoted_values():
    text = "Guide price £95,000. A 0.25 acre building plot on Church Lane, Sway SO41 6AB, freehold."
    good, rej = verify(ListingFields(asking_price_gbp=95000, land_area_value=0.25, land_area_unit="acres", postcode="SO41 6AB", tenure="freehold", address_text="Church Lane, Sway"), text)
    assert good["asking_price"] == 95000 and round(good["area_m2"]) == 1012 and good["tenure"] == "freehold" and not rej
    bad, rej = verify(ListingFields(asking_price_gbp=120000, land_area_value=2, land_area_unit="hectares", address_text="Somewhere else"), text)
    assert "asking_price" not in bad and "area_m2" not in bad and "address_text" not in bad and len(rej) == 3

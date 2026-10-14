"""Off-market discovery: geometry analysis, acquisition score and owner-benefit framing.

All geometry is synthetic TEST DATA (tests/geom_fixtures.py), not real places.
"""

from __future__ import annotations

from plot.offmarket.analyse import Analyser
from plot.offmarket.score import acquisition_score, contact_method, next_action, owner_benefits
from tests.geom_fixtures import street_area


def _by_title(data=None):
    return {c.title_id: c for c in Analyser(data or street_area()).run()}


# ------------------------------------------------------------------ analyser


def test_wide_plot_is_side_garden_subdivision_with_own_frontage():
    c = _by_title()["WIDE"]
    assert c.type == "garden_subdivision"
    assert c.access["type"] == "independent_frontage" and c.access["position"] == "side_garden"
    assert c.access["road_name"] == "Test Road"
    m = c.metrics
    # the indicative plot sits in the garden, outside the 8 m amenity zone, and the owner keeps most of the garden
    assert c.plot_geom.within(c.title_geom)
    assert c.plot_geom.distance(c.house_geom) >= 8 - 1e-6
    assert m["retained_garden_ratio"] >= 0.5 and m["separation_to_house_m"] >= 10
    assert "inferred" in m["house_identification"] and "not verified" in m["house_identification"]
    assert not c.flags


def test_backland_plot_found_with_driveway_clear_of_house_and_flagged():
    c = _by_title()["BACK"]
    assert c.type == "garden_subdivision"
    assert c.access["type"] == "backland_strip"
    assert c.access_geom is not None
    assert not c.access_geom.intersects(c.house_geom.buffer(1.0))
    assert c.access_geom.difference(c.title_geom.buffer(0.5)).area <= 0.1 * c.access_geom.area
    codes = {f["code"] for f in c.flags}
    assert "backland" in codes  # tandem development is often resisted
    assert "character" in codes  # 240 m² plot vs ~600 m² neighbours
    assert "garden_much_larger_than_neighbours" in c.signals


def test_narrow_deep_plot_cannot_fit_access_so_is_not_offered_as_subdivision():
    c = _by_title()["DEEP"]
    # 4 m gaps either side of the house cannot take a 3.6 m drive 1 m clear of the house
    assert c.type == "replacement"
    assert "small_dwelling_on_generous_plot" in c.signals


def test_paddock_adjoining_settlement_detected_with_indicative_plot():
    c = _by_title()["PADDOCK"]
    assert c.type == "edge_paddock"
    assert c.metrics["in_built_up_area"] is False and c.metrics["distance_to_built_up_m"] <= 60
    assert c.plot_geom.within(c.title_geom.buffer(0.01))
    assert 300 <= c.metrics["plot_area_m2"] <= 420


def test_typical_plots_are_not_candidates():
    found = _by_title()
    assert not any(k.startswith("T") for k in found)


def test_tree_preservation_constraint_attached_to_indicative_plot():
    c = _by_title(street_area(with_tpo_on_wide_plot=True))["WIDE"]
    assert {h["dataset"] for h in c.constraints} == {"tree-preservation-zone"}


def test_no_frontage_paddock_is_not_proposed():
    data = street_area()
    data.roads = []
    found = _by_title(data)
    assert "PADDOCK" not in found  # no road, no credible access
    assert "WIDE" not in found or found["WIDE"].type != "garden_subdivision"


# ------------------------------------------------------------------ scoring


def _analysis(c, buildings_source=True):
    return {"metrics": c.metrics, "access": c.access, "constraints": c.constraints, "flags": c.flags, "signals": c.signals, "sources": {"buildings": "test"} if buildings_source else {}}


def _assessment(cls="plausible_route", rlv=90_000, market=True, fits=True):
    return {
        "planning": {"classification": cls, "classification_label": cls.replace("_", " "), "checks": {"national_designations": "checked"}},
        "residual": {"residual_land_value": rlv, "market_value": {"available": market, "comparables_median": 525_000}},
        "design": {"fit": {"fits": fits, "site_area_m2": 300}},
        "score": {"components": {"location": {"score": 11, "max": 15}}, "fatal_flags": []},
    }


def test_strong_verified_garden_plot_scores_well_with_high_confidence():
    c = _by_title()["WIDE"]
    s = acquisition_score("garden_subdivision", _analysis(c), _assessment(), "verified", {}, "individual")
    assert set(s["components"]) == {"planning", "physical", "economics", "ownership", "home"}
    assert [s["components"][k]["max"] for k in ("planning", "physical", "economics", "ownership", "home")] == [30, 25, 20, 15, 10]
    assert abs(s["total"] - sum(v["score"] for v in s["components"].values())) < 0.6
    assert s["total"] >= 65
    assert s["confidence"]["label"] == "high" and s["confidence"]["cap_reason"] is None
    assert not s["fatal"]


def test_confidence_capped_when_access_is_uncertain_even_if_attractive():
    c = _by_title()["BACK"]
    s = acquisition_score("garden_subdivision", _analysis(c), _assessment(), "verified", {}, "individual")
    assert s["confidence"]["score"] <= 0.45 and s["confidence"]["label"] != "high"
    assert s["confidence"]["cap_reason"] == "access uncertain"


def test_confidence_capped_without_a_credible_planning_route():
    c = _by_title()["WIDE"]
    for assessment in (None, _assessment(cls="speculative_high_risk"), _assessment(cls="significant_obstacle")):
        s = acquisition_score("garden_subdivision", _analysis(c), assessment, "verified", {}, "individual")
        assert s["confidence"]["label"] != "high"
        assert s["confidence"]["cap_reason"] == "no credible planning route established"


def test_unverified_ownership_lowers_ownership_and_confidence():
    c = _by_title()["WIDE"]
    a = acquisition_score("garden_subdivision", _analysis(c), _assessment(), "verified", {}, "individual")
    b = acquisition_score("garden_subdivision", _analysis(c), _assessment(), "title_purchase_needed", {}, None)
    assert b["components"]["ownership"]["score"] < a["components"]["ownership"]["score"]
    assert b["confidence"]["score"] < a["confidence"]["score"]


def test_title_complications_and_personal_representatives_flagged():
    c = _by_title()["WIDE"]
    t = {"charges": [{"text": "x"}], "covenants": [{"text": "y"}], "easements": [], "personal_representatives": True}
    s = acquisition_score("garden_subdivision", _analysis(c), _assessment(), "verified", t, "personal_representatives")
    codes = {f["code"]: f["severity"] for f in s["flags"]}
    assert codes.get("personal_representatives") == "review"
    notes = " ".join(s["components"]["ownership"]["notes"])
    assert "lender consent" in notes and "Covenants" in notes


def test_fatal_flag_from_site_assessment_propagates():
    c = _by_title()["WIDE"]
    a = _assessment()
    a["score"]["fatal_flags"] = [{"code": "flood_zone_3", "title": "Flood Zone 3"}]
    s = acquisition_score("garden_subdivision", _analysis(c), a, "verified", {}, "individual")
    assert s["fatal"]


def test_contact_method_respects_channel_rules():
    assert contact_method(None, None) == "none_until_owner_verified"
    assert contact_method("individual", None) == "post"
    assert contact_method("individual", "published_online") == "post"  # publication is not permission
    assert contact_method("individual", "owner_initiated") == "email_or_post"
    assert contact_method("company", "corporate_subscriber") == "email_or_post"
    assert contact_method("personal_representatives", "owner_initiated") == "manual_review"


BANNED_ASSUMPTIONS = ("retire", "downsiz", "elderly", "age ", "health", "need the money", "financial difficulty", "windfall", "guarantee")


def test_owner_benefits_are_possibilities_not_assumptions():
    for typ in ("garden_subdivision", "edge_paddock", "outbuilding", "derelict", "replacement", "planning_history", "register_site"):
        texts = owner_benefits(typ, {"metrics": {"retained_garden_m2": 1600, "parcel_vs_neighbours": 2.0}})
        assert texts
        joined = " ".join(texts).lower()
        for bad in BANNED_ASSUMPTIONS:
            assert bad not in joined, (typ, bad)
        assert any(w in joined for w in ("could", "possible", "would", "if they prefer", "may"))


def test_next_action_asks_for_official_copy_before_any_contact():
    lead = {"stage": "feasibility_screened", "ownership_status": "title_purchase_needed", "analysis": {"title_id": "12345678"}, "acq_fatal": False}
    txt = next_action(lead, None, None, None)
    assert "official copy" in txt and "12345678" in txt
    assert next_action(lead | {"acq_fatal": True}, None, None, None).startswith("Deprioritise")

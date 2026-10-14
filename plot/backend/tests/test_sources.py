"""Adapter tests against mocked HTTP shaped like the documented APIs."""

from __future__ import annotations

import datetime as dt
import email
import email.policy

import httpx
import pytest
import respx

from plot.models import utcnow
from plot.sources.base import RunContext, SiteContext, SourceError
from plot.sources.bgs import discover_layers, parse_feature_info
from plot.sources.brownfield_csv import parse_register_csv
from plot.sources.ea_flood import zone_from_properties
from plot.sources.email_alerts import parse_alert, unwrap_tracking
from plot.sources.http import PublicHttp, classify
from plot.sources.land_registry import build_query, parse_bindings, summarise
from plot.sources.naptan import parse_naptan_csv
from plot.sources.planning_data import PlanningDataBrownfield, PlanningDataConstraints, PlanningDataTitleBoundary
from tests import mocks
from tests.conftest import fixture_text

HANTS = [(-1.96, 50.70, -0.73, 51.39)]


def ctx(db, config=None):
    return RunContext(db=db, config=config or {}, search_bboxes=HANTS, secrets={}, now=utcnow())


def test_proxy_denial_is_classified_and_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ProxyError("403 Forbidden", request=request)

    with respx.mock as router:
        router.get("https://blocked.example/x").mock(side_effect=handler)
        with PublicHttp(max_retries=3, sleep=lambda s: None) as http:
            with pytest.raises(SourceError) as e:
                http.get_json("https://blocked.example/x")
    assert e.value.error_class == "network_blocked" and calls["n"] == 1


def test_server_errors_are_retried():
    seq = iter([httpx.Response(503), httpx.Response(503), httpx.Response(200, json={"ok": 1})])
    with respx.mock as router:
        router.get("https://flaky.example/x").mock(side_effect=lambda r: next(seq))
        with PublicHttp(max_retries=3, sleep=lambda s: None) as http:
            assert http.get_json("https://flaky.example/x") == {"ok": 1}


def test_classify_http_statuses():
    req = httpx.Request("GET", "https://x.example/")
    assert classify(httpx.HTTPStatusError("x", request=req, response=httpx.Response(404, request=req))).error_class == "not_found"
    assert classify(httpx.HTTPStatusError("x", request=req, response=httpx.Response(429, request=req))).retryable


def test_brownfield_discovery_parses_entities_and_skips_ended(db):
    with respx.mock as router:
        mocks.install(router)
        recs = list(PlanningDataBrownfield().discover(ctx(db)))
    refs = {r.external_ref for r in recs}
    assert refs == {"1700001", "1700002"}  # ended entity excluded
    a = next(r for r in recs if r.external_ref == "1700001")
    assert a.area_m2 == pytest.approx(1200) and "has_permission" in a.signals
    assert a.url == "https://www.planning.data.gov.uk/entity/1700001"
    assert a.licence.startswith("Open Government Licence")


def test_constraints_enrichment_produces_sourced_evidence(db):
    site = SiteContext(1, -1.602, 50.790, None, 1200, None, None)
    with respx.mock as router:
        mocks.install(router)
        items = PlanningDataConstraints().enrich(ctx(db), site)
    kinds = {i.kind for i in items}
    assert {"pd:local-planning-authority", "pd:built-up-area", "pd:nutrient-neutrality-catchment", "pd:coverage"} <= kinds
    spa = next(i for i in items if i.kind == "pd-near:special-protection-area")
    assert spa.data["features"][0]["name"] == "New Forest" and spa.data["features"][0]["distance_m"] > 1000
    assert all(i.source_url for i in items if i.kind.startswith("pd:") and i.kind != "pd:coverage")


def test_title_boundary_returns_inspire_extent(db):
    site = SiteContext(1, -1.602, 50.790, None, None, None, None)
    with respx.mock as router:
        mocks.install(router)
        items = PlanningDataTitleBoundary().enrich(ctx(db), site)
    assert items[0].kind == "inspire-polygon" and items[0].data["inspire_id"] == "99999999"
    assert 1000 < items[0].data["area_m2"] < 2500
    assert "Ordnance Survey 100026316" in items[0].attribution


def test_real_new_forest_register_excerpt_parses():
    recs = parse_register_csv(fixture_text("brownfield_new_forest_excerpt.csv"), source_id="t", source_url="u", publisher="New Forest District Council", now=dt.date(2026, 10, 11))
    refs = {r.external_ref for r in recs}
    assert "BLR_Brans_001" not in refs  # EndDate set: removed from register
    assert {"BLR_Lymi_002", "BLR_Hythe_003", "BLR_Lymi_008"} <= refs
    lymi = next(r for r in recs if r.external_ref == "BLR_Lymi_002")
    assert lymi.stale and lymi.data_date == dt.date(2020, 3, 24) and -1.6 < lymi.lon < -1.5 and 50.7 < lymi.lat < 50.8
    didge = next(r for r in recs if r.external_ref == "BLR_Lymi_008")
    assert didge.area_m2 is None  # free-text hectares not guessed


def test_ea_zone_extraction_prefers_feature_properties():
    assert zone_from_properties({"flood_zone": "FZ3"}, "Flood_Zones_2_3_Rivers_and_Sea") == "3"
    assert zone_from_properties({"type": "Flood Zone 2"}) == "2"
    assert zone_from_properties({}, "flood_zone_3b") == "3b"
    assert zone_from_properties({"name": "x"}) is None


def test_land_registry_query_and_parsing():
    q = build_query(["SO41 6AB", "bad'; DROP"], dt.date(2023, 10, 1))
    assert '"SO41 6AB"' in q and "DROP" not in q
    data = {"results": {"bindings": [
        {"postcode": {"value": "SO41 6AB"}, "amount": {"value": "450000"}, "date": {"value": "2025-05-01"}, "ptype": {"value": "detached"}, "category": {"value": "Standard price paid transaction"}},
        {"postcode": {"value": "SO41 6AB"}, "amount": {"value": "380000"}, "date": {"value": "2025-03-01"}, "ptype": {"value": "semi-detached"}, "category": {"value": "Standard price paid transaction"}},
        {"postcode": {"value": "SO41 6AC"}, "amount": {"value": "520000"}, "date": {"value": "2024-11-01"}, "ptype": {"value": "detached"}, "category": {"value": "Standard price paid transaction"}},
    ]}}
    sales = parse_bindings(data)
    s = summarise(sales)
    assert s["n_houses"] == 3 and s["house_median"] == 450000 and s["n_detached"] == 2


def test_naptan_csv_keeps_rail_access_areas_only():
    csv_text = "ATCOCode,CommonName,Longitude,Latitude,StopType\n9100SWAY,Sway Rail Station,-1.6107,50.7846,RLY\n9100SWAY1,Sway Rail Station Entrance,-1.6106,50.7845,RSE\n"
    st = parse_naptan_csv(csv_text)
    assert st == [{"atco": "9100SWAY", "name": "Sway", "lon": -1.6107, "lat": 50.7846}]


def test_bgs_capabilities_and_feature_info():
    cap = """<WMS_Capabilities><Capability><Layer><Layer><Name>BGS.50k.Bedrock</Name></Layer><Layer><Name>BGS.50k.Superficial.deposits</Name></Layer><Layer><Name>BGS.625k.Bedrock</Name></Layer></Layer></Capability></WMS_Capabilities>"""
    layers = discover_layers(cap)
    assert layers["bedrock"] == "BGS.50k.Bedrock" and layers["superficial"] == "BGS.50k.Superficial.deposits"
    info = parse_feature_info('<FeatureInfoResponse><FIELDS LEX_D="BARTON CLAY FORMATION" RCS_D="CLAY"/></FeatureInfoResponse>')
    assert info == [{"LEX_D": "BARTON CLAY FORMATION", "RCS_D": "CLAY"}]


def test_alert_email_parsing_extracts_canonical_links_and_facts():
    msg = email.message_from_bytes(fixture_text("alert_email.eml").encode("utf-8"), policy=email.policy.default)  # as the pipeline does
    ls = {l.portal: l for l in parse_alert(msg)}
    assert set(ls) == {"rightmove", "zoopla"}
    rm = ls["rightmove"]
    assert rm.ref == "111111111" and rm.asking_price == 95000 and rm.price_qualifier == "guide" and rm.postcode == "SO41 6AB"
    assert round(rm.area_m2) == 1012 and rm.land_like
    zp = ls["zoopla"]
    assert zp.url == "https://www.zoopla.co.uk/for-sale/details/22222222" and zp.asking_price == 150000 and zp.price_qualifier == "offers_over"
    assert unwrap_tracking("https://t.example/c?u=https%3A%2F%2Fwww.rightmove.co.uk%2Fproperties%2F1") == "https://www.rightmove.co.uk/properties/1"

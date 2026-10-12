"""respx routes emulating the documented public APIs (TEST DATA ONLY)."""

from __future__ import annotations

import json

import httpx

from tests.conftest import fixture_text

PD = "https://www.planning.data.gov.uk/entity.json"


def planning_data_handler(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    datasets = params.get_list("dataset")
    rel = params.get("geometry_relation")
    assert rel == "intersects", "adapter must always send geometry_relation=intersects"
    if datasets == ["brownfield-land"]:
        return httpx.Response(200, json=json.loads(fixture_text("planning_data_brownfield.json")))
    if "local-planning-authority" in datasets:  # on-site constraints query
        ents = [
            {"entity": 1, "dataset": "local-planning-authority", "name": "Test LPA", "reference": "E60000000", "entry-date": "2025-01-01"},
            {"entity": 2, "dataset": "built-up-area", "name": "Test Town", "reference": "BUA1", "entry-date": "2024-01-01"},
            {"entity": 3, "dataset": "nutrient-neutrality-catchment", "name": "Test Catchment", "reference": "NN1", "entry-date": "2025-08-01"},
        ]
        return httpx.Response(200, json={"entities": ents, "links": {}, "count": len(ents)})
    if datasets == ["special-protection-area"]:
        ents = [{"entity": 9, "dataset": "special-protection-area", "name": "New Forest", "reference": "UK9011031", "geometry": "POLYGON((-1.58 50.83, -1.55 50.83, -1.55 50.86, -1.58 50.86, -1.58 50.83))"}]
        return httpx.Response(200, json={"entities": ents, "links": {}, "count": 1})
    if datasets == ["title-boundary"]:
        ents = [{"entity": 77, "dataset": "title-boundary", "reference": "99999999", "entry-date": "2026-01-01", "geometry": "POLYGON((-1.6023 50.7898, -1.6017 50.7898, -1.6017 50.7902, -1.6023 50.7902, -1.6023 50.7898))"}]
        return httpx.Response(200, json={"entities": ents, "links": {}, "count": 1})
    return httpx.Response(200, json={"entities": [], "links": {}, "count": 0})


def postcodes_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"status": 200, "result": [{"postcode": "TE1 1ST", "distance": 120.5, "admin_district": "Test District", "admin_county": "Testshire", "parish": "Testparish", "codes": {"admin_district": "E07000000"}, "longitude": -1.6, "latitude": 50.79}]})


def install(router, *, block: set[str] | None = None) -> None:
    """Register handlers; hosts in `block` raise the same ProxyError seen behind an egress policy."""
    block = block or set()

    def blocked(request: httpx.Request):
        raise httpx.ProxyError("403 Forbidden", request=request)

    def maybe(host: str, handler):
        return blocked if host in block else handler

    router.get(PD).mock(side_effect=maybe("planning", planning_data_handler))
    router.get(url__startswith="https://api.postcodes.io/").mock(side_effect=maybe("postcodes", postcodes_handler))
    router.route(host="environment.data.gov.uk").mock(side_effect=blocked)
    router.route(host="map.bgs.ac.uk").mock(side_effect=blocked)
    router.route(host="landregistry.data.gov.uk").mock(side_effect=blocked)
    router.route(host="naptan.api.dft.gov.uk").mock(side_effect=blocked)
    router.route(host="overpass-api.de").mock(side_effect=blocked)
    router.route(host="raw.githubusercontent.com").mock(return_value=httpx.Response(200, content=fixture_text("brownfield_new_forest_excerpt.csv").encode("cp1252", "replace")))

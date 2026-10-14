"""postcodes.io — reverse geocoding (nearest postcode, district, parish, ward).

API: GET https://api.postcodes.io/postcodes?lon=&lat=&radius=&limit= (MIT-licensed service;
data from ONS Postcode Directory and OS Code-Point Open, OGL v3.0).
Also used for approximate place geocoding of alert-email listings (GET /places?q=).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from plot.sources.base import EnrichmentSource, EvidenceItem, RunContext, SiteContext, SourceError, SourceMeta
from plot.sources.http import PublicHttp

BASE = "https://api.postcodes.io"
ATTR = "Contains OS data © Crown copyright and database right; Royal Mail data © Royal Mail copyright and database right; ONS data licensed under the OGL v3.0."


def nearest_postcodes(http: PublicHttp, lon: float, lat: float, radius: int = 500, limit: int = 10) -> list[dict[str, Any]]:
    data = http.get_json(f"{BASE}/postcodes", params={"lon": lon, "lat": lat, "radius": radius, "limit": limit}, cache_ttl=dt.timedelta(days=90))
    if not isinstance(data, dict) or data.get("status") != 200:
        raise SourceError("parse_error", f"postcodes.io returned {data!r}"[:300])
    return data.get("result") or []


def lookup_postcode(http: PublicHttp, postcode: str) -> dict[str, Any] | None:
    try:
        data = http.get_json(f"{BASE}/postcodes/{postcode.replace(' ', '')}", cache_ttl=dt.timedelta(days=90))
    except SourceError as e:
        if e.error_class == "not_found":
            return None
        raise
    return data.get("result") if isinstance(data, dict) else None


def search_place(http: PublicHttp, query: str) -> dict[str, Any] | None:
    data = http.get_json(f"{BASE}/places", params={"q": query, "limit": 1}, cache_ttl=dt.timedelta(days=90))
    res = data.get("result") if isinstance(data, dict) else None
    return res[0] if res else None


class PostcodesIo(EnrichmentSource):
    meta = SourceMeta(
        id="postcodes_io",
        name="postcodes.io reverse geocoding",
        category="enrichment",
        kind="live_api",
        licence="Open Government Licence v3.0 (data); MIT (service)",
        attribution=ATTR,
        terms_url="https://postcodes.io/about",
        docs_url="https://postcodes.io/docs",
    )
    topics = ("location",)
    refresh_days = 180

    def enrich(self, ctx: RunContext, site: SiteContext) -> list[EvidenceItem]:
        with PublicHttp(ctx.db) as http:
            res = nearest_postcodes(http, site.lon, site.lat, radius=1000, limit=10)
        if not res:
            return [
                EvidenceItem(
                    topic="location",
                    kind="postcode-none",
                    title="No postcode within 1 km",
                    summary="Very remote location — check access to services and utilities carefully.",
                    status="confirmed",
                    confidence=0.8,
                    source_url=f"{BASE}/docs",
                    licence=self.meta.licence,
                    attribution=ATTR,
                )
            ]
        top = res[0]
        return [
            EvidenceItem(
                topic="location",
                kind="postcode",
                title=f"Nearest postcode {top.get('postcode')} ({round(top.get('distance') or 0)} m)",
                summary=", ".join(x for x in [top.get("parish"), top.get("admin_district"), top.get("admin_county")] if x),
                data={
                    "postcode": top.get("postcode"),
                    "distance_m": top.get("distance"),
                    "admin_district": top.get("admin_district"),
                    "admin_district_code": (top.get("codes") or {}).get("admin_district"),
                    "admin_county": top.get("admin_county"),
                    "parish": top.get("parish"),
                    "ward": top.get("admin_ward"),
                    "region": top.get("region"),
                    "country": top.get("country"),
                    "nearby_postcodes": [r.get("postcode") for r in res],
                },
                source_url=f"{BASE}/postcodes/{(top.get('postcode') or '').replace(' ', '')}",
                status="confirmed",
                confidence=0.9,
                licence=self.meta.licence,
                attribution=ATTR,
            )
        ]

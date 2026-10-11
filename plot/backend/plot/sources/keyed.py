"""Free-but-keyed public APIs. Inactive (status not_configured) until a key is stored.

- EPC register (MHCLG, epc.opendatacommunities.org): floor areas for sold-price £/m².
  Auth: HTTP Basic with registered email + API key. Licence: OGL v3.0 for EPC data, with
  address data subject to Royal Mail/OS terms — used for internal analysis only.
- Ofcom Connected Nations API: fixed broadband & mobile coverage by postcode.
  Auth: Ocp-Apim-Subscription-Key header. Licence: Ofcom open data terms.
"""

from __future__ import annotations

import base64
import datetime as dt
import statistics

from plot.sources.base import EnrichmentSource, EvidenceItem, RunContext, SiteContext, SourceMeta
from plot.sources.http import PublicHttp
from plot.sources.postcodes import nearest_postcodes

EPC_BASE = "https://epc.opendatacommunities.org/api/v1/domestic/search"
OFCOM_BASE = "https://api-proxy.ofcom.org.uk/broadband/coverage"


class EpcFloorAreas(EnrichmentSource):
    meta = SourceMeta(
        id="mhclg.epc",
        name="EPC register — floor areas near site",
        category="market",
        kind="live_api",
        licence="Open Government Licence v3.0 (EPC data; address terms apply)",
        attribution="Contains EPC data © Crown copyright (MHCLG); address data © Royal Mail / OS.",
        terms_url="https://epc.opendatacommunities.org/docs/copyright",
        docs_url="https://epc.opendatacommunities.org/docs/api/domestic",
        required_secrets=("epc_email", "epc_api_key"),
        notes="Free registration. Enables size-adjusted (£/m²) comparables.",
    )
    topics = ("market",)
    refresh_days = 180

    def enrich(self, ctx: RunContext, site: SiteContext) -> list[EvidenceItem]:
        token = base64.b64encode(f"{ctx.secrets['epc_email']}:{ctx.secrets['epc_api_key']}".encode()).decode()
        headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}
        areas: list[float] = []
        rows_out = []
        with PublicHttp(ctx.db) as http:
            for r in nearest_postcodes(http, site.lon, site.lat, radius=1000, limit=15):
                data = http.get_json(EPC_BASE, params={"postcode": r["postcode"], "size": 100}, headers=headers, cache_ttl=dt.timedelta(days=90))
                for row in (data or {}).get("rows", []):
                    try:
                        a = float(row.get("total-floor-area") or 0)
                    except (TypeError, ValueError):
                        continue
                    if a > 0:
                        areas.append(a)
                        rows_out.append({"address": row.get("address"), "postcode": row.get("postcode"), "floor_area_m2": a, "property_type": row.get("property-type"), "built_form": row.get("built-form"), "lodged": row.get("lodgement-date")})
        return [
            EvidenceItem(
                topic="market",
                kind="epc-floor-areas",
                title=f"{len(areas)} EPC floor-area records within ~1 km" + (f" (median {statistics.median(areas):.0f} m²)" if areas else ""),
                data={"records": rows_out[:200]},
                source_url="https://epc.opendatacommunities.org/",
                status="confirmed" if areas else "unknown",
                confidence=0.7,
                licence=self.meta.licence,
                attribution=self.meta.attribution,
            )
        ]


class OfcomCoverage(EnrichmentSource):
    meta = SourceMeta(
        id="ofcom.coverage",
        name="Ofcom broadband & mobile coverage",
        category="enrichment",
        kind="live_api",
        licence="Ofcom open data terms",
        attribution="Contains Ofcom Connected Nations data.",
        terms_url="https://api.ofcom.org.uk/",
        docs_url="https://api.ofcom.org.uk/",
        required_secrets=("ofcom_api_key",),
        notes="Coverage at nearest postcode; a new dwelling still needs an operator connection quote.",
    )
    topics = ("utilities",)
    refresh_days = 120

    def enrich(self, ctx: RunContext, site: SiteContext) -> list[EvidenceItem]:
        with PublicHttp(ctx.db) as http:
            pcs = nearest_postcodes(http, site.lon, site.lat, radius=1000, limit=1)
            if not pcs:
                return []
            pc = pcs[0]["postcode"].replace(" ", "")
            data = http.get_json(f"{OFCOM_BASE}/{pc}", headers={"Ocp-Apim-Subscription-Key": ctx.secrets["ofcom_api_key"]}, cache_ttl=dt.timedelta(days=60))
        avail = (data or {}).get("Availability") or []
        best = max((float(a.get("MaxPredictedDown") or 0) for a in avail), default=0.0)
        return [
            EvidenceItem(
                topic="utilities",
                kind="broadband",
                title=f"Broadband at {pc}: max predicted download {best:.0f} Mbit/s",
                data={"postcode": pc, "availability": avail[:20]},
                source_url="https://www.ofcom.org.uk/phones-and-broadband/coverage-and-quality-of-service",
                status="estimated",
                confidence=0.6,
                licence=self.meta.licence,
                attribution=self.meta.attribution,
            )
        ]

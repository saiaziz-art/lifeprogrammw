"""Environment Agency flood datasets via OGC API – Features (environment.data.gov.uk/spatialdata).

Endpoint verified from the data.gov.uk catalogue record for "Flood Map for Planning – Flood
Zones" (OGC API Features resource, updated 2026-01-16). Collection ids are discovered at
runtime from /collections rather than hard-coded, so a renamed layer produces a clear
'not_found' error instead of silently returning nothing.

Groundwater: the EA does not publish a national groundwater flood-risk layer; BGS
"Susceptibility to Groundwater Flooding" is licensed. Measured seasonal groundwater depth is
never inferred from these layers — it needs monitoring/site investigation.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from shapely.geometry import Point, shape

from plot import geo
from plot.sources.base import EnrichmentSource, EvidenceItem, RunContext, SiteContext, SourceError, SourceMeta
from plot.sources.http import PublicHttp

EA_ATTRIBUTION = "© Environment Agency copyright and/or database right. All rights reserved."
OGL = "Open Government Licence v3.0"

DEFAULT_DATASETS = [
    {
        "key": "flood_zones",
        "label": "Flood Map for Planning – Flood Zones (rivers & sea)",
        "base": "https://environment.data.gov.uk/spatialdata/flood-map-for-planning-flood-zones/ogc/features/v1",
        "verified": "catalogue record (data.gov.uk), 2026-01",
    },
    {
        "key": "rofsw",
        "label": "Risk of Flooding from Surface Water (NaFRA2)",
        "base": "https://environment.data.gov.uk/spatialdata/nafra2-risk-of-flooding-from-surface-water/ogc/features/v1",
        "verified": "unverified — confirm on first run",
    },
    {
        "key": "recorded_outlines",
        "label": "Recorded Flood Outlines (historic flooding)",
        "base": "https://environment.data.gov.uk/spatialdata/recorded-flood-outlines/ogc/features/v1",
        "verified": "unverified — confirm on first run",
    },
]


class OgcFeatures:
    def __init__(self, http: PublicHttp, base: str):
        self.http = http
        self.base = base.rstrip("/")

    def collections(self) -> list[dict[str, Any]]:
        data = self.http.get_json(f"{self.base}/collections", params={"f": "json"}, cache_ttl=dt.timedelta(days=7))
        cols = data.get("collections") if isinstance(data, dict) else None
        if not isinstance(cols, list):
            raise SourceError("parse_error", f"{self.base}/collections did not return a collections list")
        return cols

    def items(self, collection: str, bbox: tuple[float, float, float, float], limit: int = 100) -> list[dict[str, Any]]:
        data = self.http.get_json(
            f"{self.base}/collections/{collection}/items",
            params={"bbox": ",".join(f"{v:.6f}" for v in bbox), "f": "json", "limit": limit},
            cache_ttl=dt.timedelta(days=7),
        )
        feats = data.get("features") if isinstance(data, dict) else None
        if not isinstance(feats, list):
            raise SourceError("parse_error", f"{collection}: items response is not a FeatureCollection")
        return feats


_ZONE_RE = re.compile(r"(?:flood[\s_]*zones?|fz)[\s_]*([23][ab]?)\b", re.I)


def zone_from_properties(props: dict[str, Any], collection_id: str = "") -> str | None:
    """Feature properties take precedence; the collection id is only a fallback."""
    for text in [*[str(v) for v in props.values() if isinstance(v, (str, int))], collection_id]:
        m = _ZONE_RE.search(str(text))
        if m:
            return m.group(1).lower()
    return None


class EAFloodRisk(EnrichmentSource):
    meta = SourceMeta(
        id="ea.flood",
        name="Environment Agency flood risk (OGC API Features)",
        category="constraints",
        kind="live_api",
        licence=OGL,
        attribution=EA_ATTRIBUTION,
        terms_url="https://environment.data.gov.uk/",
        docs_url="https://environment.data.gov.uk/spatialdata/flood-map-for-planning-flood-zones/ogc/features/v1",
        notes="Rivers & sea flood zones, surface water risk, recorded outlines. Zones indicate area risk, not individual property risk.",
        default_config={"datasets": DEFAULT_DATASETS},
    )
    topics = ("flood",)
    refresh_days = 30

    def enrich(self, ctx: RunContext, site: SiteContext) -> list[EvidenceItem]:
        geom = geo.parse_wkt(site.boundary_wkt) or Point(site.lon, site.lat)
        probe = geo.buffer_m(geom, 25) if geom.geom_type == "Point" else geom
        minx, miny, maxx, maxy = probe.bounds
        out: list[EvidenceItem] = []
        errors: list[str] = []
        with PublicHttp(ctx.db) as http:
            for ds in ctx.config.get("datasets", DEFAULT_DATASETS):
                try:
                    out.extend(self._dataset(http, ds, geom, (minx, miny, maxx, maxy)))
                except SourceError as e:
                    errors.append(f"{ds['label']}: {e}")
                    out.append(
                        EvidenceItem(
                            topic="flood",
                            kind=f"ea:{ds['key']}:unavailable",
                            title=f"{ds['label']}: could not be checked",
                            summary=str(e),
                            status="unknown",
                            confidence=0.0,
                            source_url=ds["base"],
                            licence=OGL,
                            attribution=EA_ATTRIBUTION,
                        )
                    )
        out.append(
            EvidenceItem(
                topic="flood",
                kind="groundwater-note",
                title="Groundwater: flood susceptibility and seasonal water-table depth not assessed",
                summary="No open national groundwater flood-risk layer; chalk catchments in Dorset/Hampshire can flood from groundwater. "
                "Measured seasonal groundwater depth requires monitoring (ideally over winter) during site investigation.",
                status="requires_investigation",
                confidence=0.0,
                licence=None,
                attribution=None,
            )
        )
        if errors and len(errors) == len(ctx.config.get("datasets", DEFAULT_DATASETS)):
            raise SourceError("all_endpoints_failed", "; ".join(errors))
        return out

    def _dataset(self, http: PublicHttp, ds: dict[str, Any], geom, bbox) -> list[EvidenceItem]:
        ogc = OgcFeatures(http, ds["base"])
        cols = ogc.collections()
        hits: list[dict[str, Any]] = []
        checked: list[str] = []
        for col in cols:
            cid = col.get("id")
            if not cid:
                continue
            checked.append(cid)
            for f in ogc.items(cid, bbox):
                try:
                    g = shape(f["geometry"])
                except Exception:  # noqa: BLE001
                    continue
                if not g.intersects(geom):
                    continue
                props = f.get("properties") or {}
                hits.append({"collection": cid, "title": col.get("title"), "zone": zone_from_properties(props, cid), "properties": props})
        if ds["key"] == "flood_zones":
            zones = sorted({h["zone"] for h in hits if h["zone"]}, reverse=True)
            worst = zones[0] if zones else "1"
            return [
                EvidenceItem(
                    topic="flood",
                    kind="ea:flood_zone",
                    title=f"Flood Zone {worst.upper()} (rivers & sea)" if hits else "Flood Zone 1 (outside Zones 2 and 3)",
                    summary=(
                        "Site intersects mapped Flood Zone(s) " + ", ".join(z.upper() for z in zones)
                        if hits
                        else "Not within mapped Flood Zone 2 or 3. Zone 1 = <0.1% annual chance from rivers/sea; surface water assessed separately."
                    ),
                    data={"zone": worst, "hits": hits[:10], "collections_checked": checked},
                    source_url=ds["base"],
                    status="confirmed",
                    confidence=0.85,
                    licence=OGL,
                    attribution=EA_ATTRIBUTION,
                )
            ]
        label = ds["label"]
        return [
            EvidenceItem(
                topic="flood",
                kind=f"ea:{ds['key']}",
                title=(f"{label}: site intersects mapped extent" if hits else f"{label}: no mapped extent at site"),
                summary=", ".join(sorted({str(h['title'] or h['collection']) for h in hits}))[:500] or None,
                data={"hits": hits[:10], "collections_checked": checked},
                source_url=ds["base"],
                status="confirmed",
                confidence=0.75,
                licence=OGL,
                attribution=EA_ATTRIBUTION,
            )
        ]

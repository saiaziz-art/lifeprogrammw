"""Generic ArcGIS REST FeatureServer/MapServer layer query (point-in-polygon or within-distance).

Many authoritative layers (Coal Authority, Natural England, water/electricity network open
data, council constraint maps) are published this way. Layers are user-configured in Admin
with their licence; nothing is queried until configured.

Query: {layer_url}/query?geometry=lon,lat&geometryType=esriGeometryPoint&inSR=4326
       &spatialRel=esriSpatialRelIntersects&distance=&units=esriSRUnit_Meter
       &outFields=*&returnGeometry=false&f=json
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from plot.sources.base import EnrichmentSource, EvidenceItem, RunContext, SiteContext, SourceError, SourceMeta, SourceNotConfigured
from plot.sources.http import PublicHttp

EXAMPLE_LAYERS = [
    {
        "key": "coal_high_risk",
        "label": "Coal Authority — Development High Risk Area",
        "url": "<FeatureServer layer URL from the Coal Authority open data portal>",
        "topic": "ground",
        "distance_m": 0,
        "licence": "OGL v3.0",
        "attribution": "© The Coal Authority",
    }
]


class ArcGisLayers(EnrichmentSource):
    meta = SourceMeta(
        id="arcgis.layers",
        name="Configured ArcGIS REST layers",
        category="enrichment",
        kind="live_api",
        licence="Per layer (configured)",
        attribution="Per layer (configured)",
        notes="Add authoritative layers (Coal Authority, Natural England, SSEN/water network open data, council maps).",
        default_config={"layers": []},
    )
    topics = ("ground", "ecology", "utilities", "planning")
    refresh_days = 60

    def check_ready(self, ctx: RunContext) -> None:
        super().check_ready(ctx)
        layers = [l for l in ctx.config.get("layers", []) if str(l.get("url", "")).startswith("http")]
        if not layers:
            raise SourceNotConfigured("no ArcGIS layers configured (see docs/data-sources.md for candidates)")

    def enrich(self, ctx: RunContext, site: SiteContext) -> list[EvidenceItem]:
        out: list[EvidenceItem] = []
        with PublicHttp(ctx.db) as http:
            for layer in ctx.config.get("layers", []):
                url = str(layer.get("url", ""))
                if not url.startswith("http"):
                    continue
                params: dict[str, Any] = {
                    "geometry": f"{site.lon},{site.lat}",
                    "geometryType": "esriGeometryPoint",
                    "inSR": 4326,
                    "spatialRel": "esriSpatialRelIntersects",
                    "outFields": "*",
                    "returnGeometry": "false",
                    "f": "json",
                }
                if layer.get("distance_m"):
                    params.update({"distance": layer["distance_m"], "units": "esriSRUnit_Meter"})
                data = http.get_json(url.rstrip("/") + "/query", params=params, cache_ttl=dt.timedelta(days=30))
                if isinstance(data, dict) and data.get("error"):
                    raise SourceError("bad_request", f"{layer.get('label')}: {data['error']}")
                feats = data.get("features", []) if isinstance(data, dict) else []
                out.append(
                    EvidenceItem(
                        topic=layer.get("topic", "planning"),
                        kind=f"arcgis:{layer.get('key')}",
                        title=f"{layer.get('label')}: {'present' if feats else 'not present'}"
                        + (f" within {layer['distance_m']} m" if layer.get("distance_m") else " at site"),
                        data={"features": [f.get("attributes", {}) for f in feats[:10]], "count": len(feats)},
                        source_url=url,
                        status="confirmed",
                        confidence=0.8,
                        licence=layer.get("licence"),
                        attribution=layer.get("attribution"),
                    )
                )
        return out

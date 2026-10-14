"""British Geological Survey mapped geology (WMS GetFeatureInfo).

BGS states its WMS services (incl. 1:50 000 geology) are provided under the OGL with
acknowledgement. GeoSure shrink–swell/landslide/dissolution datasets are separately
licensed (BGS Digital Data Licence) and are NOT used. PLOT infers screening-level hazards
from mapped lithology only (see engine/site_risk.py) and always requires investigation.

Default endpoint (configurable): BGS_Detailed_Geology WMS. Layer names are discovered from
GetCapabilities so the adapter fails loudly if the service changes. Marked unverified until
the first successful run from a deployment with network access.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from plot import geo
from plot.sources.base import EnrichmentSource, EvidenceItem, RunContext, SiteContext, SourceError, SourceMeta
from plot.sources.http import PublicHttp

DEFAULT_WMS = "https://map.bgs.ac.uk/arcgis/services/BGS_Detailed_Geology/MapServer/WMSServer"
ATTR = "Contains British Geological Survey materials © UKRI. Licensed under the Open Government Licence v3.0."
LAYER_KEYS = {
    "bedrock": ("bedrock",),
    "superficial": ("superficial",),
    "artificial": ("artificial",),
    "mass_movement": ("mass", "movement", "landslide"),
}


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def discover_layers(cap_xml: str) -> dict[str, str]:
    root = ET.fromstring(cap_xml)
    names = [el.text or "" for el in root.iter() if _strip_ns(el.tag) == "Name"]
    out: dict[str, str] = {}
    for key, words in LAYER_KEYS.items():
        # prefer 50k layers, else any match
        cands = [n for n in names if any(w in n.lower() for w in words)]
        cands.sort(key=lambda n: ("50k" not in n.lower(), len(n)))
        if cands:
            out[key] = cands[0]
    return out


def parse_feature_info(xml_text: str) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceError("parse_error", "BGS GetFeatureInfo did not return XML") from exc
    rows = []
    for el in root.iter():
        if el.attrib and _strip_ns(el.tag).upper() in ("FIELDS", "FEATURE", "FEATUREINFO"):
            rows.append(dict(el.attrib))
    return rows


def describe(row: dict[str, str]) -> str:
    for k in ("LEX_RCS_D", "LEX_D", "NAME", "RCS_D", "DESCRIPTION"):
        if row.get(k):
            parts = [row.get("LEX_D") or row.get(k), row.get("RCS_D")]
            return " — ".join(p for p in dict.fromkeys(parts) if p)
    return "; ".join(f"{k}={v}" for k, v in list(row.items())[:4])


class BgsGeology(EnrichmentSource):
    meta = SourceMeta(
        id="bgs.geology",
        name="BGS mapped geology (WMS)",
        category="enrichment",
        kind="live_api",
        licence="Open Government Licence v3.0 (WMS); GeoSure excluded (licensed)",
        attribution=ATTR,
        terms_url="https://www.bgs.ac.uk/technologies/web-map-services-wms/",
        docs_url="https://www.bgs.ac.uk/technologies/web-map-services-wms/",
        notes="Endpoint/layers unverified until first successful run. Screening only — not a ground investigation.",
        default_config={"wms_url": DEFAULT_WMS},
    )
    topics = ("ground",)
    refresh_days = 365

    def enrich(self, ctx: RunContext, site: SiteContext) -> list[EvidenceItem]:
        url = ctx.config.get("wms_url", DEFAULT_WMS)
        x, y = geo.lonlat_to_bng(site.lon, site.lat)
        with PublicHttp(ctx.db) as http:
            cap = http.get_text(url, params={"service": "WMS", "request": "GetCapabilities", "version": "1.3.0"}, headers={"Accept": "text/xml"})
            layers = discover_layers(cap)
            if not layers:
                raise SourceError("not_found", f"no bedrock/superficial layers advertised by {url}")
            out: list[EvidenceItem] = []
            for key, layer in layers.items():
                params = {
                    "service": "WMS",
                    "version": "1.3.0",
                    "request": "GetFeatureInfo",
                    "layers": layer,
                    "query_layers": layer,
                    "styles": "",
                    "crs": "EPSG:27700",
                    "bbox": f"{x - 50:.1f},{y - 50:.1f},{x + 50:.1f},{y + 50:.1f}",
                    "width": 101,
                    "height": 101,
                    "i": 50,
                    "j": 50,
                    "info_format": "text/xml",
                    "format": "image/png",
                }
                rows = parse_feature_info(http.get_text(url, params=params, headers={"Accept": "text/xml"}))
                out.append(
                    EvidenceItem(
                        topic="ground",
                        kind=f"bgs:{key}",
                        title=f"{key.replace('_', ' ').capitalize()}: " + (describe(rows[0]) if rows else "none mapped at this point"),
                        summary=None,
                        data={"layer": layer, "units": rows[:5]},
                        source_url=url,
                        data_date=None,
                        status="estimated",
                        confidence=0.6 if rows else 0.5,
                        licence=self.meta.licence,
                        attribution=ATTR,
                    )
                )
        return out


def lithology_text(evidence_data: list[dict[str, Any]]) -> str:
    return " ".join(describe(r) for r in evidence_data).upper()


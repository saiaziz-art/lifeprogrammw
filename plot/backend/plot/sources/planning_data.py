"""planning.data.gov.uk (MHCLG Planning Data platform).

API verified against the platform source (github.com/digital-land/digital-land.info,
application/search/filters.py): GET /entity.json with repeatable `dataset`, WKT `geometry`,
`geometry_relation` (default 'within' — we always send 'intersects'), `limit` (1..500),
`offset`, `exclude_field`. Response: {"entities": [...], "links": {...}, "count": N}.
Field names verified against github.com/digital-land/specification dataset-field.csv.

Licence: OGL v3.0; dataset-level attribution (Historic England, Natural England, OS,
HM Land Registry INSPIRE) is carried on every evidence item.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from typing import Any

from shapely.geometry import Point

from plot import geo
from plot.sources.base import (
    CandidateRecord,
    DiscoverySource,
    EnrichmentSource,
    EvidenceItem,
    RunContext,
    SiteContext,
    SourceError,
    SourceMeta,
)
from plot.sources.http import PublicHttp

BASE = "https://www.planning.data.gov.uk"
LICENCE = "Open Government Licence v3.0"
PD_ATTRIBUTION = "Contains public sector information from planning.data.gov.uk licensed under the Open Government Licence v3.0."

DATASET_ATTRIBUTION = {
    "conservation-area": "© Historic England and local planning authorities",
    "listed-building": "© Historic England",
    "listed-building-outline": "© local planning authorities",
    "scheduled-monument": "© Historic England",
    "park-and-garden": "© Historic England",
    "battlefield": "© Historic England",
    "world-heritage-site": "© Historic England",
    "world-heritage-site-buffer-zone": "© Historic England",
    "area-of-outstanding-natural-beauty": "© Natural England",
    "national-park": "Contains OS data © Crown copyright; ONS",
    "site-of-special-scientific-interest": "© Natural England",
    "special-area-of-conservation": "© Natural England",
    "special-protection-area": "© Natural England",
    "ramsar": "© Natural England",
    "ancient-woodland": "© Natural England",
    "heritage-coast": "© Natural England",
    "nutrient-neutrality-catchment": "© Natural England",
    "agricultural-land-classification": "© Natural England",
    "green-belt": "Contains OS data © Crown copyright and database right",
    "flood-risk-zone": "© Environment Agency copyright and/or database right",
    "title-boundary": "© Crown copyright HM Land Registry; contains OS data © Crown copyright and database rights Ordnance Survey 100026316",
    "built-up-area": "Source: Office for National Statistics licensed under the OGL v3.0; contains OS data © Crown copyright",
}

# Datasets checked for on-site intersection. coverage: national | partial
ONSITE_DATASETS: dict[str, str] = {
    "local-planning-authority": "national",
    "parish": "national",
    "conservation-area": "partial",
    "listed-building": "national",
    "listed-building-outline": "partial",
    "scheduled-monument": "national",
    "park-and-garden": "national",
    "battlefield": "national",
    "world-heritage-site": "national",
    "world-heritage-site-buffer-zone": "national",
    "area-of-outstanding-natural-beauty": "national",
    "national-park": "national",
    "heritage-coast": "national",
    "green-belt": "national",
    "article-4-direction-area": "partial",
    "tree-preservation-zone": "partial",
    "tree": "partial",
    "flood-risk-zone": "national",
    "ancient-woodland": "national",
    "site-of-special-scientific-interest": "national",
    "special-area-of-conservation": "national",
    "special-protection-area": "national",
    "ramsar": "national",
    "local-nature-reserve": "partial",
    "national-nature-reserve": "national",
    "nutrient-neutrality-catchment": "national",
    "agricultural-land-classification": "national",
    "common-land-and-village-green": "partial",
    "contaminated-land": "partial",
    "coastal-change-management-area": "partial",
    "local-green-space": "partial",
    "brownfield-land": "partial",
    "self-and-custom-buildarea": "partial",
    "built-up-area": "national",
}

# Datasets checked within a radius (metres) to measure distance-based policy effects.
PROXIMITY_DATASETS: dict[str, int] = {
    "special-protection-area": 15_000,  # e.g. New Forest 13.8 km recreation zone
    "special-area-of-conservation": 6_000,
    "ramsar": 6_000,
    "site-of-special-scientific-interest": 3_000,
    "ancient-woodland": 500,
    "listed-building": 250,
    "scheduled-monument": 500,
    "conservation-area": 300,
    "built-up-area": 10_000,
    "suitable-alternative-green-space": 3_000,
}

# geometry-heavy fields we do not need to keep in evidence
_DROP_FIELDS = {"geometry", "point", "typology", "prefix"}


class PlanningDataClient:
    def __init__(self, http: PublicHttp, base: str = BASE):
        self.http = http
        self.base = base.rstrip("/")

    def search(
        self,
        datasets: Iterable[str],
        *,
        geometry_wkt: str | None = None,
        relation: str = "intersects",
        limit: int = 500,
        max_pages: int = 40,
        cache_ttl: dt.timedelta | None = None,
        extra: list[tuple[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        params: list[tuple[str, Any]] = [("dataset", d) for d in datasets]
        if geometry_wkt:
            params += [("geometry", geometry_wkt), ("geometry_relation", relation)]
        params += [("limit", limit)]
        params += extra or []
        out: list[dict[str, Any]] = []
        offset = 0
        for _ in range(max_pages):
            page = self.http.get_json(f"{self.base}/entity.json", params=params + [("offset", offset)], cache_ttl=cache_ttl)
            if not isinstance(page, dict) or "entities" not in page:
                raise SourceError("parse_error", "planning.data.gov.uk response missing 'entities'")
            ents = page["entities"]
            out.extend(ents)
            count = page.get("count")
            offset += len(ents)
            if len(ents) < limit or (isinstance(count, int) and offset >= count):
                break
        else:
            raise SourceError("too_many_results", f"more than {max_pages * limit} results; narrow the search area")
        return out

    def entity_url(self, entity: int | str) -> str:
        return f"{self.base}/entity/{entity}"


def _date(v: Any) -> dt.date | None:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _float(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def entity_point(ent: dict[str, Any]):
    g = geo.parse_wkt(ent.get("point"))
    if g is None:
        geom = geo.parse_wkt(ent.get("geometry"))
        if geom is not None:
            g = geom.representative_point()
    if g is None:
        lon, lat = _float(ent.get("longitude")), _float(ent.get("latitude"))
        if lon is not None and lat is not None:
            g = Point(lon, lat)
    return g


# --------------------------------------------------------------------------- discovery


class PlanningDataBrownfield(DiscoverySource):
    """Brownfield land registers (all English LPAs) within the enabled search areas.

    These are lawful public records of previously developed sites the LPA considers
    suitable for housing. Inclusion does NOT mean the site is for sale.
    """

    meta = SourceMeta(
        id="planning_data.brownfield",
        name="Brownfield land registers (planning.data.gov.uk)",
        category="off_market",
        kind="live_api",
        licence=LICENCE,
        attribution=PD_ATTRIBUTION,
        terms_url="https://www.planning.data.gov.uk/about/",
        docs_url="https://www.planning.data.gov.uk/docs",
        notes="Statutory registers; sites are typically ≥0.25 ha or ≥5 dwellings. Not an indication of availability.",
    )

    def discover(self, ctx: RunContext) -> Iterable[CandidateRecord]:
        with PublicHttp(ctx.db) as http:
            client = PlanningDataClient(http, ctx.config.get("base_url", BASE))
            seen: set[str] = set()
            for bbox in ctx.search_bboxes:
                ents = client.search(
                    ["brownfield-land"],
                    geometry_wkt=geo.bbox_polygon(bbox).wkt,
                    extra=[("exclude_field", "geometry")],
                )
                for ent in ents:
                    rec = brownfield_entity_to_candidate(ent, client)
                    if rec is not None and rec.external_ref not in seen:
                        seen.add(rec.external_ref)
                        yield rec


def brownfield_entity_to_candidate(ent: dict[str, Any], client: PlanningDataClient | None = None) -> CandidateRecord | None:
    if ent.get("end-date"):
        return None  # removed from the register
    pt = entity_point(ent)
    if pt is None:
        return None
    hectares = _float(ent.get("hectares"))
    address = ent.get("site-address") or ent.get("name")
    status = ent.get("planning-permission-status") or ""
    signals = ["brownfield_register"]
    if status == "permissioned":
        signals.append("has_permission")
    elif status in ("not-permissioned", "not permissioned"):
        signals.append("allocated_or_suitable_no_permission")
    if ent.get("planning-permission-type"):
        signals.append(f"permission_type:{ent['planning-permission-type']}")
    entity = ent.get("entity")
    url = (client.entity_url(entity) if client else f"{BASE}/entity/{entity}") if entity else None
    return CandidateRecord(
        source_id=PlanningDataBrownfield.meta.id,
        external_ref=str(entity or ent.get("reference")),
        record_type="register_site",
        lon=pt.x,
        lat=pt.y,
        title=f"Brownfield register: {address}",
        category="brownfield",
        address=address,
        area_m2=hectares * 10_000 if hectares else None,
        description=ent.get("notes"),
        url=url,
        data_date=_date(ent.get("entry-date")),
        publisher=f"organisation-entity {ent.get('organisation-entity')}",
        licence=LICENCE,
        attribution=PD_ATTRIBUTION,
        confidence="high",
        signals=signals,
        raw={k: v for k, v in ent.items() if k not in _DROP_FIELDS},
    )


# --------------------------------------------------------------------------- enrichment


class PlanningDataConstraints(EnrichmentSource):
    meta = SourceMeta(
        id="planning_data.constraints",
        name="Planning constraints (planning.data.gov.uk)",
        category="constraints",
        kind="live_api",
        licence=LICENCE,
        attribution=PD_ATTRIBUTION,
        terms_url="https://www.planning.data.gov.uk/about/",
        docs_url="https://www.planning.data.gov.uk/docs",
        notes="Designations, heritage, ecology, Green Belt, flood zones, nutrient catchments, LPA. "
        "Some datasets have partial LPA coverage: absence there is 'unknown', not 'clear'.",
    )
    topics = ("planning", "heritage", "ecology", "flood", "location")
    refresh_days = 30

    def enrich(self, ctx: RunContext, site: SiteContext) -> list[EvidenceItem]:
        geom = geo.parse_wkt(site.boundary_wkt) or Point(site.lon, site.lat)
        ttl = dt.timedelta(days=7)
        out: list[EvidenceItem] = []
        with PublicHttp(ctx.db) as http:
            client = PlanningDataClient(http, ctx.config.get("base_url", BASE))
            onsite = client.search(list(ONSITE_DATASETS), geometry_wkt=geom.wkt, cache_ttl=ttl, extra=[("exclude_field", "geometry")])
            hit_datasets: set[str] = set()
            for ent in onsite:
                ds = ent.get("dataset", "")
                if ent.get("end-date"):
                    continue
                hit_datasets.add(ds)
                out.append(self._onsite_item(client, ent, ds))
            out.append(
                EvidenceItem(
                    topic="planning",
                    kind="pd:coverage",
                    title="Constraint datasets checked on planning.data.gov.uk",
                    summary=f"{len(ONSITE_DATASETS)} datasets checked on site; {len(hit_datasets)} intersect.",
                    data={
                        "checked": ONSITE_DATASETS,
                        "intersecting": sorted(hit_datasets),
                        "geometry_used": "boundary" if site.boundary_wkt else "point",
                    },
                    source_url=f"{BASE}/docs",
                    status="confirmed",
                    confidence=0.9,
                    licence=LICENCE,
                    attribution=PD_ATTRIBUTION,
                )
            )
            # proximity checks: one query per radius band keeps payloads modest
            for ds, radius in PROXIMITY_DATASETS.items():
                circle = geo.buffer_m(geom, radius, resolution=6).wkt
                ents = client.search([ds], geometry_wkt=circle, cache_ttl=dt.timedelta(days=30))
                near = []
                for ent in ents:
                    if ent.get("end-date"):
                        continue
                    g = geo.parse_wkt(ent.get("geometry")) or entity_point(ent)
                    if g is None:
                        continue
                    near.append(
                        {
                            "entity": ent.get("entity"),
                            "name": ent.get("name"),
                            "reference": ent.get("reference"),
                            "distance_m": round(geo.distance_m(geom, g)),
                            "area_ha": round(geo.area_m2(g) / 10_000, 1) if g.geom_type in ("Polygon", "MultiPolygon") else None,
                        }
                    )
                near.sort(key=lambda r: r["distance_m"])
                out.append(
                    EvidenceItem(
                        topic=_topic_for(ds),
                        kind=f"pd-near:{ds}",
                        title=f"{ds.replace('-', ' ').capitalize()} within {radius / 1000:g} km",
                        summary=(f"Nearest: {near[0]['name']} at {near[0]['distance_m']} m" if near else "None found within radius"),
                        data={"radius_m": radius, "features": near[:25], "count": len(near), "coverage": ONSITE_DATASETS.get(ds, "national")},
                        source_url=f"{BASE}/dataset/{ds}",
                        status="confirmed" if ONSITE_DATASETS.get(ds, "national") == "national" or near else "unknown",
                        confidence=0.85,
                        licence=LICENCE,
                        attribution=DATASET_ATTRIBUTION.get(ds, PD_ATTRIBUTION),
                    )
                )
        return out

    def _onsite_item(self, client: PlanningDataClient, ent: dict[str, Any], ds: str) -> EvidenceItem:
        data = {k: v for k, v in ent.items() if k not in _DROP_FIELDS}
        partial = ONSITE_DATASETS.get(ds) == "partial"
        return EvidenceItem(
            topic=_topic_for(ds),
            kind=f"pd:{ds}",
            title=f"{ds.replace('-', ' ').capitalize()}: {ent.get('name') or ent.get('reference') or ent.get('entity')}",
            summary=_summary_for(ds, ent),
            data=data,
            source_url=client.entity_url(ent["entity"]) if ent.get("entity") else None,
            source_ref=str(ent.get("entity") or ent.get("reference")),
            data_date=_date(ent.get("entry-date")) or _date(ent.get("start-date")),
            status="confirmed" if not partial else "estimated",
            confidence=0.9 if not partial else 0.7,
            licence=LICENCE,
            attribution=DATASET_ATTRIBUTION.get(ds, PD_ATTRIBUTION),
        )


class PlanningDataTitleBoundary(EnrichmentSource):
    """HM Land Registry INSPIRE Index Polygons served via planning.data.gov.uk (title-boundary).

    INSPIRE polygons show the indicative extent of a registered freehold title. They are not
    legal boundaries and carry no owner names: ownership needs an official title register.
    """

    meta = SourceMeta(
        id="planning_data.title_boundary",
        name="INSPIRE title extents (HM Land Registry via planning.data.gov.uk)",
        category="enrichment",
        kind="live_api",
        licence=LICENCE + " + Ordnance Survey attribution",
        attribution=DATASET_ATTRIBUTION["title-boundary"],
        terms_url="https://use-land-property-data.service.gov.uk/datasets/inspire",
        docs_url="https://www.planning.data.gov.uk/dataset/title-boundary",
        notes="Indicative freehold extents only; unregistered land has no polygon. Owner identity requires an official copy of the register (£).",
    )
    topics = ("ownership",)
    refresh_days = 90

    def enrich(self, ctx: RunContext, site: SiteContext) -> list[EvidenceItem]:
        pt = Point(site.lon, site.lat)
        with PublicHttp(ctx.db) as http:
            client = PlanningDataClient(http, ctx.config.get("base_url", BASE))
            ents = client.search(["title-boundary"], geometry_wkt=pt.wkt, cache_ttl=dt.timedelta(days=30))
        out = []
        for ent in ents:
            g = geo.parse_wkt(ent.get("geometry"))
            out.append(
                EvidenceItem(
                    topic="ownership",
                    kind="inspire-polygon",
                    title=f"Registered title extent (INSPIRE ID {ent.get('reference')})",
                    summary=(f"Indicative extent {geo.area_m2(g):,.0f} m²" if g is not None else "Polygon geometry not returned"),
                    data={
                        "inspire_id": ent.get("reference"),
                        "entity": ent.get("entity"),
                        "area_m2": round(geo.area_m2(g)) if g is not None else None,
                        "boundary_wkt": geo.simplify_for_display(g, 0.5).wkt if g is not None else None,
                    },
                    source_url=client.entity_url(ent["entity"]) if ent.get("entity") else None,
                    source_ref=str(ent.get("reference")),
                    data_date=_date(ent.get("entry-date")),
                    status="confirmed",
                    confidence=0.8,
                    licence=self.meta.licence,
                    attribution=self.meta.attribution,
                )
            )
        if not out:
            out.append(
                EvidenceItem(
                    topic="ownership",
                    kind="inspire-none",
                    title="No registered freehold title polygon at this point",
                    summary="Land may be unregistered, or the point falls on a road/boundary. Ownership research needed.",
                    status="requires_investigation",
                    confidence=0.6,
                    source_url=f"{BASE}/dataset/title-boundary",
                    licence=self.meta.licence,
                    attribution=self.meta.attribution,
                )
            )
        return out


class PlanningDataApplications(EnrichmentSource):
    """Planning applications near a site (alpha dataset with partial LPA coverage)."""

    meta = SourceMeta(
        id="planning_data.applications",
        name="Planning applications (planning.data.gov.uk, partial coverage)",
        category="enrichment",
        kind="live_api",
        licence=LICENCE,
        attribution=PD_ATTRIBUTION,
        docs_url="https://www.planning.data.gov.uk/dataset/planning-application",
        notes="Alpha dataset; many LPAs (incl. Dorset/Hampshire districts) may not yet publish. "
        "Absence of records is NOT evidence of no planning history.",
    )
    topics = ("planning",)
    refresh_days = 14

    def enrich(self, ctx: RunContext, site: SiteContext) -> list[EvidenceItem]:
        circle = geo.circle_wkt(site.lon, site.lat, ctx.config.get("radius_m", 250))
        with PublicHttp(ctx.db) as http:
            client = PlanningDataClient(http, ctx.config.get("base_url", BASE))
            ents = client.search(["planning-application"], geometry_wkt=circle, cache_ttl=dt.timedelta(days=3), extra=[("exclude_field", "geometry")])
        out = []
        for ent in ents:
            out.append(
                EvidenceItem(
                    topic="planning",
                    kind="planning-application",
                    title=f"Planning application {ent.get('reference')}: {(ent.get('description') or '')[:120]}",
                    summary=f"Decision: {ent.get('planning-decision') or 'n/a'}; status: {ent.get('planning-application-status') or 'n/a'}",
                    data={k: v for k, v in ent.items() if k not in _DROP_FIELDS},
                    source_url=ent.get("documentation-url") or client.entity_url(ent.get("entity")),
                    source_ref=str(ent.get("reference")),
                    data_date=_date(ent.get("decision-date")) or _date(ent.get("start-date")),
                    status="confirmed",
                    confidence=0.8,
                    licence=LICENCE,
                    attribution=PD_ATTRIBUTION,
                )
            )
        out.append(
            EvidenceItem(
                topic="planning",
                kind="planning-history-coverage",
                title=f"{len(ents)} planning application record(s) within 250 m on planning.data.gov.uk",
                summary="Coverage of this dataset is partial; check the LPA's public access register for the full history.",
                status="requires_investigation" if not ents else "estimated",
                confidence=0.4,
                source_url=f"{BASE}/dataset/planning-application",
                licence=LICENCE,
                attribution=PD_ATTRIBUTION,
            )
        )
        return out


def _topic_for(ds: str) -> str:
    if ds in ("conservation-area", "listed-building", "listed-building-outline", "scheduled-monument", "park-and-garden", "battlefield", "world-heritage-site", "world-heritage-site-buffer-zone"):
        return "heritage"
    if ds in ("site-of-special-scientific-interest", "special-area-of-conservation", "special-protection-area", "ramsar", "ancient-woodland", "local-nature-reserve", "national-nature-reserve", "nutrient-neutrality-catchment", "suitable-alternative-green-space", "tree", "tree-preservation-zone"):
        return "ecology"
    if ds in ("flood-risk-zone",):
        return "flood"
    if ds in ("built-up-area", "parish", "local-planning-authority", "agricultural-land-classification"):
        return "location"
    if ds in ("contaminated-land",):
        return "ground"
    return "planning"


def _summary_for(ds: str, ent: dict[str, Any]) -> str | None:
    if ds == "flood-risk-zone":
        return f"Flood risk level {ent.get('flood-risk-level')} ({ent.get('flood-risk-type') or 'type n/a'})"
    if ds == "listed-building":
        return f"Grade {ent.get('listed-building-grade') or '?'} listed building"
    if ds == "agricultural-land-classification":
        return f"ALC grade {ent.get('agricultural-land-classification-grade') or '?'} (provisional)"
    if ds == "article-4-direction-area":
        return f"Article 4 direction; PD rights affected: {ent.get('permitted-development-rights') or 'see direction'}"
    if ds == "brownfield-land":
        return f"On brownfield register; permission status: {ent.get('planning-permission-status') or 'n/a'}"
    return ent.get("description") or ent.get("notes")

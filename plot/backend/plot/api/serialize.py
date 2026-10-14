"""Response shaping shared by API routes."""

from __future__ import annotations

from typing import Any

from geoalchemy2.shape import to_shape
from shapely.geometry import mapping
from sqlalchemy import select
from sqlalchemy.orm import Session

from plot import geo
from plot.models import Assessment, Lead, Listing, ListingEvent, PipelineItem, Site


def glyph(site: Site) -> dict[str, Any]:
    """Tiny normalised outline for list thumbnails (no external map requests)."""
    if site.boundary is not None:
        g = geo.to_bng(to_shape(site.boundary))
        if g.geom_type == "MultiPolygon":
            g = max(g.geoms, key=lambda x: x.area)
        if g.geom_type == "Polygon":
            minx, miny, maxx, maxy = g.bounds
            span = max(maxx - minx, maxy - miny) or 1
            pts = [[round((x - minx) / span, 3), round(1 - (y - miny) / span, 3)] for x, y in list(g.exterior.coords)[:200]]
            return {"type": "polygon", "points": pts, "span_m": round(span)}
    return {"type": "point", "area_m2": site.area_m2}


def latest_assessments(db: Session, site_ids: list[int]) -> dict[int, Assessment]:
    if not site_ids:
        return {}
    rows = db.scalars(select(Assessment).where(Assessment.site_id.in_(site_ids), Assessment.is_current.is_(True))).all()
    return {a.site_id: a for a in rows}


def site_row(site: Site, a: Assessment | None, listings: list[Listing], lead: Lead | None, pipe: PipelineItem | None, reductions: int) -> dict[str, Any]:
    p = to_shape(site.point)
    live = [l for l in listings if l.status not in ("withdrawn", "sold")]
    main = min(live, key=lambda l: l.asking_price or 10**12) if live else (listings[0] if listings else None)
    res = (a.residual if a else {}) or {}
    sc = (a.score if a else {}) or {}
    pl = (a.planning if a else {}) or {}
    conf = ((a.explanation or {}).get("confidence") if a else None) or {"score": 0, "label": "low"}
    return {
        "id": site.id,
        "headline": site.headline,
        "address": site.address,
        "postcode": site.postcode,
        "lpa": site.lpa_name,
        "category": site.category,
        "origin": site.origin,
        "lon": p.x,
        "lat": p.y,
        "area_m2": site.area_m2,
        "area_source": site.area_source,
        "boundary_source": site.boundary_source,
        "asking_price": main.asking_price if main else None,
        "price_qualifier": main.price_qualifier if main else None,
        "listing_url": main.url if main else None,
        "listing_status": main.status if main else None,
        "listing_source": main.source_id if main else None,
        "price_reductions": reductions,
        "first_seen_at": site.first_seen_at.isoformat(),
        "last_changed_at": site.last_changed_at.isoformat(),
        "last_seen_at": site.last_seen_at.isoformat(),
        "stale": site.stale,
        "excluded": site.excluded,
        "exclusion_reason": site.exclusion_reason,
        "score": {
            "total": a.total_score if a else None,
            "rank": a.rank if a else None,
            "fatal": a.fatal if a else False,
            "fatal_flags": sc.get("fatal_flags", []),
            "components": {k: {"score": v.get("score"), "max": v.get("max")} for k, v in (sc.get("components") or {}).items()},
        },
        "planning_class": pl.get("classification"),
        "planning_label": pl.get("classification_label"),
        "residual_land_value": res.get("residual_land_value"),
        "max_offer": res.get("max_recommended_offer"),
        "total_cost_base": (res.get("total_project_cost") or {}).get("base"),
        "non_land_cost_base": (res.get("non_land_cost") or {}).get("base"),
        "within_budget": res.get("within_budget"),
        "headroom": res.get("headroom_vs_asking"),
        "confidence": conf,
        "lead_stage": lead.stage if lead else None,
        "pipeline_stage": pipe.stage if pipe else None,
        "next_action": (a.explanation or {}).get("next_action") if a else None,
        "assessed_at": a.created_at.isoformat() if a else None,
        "provenance": site.provenance,
        "glyph": glyph(site),
    }


def rows_for(db: Session, sites: list[Site]) -> list[dict[str, Any]]:
    ids = [s.id for s in sites]
    assess = latest_assessments(db, ids)
    listings: dict[int, list[Listing]] = {}
    for l in db.scalars(select(Listing).where(Listing.site_id.in_(ids))).all() if ids else []:
        listings.setdefault(l.site_id, []).append(l)
    leads = {l.site_id: l for l in db.scalars(select(Lead).where(Lead.site_id.in_(ids))).all()} if ids else {}
    pipes = {p.site_id: p for p in db.scalars(select(PipelineItem).where(PipelineItem.site_id.in_(ids))).all()} if ids else {}
    reductions: dict[int, int] = {}
    if ids:
        for ev, l in db.execute(select(ListingEvent, Listing).join(Listing, Listing.id == ListingEvent.listing_id).where(Listing.site_id.in_(ids), ListingEvent.event == "price_change")).all():
            if (ev.new.get("asking_price") or 0) < (ev.old.get("asking_price") or 0):
                reductions[l.site_id] = reductions.get(l.site_id, 0) + 1
    return [site_row(s, assess.get(s.id), listings.get(s.id, []), leads.get(s.id), pipes.get(s.id), reductions.get(s.id, 0)) for s in sites]


def feature(site_row_: dict[str, Any]) -> dict[str, Any]:
    props = {k: site_row_[k] for k in ("id", "headline", "origin", "category", "asking_price", "planning_class", "residual_land_value", "stale", "excluded", "lead_stage", "pipeline_stage")}
    props["score"] = site_row_["score"]["total"]
    props["fatal"] = site_row_["score"]["fatal"]
    props["confidence"] = site_row_["confidence"]["label"]
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [site_row_["lon"], site_row_["lat"]]}, "properties": props}


def boundary_feature(site: Site, row: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if site.boundary is None:
        return None
    return {"type": "Feature", "geometry": mapping(to_shape(site.boundary)), "properties": {"id": site.id, "boundary_source": site.boundary_source, "score": (row or {}).get("score", {}).get("total")}}

"""Opportunities, site dossiers, map data, imports and the ingest inbox."""

from __future__ import annotations

import base64
import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from geoalchemy2.shape import from_shape
from pydantic import BaseModel, Field, field_validator
from shapely.geometry import shape
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from plot import geo
from plot.api.deps import audit, current_user
from plot.api.serialize import boundary_feature, feature, rows_for
from plot.db import get_db
from plot.ingest import upsert
from plot.jobs.queue import enqueue
from plot.models import Assessment, Evidence, Feedback, IngestInbox, Lead, LeadEvent, Listing, Override, PipelineItem, Site, User, utcnow
from plot.sources.base import CandidateRecord

router = APIRouter()

SORTS = {"score", "residual", "price", "newest", "headroom", "confidence"}


@router.get("/opportunities")
def opportunities(
    db: Session = Depends(get_db),
    origin: str | None = None,
    category: str | None = None,
    planning_class: str | None = None,
    q: str | None = None,
    min_score: float | None = None,
    include_excluded: bool = False,
    include_fatal: bool = True,
    include_stale: bool = True,
    within_budget: bool | None = None,
    sort: str = "score",
    limit: int = Query(200, le=1000),
    offset: int = 0,
) -> dict[str, Any]:
    stmt = select(Site)
    if not include_excluded:
        stmt = stmt.where(Site.excluded.is_(False))
    if origin:
        stmt = stmt.where(Site.origin == origin)
    if category:
        stmt = stmt.where(Site.category == category)
    if not include_stale:
        stmt = stmt.where(Site.stale.is_(False))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(func.coalesce(Site.headline, "") + " " + func.coalesce(Site.address, "") + " " + func.coalesce(Site.lpa_name, "")).like(like))
    rows = rows_for(db, db.scalars(stmt).all())
    if planning_class:
        rows = [r for r in rows if r["planning_class"] == planning_class]
    if min_score is not None:
        rows = [r for r in rows if (r["score"]["total"] or 0) >= min_score]
    if not include_fatal:
        rows = [r for r in rows if not r["score"]["fatal"]]
    if within_budget is not None:
        rows = [r for r in rows if bool(r["within_budget"]) == within_budget]
    key = {
        "score": lambda r: (not r["score"]["fatal"], r["score"]["total"] or -1),
        "residual": lambda r: r["residual_land_value"] or -1e9,
        "price": lambda r: -(r["asking_price"] or 1e12),
        "newest": lambda r: r["first_seen_at"],
        "headroom": lambda r: r["headroom"] if r["headroom"] is not None else -1e9,
        "confidence": lambda r: r["confidence"]["score"],
    }[sort if sort in SORTS else "score"]
    rows.sort(key=key, reverse=True)
    return {"total": len(rows), "items": rows[offset : offset + limit]}


@router.get("/sites/{site_id}")
def site_dossier(site_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    site = db.get(Site, site_id)
    if site is None:
        raise HTTPException(404, "site not found")
    row = rows_for(db, [site])[0]
    a = db.scalar(select(Assessment).where(Assessment.site_id == site_id, Assessment.is_current.is_(True)))
    ev = db.scalars(select(Evidence).where(Evidence.site_id == site_id, Evidence.superseded_at.is_(None)).order_by(Evidence.topic, Evidence.kind)).all()
    listings = db.scalars(select(Listing).where(Listing.site_id == site_id)).all()
    history = db.execute(select(Assessment.created_at, Assessment.total_score, Assessment.fatal).where(Assessment.site_id == site_id).order_by(Assessment.created_at)).all()
    lead = db.scalar(select(Lead).where(Lead.site_id == site_id))
    lead_events = db.scalars(select(LeadEvent).where(LeadEvent.lead_id == lead.id).order_by(LeadEvent.at)).all() if lead else []
    pipe = db.scalar(select(PipelineItem).where(PipelineItem.site_id == site_id))
    overrides = db.scalars(select(Override).where(Override.site_id == site_id).order_by(Override.created_at.desc())).all()
    feedback = db.scalars(select(Feedback).where(Feedback.site_id == site_id).order_by(Feedback.created_at.desc())).all()
    return {
        "site": row,
        "boundary": boundary_feature(site),
        "listings": [
            {
                "id": l.id,
                "source_id": l.source_id,
                "url": l.url,
                "title": l.title,
                "description": l.description,
                "asking_price": l.asking_price,
                "price_qualifier": l.price_qualifier,
                "tenure": l.tenure,
                "status": l.status,
                "agent": l.agent,
                "is_auction": l.is_auction,
                "first_seen_at": l.first_seen_at.isoformat(),
                "last_seen_at": l.last_seen_at.isoformat(),
                "provenance": l.provenance,
                "events": [{"event": e.event, "old": e.old, "new": e.new, "at": e.observed_at.isoformat()} for e in l.events],
            }
            for l in listings
        ],
        "evidence": [
            {
                "id": e.id,
                "topic": e.topic,
                "kind": e.kind,
                "title": e.title,
                "summary": e.summary,
                "data": e.data,
                "status": e.status,
                "confidence": e.confidence,
                "source_id": e.source_id,
                "source_url": e.source_url,
                "data_date": e.data_date.isoformat() if e.data_date else None,
                "retrieved_at": e.retrieved_at.isoformat(),
                "licence": e.licence,
                "attribution": e.attribution,
            }
            for e in ev
        ],
        "assessment": None
        if a is None
        else {
            "id": a.id,
            "created_at": a.created_at.isoformat(),
            "engine_version": a.engine_version,
            "preferences_version": a.preferences_version,
            "planning": a.planning,
            "site_risk": a.site_risk,
            "design": a.design,
            "costs": a.costs,
            "residual": a.residual,
            "score": a.score,
            "explanation": a.explanation,
        },
        "score_history": [{"at": h.created_at.isoformat(), "score": h.total_score, "fatal": h.fatal} for h in history],
        "lead": None if lead is None else {"id": lead.id, "stage": lead.stage, "signals": lead.signals, "owner": lead.owner, "availability_evidence": lead.availability_evidence, "notes": lead.notes, "events": [{"from": e.from_stage, "to": e.to_stage, "at": e.at.isoformat(), "actor": e.actor, "note": e.note} for e in lead_events]},
        "pipeline": None if pipe is None else {"id": pipe.id, "stage": pipe.stage, "notes": pipe.notes, "checklist": pipe.checklist},
        "overrides": [{"id": o.id, "field": o.field, "value": o.value, "reason": o.reason, "active": o.active, "by": o.created_by, "at": o.created_at.isoformat()} for o in overrides],
        "feedback": [{"signal": f.signal, "reason": f.reason, "note": f.note, "at": f.created_at.isoformat()} for f in feedback],
    }


class FeedbackIn(BaseModel):
    signal: Literal[-1, 1]
    reason: str | None = Field(None, max_length=60)
    note: str | None = Field(None, max_length=2000)


@router.post("/sites/{site_id}/feedback")
def add_feedback(site_id: int, body: FeedbackIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    if db.get(Site, site_id) is None:
        raise HTTPException(404)
    db.add(Feedback(site_id=site_id, signal=body.signal, reason=body.reason, note=body.note))
    audit(db, user, "feedback.add", "site", site_id, signal=body.signal, reason=body.reason)
    db.commit()
    _reassess_now(db, site_id)
    return {"ok": True}


OVERRIDE_FIELDS = ("planning.class", "planning.verified", "listing.asking_price")


class OverrideIn(BaseModel):
    field: str
    value: Any
    reason: str = Field(min_length=3, max_length=2000)

    @field_validator("field")
    @classmethod
    def _f(cls, v: str) -> str:
        if v in OVERRIDE_FIELDS or v.startswith(("score.", "flag.", "cost.")):
            return v
        raise ValueError("unsupported override field")


@router.post("/sites/{site_id}/overrides")
def add_override(site_id: int, body: OverrideIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    if db.get(Site, site_id) is None:
        raise HTTPException(404)
    if body.field == "planning.class":
        from plot.engine.planning.base import CLASSES

        if body.value not in CLASSES:
            raise HTTPException(422, f"planning class must be one of {list(CLASSES)}")
    for old in db.scalars(select(Override).where(Override.site_id == site_id, Override.field == body.field, Override.active.is_(True))).all():
        old.active = False
    db.add(Override(site_id=site_id, field=body.field, value={"value": body.value}, reason=body.reason, created_by=user.email))
    audit(db, user, "override.add", "site", site_id, field=body.field, value=body.value, reason=body.reason)
    db.commit()
    _reassess_now(db, site_id)
    return {"ok": True}


@router.delete("/overrides/{override_id}")
def remove_override(override_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    o = db.get(Override, override_id)
    if o is None:
        raise HTTPException(404)
    o.active = False
    audit(db, user, "override.remove", "site", o.site_id, field=o.field)
    db.commit()
    _reassess_now(db, o.site_id)
    return {"ok": True}


class ExcludeIn(BaseModel):
    excluded: bool
    reason: str | None = None


@router.post("/sites/{site_id}/exclude")
def exclude_site(site_id: int, body: ExcludeIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    s = db.get(Site, site_id)
    if s is None:
        raise HTTPException(404)
    s.excluded = body.excluded
    s.exclusion_reason = f"user: {body.reason or 'excluded manually'}" if body.excluded else None
    audit(db, user, "site.exclude" if body.excluded else "site.include", "site", site_id, reason=body.reason)
    db.commit()
    return {"ok": True}


class BoundaryIn(BaseModel):
    geometry: dict[str, Any]


@router.post("/sites/{site_id}/boundary")
def set_boundary(site_id: int, body: BoundaryIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    s = db.get(Site, site_id)
    if s is None:
        raise HTTPException(404)
    try:
        g = shape(body.geometry)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, "invalid GeoJSON geometry") from exc
    if g.geom_type not in ("Polygon", "MultiPolygon") or not g.is_valid:
        raise HTTPException(422, "boundary must be a valid polygon")
    a = geo.area_m2(g)
    if not 20 <= a <= 500_000:
        raise HTTPException(422, f"boundary area {a:,.0f} m² is implausible")
    s.boundary = from_shape(g, srid=4326)
    s.boundary_source = "user_drawn"
    s.area_m2 = round(a)
    s.area_source = "drawn by you"
    s.last_changed_at = utcnow()
    audit(db, user, "site.boundary", "site", site_id, area_m2=round(a))
    db.commit()
    _reassess_now(db, site_id)
    return {"ok": True, "area_m2": round(a)}


@router.post("/sites/{site_id}/refresh")
def refresh_site(site_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    if db.get(Site, site_id) is None:
        raise HTTPException(404)
    j = enqueue(db, "enrich_site", {"site_id": site_id}, max_attempts=2)
    audit(db, user, "site.refresh", "site", site_id)
    db.commit()
    return {"queued_job": j.id if j else None}


def _reassess_now(db: Session, site_id: int) -> None:
    from plot.engine.assess import assess_site, rerank
    from plot.preferences import load_preferences

    prefs, v = load_preferences(db)
    assess_site(db, db.get(Site, site_id), prefs, v)
    rerank(db)
    db.commit()


# ------------------------------------------------------------------------------- map


@router.get("/map/sites")
def map_sites(db: Session = Depends(get_db), include_excluded: bool = False) -> dict:
    stmt = select(Site) if include_excluded else select(Site).where(Site.excluded.is_(False))
    sites = db.scalars(stmt).all()
    rows = rows_for(db, sites)
    by_id = {r["id"]: r for r in rows}
    return {
        "points": {"type": "FeatureCollection", "features": [feature(r) for r in rows]},
        "boundaries": {"type": "FeatureCollection", "features": [f for s in sites if (f := boundary_feature(s, by_id.get(s.id)))]},
    }


@router.get("/map/config")
def map_config(db: Session = Depends(get_db)) -> dict:
    from plot.preferences import load_preferences

    prefs, _ = load_preferences(db)
    return {
        "search_areas": [a.model_dump(mode="json") for a in prefs.search_areas],
        "basemaps": [
            {"id": "streets", "name": "Streets (OpenFreeMap / OpenStreetMap)", "style": "https://tiles.openfreemap.org/styles/positron", "attribution": "© OpenStreetMap contributors, OpenFreeMap"},
            {"id": "satellite", "name": "Satellite (Esri World Imagery)", "raster": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", "attribution": "Imagery © Esri, Maxar, Earthstar Geographics", "note": "Check Esri terms for your use; replace with a licensed imagery source if needed."},
        ],
        "overlays": [
            {"id": "flood-zones", "name": "EA Flood Zones 2 & 3", "type": "wms", "url": "https://environment.data.gov.uk/spatialdata/flood-map-for-planning-flood-zones/wms", "layers": "Flood_Zones_2_3_Rivers_and_Sea", "attribution": "© Environment Agency", "verified": False},
            {"id": "geology", "name": "BGS 1:50k bedrock geology", "type": "wms", "url": "https://map.bgs.ac.uk/arcgis/services/BGS_Detailed_Geology/MapServer/WMSServer", "layers": "BGS.50k.Bedrock", "attribution": "© UKRI (BGS)", "verified": False},
        ],
        "constraint_datasets": ["conservation-area", "green-belt", "area-of-outstanding-natural-beauty", "national-park", "flood-risk-zone", "ancient-woodland", "site-of-special-scientific-interest", "special-protection-area", "listed-building-outline", "tree-preservation-zone", "article-4-direction-area", "nutrient-neutrality-catchment", "brownfield-land"],
        "notes": "Basemap tiles and WMS overlays load directly in your browser. Layer names marked verified=false are taken from service documentation and confirmed on first use.",
    }


@router.get("/map/constraints")
def map_constraints(bbox: str, datasets: str = "conservation-area,green-belt,area-of-outstanding-natural-beauty,national-park,ancient-woodland,site-of-special-scientific-interest", db: Session = Depends(get_db)) -> dict:
    """Constraint polygons in view (proxied & cached from planning.data.gov.uk)."""
    from plot.sources.base import SourceError
    from plot.sources.http import PublicHttp
    from plot.sources.planning_data import PlanningDataClient

    try:
        b = tuple(float(x) for x in bbox.split(","))
        assert len(b) == 4
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, "bbox must be minLon,minLat,maxLon,maxLat") from exc
    if (b[2] - b[0]) * (b[3] - b[1]) > 0.25:
        return {"type": "FeatureCollection", "features": [], "note": "zoom in to load constraint polygons"}
    try:
        with PublicHttp(db) as http:
            ents = PlanningDataClient(http).search(datasets.split(","), geometry_wkt=geo.bbox_polygon(b).wkt, cache_ttl=dt.timedelta(days=7), max_pages=4)
        db.commit()
    except SourceError as e:
        raise HTTPException(502, f"planning.data.gov.uk unavailable: {e.args[0]}") from e
    feats = []
    for e in ents:
        g = geo.parse_wkt(e.get("geometry"))
        if g is None:
            continue
        feats.append({"type": "Feature", "geometry": geo.geojson(geo.simplify_for_display(g, 3)), "properties": {"dataset": e.get("dataset"), "name": e.get("name"), "entity": e.get("entity")}})
    return {"type": "FeatureCollection", "features": feats}


# ------------------------------------------------------------------------------- imports

UNITS = {"m2": 1.0, "sqft": 0.09290304, "acres": 4046.8564224, "hectares": 10_000.0}


class ListingIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    url: str | None = Field(None, max_length=1000)
    asking_price: int | None = Field(None, ge=0, le=20_000_000)
    price_qualifier: str | None = None
    tenure: Literal["freehold", "leasehold", "unknown"] | None = None
    lat: float | None = Field(None, ge=49.8, le=61)
    lon: float | None = Field(None, ge=-8.7, le=2.0)
    postcode: str | None = None
    address: str | None = None
    area_value: float | None = Field(None, gt=0)
    area_unit: Literal["m2", "sqft", "acres", "hectares"] = "m2"
    description: str | None = Field(None, max_length=10_000)
    category: Literal["plot", "land", "barn", "renovation", "brownfield", "building", "other"] = "plot"
    agent: str | None = None
    is_auction: bool = False
    auction_date: dt.date | None = None

    @field_validator("url")
    @classmethod
    def _u(cls, v: str | None) -> str | None:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("url must start with http(s)://")
        return v


@router.post("/import/listing")
def import_listing(body: ListingIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    lon, lat, quality = body.lon, body.lat, "exact"
    if lon is None or lat is None:
        if not body.postcode:
            raise HTTPException(422, "provide a map position (lat/lon) or a postcode")
        from plot.sources.base import SourceError
        from plot.sources.http import PublicHttp
        from plot.sources.postcodes import lookup_postcode

        try:
            with PublicHttp(db) as http:
                r = lookup_postcode(http, body.postcode)
        except SourceError as e:
            raise HTTPException(502, f"postcode lookup unavailable ({e.args[0]}); drop a pin on the map instead") from e
        if not r:
            raise HTTPException(422, "postcode not found")
        lon, lat, quality = r["longitude"], r["latitude"], "postcode_centroid"
    ref = body.url or f"manual:{user.email}:{utcnow().timestamp()}"
    rec = CandidateRecord(
        source_id="manual",
        external_ref=ref[:300],
        record_type="listing",
        lon=lon,
        lat=lat,
        title=body.title,
        category=body.category,
        address=body.address,
        postcode=body.postcode,
        area_m2=body.area_value * UNITS[body.area_unit] if body.area_value else None,
        description=body.description,
        url=body.url,
        asking_price=body.asking_price,
        price_qualifier=body.price_qualifier,
        tenure=body.tenure,
        agent=body.agent,
        is_auction=body.is_auction,
        auction_date=body.auction_date,
        publisher="you",
        licence="User-supplied",
        attribution="Entered manually",
        confidence="medium",
        signals=["on_market", f"location:{quality}"],
        raw={"location_quality": quality, "entered_by": user.email},
    )
    site, outcome = upsert(db, rec, None)
    audit(db, user, "import.listing", "site", site.id, url=body.url, outcome=outcome)
    db.commit()
    _reassess_now(db, site.id)
    j = enqueue(db, "enrich_site", {"site_id": site.id}, max_attempts=2)
    db.commit()
    return {"site_id": site.id, "outcome": outcome, "enrichment_job": j.id if j else None}


class OffMarketIn(BaseModel):
    lat: float = Field(ge=49.8, le=61)
    lon: float = Field(ge=-8.7, le=2.0)
    title: str = Field(min_length=3, max_length=300)
    category: Literal["land", "barn", "building", "brownfield", "renovation", "other"] = "land"
    notes: str | None = Field(None, max_length=5000)
    signals: list[str] = Field(default_factory=list)


@router.post("/import/off-market")
def import_off_market(body: OffMarketIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    rec = CandidateRecord(
        source_id="manual",
        external_ref=f"offmarket:{body.lon:.6f},{body.lat:.6f}",
        record_type="off_market_signal",
        lon=body.lon,
        lat=body.lat,
        title=body.title,
        category=body.category,
        description=body.notes,
        publisher="you",
        licence="User-supplied",
        attribution="Identified by you",
        confidence="medium",
        signals=["user_identified", *body.signals],
        raw={"notes": body.notes, "location_quality": "exact"},
    )
    site, outcome = upsert(db, rec, None)
    audit(db, user, "import.off_market", "site", site.id)
    db.commit()
    _reassess_now(db, site.id)
    j = enqueue(db, "enrich_site", {"site_id": site.id}, max_attempts=2)
    db.commit()
    return {"site_id": site.id, "outcome": outcome, "enrichment_job": j.id if j else None}


@router.post("/import/eml")
async def import_eml(files: list[UploadFile] = File(...), db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    emls = []
    for f in files[:50]:
        data = await f.read()
        if len(data) > 5_000_000:
            raise HTTPException(413, f"{f.filename} is larger than 5 MB")
        emls.append(base64.b64encode(data).decode())
    j = enqueue(db, "ingest_eml", {"emls": emls}, max_attempts=1)
    audit(db, user, "import.eml", None, None, files=len(emls))
    db.commit()
    return {"queued_job": j.id if j else None, "files": len(emls)}


@router.get("/inbox")
def inbox(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(IngestInbox).where(IngestInbox.dismissed.is_(False), IngestInbox.resolved_site_id.is_(None)).order_by(IngestInbox.received_at.desc())).all()
    return [{"id": r.id, "source_id": r.source_id, "url": r.url, "title": r.title, "asking_price": r.asking_price, "text": r.text, "reason": r.reason, "received_at": r.received_at.isoformat()} for r in rows]


class PlaceIn(BaseModel):
    lat: float = Field(ge=49.8, le=61)
    lon: float = Field(ge=-8.7, le=2.0)


@router.post("/inbox/{item_id}/place")
def place_inbox(item_id: int, body: PlaceIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    it = db.get(IngestInbox, item_id)
    if it is None:
        raise HTTPException(404)
    rec = CandidateRecord(source_id=it.source_id, external_ref=it.external_ref, record_type="listing", lon=body.lon, lat=body.lat, title=it.title or "Listing", url=it.url, asking_price=it.asking_price, description=it.text, category="plot", publisher=it.source_id, licence="Quoted from alert email", attribution="Placed on map by you", confidence="medium", signals=["on_market", "location:user_placed"], raw={"location_quality": "user_placed_approximate"})
    site, _ = upsert(db, rec, None)
    it.resolved_site_id = site.id
    audit(db, user, "inbox.place", "site", site.id)
    db.commit()
    _reassess_now(db, site.id)
    return {"site_id": site.id}


@router.post("/inbox/{item_id}/dismiss")
def dismiss_inbox(item_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    it = db.get(IngestInbox, item_id)
    if it is None:
        raise HTTPException(404)
    it.dismissed = True
    audit(db, user, "inbox.dismiss", "inbox", item_id)
    db.commit()
    return {"ok": True}



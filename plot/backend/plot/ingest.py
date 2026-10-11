"""Upsert discovered records into sites/listings/evidence with change detection.

Rules:
- A listing is an advert; register records and off-market signals are stored as evidence
  ("source-record:<source>") on a site and never become listings.
- Price/status changes create ListingEvents; a listing is marked withdrawn only after it is
  missing from several consecutive SUCCESSFUL fetches of its source (never on a failed run).
- Off-market/register sites get a Lead at 'potential_site_identified' — availability is
  never implied.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import asdict

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point
from sqlalchemy import select
from sqlalchemy.orm import Session

from plot.models import Evidence, Lead, LeadEvent, Listing, ListingEvent, Site, utcnow
from plot.sources.base import CandidateRecord

WITHDRAW_AFTER_MISSES = 3
_STOP = {"land", "at", "of", "the", "and", "site", "plot", "former", "rear", "to", "adj", "adjacent", "on", "near", "off"}


def address_tokens(addr: str | None) -> set[str]:
    if not addr:
        return set()
    return {t for t in re.findall(r"[a-z0-9]+", addr.lower()) if t not in _STOP and len(t) > 1}


def dedupe_key(lon: float, lat: float) -> str:
    return f"{round(lon, 4):.4f},{round(lat, 4):.4f}"


def _record_json(rec: CandidateRecord) -> dict:
    d = asdict(rec)
    for k in ("auction_date", "data_date"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


def _new_site(db: Session, rec: CandidateRecord, origin: str) -> Site:
    now = utcnow()
    site = Site(
        point=from_shape(Point(rec.lon, rec.lat), srid=4326),
        boundary=from_shape(_wkt(rec.boundary_wkt), srid=4326) if rec.boundary_wkt else None,
        boundary_source="register_polygon" if rec.boundary_wkt else "none",
        area_m2=rec.area_m2,
        area_source=f"reported by {rec.source_id}" if rec.area_m2 else None,
        address=rec.address,
        postcode=rec.postcode,
        origin=origin,
        category=rec.category,
        headline=rec.title[:300],
        dedupe_key=dedupe_key(rec.lon, rec.lat),
        provenance=[{"source_id": rec.source_id, "ref": rec.external_ref, "url": rec.url, "retrieved_at": now.isoformat(), "data_date": rec.data_date.isoformat() if rec.data_date else None, "licence": rec.licence, "attribution": rec.attribution, "location_quality": rec.raw.get("location_quality", "exact")}],
        confidence=rec.confidence,
        stale=rec.stale,
        first_seen_at=now,
        last_seen_at=now,
        last_changed_at=now,
    )
    db.add(site)
    db.flush()
    return site


def _wkt(text: str):
    from shapely import wkt

    return wkt.loads(text)


def upsert(db: Session, rec: CandidateRecord, run_id: int | None) -> tuple[Site, str]:
    """Returns (site, outcome) where outcome ∈ {'new', 'changed', 'unchanged'}."""
    now = utcnow()
    if rec.record_type == "listing":
        return _upsert_listing(db, rec, run_id, now)
    return _upsert_record(db, rec, now)


def _upsert_listing(db: Session, rec: CandidateRecord, run_id: int | None, now: dt.datetime) -> tuple[Site, str]:
    lst = db.scalar(select(Listing).where(Listing.source_id == rec.source_id, Listing.external_ref == rec.external_ref))
    raw = rec.raw | {"_signals": rec.signals, "_record_type": rec.record_type, "_misses": 0}
    if lst is None:
        site = _find_duplicate_site(db, rec) or _new_site(db, rec, "on_market")
        if site.origin != "on_market":
            site.origin = "on_market"
            site.last_changed_at = now
        lst = Listing(
            site_id=site.id,
            source_id=rec.source_id,
            external_ref=rec.external_ref,
            url=rec.url,
            title=rec.title,
            description=rec.description,
            asking_price=rec.asking_price,
            price_qualifier=rec.price_qualifier,
            tenure=rec.tenure,
            reported_area_m2=rec.area_m2,
            status=rec.status,
            agent=rec.agent,
            is_auction=rec.is_auction,
            auction_date=rec.auction_date,
            raw=raw,
            provenance={"retrieved_at": now.isoformat(), "licence": rec.licence, "attribution": rec.attribution, "publisher": rec.publisher},
            first_seen_at=now,
            last_seen_at=now,
            last_changed_at=now,
        )
        db.add(lst)
        db.flush()
        db.add(ListingEvent(listing_id=lst.id, event="new", new={"asking_price": rec.asking_price, "status": rec.status}, observed_at=now, run_id=run_id))
        return site, "new"
    site = db.get(Site, lst.site_id)
    changed = False
    if lst.status == "withdrawn":
        db.add(ListingEvent(listing_id=lst.id, event="relisted", old={"status": "withdrawn"}, new={"status": rec.status, "asking_price": rec.asking_price}, observed_at=now, run_id=run_id))
        lst.status = rec.status
        changed = True
    if rec.asking_price is not None and rec.asking_price != lst.asking_price:
        db.add(ListingEvent(listing_id=lst.id, event="price_change", old={"asking_price": lst.asking_price}, new={"asking_price": rec.asking_price}, observed_at=now, run_id=run_id))
        lst.asking_price = rec.asking_price
        changed = True
    if rec.status != lst.status:
        db.add(ListingEvent(listing_id=lst.id, event="status_change", old={"status": lst.status}, new={"status": rec.status}, observed_at=now, run_id=run_id))
        lst.status = rec.status
        changed = True
    lst.last_seen_at = now
    lst.raw = raw
    site.last_seen_at = now
    if changed:
        lst.last_changed_at = now
        site.last_changed_at = now
    return site, "changed" if changed else "unchanged"


def _upsert_record(db: Session, rec: CandidateRecord, now: dt.datetime) -> tuple[Site, str]:
    kind = f"source-record:{rec.source_id}"
    ev = db.scalar(select(Evidence).where(Evidence.kind == kind, Evidence.source_ref == rec.external_ref))
    data = rec.raw | {"_signals": rec.signals, "_record_type": rec.record_type, "_title": rec.title, "_stale": rec.stale}
    fp = hashlib.sha256(f"{kind}|{rec.external_ref}".encode()).hexdigest()
    if ev is None:
        site = _find_duplicate_site(db, rec)
        created = site is None
        if site is None:
            site = _new_site(db, rec, "register" if rec.record_type == "register_site" else "off_market")
        else:
            site.provenance = list(site.provenance or []) + [{"source_id": rec.source_id, "ref": rec.external_ref, "url": rec.url, "retrieved_at": now.isoformat(), "data_date": rec.data_date.isoformat() if rec.data_date else None, "licence": rec.licence}]
            # a fresher, authoritative record should clear staleness
            if not rec.stale and site.stale:
                site.stale = False
                site.confidence = rec.confidence
        db.add(
            Evidence(
                site_id=site.id,
                topic="planning" if rec.record_type == "register_site" else "ownership",
                kind=kind,
                source_id=rec.source_id,
                source_url=rec.url,
                source_ref=rec.external_ref,
                title=rec.title,
                summary=rec.description,
                data=data,
                data_date=rec.data_date,
                status="estimated" if rec.stale else "confirmed",
                confidence=0.4 if rec.stale else 0.85,
                licence=rec.licence,
                attribution=rec.attribution,
                fingerprint=fp,
            )
        )
        if db.scalar(select(Lead).where(Lead.site_id == site.id)) is None and site.origin != "on_market":
            lead = Lead(site_id=site.id, stage="potential_site_identified", signals=rec.signals)
            db.add(lead)
            db.flush()
            db.add(LeadEvent(lead_id=lead.id, from_stage=None, to_stage="potential_site_identified", actor="system", note=f"Identified from {rec.source_id}"))
        db.flush()
        return site, "new" if created else "changed"
    site = db.get(Site, ev.site_id)
    old_status = (ev.data or {}).get("planning_status") or (ev.data or {}).get("planning-permission-status")
    new_status = data.get("planning_status") or data.get("planning-permission-status")
    changed = old_status != new_status or (ev.data or {}).get("_stale") != rec.stale
    ev.data = data
    ev.retrieved_at = now
    ev.data_date = rec.data_date
    ev.status = "estimated" if rec.stale else "confirmed"
    site.last_seen_at = now
    if changed:
        site.last_changed_at = now
    return site, "changed" if changed else "unchanged"


def _find_duplicate_site(db: Session, rec: CandidateRecord, radius_deg: float = 0.0003) -> Site | None:
    """Same physical site if within ~25 m and addresses share a distinctive token (or no address)."""
    cands = db.scalars(select(Site).where(Site.point.ST_DWithin(from_shape(Point(rec.lon, rec.lat), srid=4326), radius_deg))).all()
    toks = address_tokens(rec.address or rec.title)
    for s in cands:
        if s.exclusion_reason and s.exclusion_reason.startswith("duplicate"):
            continue
        st = address_tokens(s.address or s.headline)
        if not toks or not st or toks & st:
            return s
    return None


def mark_missing(db: Session, source_id: str, seen_refs: set[str], run_id: int | None) -> int:
    """After a SUCCESSFUL source fetch, count misses; withdraw listings missing N runs in a row."""
    n = 0
    now = utcnow()
    for lst in db.scalars(select(Listing).where(Listing.source_id == source_id, Listing.status != "withdrawn")).all():
        if lst.external_ref in seen_refs:
            continue
        misses = int((lst.raw or {}).get("_misses", 0)) + 1
        lst.raw = (lst.raw or {}) | {"_misses": misses}
        if misses >= WITHDRAW_AFTER_MISSES:
            db.add(ListingEvent(listing_id=lst.id, event="withdrawn", old={"status": lst.status}, new={"status": "withdrawn"}, observed_at=now, run_id=run_id))
            lst.status = "withdrawn"
            lst.last_changed_at = now
            n += 1
    return n


def site_point(site: Site) -> tuple[float, float]:
    p = to_shape(site.point)
    return p.x, p.y

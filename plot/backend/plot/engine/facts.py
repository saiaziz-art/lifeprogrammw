"""SiteFacts: everything the engines know about one site, assembled from the database.

Engines are pure functions of SiteFacts + preferences + cost rates, which makes them
deterministic, testable without a database, and lets assessments be fingerprinted.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from geoalchemy2.shape import to_shape
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from plot.models import Evidence, Listing, ListingEvent, Override, Site


@dataclass
class EvidenceView:
    id: int | None
    topic: str
    kind: str
    title: str
    summary: str | None
    data: dict[str, Any]
    status: str
    confidence: float
    source_id: str | None
    source_url: str | None
    data_date: str | None
    retrieved_at: str | None


@dataclass
class ListingView:
    id: int
    source_id: str
    url: str | None
    title: str | None
    description: str | None
    asking_price: int | None
    price_qualifier: str | None
    status: str
    tenure: str | None
    first_seen_at: str
    is_auction: bool
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SiteFacts:
    site_id: int
    lon: float
    lat: float
    boundary_wkt: str | None
    boundary_source: str
    area_m2: float | None
    area_source: str | None
    address: str | None
    postcode: str | None
    lpa_name: str | None
    category: str
    origin: str
    headline: str | None
    stale: bool
    signals: list[str]
    raw_register: dict[str, Any] | None
    evidence: list[EvidenceView]
    listings: list[ListingView]
    overrides: dict[str, dict[str, Any]]
    nearest_rail: dict[str, Any] | None
    nearest_london_rail: dict[str, Any] | None
    today: dt.date
    enrichment_status: dict[str, str] = field(default_factory=dict)  # source_id -> ok|failed|not_configured...

    # ------------------------------------------------------------------ helpers

    def items(self, prefix: str) -> list[EvidenceView]:
        return [e for e in self.evidence if e.kind == prefix or e.kind.startswith(prefix)]

    def first(self, prefix: str) -> EvidenceView | None:
        it = self.items(prefix)
        return it[0] if it else None

    def onsite(self, dataset: str) -> list[EvidenceView]:
        return self.items(f"pd:{dataset}")

    def near(self, dataset: str) -> list[dict[str, Any]]:
        e = self.first(f"pd-near:{dataset}")
        return list(e.data.get("features", [])) if e else []

    def near_checked(self, dataset: str) -> bool:
        return self.first(f"pd-near:{dataset}") is not None

    def constraints_checked(self) -> bool:
        return self.first("pd:coverage") is not None

    def nearest_distance(self, dataset: str, name_contains: str | None = None) -> float | None:
        feats = self.near(dataset)
        if name_contains:
            feats = [f for f in feats if name_contains.lower() in (f.get("name") or "").lower()]
        return min((f["distance_m"] for f in feats if f.get("distance_m") is not None), default=None)

    @property
    def asking_price(self) -> int | None:
        if "listing.asking_price" in self.overrides:
            return int(self.overrides["listing.asking_price"]["value"])
        live = [l for l in self.listings if l.asking_price and l.status not in ("withdrawn", "sold")]
        return min((l.asking_price for l in live), default=None)

    @property
    def listing_text(self) -> str:
        return " ".join(filter(None, [self.headline] + [f"{l.title or ''} {l.description or ''}" for l in self.listings])).lower()

    def fingerprint(self, prefs_version: int, rates_version: str, engine_version: str) -> str:
        payload = asdict(self)
        payload.pop("today", None)
        for e in payload["evidence"]:
            e.pop("retrieved_at", None)
            e.pop("id", None)
        payload["_v"] = [prefs_version, rates_version, engine_version, self.today.isoformat()[:7]]
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def load_facts(db: Session, site: Site, today: dt.date | None = None) -> SiteFacts:
    pt = to_shape(site.point)
    boundary = to_shape(site.boundary).wkt if site.boundary is not None else None
    ev_rows = db.scalars(select(Evidence).where(Evidence.site_id == site.id, Evidence.superseded_at.is_(None)).order_by(Evidence.id)).all()
    evidence = [
        EvidenceView(
            id=e.id,
            topic=e.topic,
            kind=e.kind,
            title=e.title,
            summary=e.summary,
            data=e.data or {},
            status=e.status,
            confidence=e.confidence,
            source_id=e.source_id,
            source_url=e.source_url,
            data_date=e.data_date.isoformat() if e.data_date else None,
            retrieved_at=e.retrieved_at.isoformat() if e.retrieved_at else None,
        )
        for e in ev_rows
    ]
    listings = []
    raw_register = None
    signals: list[str] = []
    for e in evidence:
        if e.kind.startswith("source-record:"):
            signals.extend(e.data.get("_signals", []))
            if raw_register is None and e.data.get("_record_type") == "register_site":
                raw_register = e.data
    for l in db.scalars(select(Listing).where(Listing.site_id == site.id)).all():
        events = [
            {"event": ev.event, "old": ev.old, "new": ev.new, "at": ev.observed_at.isoformat()}
            for ev in db.scalars(select(ListingEvent).where(ListingEvent.listing_id == l.id).order_by(ListingEvent.observed_at)).all()
        ]
        signals.extend(l.raw.get("_signals", []))
        listings.append(
            ListingView(
                id=l.id,
                source_id=l.source_id,
                url=l.url,
                title=l.title,
                description=l.description,
                asking_price=l.asking_price,
                price_qualifier=l.price_qualifier,
                status=l.status,
                tenure=l.tenure,
                first_seen_at=l.first_seen_at.isoformat(),
                is_auction=l.is_auction,
                events=events,
            )
        )
    overrides = {o.field: {"value": o.value.get("value"), "reason": o.reason, "by": o.created_by} for o in db.scalars(select(Override).where(Override.site_id == site.id, Override.active.is_(True))).all()}
    nearest = _nearest_station(db, pt.x, pt.y, london_only=False)
    nearest_london = _nearest_station(db, pt.x, pt.y, london_only=True)
    return SiteFacts(
        site_id=site.id,
        lon=pt.x,
        lat=pt.y,
        boundary_wkt=boundary,
        boundary_source=site.boundary_source,
        area_m2=site.area_m2,
        area_source=site.area_source,
        address=site.address,
        postcode=site.postcode,
        lpa_name=site.lpa_name,
        category=site.category,
        origin=site.origin,
        headline=site.headline,
        stale=site.stale,
        signals=sorted(set(signals)),
        raw_register=raw_register,
        evidence=evidence,
        listings=listings,
        overrides=overrides,
        nearest_rail=nearest,
        nearest_london_rail=nearest_london,
        today=today or dt.date.today(),
    )


def _nearest_station(db: Session, lon: float, lat: float, london_only: bool) -> dict[str, Any] | None:
    cond = "AND (props->>'london_direct')::boolean" if london_only else ""
    row = db.execute(
        text(
            f"""SELECT name, props, ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography) AS d
                FROM ref_features WHERE dataset='rail_station' {cond}
                ORDER BY geom <-> ST_SetSRID(ST_MakePoint(:lon,:lat),4326) LIMIT 1"""
        ),
        {"lon": lon, "lat": lat},
    ).first()
    if row is None:
        return None
    return {"name": row.name, "distance_m": round(row.d), "london_direct": bool((row.props or {}).get("london_direct"))}


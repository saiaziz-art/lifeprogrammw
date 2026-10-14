"""Builders for synthetic SiteFacts used by engine unit tests (test data only)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from plot.engine.facts import EvidenceView, ListingView, SiteFacts

SQUARE_30M = "POLYGON((-1.6 50.8, -1.59957 50.8, -1.59957 50.80027, -1.6 50.80027, -1.6 50.8))"  # ≈30 m × 30 m


def ev(kind: str, data: dict[str, Any] | None = None, status: str = "confirmed", topic: str = "planning", title: str | None = None) -> EvidenceView:
    return EvidenceView(None, topic, kind, title or kind, None, data or {}, status, 0.9, "test", "https://example.test/evidence", None, None)


def listing(price: int | None = 100_000, desc: str = "", events: list | None = None, first_seen: str = "2026-09-01T00:00:00") -> ListingView:
    return ListingView(1, "manual", "https://example.test/listing", "Test plot", desc, price, "asking", "available", "freehold", first_seen, False, events or [])


def facts(
    *,
    evidence: list[EvidenceView] | None = None,
    listings: list[ListingView] | None = None,
    area: float | None = 900,
    boundary: str | None = None,
    category: str = "plot",
    origin: str = "on_market",
    headline: str = "Test plot",
    stale: bool = False,
    signals: list[str] | None = None,
    raw_register: dict | None = None,
    overrides: dict | None = None,
    checked: bool = True,
    rail: dict | None = None,
    today: dt.date = dt.date(2026, 10, 11),
) -> SiteFacts:
    evs = list(evidence or [])
    if checked and not any(e.kind == "pd:coverage" for e in evs):
        evs.append(ev("pd:coverage", {"checked": {}, "intersecting": []}))
    return SiteFacts(
        site_id=1,
        lon=-1.6,
        lat=50.8,
        boundary_wkt=boundary,
        boundary_source="user_drawn" if boundary else "none",
        area_m2=area,
        area_source="test",
        address="Test Lane",
        postcode=None,
        lpa_name="Test District",
        category=category,
        origin=origin,
        headline=headline,
        stale=stale,
        signals=signals or [],
        raw_register=raw_register,
        evidence=evs,
        listings=listings if listings is not None else [listing()],
        overrides=overrides or {},
        nearest_rail=rail,
        nearest_london_rail=rail if rail and rail.get("london_direct") else None,
        today=today,
    )


def near(dataset: str, *features: tuple[str, float]) -> EvidenceView:
    return ev(f"pd-near:{dataset}", {"features": [{"name": n, "distance_m": d} for n, d in features], "count": len(features)})

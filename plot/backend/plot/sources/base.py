"""Source adapter contracts.

Two kinds of adapter:
- DiscoverySource: yields CandidateRecords (listings, register sites, off-market signals).
- EnrichmentSource: returns EvidenceItems about one site (constraints, flood, geology...).

Adapters never invent data. When they cannot run they raise SourceError with a precise
error_class so the run history and admin screen show the real blocker.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from sqlalchemy.orm import Session


class SourceError(Exception):
    """A source could not deliver. error_class is machine-readable, message human-readable."""

    def __init__(self, error_class: str, message: str, *, http_status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.error_class = error_class
        self.http_status = http_status
        self.retryable = retryable

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.error_class}] {self.args[0]}"


class SourceNotConfigured(SourceError):
    def __init__(self, message: str):
        super().__init__("not_configured", message)


class SourceBlocked(SourceError):
    """The source cannot lawfully be automated (terms of use, licence) or needs permission."""

    def __init__(self, message: str):
        super().__init__("blocked_by_terms", message)


@dataclass(frozen=True)
class SourceMeta:
    id: str
    name: str
    category: str  # on_market | off_market | constraints | enrichment | market
    kind: str  # live_api | bulk | email | manual | blocked | mirror
    licence: str
    attribution: str
    terms_url: str | None = None
    docs_url: str | None = None
    notes: str = ""
    required_secrets: tuple[str, ...] = ()
    default_enabled: bool = True
    default_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class CandidateRecord:
    source_id: str
    external_ref: str
    record_type: str  # listing | register_site | off_market_signal
    lon: float
    lat: float
    title: str
    category: str = "land"  # plot | land | brownfield | barn | renovation | building | other
    address: str | None = None
    postcode: str | None = None
    boundary_wkt: str | None = None
    area_m2: float | None = None
    description: str | None = None
    url: str | None = None
    asking_price: int | None = None
    price_qualifier: str | None = None
    tenure: str | None = None
    status: str = "available"
    agent: str | None = None
    is_auction: bool = False
    auction_date: dt.date | None = None
    data_date: dt.date | None = None
    publisher: str | None = None
    licence: str | None = None
    attribution: str | None = None
    confidence: str = "medium"
    stale: bool = False
    signals: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceItem:
    topic: str
    kind: str
    title: str
    summary: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    source_url: str | None = None
    source_ref: str | None = None
    data_date: dt.date | None = None
    status: str = "estimated"  # confirmed | estimated | unknown | requires_investigation
    confidence: float = 0.5
    licence: str | None = None
    attribution: str | None = None

    def fingerprint(self, source_id: str) -> str:
        payload = json.dumps(
            {"s": source_id, "k": self.kind, "r": self.source_ref, "t": self.title, "d": self.data},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class SiteContext:
    site_id: int
    lon: float
    lat: float
    boundary_wkt: str | None
    area_m2: float | None
    postcode: str | None
    address: str | None


@dataclass
class RunContext:
    db: Session
    config: dict[str, Any]
    search_bboxes: list[tuple[float, float, float, float]]
    secrets: dict[str, str | None]
    now: dt.datetime
    run_id: int | None = None


class SourceAdapter:
    meta: ClassVar[SourceMeta]

    def check_ready(self, ctx: RunContext) -> None:
        """Raise SourceNotConfigured/SourceBlocked if the adapter cannot run."""
        missing = [k for k in self.meta.required_secrets if not ctx.secrets.get(k)]
        if missing:
            raise SourceNotConfigured(f"missing credentials: {', '.join(missing)} (set them in Admin → Integrations)")


class DiscoverySource(SourceAdapter):
    def discover(self, ctx: RunContext) -> Iterable[CandidateRecord]:  # pragma: no cover - interface
        raise NotImplementedError


class EnrichmentSource(SourceAdapter):
    #: topics this adapter contributes, used to decide when to re-enrich
    topics: ClassVar[tuple[str, ...]] = ()
    #: re-query after this many days
    refresh_days: ClassVar[int] = 30

    def enrich(self, ctx: RunContext, site: SiteContext) -> list[EvidenceItem]:  # pragma: no cover - interface
        raise NotImplementedError

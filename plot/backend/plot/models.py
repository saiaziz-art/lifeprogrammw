"""Persistent data model.

Design rules encoded in the schema:
- Every site, listing and evidence item carries provenance (source, URL, retrieval time,
  data date) and a confidence/status so nothing is presented without its origin.
- On-market listings and off-market leads are separate: an unadvertised parcel is never a
  listing, and a lead only reaches "interest confirmed" with recorded evidence.
- Assessments are versioned (one current per site) so changes can be detected and explained.
- Jobs/runs/source_runs give an honest, queryable history of the autonomous workflow.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


TS = DateTime(timezone=True)


# --------------------------------------------------------------------------- auth


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(TS)


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(TS)
    last_seen_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    user_agent: Mapped[str | None] = mapped_column(String(400))
    user: Mapped[User] = relationship()


class Secret(Base):
    """Encrypted integration credential (API key, IMAP/SMTP password...)."""

    __tablename__ = "secrets"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(320))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(120))
    entity: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(60))
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)


# --------------------------------------------------------------------------- preferences


class Preferences(Base):
    """Single-row (id=1) editable acquisition strategy. History kept in PreferencesHistory."""

    __tablename__ = "preferences"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict[str, Any]] = mapped_column()
    updated_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)


class PreferencesHistory(Base):
    __tablename__ = "preferences_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column()
    saved_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    saved_by: Mapped[str | None] = mapped_column(String(320))


# --------------------------------------------------------------------------- sources


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40))  # on_market|off_market|constraints|enrichment|market
    kind: Mapped[str] = mapped_column(String(30))  # live_api|bulk|email|manual|blocked|mirror
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    licence: Mapped[str | None] = mapped_column(String(200))
    attribution: Mapped[str | None] = mapped_column(Text)
    terms_url: Mapped[str | None] = mapped_column(String(500))
    docs_url: Mapped[str | None] = mapped_column(String(500))
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    last_attempt_at: Mapped[dt.datetime | None] = mapped_column(TS)
    last_success_at: Mapped[dt.datetime | None] = mapped_column(TS)
    last_data_change_at: Mapped[dt.datetime | None] = mapped_column(TS)
    last_status: Mapped[str | None] = mapped_column(String(30))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)


# --------------------------------------------------------------------------- sites & listings


class Site(Base):
    """A physical candidate location (plot, parcel, building, register site)."""

    __tablename__ = "sites"
    id: Mapped[int] = mapped_column(primary_key=True)
    point = mapped_column(Geometry("POINT", srid=4326, spatial_index=True), nullable=False)
    boundary = mapped_column(Geometry("GEOMETRY", srid=4326, spatial_index=True), nullable=True)
    # surveyed | inspire | register_polygon | user_drawn | none
    boundary_source: Mapped[str] = mapped_column(String(30), default="none")
    area_m2: Mapped[float | None] = mapped_column(Float)
    area_source: Mapped[str | None] = mapped_column(String(60))
    address: Mapped[str | None] = mapped_column(Text)
    postcode: Mapped[str | None] = mapped_column(String(10))
    lpa_code: Mapped[str | None] = mapped_column(String(12))
    lpa_name: Mapped[str | None] = mapped_column(String(120))
    county: Mapped[str | None] = mapped_column(String(120))
    jurisdiction: Mapped[str] = mapped_column(String(20), default="england")
    uprn: Mapped[str | None] = mapped_column(String(20))
    title_numbers: Mapped[list[Any]] = mapped_column(default=list)
    inspire_ids: Mapped[list[Any]] = mapped_column(default=list)
    origin: Mapped[str] = mapped_column(String(20))  # on_market|off_market|register
    category: Mapped[str] = mapped_column(String(30), default="land")
    headline: Mapped[str | None] = mapped_column(String(300))
    dedupe_key: Mapped[str | None] = mapped_column(String(120), index=True)
    provenance: Mapped[list[Any]] = mapped_column(default=list)
    confidence: Mapped[str] = mapped_column(String(10), default="low")  # data confidence
    first_seen_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    last_changed_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    enriched_at: Mapped[dt.datetime | None] = mapped_column(TS)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    exclusion_reason: Mapped[str | None] = mapped_column(Text)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)

    listings: Mapped[list[Listing]] = relationship(back_populates="site")
    evidence: Mapped[list[Evidence]] = relationship(back_populates="site")


class Listing(Base):
    """An on-market advert (portal, agent, auction, user-supplied, alert email)."""

    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("source_id", "external_ref", name="uq_listing_source_ref"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    external_ref: Mapped[str] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(1000))
    title: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    asking_price: Mapped[int | None] = mapped_column(Integer)
    price_qualifier: Mapped[str | None] = mapped_column(String(60))
    tenure: Mapped[str | None] = mapped_column(String(40))
    reported_area_m2: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30), default="available")
    agent: Mapped[str | None] = mapped_column(String(300))
    is_auction: Mapped[bool] = mapped_column(Boolean, default=False)
    auction_date: Mapped[dt.date | None] = mapped_column(Date)
    first_seen_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    last_changed_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    raw: Mapped[dict[str, Any]] = mapped_column(default=dict)
    provenance: Mapped[dict[str, Any]] = mapped_column(default=dict)
    site: Mapped[Site] = relationship(back_populates="listings")
    events: Mapped[list[ListingEvent]] = relationship(back_populates="listing", order_by="ListingEvent.observed_at")


class ListingEvent(Base):
    __tablename__ = "listing_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(30))  # new|price_change|status_change|relisted|withdrawn
    old: Mapped[dict[str, Any]] = mapped_column(default=dict)
    new: Mapped[dict[str, Any]] = mapped_column(default=dict)
    observed_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    listing: Mapped[Listing] = relationship(back_populates="events")


class Evidence(Base):
    """A single sourced finding about a site (constraint hit, flood zone, comparable, etc.)."""

    __tablename__ = "evidence"
    __table_args__ = (UniqueConstraint("site_id", "fingerprint", name="uq_evidence_fingerprint"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(30))  # planning|flood|ground|ecology|heritage|utilities|access|market|ownership|listing|location
    kind: Mapped[str] = mapped_column(String(60))  # e.g. constraint:conservation-area, flood-zone, comparable-sale
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_ref: Mapped[str | None] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    data_date: Mapped[dt.date | None] = mapped_column(Date)
    retrieved_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    # confirmed | estimated | unknown | requires_investigation
    status: Mapped[str] = mapped_column(String(30), default="estimated")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    licence: Mapped[str | None] = mapped_column(String(200))
    attribution: Mapped[str | None] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    superseded_at: Mapped[dt.datetime | None] = mapped_column(TS)
    site: Mapped[Site] = relationship(back_populates="evidence")


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (Index("ix_assessment_current", "site_id", "is_current"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    inputs_fingerprint: Mapped[str] = mapped_column(String(64))
    preferences_version: Mapped[int] = mapped_column(Integer)
    engine_version: Mapped[str] = mapped_column(String(20))
    planning: Mapped[dict[str, Any]] = mapped_column(default=dict)
    site_risk: Mapped[dict[str, Any]] = mapped_column(default=dict)
    design: Mapped[dict[str, Any]] = mapped_column(default=dict)
    costs: Mapped[dict[str, Any]] = mapped_column(default=dict)
    residual: Mapped[dict[str, Any]] = mapped_column(default=dict)
    score: Mapped[dict[str, Any]] = mapped_column(default=dict)
    total_score: Mapped[float] = mapped_column(Float, default=0)
    fatal: Mapped[bool] = mapped_column(Boolean, default=False)
    data_confidence: Mapped[float] = mapped_column(Float, default=0)
    explanation: Mapped[dict[str, Any]] = mapped_column(default=dict)
    rank: Mapped[int | None] = mapped_column(Integer)


# --------------------------------------------------------------------------- acquisition workflow

# Off-market CRM stages (owner outreach pipeline)
LEAD_STAGES = [
    "discovered",
    "feasibility_screened",
    "owner_verified",
    "draft_prepared",
    "awaiting_approval",
    "contacted",
    "awaiting_response",
    "interested",
    "feasibility_discussion",
    "negotiation",
    "under_offer",
    "completed",
    "closed",
]

OWNERSHIP_STATUSES = [
    "unknown",  # nothing matched yet
    "inspire_matched",  # registered title extent found; owner unknown
    "title_purchase_needed",  # owner identity needs an HMLR official copy
    "inferred_ccod",  # company owner suggested by HMLR CCOD address match (unconfirmed)
    "verified",  # owner confirmed from an official title register you supplied
]

PIPELINE_STAGES = [
    "shortlist",
    "research",
    "contact",
    "inspect",
    "negotiate",
    "survey",
    "under_offer",
    "acquired",
    "dropped",
]


class Lead(Base):
    """Off-market lead. Never implies availability without availability_evidence."""

    __tablename__ = "leads"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), unique=True)
    stage: Mapped[str] = mapped_column(String(40), default="discovered")
    signals: Mapped[list[Any]] = mapped_column(default=list)  # why it was flagged
    owner: Mapped[dict[str, Any]] = mapped_column(default=dict)  # verified fields only, each with source
    availability_evidence: Mapped[list[Any]] = mapped_column(default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    # off-market acquisition agent
    opportunity_type: Mapped[str] = mapped_column(String(40), default="register_site")
    ownership_status: Mapped[str] = mapped_column(String(30), default="unknown")
    analysis: Mapped[dict[str, Any]] = mapped_column(default=dict)  # subdivision geometry, access, character...
    acq_score: Mapped[dict[str, Any]] = mapped_column(default=dict)
    acq_score_total: Mapped[float | None] = mapped_column(Float)
    acq_confidence: Mapped[float | None] = mapped_column(Float)
    acq_fatal: Mapped[bool] = mapped_column(Boolean, default=False)
    benefits: Mapped[list[Any]] = mapped_column(default=list)
    contact_method: Mapped[str | None] = mapped_column(String(30))
    next_action: Mapped[str | None] = mapped_column(Text)
    decline_reason: Mapped[str | None] = mapped_column(String(40))
    closed_reason: Mapped[str | None] = mapped_column(Text)
    stage_changed_at: Mapped[dt.datetime | None] = mapped_column(TS)
    last_contact_at: Mapped[dt.datetime | None] = mapped_column(TS)
    discovered_run_id: Mapped[int | None] = mapped_column(Integer)


class LeadEvent(Base):
    __tablename__ = "lead_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    from_stage: Mapped[str | None] = mapped_column(String(40))
    to_stage: Mapped[str] = mapped_column(String(40))
    at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    actor: Mapped[str] = mapped_column(String(320))
    note: Mapped[str | None] = mapped_column(Text)


class PipelineItem(Base):
    __tablename__ = "pipeline_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), unique=True)
    stage: Mapped[str] = mapped_column(String(30), default="shortlist")
    notes: Mapped[str | None] = mapped_column(Text)
    checklist: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class Outreach(Base):
    """Owner-contact draft. Sending is impossible until a human approves it."""

    __tablename__ = "outreach"
    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(10))  # post|email
    recipient_type: Mapped[str] = mapped_column(String(20))  # individual|company|public_body|unknown
    recipient_name: Mapped[str | None] = mapped_column(String(300))
    recipient_address: Mapped[str | None] = mapped_column(Text)
    recipient_email: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    # draft | needs_changes | needs_compliance_review | awaiting_approval | approved | sending | sent |
    # posted | delivery_unknown | failed | rejected | withdrawn | superseded
    status: Mapped[str] = mapped_column(String(30), default="draft")
    compliance: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    approved_at: Mapped[dt.datetime | None] = mapped_column(TS)
    approved_by: Mapped[str | None] = mapped_column(String(320))
    sent_at: Mapped[dt.datetime | None] = mapped_column(TS)
    # agent extensions
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="initial")  # initial | follow_up | reply
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("outreach.id", ondelete="SET NULL"))
    persona: Mapped[str | None] = mapped_column(String(20))
    generator: Mapped[str | None] = mapped_column(String(20))  # ai | composer | manual
    generator_meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
    footer: Mapped[str | None] = mapped_column(Text)  # identity, data source, privacy & opt-out notice (hashed with the body)
    word_count: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    recipient_hash: Mapped[str | None] = mapped_column(String(64))
    approved_hash: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(30))  # gmail | outlook | stannp | manual_post
    provider_message_id: Mapped[str | None] = mapped_column(String(300), unique=True)
    provider_thread_id: Mapped[str | None] = mapped_column(String(300))
    provider_draft_id: Mapped[str | None] = mapped_column(String(300))
    delivery_status: Mapped[str | None] = mapped_column(String(30))
    delivery_detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
    pdf: Mapped[bytes | None] = mapped_column(LargeBinary)
    postage_estimate_gbp: Mapped[float | None] = mapped_column(Float)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    send_attempted_at: Mapped[dt.datetime | None] = mapped_column(TS)
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    review_reason: Mapped[str | None] = mapped_column(Text)


class Suppression(Base):
    """Do-not-contact list (objections / opt-outs). Matching is by normalised name+address hash."""

    __tablename__ = "suppressions"
    id: Mapped[int] = mapped_column(primary_key=True)
    match_hash: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(20), default="name_address")  # name_address | address | email
    label: Mapped[str | None] = mapped_column(String(300))
    reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(40))  # user | reply | complaint
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    signal: Mapped[int] = mapped_column(Integer)  # +1 like, -1 dislike
    reason: Mapped[str | None] = mapped_column(String(60))  # tag e.g. 'too_urban', 'great_landscape'
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)


class Override(Base):
    __tablename__ = "overrides"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(60))  # e.g. score.planning, planning.class, flag.<code>, cost.<key>
    value: Mapped[dict[str, Any]] = mapped_column()
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    created_by: Mapped[str] = mapped_column(String(320))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


# --------------------------------------------------------------------------- jobs & runs


class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    cron: Mapped[str] = mapped_column(String(60))
    timezone: Mapped[str] = mapped_column(String(60), default="Europe/London")
    job_type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_enqueued_for: Mapped[dt.datetime | None] = mapped_column(TS)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "run_after"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|succeeded|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    locked_by: Mapped[str | None] = mapped_column(String(120))
    locked_at: Mapped[dt.datetime | None] = mapped_column(TS)
    heartbeat_at: Mapped[dt.datetime | None] = mapped_column(TS)
    started_at: Mapped[dt.datetime | None] = mapped_column(TS)
    finished_at: Mapped[dt.datetime | None] = mapped_column(TS)
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey("schedules.id", ondelete="SET NULL"))
    scheduled_for: Mapped[dt.datetime | None] = mapped_column(TS)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)


class Run(Base):
    """One execution of the sourcing workflow (scheduled or manual)."""

    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(40))
    trigger: Mapped[str] = mapped_column(String(20))  # schedule|manual|cli
    started_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(TS)
    # running | succeeded | partial (completed, some sources failed) | failed
    status: Mapped[str] = mapped_column(String(20), default="running")
    steps: Mapped[list[Any]] = mapped_column(default=list)
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class SourceRun(Base):
    __tablename__ = "source_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    started_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(TS)
    # ok | failed | blocked | not_configured | skipped | disabled
    status: Mapped[str] = mapped_column(String(20))
    records_fetched: Mapped[int] = mapped_column(Integer, default=0)
    records_new: Mapped[int] = mapped_column(Integer, default=0)
    records_changed: Mapped[int] = mapped_column(Integer, default=0)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_class: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    data_date: Mapped[dt.date | None] = mapped_column(Date)


class Digest(Base):
    __tablename__ = "digests"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    digest_date: Mapped[dt.date] = mapped_column(Date, index=True)
    items: Mapped[list[Any]] = mapped_column(default=list)
    briefing: Mapped[dict[str, Any]] = mapped_column(default=dict)  # off-market acquisition briefing (real counts)
    body_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    # not_configured | sent | failed | nothing_to_send
    delivery_status: Mapped[str] = mapped_column(String(20), default="not_configured")
    delivery_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[dt.datetime | None] = mapped_column(TS)


class CostRate(Base):
    """Date-stamped, sourced unit cost assumption (editable)."""

    __tablename__ = "cost_rates"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    category: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(30))
    low: Mapped[float] = mapped_column(Float)
    base: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    basis: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    source_date: Mapped[dt.date] = mapped_column(Date)
    regional: Mapped[bool] = mapped_column(Boolean, default=True)
    user_override: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class Expense(Base):
    __tablename__ = "expenses"
    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(60))  # api|title_register|survey|other
    description: Mapped[str] = mapped_column(Text)
    amount_gbp: Mapped[float] = mapped_column(Float)
    incurred_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))


class ApiCache(Base):
    """Response cache for polite use of public APIs (keyed by request fingerprint)."""

    __tablename__ = "api_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow, server_default=func.now())
    status: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict[str, Any]] = mapped_column()


class RefFeature(Base):
    """Reference geodata refreshed by the daily run (rail stations, settlements...)."""

    __tablename__ = "ref_features"
    __table_args__ = (UniqueConstraint("dataset", "ref", name="uq_ref_feature"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset: Mapped[str] = mapped_column(String(40), index=True)
    ref: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(300))
    geom = mapped_column(Geometry("GEOMETRY", srid=4326, spatial_index=True), nullable=False)
    props: Mapped[dict[str, Any]] = mapped_column(default=dict)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"))
    fetched_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)


class IngestInbox(Base):
    """Listings received (e.g. from alert emails) that could not be placed on the map yet."""

    __tablename__ = "ingest_inbox"
    __table_args__ = (UniqueConstraint("source_id", "external_ref", name="uq_inbox_source_ref"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    external_ref: Mapped[str] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(1000))
    title: Mapped[str | None] = mapped_column(String(500))
    asking_price: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    received_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    resolved_site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)


class ProcessedMessage(Base):
    """Alert emails already ingested (by Message-ID hash) so re-runs are idempotent."""

    __tablename__ = "processed_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    message_hash: Mapped[str] = mapped_column(String(64), unique=True)
    source_id: Mapped[str] = mapped_column(String(80))
    received_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    listings_found: Mapped[int] = mapped_column(Integer, default=0)


class ServiceHeartbeat(Base):
    """Liveness of background processes (scheduler, workers) for the admin screen."""

    __tablename__ = "service_heartbeats"
    name: Mapped[str] = mapped_column(String(120), primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)



class Contact(Base):
    """A proposed recipient. Created only from authoritative sources (title register, Companies
    House, the owner's own correspondence) or entered by you with a stated source."""

    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    # individual | company | public_body | charity | personal_representatives | trustees | unknown
    kind: Mapped[str] = mapped_column(String(30), default="unknown")
    name: Mapped[str] = mapped_column(String(300))
    salutation: Mapped[str | None] = mapped_column(String(120))
    address_lines: Mapped[list[Any]] = mapped_column(default=list)
    postcode: Mapped[str | None] = mapped_column(String(10))
    country: Mapped[str] = mapped_column(String(60), default="United Kingdom")
    email: Mapped[str | None] = mapped_column(String(320))
    # null | owner_initiated | consent_recorded | corporate_subscriber
    email_basis: Mapped[str | None] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(String(40))  # title_register | companies_house | ccod | owner_reply | manual
    source_ref: Mapped[str | None] = mapped_column(String(300))
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("title_documents.id", ondelete="SET NULL"))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_by: Mapped[str | None] = mapped_column(String(320))
    verified_at: Mapped[dt.datetime | None] = mapped_column(TS)
    retention_until: Mapped[dt.date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)


class TitleDocument(Base):
    """An HM Land Registry official copy you obtained (PDF), with deterministic extraction."""

    __tablename__ = "title_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    text: Mapped[str | None] = mapped_column(Text)
    title_number: Mapped[str | None] = mapped_column(String(20))
    tenure: Mapped[str | None] = mapped_column(String(20))
    extracted: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="uploaded")  # uploaded | parsed | verified | rejected
    source_note: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    uploaded_by: Mapped[str | None] = mapped_column(String(320))
    verified_at: Mapped[dt.datetime | None] = mapped_column(TS)
    verified_by: Mapped[str | None] = mapped_column(String(320))
    retention_until: Mapped[dt.date | None] = mapped_column(Date)


class InboundMessage(Base):
    """A reply from an owner (synced from a connected mailbox or logged by you for letters)."""

    __tablename__ = "inbound_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    outreach_id: Mapped[int | None] = mapped_column(ForeignKey("outreach.id", ondelete="SET NULL"))
    channel: Mapped[str] = mapped_column(String(20))  # email | post | phone_note
    from_name: Mapped[str | None] = mapped_column(String(300))
    from_address: Mapped[str | None] = mapped_column(String(500))
    subject: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    received_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    provider_message_id: Mapped[str | None] = mapped_column(String(300), unique=True)
    # interested | possibly_interested | wants_information | requests_price | not_interested |
    # requests_no_contact | unknown
    category: Mapped[str] = mapped_column(String(30), default="unknown")
    category_source: Mapped[str] = mapped_column(String(20), default="rules")  # rules | ai | user
    category_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[str | None] = mapped_column(Text)
    decline_reason: Mapped[str | None] = mapped_column(String(40))
    handled: Mapped[bool] = mapped_column(Boolean, default=False)
    suggested_reply_id: Mapped[int | None] = mapped_column(ForeignKey("outreach.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)


class MailAccount(Base):
    """A connected Gmail / Microsoft 365 mailbox (OAuth tokens encrypted at rest)."""

    __tablename__ = "mail_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))  # google | microsoft
    email_address: Mapped[str] = mapped_column(String(320))
    token_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    scopes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="connected")
    connected_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    last_sync_at: Mapped[dt.datetime | None] = mapped_column(TS)
    last_error: Mapped[str | None] = mapped_column(Text)
    sync_cursor: Mapped[dict[str, Any]] = mapped_column(default=dict)


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state: Mapped[str] = mapped_column(String(80), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    verifier_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)

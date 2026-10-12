"""Source registry: every adapter PLOT knows about, synced into the `sources` table."""

from __future__ import annotations

from sqlalchemy.orm import Session

from plot.models import Source
from plot.offmarket.agent import OffMarketDiscovery
from plot.security.crypto import get_secret
from plot.sources.arcgis import ArcGisLayers
from plot.sources.base import DiscoverySource, EnrichmentSource, SourceAdapter, SourceMeta
from plot.sources.bgs import BgsGeology
from plot.sources.blocked import BLOCKED_SOURCES
from plot.sources.brownfield_csv import DigitalLandSnapshots, LpaRegisterCsv
from plot.sources.ea_flood import EAFloodRisk
from plot.sources.email_alerts import EmailAlertIngest
from plot.sources.keyed import EpcFloorAreas, OfcomCoverage
from plot.sources.land_registry import LandRegistryPricePaid
from plot.sources.naptan import NaptanRailStations
from plot.sources.planning_data import PlanningDataApplications, PlanningDataBrownfield, PlanningDataConstraints, PlanningDataTitleBoundary
from plot.sources.postcodes import PostcodesIo

DISCOVERY: list[type[DiscoverySource]] = [
    PlanningDataBrownfield,
    LpaRegisterCsv,
    DigitalLandSnapshots,
    EmailAlertIngest,
    NaptanRailStations,
    OffMarketDiscovery,
    *BLOCKED_SOURCES,
]

# order matters: postcode first (cheap, gives LPA/district), then constraints...
ENRICHMENT: list[type[EnrichmentSource]] = [
    PostcodesIo,
    PlanningDataConstraints,
    PlanningDataTitleBoundary,
    EAFloodRisk,
    BgsGeology,
    PlanningDataApplications,
    LandRegistryPricePaid,
    EpcFloorAreas,
    OfcomCoverage,
    ArcGisLayers,
]

# Non-scheduled origins (records created through the UI/API)
PSEUDO_SOURCES = [
    SourceMeta(
        id="manual",
        name="Manual import (your own listings / sites)",
        category="on_market",
        kind="manual",
        licence="User-supplied",
        attribution="Entered by you",
        notes="Paste a listing URL and its headline facts, or drop a pin for an off-market site.",
    ),
    SourceMeta(
        id="email.upload",
        name="Uploaded alert emails (.eml)",
        category="on_market",
        kind="email",
        licence="Your own received emails",
        attribution="Quoted from uploaded alert email",
    ),
]

SECRET_KEYS = {
    "imap_host": "Alert mailbox IMAP host",
    "imap_user": "Alert mailbox username",
    "imap_password": "Alert mailbox password / app password",
    "smtp_host": "SMTP host for the daily digest",
    "smtp_port": "SMTP port (465 SSL or 587 STARTTLS)",
    "smtp_user": "SMTP username",
    "smtp_password": "SMTP password",
    "smtp_from": "Digest sender address",
    "notify_webhook_url": "Webhook URL for app notifications (e.g. ntfy topic)",
    "anthropic_api_key": "Anthropic API key (optional AI extraction)",
    "epc_email": "EPC register account email",
    "epc_api_key": "EPC register API key",
    "ofcom_api_key": "Ofcom API subscription key",
    "google_client_id": "Google OAuth client ID (Gmail connection)",
    "google_client_secret": "Google OAuth client secret",
    "ms_client_id": "Microsoft Entra app (client) ID (Outlook connection)",
    "ms_client_secret": "Microsoft Entra client secret",
    "stannp_api_key": "Stannp API key (print-and-post letters)",
    "companies_house_api_key": "Companies House API key (company proprietors)",
}


def all_adapters() -> list[type[SourceAdapter]]:
    return [*DISCOVERY, *ENRICHMENT]


def adapter_by_id(source_id: str) -> type[SourceAdapter] | None:
    return next((a for a in all_adapters() if a.meta.id == source_id), None)


def sync_sources(db: Session) -> None:
    metas = [a.meta for a in all_adapters()] + PSEUDO_SOURCES
    for meta in metas:
        row = db.get(Source, meta.id)
        if row is None:
            row = Source(id=meta.id, enabled=meta.default_enabled, config=dict(meta.default_config))
            db.add(row)
        else:
            merged = dict(meta.default_config)
            merged.update(row.config or {})
            row.config = merged
        row.name = meta.name
        row.category = meta.category
        row.kind = meta.kind
        row.licence = meta.licence
        row.attribution = meta.attribution
        row.terms_url = meta.terms_url
        row.docs_url = meta.docs_url
        row.notes = meta.notes
    db.flush()


def load_secrets(db: Session) -> dict[str, str | None]:
    import os

    out = {k: get_secret(db, k) for k in SECRET_KEYS}
    if not out.get("anthropic_api_key") and os.environ.get("ANTHROPIC_API_KEY"):
        out["anthropic_api_key"] = os.environ["ANTHROPIC_API_KEY"]
    return out

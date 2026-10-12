"""Ownership research: official copies of the register, your verification, Companies House.

Verified ownership comes only from an HM Land Registry official copy that you uploaded and
confirmed (or from the owner themselves). Map-based inferences stay labelled as inferences.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from plot.models import AuditLog, Contact, Expense, Lead, LeadEvent, Site, TitleDocument, utcnow
from plot.offmarket import title as T
from plot.preferences import load_preferences
from plot.sources.base import SourceNotConfigured
from plot.sources.http import PublicHttp

CH_BASE = "https://api.company-information.service.gov.uk"
TITLE_FEE_GBP = 7.0  # HM Land Registry official copy of the register (online), Oct 2026 — editable as an expense


class OwnershipError(ValueError):
    pass


def retention_date(db: Session) -> dt.date:
    prefs, _ = load_preferences(db)
    return (utcnow() + dt.timedelta(days=30 * prefs.outreach.retention_months)).date()


def upload_title(db: Session, lead: Lead, filename: str, content: bytes, actor: str, *, record_fee: bool = True) -> TitleDocument:
    if not content:
        raise OwnershipError("empty file")
    sha = hashlib.sha256(content).hexdigest()
    existing = db.scalar(select(TitleDocument).where(TitleDocument.sha256 == sha))
    if existing is not None:
        if existing.lead_id != lead.id:
            raise OwnershipError(f"this document is already attached to lead #{existing.lead_id}")
        return existing
    if content[:5] == b"%PDF-":
        try:
            text = T.pdf_text(content)
        except Exception as exc:  # noqa: BLE001
            raise OwnershipError(f"could not read the PDF: {exc}") from exc
    else:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OwnershipError("upload the official copy as a PDF (or plain text)") from exc
    extracted = T.parse_register(text)
    doc = TitleDocument(
        lead_id=lead.id,
        site_id=lead.site_id,
        filename=filename[:300],
        sha256=sha,
        content=content,
        text=text,
        title_number=extracted.get("title_number"),
        tenure=extracted.get("tenure"),
        extracted=extracted,
        status="parsed" if extracted.get("proprietors") else "uploaded",
        source_note="HM Land Registry official copy of the register, uploaded by you",
        uploaded_by=actor,
        retention_until=retention_date(db),
    )
    db.add(doc)
    if record_fee:
        db.add(Expense(category="title_register", description=f"Official copy {extracted.get('title_number') or filename}", amount_gbp=TITLE_FEE_GBP, site_id=lead.site_id))
    db.add(AuditLog(actor=actor, action="title.uploaded", entity="lead", entity_id=str(lead.id), detail={"sha256": sha, "title_number": extracted.get("title_number")}))
    db.flush()
    return doc


def verify_title(db: Session, doc: TitleDocument, actor: str, *, confirm_matches_land: bool, confirm_details_checked: bool, edits: dict[str, Any] | None = None) -> Contact:
    """You confirm the register covers this land and the extracted owner details are right."""
    if not (confirm_matches_land and confirm_details_checked):
        raise OwnershipError("confirm both that the register covers this land and that you have checked the owner details against it")
    if doc.status == "rejected":
        raise OwnershipError("this document was rejected")
    lead = db.get(Lead, doc.lead_id)
    ex = dict(doc.extracted or {})
    e = edits or {}
    name = (e.get("name") or " and ".join(p["name"] for p in ex.get("proprietors", []))).strip()
    if not name:
        raise OwnershipError("no proprietor name — enter it exactly as shown on the register")
    kind = e.get("kind") or T.contact_kind(ex)
    lines = e.get("address_lines") or T.address_lines(ex.get("proprietor_address"))
    if not lines:
        raise OwnershipError("no address for the proprietor — enter it exactly as shown on the register")
    postcode = e.get("postcode") or ex.get("proprietor_postcode")
    # the postcode is stored separately; never repeat it as the last address line
    if lines and postcode and re.sub(r"\s+", "", lines[-1]).upper() == re.sub(r"\s+", "", postcode).upper():
        lines = lines[:-1]
    salutation = e.get("salutation") if e.get("salutation") is not None else T.default_salutation(ex, kind)
    company_number = next((p.get("company_number") for p in ex.get("proprietors", []) if p.get("company_number")), None)
    # supersede an earlier verified register contact for this lead
    for old in db.scalars(select(Contact).where(Contact.lead_id == lead.id, Contact.source == "title_register", Contact.verified.is_(True))).all():
        old.verified = False
        old.notes = (old.notes or "") + f" Superseded by title document #{doc.id} on {utcnow():%d %b %Y}."
    now = utcnow()
    contact = Contact(
        lead_id=lead.id,
        kind=kind,
        name=name[:300],
        salutation=salutation,
        address_lines=lines,
        postcode=postcode,
        source="title_register",
        source_ref=f"{doc.title_number or 'title'}: {ex.get('proprietor_entry') or ''}"[:300],
        source_document_id=doc.id,
        verified=True,
        verified_by=actor,
        verified_at=now,
        retention_until=retention_date(db),
        notes=(f"Company number {company_number}. " if company_number else "") + ("Edited by you during verification. " if _edited(ex, name, lines, postcode, kind, e) else ""),
    )
    db.add(contact)
    doc.status = "verified"
    doc.verified_at = now
    doc.verified_by = actor
    lead.ownership_status = "verified"
    site = db.get(Site, lead.site_id)
    if doc.title_number and doc.title_number not in (site.title_numbers or []):
        site.title_numbers = list(site.title_numbers or []) + [doc.title_number]
    lead.owner = {"name": name, "kind": kind, "source": "HM Land Registry official copy", "title_number": doc.title_number, "verified_by": actor, "verified_at": now.isoformat()}
    if lead.stage in ("discovered", "feasibility_screened"):
        db.add(LeadEvent(lead_id=lead.id, from_stage=lead.stage, to_stage="owner_verified", actor=actor, note=f"Owner verified from official copy {doc.title_number or ''} ({kind.replace('_', ' ')})"))
        lead.stage = "owner_verified"
        lead.stage_changed_at = now
    db.add(AuditLog(actor=actor, action="owner.verified", entity="lead", entity_id=str(lead.id), detail={"title_document": doc.id, "kind": kind, "edited": bool(edits)}))
    db.flush()
    return contact


def _edited(ex: dict[str, Any], name: str, lines: list[str], postcode: str | None, kind: str, e: dict[str, Any]) -> bool:
    """True when what you confirmed differs from what was read off the register."""
    norm = lambda v: " ".join(str(v or "").split()).lower()  # noqa: E731
    reg_name = " and ".join(p["name"] for p in ex.get("proprietors", []))
    reg_lines = T.address_lines(ex.get("proprietor_address"))
    return norm(name) != norm(reg_name) or [norm(x) for x in lines] != [norm(x) for x in reg_lines] or norm(postcode) != norm(ex.get("proprietor_postcode")) or kind != T.contact_kind(ex)


def reject_title(db: Session, doc: TitleDocument, actor: str, reason: str) -> None:
    doc.status = "rejected"
    doc.source_note = (doc.source_note or "") + f" Rejected by {actor}: {reason}"
    db.add(AuditLog(actor=actor, action="title.rejected", entity="lead", entity_id=str(doc.lead_id), detail={"title_document": doc.id, "reason": reason}))
    db.flush()


def companies_house_profile(db: Session, api_key: str | None, number: str) -> dict[str, Any]:
    """Registered office and status for a company proprietor (Companies House public data API)."""
    if not api_key:
        raise SourceNotConfigured("Companies House API key not set (Admin → Integrations → companies_house_api_key)")
    with PublicHttp(db, timeout=30, auth=(api_key, "")) as http:
        data = http.get_json(f"{CH_BASE}/company/{number}", cache_ttl=dt.timedelta(days=7))
    ro = data.get("registered_office_address") or {}
    return {
        "company_number": data.get("company_number"),
        "name": data.get("company_name"),
        "status": data.get("company_status"),
        "type": data.get("type"),
        "registered_office": [x for x in (ro.get("address_line_1"), ro.get("address_line_2"), ro.get("locality"), ro.get("region"), ro.get("postal_code")) if x],
        "source": "Companies House public data API (OGL)",
        "retrieved_at": utcnow().isoformat(),
    }

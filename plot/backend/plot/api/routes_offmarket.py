"""Off-Market CRM: prospects, ownership verification, drafts, approval, delivery and replies.

Every outbound action is individual: there is no bulk approve or bulk send endpoint.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from plot.api.deps import audit, current_user
from plot.api.serialize import rows_for
from plot.config import get_settings
from plot.db import get_db
from plot.models import LEAD_STAGES, Assessment, Contact, InboundMessage, Lead, LeadEvent, MailAccount, Outreach, Site, Suppression, TitleDocument, User, utcnow
from plot.offmarket import agent, ownership
from plot.offmarket.score import TYPE_LABEL
from plot.outreach import compliance as C
from plot.outreach import delivery, drafting, mail, replies
from plot.preferences import load_preferences

router = APIRouter()
MANUAL_STAGES = ("feasibility_discussion", "negotiation", "under_offer", "completed", "closed")
POSITIVE = ("interested", "possibly_interested", "wants_information", "requests_price")


def _err(exc: Exception, code: int = 422):
    raise HTTPException(code, str(exc)) from exc


def _lead(db: Session, lead_id: int) -> Lead:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "lead not found")
    return lead


def _outreach(db: Session, oid: int) -> Outreach:
    o = db.get(Outreach, oid)
    if o is None:
        raise HTTPException(404, "message not found")
    return o


# --------------------------------------------------------------------------- serialisers


def outreach_out(o: Outreach, *, full: bool = True) -> dict[str, Any]:
    d = {
        "id": o.id,
        "lead_id": o.lead_id,
        "contact_id": o.contact_id,
        "kind": o.kind,
        "channel": o.channel,
        "status": o.status,
        "recipient_type": o.recipient_type,
        "recipient_name": o.recipient_name,
        "recipient_address": o.recipient_address,
        "recipient_email": o.recipient_email,
        "recipient_hash": o.recipient_hash,
        "subject": o.subject,
        "word_count": o.word_count,
        "persona": o.persona,
        "generator": o.generator,
        "reference": (o.generator_meta or {}).get("reference"),
        "created_at": o.created_at.isoformat(),
        "approved_at": o.approved_at and o.approved_at.isoformat(),
        "approved_by": o.approved_by,
        "sent_at": o.sent_at and o.sent_at.isoformat(),
        "provider": o.provider,
        "provider_message_id": o.provider_message_id,
        "delivery_status": o.delivery_status,
        "postage_estimate_gbp": o.postage_estimate_gbp,
        "review_reason": o.review_reason,
        "rejected_reason": o.rejected_reason,
    }
    if full:
        d |= {
            "body": o.body,
            "footer": o.footer,
            "compliance": o.compliance,
            "generator_meta": {k: v for k, v in (o.generator_meta or {}).items() if k in ("note", "instructions", "applied_instructions", "edited_by_you", "issues", "model", "auto", "include_price", "price_confirmed")},
            "delivery_detail": o.delivery_detail,
            "has_final_pdf": o.pdf is not None,
        }
    return d


def contact_out(c: Contact) -> dict[str, Any]:
    return {"id": c.id, "kind": c.kind, "name": c.name, "salutation": c.salutation, "address_lines": c.address_lines, "postcode": c.postcode, "email": c.email, "email_basis": c.email_basis, "source": c.source, "source_ref": c.source_ref, "source_document_id": c.source_document_id, "verified": c.verified, "verified_by": c.verified_by, "verified_at": c.verified_at and c.verified_at.isoformat(), "retention_until": c.retention_until and c.retention_until.isoformat(), "notes": c.notes}


def title_out(d: TitleDocument) -> dict[str, Any]:
    return {"id": d.id, "filename": d.filename, "title_number": d.title_number, "tenure": d.tenure, "status": d.status, "extracted": d.extracted, "uploaded_at": d.uploaded_at.isoformat(), "uploaded_by": d.uploaded_by, "verified_at": d.verified_at and d.verified_at.isoformat(), "verified_by": d.verified_by, "source_note": d.source_note, "text_excerpt": (d.text or "")[:6000]}


def inbound_out(m: InboundMessage) -> dict[str, Any]:
    return {"id": m.id, "lead_id": m.lead_id, "outreach_id": m.outreach_id, "channel": m.channel, "from_name": m.from_name, "from_address": m.from_address, "subject": m.subject, "body": m.body, "received_at": m.received_at.isoformat(), "category": m.category, "category_source": m.category_source, "category_confidence": m.category_confidence, "evidence": m.evidence, "decline_reason": m.decline_reason, "handled": m.handled, "suggested_reply_id": m.suggested_reply_id}


def lead_summary(db: Session, lead: Lead, row: dict[str, Any] | None, drafts: dict[int, Outreach], unhandled: set[int]) -> dict[str, Any]:
    s = lead.acq_score or {}
    an = lead.analysis or {}
    d = drafts.get(lead.id)
    return {
        "id": lead.id,
        "site_id": lead.site_id,
        "stage": lead.stage,
        "type": lead.opportunity_type,
        "type_label": TYPE_LABEL.get(lead.opportunity_type, lead.opportunity_type),
        "ownership_status": lead.ownership_status,
        "score": lead.acq_score_total,
        "components": {k: {"score": v.get("score"), "max": v.get("max")} for k, v in (s.get("components") or {}).items()},
        "confidence": s.get("confidence"),
        "fatal": lead.acq_fatal,
        "flags": s.get("flags", []),
        "benefits": lead.benefits,
        "contact_method": lead.contact_method,
        "next_action": lead.next_action,
        "access": an.get("access"),
        "metrics": an.get("metrics"),
        "constraints": an.get("constraints", []),
        "title_id": an.get("title_id"),
        "area": an.get("area"),
        "geometry": agent.lead_geometry(db, lead),
        "draft": {"id": d.id, "status": d.status, "channel": d.channel, "kind": d.kind} if d else None,
        "reply_waiting": lead.id in unhandled,
        "decline_reason": lead.decline_reason,
        "stage_changed_at": lead.stage_changed_at and lead.stage_changed_at.isoformat(),
        "created_at": lead.created_at.isoformat(),
        "site": row,
    }


def _summaries(db: Session, leads: list[Lead]) -> list[dict[str, Any]]:
    if not leads:
        return []
    sites = db.scalars(select(Site).where(Site.id.in_([l.site_id for l in leads]))).all()
    rows = {r["id"]: r for r in rows_for(db, sites)}
    drafts: dict[int, Outreach] = {}
    for o in db.scalars(select(Outreach).where(Outreach.lead_id.in_([l.id for l in leads]), Outreach.status.in_(C.PENDING + ("delivery_unknown",))).order_by(Outreach.created_at)).all():
        drafts[o.lead_id] = o
    unhandled = set(db.scalars(select(InboundMessage.lead_id).where(InboundMessage.handled.is_(False))).all())
    return [lead_summary(db, l, rows.get(l.site_id), drafts, unhandled) for l in leads]


# --------------------------------------------------------------------------- dashboard & lists


@router.get("/offmarket/dashboard")
def dashboard(db: Session = Depends(get_db)) -> dict:
    now = utcnow()
    week = now - dt.timedelta(days=7)
    leads = db.scalars(select(Lead)).all()
    stage_counts = {s: 0 for s in LEAD_STAGES}
    for l in leads:
        stage_counts[l.stage] = stage_counts.get(l.stage, 0) + 1
    sent = db.scalars(select(Outreach).where(Outreach.status.in_(("sent", "posted")))).all()
    contacted_leads = {o.lead_id for o in sent if o.kind == "initial"}
    inbound = db.scalars(select(InboundMessage)).all()
    responded = {m.lead_id for m in inbound if m.lead_id in contacted_leads}
    positive = {m.lead_id for m in inbound if m.lead_id in contacted_leads and m.category in POSITIVE}
    prefs, _ = load_preferences(db)
    followups = [l.id for l in leads if (due := agent.followup_due(db, l, prefs.outreach.follow_up_after_days, prefs.outreach.max_follow_ups)) and due <= now.date()]
    active = [l for l in leads if l.stage not in ("closed", "completed") and not l.acq_fatal]
    strongest = sorted([l for l in active if l.acq_score_total is not None], key=lambda l: -(l.acq_score_total or 0))[:5]
    values = []
    for l in active:
        a = db.scalar(select(Assessment).where(Assessment.site_id == l.site_id, Assessment.is_current.is_(True)))
        if a is not None and (a.residual or {}).get("max_recommended_offer") is not None:
            values.append({"lead_id": l.id, "max_viable_gbp": a.residual["max_recommended_offer"], "prudent_gbp": a.residual.get("prudent_offer"), "stage": l.stage})
    values.sort(key=lambda v: -(v["max_viable_gbp"] or 0))
    digest_brief = agent.briefing(db, now - dt.timedelta(hours=24))
    return {
        "kpis": {
            "viable_prospects": len(active),
            "new_this_week": sum(1 for l in leads if l.created_at >= week),
            "drafts_awaiting_approval": db.scalar(select(func.count()).select_from(Outreach).where(Outreach.status == "awaiting_approval")),
            "drafts_needing_review": db.scalar(select(func.count()).select_from(Outreach).where(Outreach.status.in_(("needs_changes", "needs_compliance_review")))),
            "letters_posted": sum(1 for o in sent if o.channel == "post"),
            "emails_sent": sum(1 for o in sent if o.channel == "email"),
            "owners_contacted": len(contacted_leads),
            "response_rate": (len(responded) / len(contacted_leads)) if contacted_leads else None,
            "positive_rate": (len(positive) / len(contacted_leads)) if contacted_leads else None,
            "replies_to_handle": sum(1 for m in inbound if not m.handled),
            "follow_ups_due": len(followups),
            "active_negotiations": sum(1 for l in leads if l.stage in ("negotiation", "under_offer")),
        },
        "stages": [{"stage": s, "count": stage_counts.get(s, 0)} for s in LEAD_STAGES],
        "strongest": _summaries(db, strongest),
        "land_values": values[:10],
        "declines": replies.decline_feedback(db),
        "briefing": digest_brief,
        "sandbox": get_settings().is_sandbox,
        "real_activity_only": True,
    }


@router.get("/offmarket/leads")
def list_leads(stage: str | None = None, type: str | None = None, include_closed: bool = False, db: Session = Depends(get_db)) -> dict:
    q = select(Lead)
    if stage:
        q = q.where(Lead.stage == stage)
    elif not include_closed:
        q = q.where(Lead.stage != "closed")
    if type:
        q = q.where(Lead.opportunity_type == type)
    leads = db.scalars(q.order_by(Lead.acq_fatal, Lead.acq_score_total.desc().nullslast())).all()
    return {"stages": LEAD_STAGES, "items": _summaries(db, leads), "types": TYPE_LABEL}


@router.get("/offmarket/leads/{lead_id}")
def lead_detail(lead_id: int, db: Session = Depends(get_db)) -> dict:
    lead = _lead(db, lead_id)
    summary = _summaries(db, [lead])[0]
    a = db.scalar(select(Assessment).where(Assessment.site_id == lead.site_id, Assessment.is_current.is_(True)))
    docs = db.scalars(select(TitleDocument).where(TitleDocument.lead_id == lead.id).order_by(TitleDocument.uploaded_at.desc())).all()
    contacts = db.scalars(select(Contact).where(Contact.lead_id == lead.id).order_by(Contact.id.desc())).all()
    msgs = db.scalars(select(Outreach).where(Outreach.lead_id == lead.id).order_by(Outreach.created_at.desc())).all()
    inbound = db.scalars(select(InboundMessage).where(InboundMessage.lead_id == lead.id).order_by(InboundMessage.received_at.desc())).all()
    events = db.scalars(select(LeadEvent).where(LeadEvent.lead_id == lead.id).order_by(LeadEvent.at.desc())).all()
    brief = replies.negotiation_brief(db, lead) if lead.stage in ("interested", "feasibility_discussion", "negotiation", "under_offer") else None
    prefs, _ = load_preferences(db)
    return summary | {
        "analysis": lead.analysis,
        "acq_score": lead.acq_score,
        "owner": lead.owner,
        "notes": lead.notes,
        "assessment": {
            "planning": {k: (a.planning or {}).get(k) for k in ("classification", "classification_label", "summary", "confidence")} if a else None,
            "residual": {k: (a.residual or {}).get(k) for k in ("residual_land_value", "max_recommended_offer", "prudent_offer", "budget", "market_value")} if a else None,
            "fit": (a.design or {}).get("fit") if a else None,
            "fatal_flags": (a.score or {}).get("fatal_flags", []) if a else [],
        },
        "titles": [title_out(d) for d in docs],
        "contacts": [contact_out(c) for c in contacts],
        "outreach": [outreach_out(o) for o in msgs],
        "inbound": [inbound_out(m) for m in inbound],
        "events": [{"at": e.at.isoformat(), "from": e.from_stage, "to": e.to_stage, "actor": e.actor, "note": e.note} for e in events],
        "brief": brief,
        "postal": {"provider": prefs.outreach.postal_provider, "test_mode": prefs.outreach.postal_provider_test_mode, "estimate_gbp": prefs.outreach.postage_estimate_gbp},
        "mailbox": (lambda m: {"provider": m.provider, "email": m.email_address} if m else None)(mail.connected_account(db)),
        "buyer_confirmed": prefs.buyer.facts_confirmed,
    }


class LeadPatch(BaseModel):
    stage: str | None = None
    note: str | None = Field(None, max_length=5000)
    closed_reason: str | None = Field(None, max_length=1000)
    decline_reason: str | None = None
    notes: str | None = Field(None, max_length=20_000)


@router.patch("/offmarket/leads/{lead_id}")
def patch_lead(lead_id: int, body: LeadPatch, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    lead = _lead(db, lead_id)
    if body.notes is not None:
        lead.notes = body.notes
    if body.stage and body.stage != lead.stage:
        if body.stage == "closed":
            if not (body.closed_reason or "").strip():
                raise HTTPException(422, "give a reason for closing")
            lead.closed_reason = body.closed_reason.strip()
            if body.decline_reason:
                lead.decline_reason = body.decline_reason
            for o in db.scalars(select(Outreach).where(Outreach.lead_id == lead.id, Outreach.status.in_(("draft", "needs_changes", "needs_compliance_review", "awaiting_approval", "approved")))).all():
                o.status = "withdrawn"
        elif lead.stage == "closed":
            if lead.decline_reason == "requests_no_contact":
                raise HTTPException(422, "the owner asked for no further contact; this lead cannot be reopened")
            if body.stage not in ("discovered", "feasibility_screened", "owner_verified"):
                raise HTTPException(422, "reopen to an early stage (contact stages follow from real correspondence)")
            lead.closed_reason = None
        elif body.stage not in MANUAL_STAGES:
            raise HTTPException(422, "this stage follows from evidence (verification, drafting, sending or a reply), not a manual move")
        elif LEAD_STAGES.index(body.stage) > LEAD_STAGES.index(lead.stage) + 1 and lead.stage != "awaiting_response":
            raise HTTPException(422, f"advance one stage at a time (next is {LEAD_STAGES[LEAD_STAGES.index(lead.stage) + 1]})")
        elif body.stage in ("feasibility_discussion", "negotiation", "under_offer", "completed") and LEAD_STAGES.index(lead.stage) < LEAD_STAGES.index("interested"):
            raise HTTPException(422, "record the owner's interest (their reply) first")
        agent.set_stage(db, lead, body.stage, user.email, body.note or body.closed_reason)
        audit(db, user, "lead.stage", "lead", lead.id, to=body.stage)
    agent.rescore_lead(db, lead)
    db.commit()
    return {"ok": True, "stage": lead.stage}


@router.post("/offmarket/leads/{lead_id}/rescore")
def rescore(lead_id: int, db: Session = Depends(get_db)) -> dict:
    lead = _lead(db, lead_id)
    agent.rescore_lead(db, lead)
    db.commit()
    return {"score": lead.acq_score_total, "confidence": (lead.acq_score or {}).get("confidence")}


@router.post("/offmarket/discover")
def discover_now(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    from plot.jobs.queue import enqueue

    job = enqueue(db, "offmarket_discovery", {"trigger": "manual"}, dedupe_key=f"offmarket_discovery:{utcnow():%Y%m%d%H%M}")
    audit(db, user, "offmarket.discover")
    db.commit()
    return {"queued": job is not None, "job_id": job.id if job else None}


# --------------------------------------------------------------------------- ownership


@router.post("/offmarket/leads/{lead_id}/titles")
async def upload_title(lead_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    lead = _lead(db, lead_id)
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "file too large (10 MB max)")
    try:
        doc = ownership.upload_title(db, lead, file.filename or "register.pdf", content, user.email)
    except ownership.OwnershipError as exc:
        _err(exc)
    db.commit()
    return title_out(doc)


@router.get("/offmarket/titles/{doc_id}/file")
def title_file(doc_id: int, db: Session = Depends(get_db)) -> Response:
    d = db.get(TitleDocument, doc_id)
    if d is None or not d.content:
        raise HTTPException(404)
    pdf = d.content[:5] == b"%PDF-"
    return Response(d.content, media_type="application/pdf" if pdf else "text/plain; charset=utf-8", headers={"Content-Disposition": f'inline; filename="{d.filename}"'})


class VerifyIn(BaseModel):
    confirm_matches_land: bool
    confirm_details_checked: bool
    name: str | None = Field(None, max_length=300)
    kind: Literal["individual", "company", "public_body", "charity", "trustees", "personal_representatives", "partnership"] | None = None
    salutation: str | None = Field(None, max_length=120)
    address_lines: list[str] | None = None
    postcode: str | None = Field(None, max_length=10)


@router.post("/offmarket/titles/{doc_id}/verify")
def verify_title(doc_id: int, body: VerifyIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    d = db.get(TitleDocument, doc_id)
    if d is None:
        raise HTTPException(404)
    edits = {k: v for k, v in body.model_dump().items() if k not in ("confirm_matches_land", "confirm_details_checked") and v is not None}
    try:
        c = ownership.verify_title(db, d, user.email, confirm_matches_land=body.confirm_matches_land, confirm_details_checked=body.confirm_details_checked, edits=edits or None)
    except ownership.OwnershipError as exc:
        _err(exc)
    agent.rescore_lead(db, db.get(Lead, d.lead_id))
    db.commit()
    return contact_out(c)


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


@router.post("/offmarket/titles/{doc_id}/reject")
def reject_title(doc_id: int, body: RejectIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    d = db.get(TitleDocument, doc_id)
    if d is None:
        raise HTTPException(404)
    ownership.reject_title(db, d, user.email, body.reason)
    db.commit()
    return {"ok": True}


class ContactPatch(BaseModel):
    salutation: str | None = Field(None, max_length=120)
    kind: Literal["individual", "company", "public_body", "charity", "trustees", "personal_representatives", "partnership", "unknown"] | None = None
    address_lines: list[str] | None = None
    postcode: str | None = Field(None, max_length=10)
    email: str | None = Field(None, max_length=320)
    email_basis: Literal["owner_initiated", "consent_recorded", "corporate_subscriber", "none"] | None = None
    basis_note: str | None = Field(None, max_length=2000)


@router.patch("/offmarket/contacts/{contact_id}")
def patch_contact(contact_id: int, body: ContactPatch, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    c = db.get(Contact, contact_id)
    if c is None:
        raise HTTPException(404)
    data = body.model_dump(exclude_unset=True)
    if data.get("email_basis") in ("owner_initiated", "consent_recorded") and len((body.basis_note or "").strip()) < 10:
        raise HTTPException(422, "record how the owner gave you this email address or their consent (date and how)")
    if data.get("email_basis") == "corporate_subscriber" and (data.get("kind") or c.kind) not in ("company", "public_body"):
        raise HTTPException(422, "corporate subscriber basis applies only to companies and public bodies")
    for k in ("salutation", "kind", "address_lines", "postcode", "email"):
        if k in data:
            setattr(c, k, data[k])
    if "email_basis" in data:
        c.email_basis = None if data["email_basis"] == "none" else data["email_basis"]
        if body.basis_note:
            c.notes = (c.notes or "") + f" Email basis ({c.email_basis}) recorded by {user.email} on {utcnow():%d %b %Y}: {body.basis_note.strip()}"
    audit(db, user, "contact.updated", "contact", c.id, fields=sorted(data))
    n = C.invalidate_for_contact(db, c, user.email)
    agent.rescore_lead(db, db.get(Lead, c.lead_id))
    db.commit()
    return contact_out(c) | {"messages_rechecked": n}


@router.post("/offmarket/contacts/{contact_id}/companies-house")
def companies_house(contact_id: int, db: Session = Depends(get_db)) -> dict:
    from plot.security.crypto import get_secret
    from plot.sources.base import SourceError

    c = db.get(Contact, contact_id)
    if c is None:
        raise HTTPException(404)
    num = next((p.get("company_number") for d in [db.get(TitleDocument, c.source_document_id)] if d for p in (d.extracted or {}).get("proprietors", []) if p.get("company_number")), None)
    if not num:
        raise HTTPException(422, "no company registration number on the register entry")
    try:
        return ownership.companies_house_profile(db, get_secret(db, "companies_house_api_key"), num)
    except SourceError as exc:
        raise HTTPException(502, f"{exc.error_class}: {exc.args[0]}") from exc


# --------------------------------------------------------------------------- drafts & approval


class DraftIn(BaseModel):
    channel: Literal["post", "email"] | None = None
    kind: Literal["initial", "follow_up"] = "initial"
    instructions: str | None = Field(None, max_length=1000)


@router.post("/offmarket/leads/{lead_id}/drafts")
def new_draft(lead_id: int, body: DraftIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    lead = _lead(db, lead_id)
    if lead.stage == "closed":
        raise HTTPException(422, "this lead is closed")
    try:
        o = drafting.create_draft(db, lead, user.email, channel=body.channel, kind=body.kind, instructions=body.instructions)
    except drafting.DraftError as exc:
        _err(exc)
    agent.rescore_lead(db, lead)
    db.commit()
    return outreach_out(o)


@router.get("/offmarket/outreach/{oid}")
def get_outreach(oid: int, db: Session = Depends(get_db)) -> dict:
    return outreach_out(_outreach(db, oid))


class EditIn(BaseModel):
    subject: str | None = Field(None, max_length=300)
    body: str | None = Field(None, max_length=10_000)
    include_price: bool | None = None


@router.patch("/offmarket/outreach/{oid}")
def edit_outreach(oid: int, body: EditIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    o = _outreach(db, oid)
    try:
        drafting.edit_draft(db, o, user.email, subject=body.subject, body=body.body, include_price=body.include_price)
    except drafting.DraftError as exc:
        _err(exc, 409)
    agent.rescore_lead(db, db.get(Lead, o.lead_id))
    db.commit()
    return outreach_out(o)


class RegenIn(BaseModel):
    instructions: str | None = Field(None, max_length=1000)


@router.post("/offmarket/outreach/{oid}/regenerate")
def regenerate(oid: int, body: RegenIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    o = _outreach(db, oid)
    try:
        n = drafting.regenerate(db, o, user.email, body.instructions)
    except drafting.DraftError as exc:
        _err(exc, 409)
    agent.rescore_lead(db, db.get(Lead, o.lead_id))
    db.commit()
    return outreach_out(n)


@router.post("/offmarket/outreach/{oid}/reject")
def reject(oid: int, body: RegenIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    o = _outreach(db, oid)
    try:
        drafting.reject(db, o, user.email, body.instructions)
    except drafting.DraftError as exc:
        _err(exc, 409)
    agent.rescore_lead(db, db.get(Lead, o.lead_id))
    db.commit()
    return outreach_out(o)


class NoteIn(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


@router.post("/offmarket/outreach/{oid}/clear-review")
def clear_review(oid: int, body: NoteIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    o = _outreach(db, oid)
    try:
        C.clear_review(db, o, user.email, body.note)
    except C.ComplianceError as exc:
        _err(exc)
    db.commit()
    return outreach_out(o)


class ApproveIn(BaseModel):
    confirm_recipient_hash: str = Field(min_length=64, max_length=64)
    confirm_price: bool = False


@router.post("/offmarket/outreach/{oid}/approve")
def approve(oid: int, body: ApproveIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    o = _outreach(db, oid)
    try:
        C.approve(db, o, user.email, confirm_recipient_hash=body.confirm_recipient_hash, confirm_price=body.confirm_price)
    except C.ComplianceError as exc:
        db.commit()  # keep the refreshed compliance result
        _err(exc, 409)
    agent.rescore_lead(db, db.get(Lead, o.lead_id))
    db.commit()
    return outreach_out(o)


@router.get("/offmarket/approvals")
def approval_queue(db: Session = Depends(get_db)) -> dict:
    """One message at a time. Approval always needs that message's own recipient confirmation."""
    items = db.scalars(select(Outreach).where(Outreach.status.in_(("awaiting_approval", "needs_compliance_review", "needs_changes"))).order_by(Outreach.status, Outreach.created_at)).all()
    return {"items": [outreach_out(o, full=False) | {"lead_headline": (lambda l: db.get(Site, l.site_id).headline)(db.get(Lead, o.lead_id))} for o in items]}


@router.get("/offmarket/outreach/{oid}/pdf")
def outreach_pdf(oid: int, final: bool = Query(False), db: Session = Depends(get_db)) -> Response:
    o = _outreach(db, oid)
    try:
        pdf = delivery.letter_pdf(db, o, final=final)
    except (delivery.DeliveryError, C.ComplianceError) as exc:
        _err(exc, 409)
    db.commit()
    ref = (o.generator_meta or {}).get("reference", o.id)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{ref}{"" if final else "-DRAFT"}.pdf"'})


class PostedIn(BaseModel):
    posted_on: dt.date
    postage_paid_gbp: float | None = Field(None, ge=0, le=50)


@router.post("/offmarket/outreach/{oid}/mark-posted")
def mark_posted(oid: int, body: PostedIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    o = _outreach(db, oid)
    try:
        delivery.mark_posted(db, o, user.email, posted_on=body.posted_on, postage_paid_gbp=body.postage_paid_gbp)
    except (delivery.DeliveryError, C.ComplianceError) as exc:
        _err(exc, 409)
    agent.rescore_lead(db, db.get(Lead, o.lead_id))
    db.commit()
    return outreach_out(o)


class StannpIn(BaseModel):
    confirm_cost_gbp: float = Field(ge=0, le=20)


@router.post("/offmarket/outreach/{oid}/stannp")
def stannp(oid: int, body: StannpIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    o = _outreach(db, oid)
    try:
        delivery.submit_stannp(db, o, user.email, confirm_cost_gbp=body.confirm_cost_gbp)
    except (delivery.DeliveryError, C.ComplianceError) as exc:
        _err(exc, 409)
    db.commit()
    return outreach_out(o)


@router.post("/offmarket/outreach/{oid}/mailbox-draft")
def mailbox_draft(oid: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    o = _outreach(db, oid)
    try:
        delivery.create_mailbox_draft(db, o, user.email)
    except (delivery.DeliveryError, mail.MailError) as exc:
        _err(exc, 409)
    except Exception as exc:  # noqa: BLE001
        _err(exc, 502)
    db.commit()
    return outreach_out(o)


@router.post("/offmarket/outreach/{oid}/send")
def send(oid: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    o = _outreach(db, oid)
    try:
        delivery.send_email(db, o, user.email)
    except (delivery.DeliveryError, C.ComplianceError) as exc:
        _err(exc, 409)
    agent.rescore_lead(db, db.get(Lead, o.lead_id))
    db.commit()
    return outreach_out(o)


@router.post("/offmarket/outreach/{oid}/reconcile")
def reconcile(oid: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    o = _outreach(db, oid)
    try:
        delivery.reconcile_email(db, o, user.email)
    except (delivery.DeliveryError, mail.MailError) as exc:
        _err(exc, 409)
    db.commit()
    return outreach_out(o)


@router.post("/offmarket/outreach/{oid}/release")
def release(oid: int, body: NoteIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    o = _outreach(db, oid)
    try:
        delivery.release_unsent(db, o, user.email, body.note)
    except delivery.DeliveryError as exc:
        _err(exc, 409)
    db.commit()
    return outreach_out(o)


# --------------------------------------------------------------------------- replies


class ReplyIn(BaseModel):
    channel: Literal["post", "phone_note", "email", "in_person"] = "post"
    body: str = Field(min_length=2, max_length=20_000)
    received_on: dt.date
    from_name: str | None = Field(None, max_length=300)
    from_address: str | None = Field(None, max_length=500)


@router.post("/offmarket/leads/{lead_id}/replies")
def log_reply(lead_id: int, body: ReplyIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    lead = _lead(db, lead_id)
    m = replies.record_inbound(db, lead.id, channel=body.channel, body=body.body, actor=user.email, from_name=body.from_name, from_address=body.from_address, received_at=dt.datetime.combine(body.received_on, dt.time(12), tzinfo=dt.UTC))
    agent.rescore_lead(db, lead)
    db.commit()
    return inbound_out(m)


class ReplyPatch(BaseModel):
    category: str | None = None
    decline_reason: str | None = None
    handled: bool | None = None


@router.patch("/offmarket/replies/{mid}")
def patch_reply(mid: int, body: ReplyPatch, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    m = db.get(InboundMessage, mid)
    if m is None:
        raise HTTPException(404)
    try:
        if body.category and body.category != m.category:
            replies.recategorise(db, m, body.category, user.email, body.decline_reason)
        elif body.decline_reason:
            m.decline_reason = body.decline_reason
            lead = db.get(Lead, m.lead_id)
            if lead.decline_reason and lead.decline_reason != "requests_no_contact":
                lead.decline_reason = body.decline_reason
    except ValueError as exc:
        _err(exc)
    if body.handled is not None:
        m.handled = body.handled
    agent.rescore_lead(db, db.get(Lead, m.lead_id))
    db.commit()
    return inbound_out(m)


@router.post("/offmarket/replies/{mid}/draft")
def draft_reply(mid: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    m = db.get(InboundMessage, mid)
    if m is None:
        raise HTTPException(404)
    if m.category == "requests_no_contact":
        raise HTTPException(422, "the owner asked for no further contact")
    lead = db.get(Lead, m.lead_id)
    parent = db.get(Outreach, m.outreach_id) if m.outreach_id else None
    try:
        o = drafting.create_draft(db, lead, user.email, channel="email" if (m.channel == "email" and m.from_address) else "post", kind="reply", parent=parent, inbound=m)
    except drafting.DraftError as exc:
        _err(exc)
    db.commit()
    return outreach_out(o)


@router.post("/offmarket/sync-replies")
def sync_replies(db: Session = Depends(get_db)) -> dict:
    out = delivery.sync_replies(db)
    db.commit()
    return out


@router.get("/offmarket/leads/{lead_id}/brief")
def brief(lead_id: int, db: Session = Depends(get_db)) -> dict:
    return replies.negotiation_brief(db, _lead(db, lead_id))


# --------------------------------------------------------------------------- suppressions


@router.get("/offmarket/suppressions")
def list_suppressions(db: Session = Depends(get_db)) -> dict:
    return {"items": [{"id": s.id, "kind": s.kind, "label": s.label, "reason": s.reason, "source": s.source, "created_at": s.created_at.isoformat()} for s in db.scalars(select(Suppression).order_by(Suppression.created_at.desc())).all()]}


class SuppressIn(BaseModel):
    name: str | None = Field(None, max_length=300)
    address_lines: list[str] = Field(default_factory=list)
    postcode: str | None = Field(None, max_length=10)
    email: str | None = Field(None, max_length=320)
    reason: str = Field(min_length=3, max_length=500)


@router.post("/offmarket/suppressions")
def add_suppression(body: SuppressIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    keys = C.suppression_keys(body.name, body.address_lines, body.postcode, body.email)
    if not keys:
        raise HTTPException(422, "give a name and address, an address with postcode, or an email")
    n = 0
    for kind, h in keys:
        if db.scalar(select(Suppression).where(Suppression.match_hash == h)) is None:
            db.add(Suppression(match_hash=h, kind=kind, label=f"{(body.name or '?')[:1]}. … — {body.postcode or (body.email or '').split('@')[-1]}", reason=body.reason, source="user"))
            n += 1
    audit(db, user, "suppression.add", entries=n)
    db.flush()
    for c in db.scalars(select(Contact).where(Contact.verified.is_(True))).all():
        if C.suppressed(db, c):
            C.invalidate_for_contact(db, c, user.email)
    db.commit()
    return {"ok": True, "added": n}


# --------------------------------------------------------------------------- mail accounts


@router.get("/mail/accounts")
def mail_accounts(db: Session = Depends(get_db)) -> dict:
    from plot.security.crypto import secret_status

    return {
        "items": [{"id": m.id, "provider": m.provider, "email": m.email_address, "status": m.status, "scopes": m.scopes, "connected_at": m.connected_at.isoformat(), "last_sync_at": m.last_sync_at and m.last_sync_at.isoformat(), "last_error": m.last_error} for m in db.scalars(select(MailAccount)).all()],
        "redirect_uri": mail.redirect_uri(),
        "configured": {p: secret_status(db, cfg["client_id"]) == "set" for p, cfg in mail.PROVIDERS.items()},
        "sandbox": get_settings().is_sandbox,
    }


class OAuthStartIn(BaseModel):
    provider: Literal["google", "microsoft"]


@router.post("/mail/oauth/start")
def oauth_start(body: OAuthStartIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    try:
        url = mail.start_oauth(db, body.provider)
    except mail.MailError as exc:
        _err(exc)
    audit(db, user, "mail.oauth_start", provider=body.provider)
    db.commit()
    return {"url": url}


@router.get("/mail/oauth/callback")
def oauth_callback(state: str = "", code: str = "", error: str = "", db: Session = Depends(get_db), user: User = Depends(current_user)) -> RedirectResponse:
    if error or not code:
        return RedirectResponse(f"/admin?tab=mail&error={error or 'no_code'}", status_code=303)
    try:
        mail.finish_oauth(db, state, code, user.email)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return RedirectResponse(f"/admin?tab=mail&error={type(exc).__name__}", status_code=303)
    return RedirectResponse("/admin?tab=mail&connected=1", status_code=303)


@router.post("/mail/accounts/{aid}/disconnect")
def disconnect(aid: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    m = db.get(MailAccount, aid)
    if m is None:
        raise HTTPException(404)
    m.status = "disconnected"
    m.token_ciphertext = b""
    audit(db, user, "mail.disconnected", "mail_account", m.email_address)
    db.commit()
    return {"ok": True}

"""Acquisition pipeline, off-market lead stages and compliant owner outreach.

Lead stage changes are gated by evidence (an owner can't be 'verified' without a recorded
source; 'interest confirmed' needs recorded availability evidence). Outreach letters are
drafted deterministically, checked against UK GDPR/PECR-oriented rules, and can only be
marked sent after you approve them. PLOT never sends owner letters automatically.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from plot.api.deps import audit, current_user
from plot.api.serialize import rows_for
from plot.db import get_db
from plot.models import LEAD_STAGES, PIPELINE_STAGES, Assessment, Lead, LeadEvent, Outreach, PipelineItem, Site, Suppression, User, utcnow

router = APIRouter()

STANDARD_CHECKLIST = [
    ("planning_verify", "Verify planning status/history on the LPA register"),
    ("pre_app", "Pre-application advice from the LPA"),
    ("title", "Title register & plan: access rights, covenants, easements, ransom strips"),
    ("searches", "Local authority, drainage & water, environmental searches"),
    ("site_visit", "Site visit: access, slope, trees, neighbours, noise, light"),
    ("topo", "Topographical survey"),
    ("utilities", "DNO electricity estimate; water & sewer quotes; broadband enquiry"),
    ("ground", "Ground investigation & percolation test"),
    ("ecology", "Preliminary ecological appraisal (seasonal surveys if triggered)"),
    ("flood", "Flood risk assessment (if flood/surface-water risk)"),
    ("trees", "Arboricultural survey (if trees near works)"),
    ("design_cost", "Architect concept + independent cost check"),
    ("finance", "Self-build mortgage agreement in principle"),
    ("cil", "CIL self-build exemption (submit before commencement)"),
    ("insurance", "Site insurance & structural warranty quotes"),
    ("offer", "Offer strategy: max offer, conditional/option terms, solicitor briefed"),
]


# ---------------------------------------------------------------------------- pipeline


@router.get("/pipeline")
def pipeline(db: Session = Depends(get_db)) -> dict:
    items = db.scalars(select(PipelineItem)).all()
    sites = {s.id: s for s in db.scalars(select(Site).where(Site.id.in_([i.site_id for i in items]))).all()} if items else {}
    rows = {r["id"]: r for r in rows_for(db, list(sites.values()))}
    return {
        "stages": PIPELINE_STAGES,
        "items": [{"id": i.id, "site_id": i.site_id, "stage": i.stage, "notes": i.notes, "checklist": i.checklist, "updated_at": i.updated_at.isoformat(), "site": rows.get(i.site_id)} for i in items],
    }


class PipelineIn(BaseModel):
    site_id: int


@router.post("/pipeline")
def add_pipeline(body: PipelineIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    if db.get(Site, body.site_id) is None:
        raise HTTPException(404)
    existing = db.scalar(select(PipelineItem).where(PipelineItem.site_id == body.site_id))
    if existing:
        return {"id": existing.id}
    a = db.scalar(select(Assessment).where(Assessment.site_id == body.site_id, Assessment.is_current.is_(True)))
    checklist = {k: {"label": lbl, "done": False, "note": ""} for k, lbl in STANDARD_CHECKLIST}
    if a:
        for i, act in enumerate((a.planning or {}).get("actions", [])[:6]):
            checklist[f"planning_action_{i}"] = {"label": act, "done": False, "note": "", "from": "planning screen"}
        for it in (a.site_risk or {}).get("items", []):
            if it.get("action") and it.get("severity") in ("high", "medium"):
                checklist[f"risk_{it['code']}"] = {"label": it["action"], "done": False, "note": "", "from": f"site risk: {it['title']}"}
        offer = (a.residual or {}).get("max_recommended_offer")
        if offer is not None:
            checklist["offer"]["label"] += f" (current max recommended offer £{offer:,})"
    item = PipelineItem(site_id=body.site_id, stage="shortlist", checklist=checklist)
    db.add(item)
    audit(db, user, "pipeline.add", "site", body.site_id)
    db.commit()
    return {"id": item.id}


class PipelinePatch(BaseModel):
    stage: Literal[tuple(PIPELINE_STAGES)] | None = None  # type: ignore[valid-type]
    notes: str | None = Field(None, max_length=20_000)
    checklist: dict[str, Any] | None = None


@router.patch("/pipeline/{item_id}")
def patch_pipeline(item_id: int, body: PipelinePatch, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    it = db.get(PipelineItem, item_id)
    if it is None:
        raise HTTPException(404)
    if body.stage:
        audit(db, user, "pipeline.stage", "site", it.site_id, frm=it.stage, to=body.stage)
        it.stage = body.stage
    if body.notes is not None:
        it.notes = body.notes
    if body.checklist is not None:
        it.checklist = body.checklist
    db.commit()
    return {"ok": True}


@router.delete("/pipeline/{item_id}")
def delete_pipeline(item_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    it = db.get(PipelineItem, item_id)
    if it is None:
        raise HTTPException(404)
    db.delete(it)
    audit(db, user, "pipeline.remove", "site", it.site_id)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------- leads


@router.get("/leads")
def leads(db: Session = Depends(get_db)) -> dict:
    ls = db.scalars(select(Lead)).all()
    sites = db.scalars(select(Site).where(Site.id.in_([l.site_id for l in ls]))).all() if ls else []
    rows = {r["id"]: r for r in rows_for(db, sites)}
    outreach = {}
    for o in db.scalars(select(Outreach)).all():
        outreach.setdefault(o.lead_id, []).append({"id": o.id, "status": o.status, "channel": o.channel, "created_at": o.created_at.isoformat()})
    return {
        "stages": LEAD_STAGES,
        "items": [{"id": l.id, "site_id": l.site_id, "stage": l.stage, "signals": l.signals, "owner": l.owner, "availability_evidence": l.availability_evidence, "notes": l.notes, "updated_at": l.updated_at.isoformat(), "outreach": outreach.get(l.id, []), "site": rows.get(l.site_id)} for l in ls],
    }


class OwnerIn(BaseModel):
    name: str = Field(min_length=2, max_length=300)
    address: str | None = Field(None, max_length=1000)
    owner_type: Literal["individual", "company", "public_body", "charity", "unknown"] = "unknown"
    title_number: str | None = Field(None, max_length=30)
    source: str = Field(min_length=5, max_length=500, description="e.g. 'HMLR official copy of register, title DT123456, dated 2026-10-01'")
    source_date: dt.date


class EvidenceIn(BaseModel):
    kind: Literal["owner_reply", "agent_confirmation", "meeting_note", "written_offer", "other"]
    summary: str = Field(min_length=5, max_length=5000)
    date: dt.date


class LeadPatch(BaseModel):
    stage: str | None = None
    note: str | None = Field(None, max_length=5000)
    owner: OwnerIn | None = None
    add_evidence: EvidenceIn | None = None
    notes: str | None = Field(None, max_length=20_000)


def transition_problems(db: Session, lead: Lead, to: str) -> list[str]:
    if to not in LEAD_STAGES:
        return [f"unknown stage {to}"]
    i_to, i_from = LEAD_STAGES.index(to), LEAD_STAGES.index(lead.stage)
    if i_to <= i_from:
        return []  # moving back is always allowed (with a note)
    if i_to - i_from > 1:
        return [f"advance one stage at a time (next is {LEAD_STAGES[i_from + 1]})"]
    probs = []
    owner = lead.owner or {}
    sent = db.scalar(select(Outreach).where(Outreach.lead_id == lead.id, Outreach.status == "sent"))
    approved = db.scalar(select(Outreach).where(Outreach.lead_id == lead.id, Outreach.status.in_(("approved", "sent"))))
    if to == "owner_verified" and not (owner.get("name") and owner.get("source")):
        probs.append("record the owner's name and the verifying source (e.g. HM Land Registry official copy)")
    if to == "outreach_approved" and not approved:
        probs.append("approve an outreach letter first")
    if to == "owner_contacted" and not sent:
        probs.append("mark an approved letter as sent first")
    if to == "interest_confirmed" and not lead.availability_evidence:
        probs.append("record evidence that the owner is willing to sell (reply, meeting note…)")
    return probs


@router.patch("/leads/{lead_id}")
def patch_lead(lead_id: int, body: LeadPatch, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404)
    if body.owner is not None:
        lead.owner = body.owner.model_dump(mode="json") | {"recorded_by": user.email, "recorded_at": utcnow().isoformat()}
        audit(db, user, "lead.owner", "lead", lead_id, source=body.owner.source)
    if body.add_evidence is not None:
        lead.availability_evidence = list(lead.availability_evidence or []) + [body.add_evidence.model_dump(mode="json") | {"recorded_by": user.email}]
        audit(db, user, "lead.evidence", "lead", lead_id, kind=body.add_evidence.kind)
    if body.notes is not None:
        lead.notes = body.notes
    if body.stage and body.stage != lead.stage:
        probs = transition_problems(db, lead, body.stage)
        if probs:
            db.rollback()
            raise HTTPException(422, {"message": "stage change not allowed yet", "requirements": probs})
        db.add(LeadEvent(lead_id=lead.id, from_stage=lead.stage, to_stage=body.stage, actor=user.email, note=body.note))
        audit(db, user, "lead.stage", "lead", lead_id, frm=lead.stage, to=body.stage)
        lead.stage = body.stage
    db.commit()
    return {"ok": True, "stage": lead.stage}


# ---------------------------------------------------------------------------- outreach

RISKY_WORDS = re.compile(r"\b(urgent|final notice|compulsory|legal action|must respond|deadline|cash buyer guaranteed|we represent the council)\b", re.I)


def suppression_hash(name: str, address: str | None) -> str:
    norm = re.sub(r"[^a-z0-9]", "", f"{name}|{address or ''}".lower())
    return hashlib.sha256(norm.encode()).hexdigest()


def draft_letter(site: Site, lead: Lead, sender_name: str, sender_contact: str) -> str:
    owner = lead.owner or {}
    title = owner.get("title_number")
    place = site.address or site.headline or "the land"
    return f"""{sender_name}
{sender_contact}

{dt.date.today():%d %B %Y}

{owner.get('name', '[Owner name]')}
{owner.get('address') or '[Owner address]'}

Dear {owner.get('name', 'Sir or Madam')},

Re: Land at {place}{f' (HM Land Registry title {title})' if title else ''}

I am a private individual looking for a site on which to build a single, carefully designed home to live in. I came across the land referred to above and wondered whether you might consider selling it, or part of it, now or in the future.

I am not a developer or an agent, and I am not asking for any commitment. If you would be open to an informal conversation, I would be glad to hear from you using the contact details above. If you are not interested there is no need to reply, and I will not write to you again.

How I obtained your details: your name and address appear on the HM Land Registry title register for this land, which is a public register. I will use them only to send this letter and, if you reply, to correspond with you about the land. If we do not correspond I will delete them within six months, and I will remove them straight away if you ask. You have rights under UK data protection law, including the right to object; you can also complain to the Information Commissioner's Office (ico.org.uk).

Yours sincerely,

{sender_name}
"""


def compliance_checks(db: Session, lead: Lead, o: Outreach, lia_confirmed: bool) -> dict[str, Any]:
    checks = []

    def add(code: str, ok: bool, blocking: bool, text: str) -> None:
        checks.append({"code": code, "ok": ok, "blocking": blocking, "text": text})

    owner = lead.owner or {}
    add("owner_source", bool(owner.get("name") and owner.get("source")), True, "Owner identity recorded with its source (e.g. HMLR official copy).")
    if o.channel == "email":
        add("pecr_channel", o.recipient_type in ("company", "public_body"), True, "Unsolicited emails to individuals need prior consent (PECR); use a posted letter for individuals.")
    else:
        add("pecr_channel", True, True, "Posted letter: permitted channel for an unsolicited approach.")
    sup = db.scalar(select(Suppression).where(Suppression.match_hash == suppression_hash(o.recipient_name or "", o.recipient_address)))
    add("suppression", sup is None, True, "Recipient not on your do-not-contact list.")
    body = o.body.lower()
    add("privacy_info", ("land registry" in body or "how i obtained" in body) and ("not write" in body or "remove" in body), True, "Letter explains where the details came from and how to opt out.")
    recent = db.scalar(select(Outreach).where(Outreach.lead_id == lead.id, Outreach.status == "sent", Outreach.sent_at > utcnow() - dt.timedelta(days=90), Outreach.id != o.id))
    add("frequency", recent is None, True, "No letter sent to this owner in the last 90 days.")
    add("lia", lia_confirmed, True, "You confirm a legitimate-interests assessment: a one-off, relevant approach about their land, with easy opt-out.")
    add("tone", RISKY_WORDS.search(o.body) is None, False, "No pressure or misleading wording (urgent, final notice, compulsory…).")
    return {"checks": checks, "passed": all(c["ok"] for c in checks if c["blocking"])}


class OutreachDraftIn(BaseModel):
    channel: Literal["post", "email"] = "post"
    sender_name: str = Field(min_length=2, max_length=200)
    sender_contact: str = Field(min_length=5, max_length=500)
    recipient_email: str | None = None


@router.post("/leads/{lead_id}/outreach")
def create_outreach(lead_id: int, body: OutreachDraftIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404)
    if LEAD_STAGES.index(lead.stage) < LEAD_STAGES.index("owner_verified"):
        raise HTTPException(422, "verify the owner (recorded name + source) before drafting outreach")
    site = db.get(Site, lead.site_id)
    owner = lead.owner or {}
    o = Outreach(lead_id=lead_id, channel=body.channel, recipient_type=owner.get("owner_type", "unknown"), recipient_name=owner.get("name"), recipient_address=owner.get("address"), recipient_email=body.recipient_email, subject=f"Land at {site.address or site.headline}", body=draft_letter(site, lead, body.sender_name, body.sender_contact), status="draft")
    db.add(o)
    db.flush()
    o.compliance = compliance_checks(db, lead, o, lia_confirmed=False)
    audit(db, user, "outreach.draft", "lead", lead_id, outreach_id=o.id)
    db.commit()
    return _outreach_out(o)


class OutreachPatch(BaseModel):
    body: str | None = Field(None, max_length=20_000)
    action: Literal["save", "approve", "withdraw", "mark_sent"] = "save"
    lia_confirmed: bool = False


def _outreach_out(o: Outreach) -> dict:
    return {"id": o.id, "lead_id": o.lead_id, "channel": o.channel, "recipient_type": o.recipient_type, "recipient_name": o.recipient_name, "subject": o.subject, "body": o.body, "status": o.status, "compliance": o.compliance, "approved_at": o.approved_at and o.approved_at.isoformat(), "approved_by": o.approved_by, "sent_at": o.sent_at and o.sent_at.isoformat()}


@router.get("/outreach/{outreach_id}")
def get_outreach(outreach_id: int, db: Session = Depends(get_db)) -> dict:
    o = db.get(Outreach, outreach_id)
    if o is None:
        raise HTTPException(404)
    return _outreach_out(o)


@router.patch("/outreach/{outreach_id}")
def patch_outreach(outreach_id: int, body: OutreachPatch, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    o = db.get(Outreach, outreach_id)
    if o is None:
        raise HTTPException(404)
    lead = db.get(Lead, o.lead_id)
    if body.body is not None:
        if o.status not in ("draft",):
            raise HTTPException(409, "only drafts can be edited")
        o.body = body.body
    o.compliance = compliance_checks(db, lead, o, lia_confirmed=body.lia_confirmed or bool((o.compliance or {}).get("lia_confirmed")))
    if body.lia_confirmed:
        o.compliance = o.compliance | {"lia_confirmed": True}
    if body.action == "approve":
        if o.status != "draft":
            raise HTTPException(409, "only drafts can be approved")
        if not o.compliance["passed"]:
            raise HTTPException(422, {"message": "compliance checks not passed", "checks": o.compliance["checks"]})
        o.status = "approved"
        o.approved_at = utcnow()
        o.approved_by = user.email
    elif body.action == "withdraw":
        o.status = "withdrawn"
    elif body.action == "mark_sent":
        if o.status != "approved":
            raise HTTPException(409, "only approved letters can be marked as sent")
        o.status = "sent"
        o.sent_at = utcnow()
    audit(db, user, f"outreach.{body.action}", "lead", o.lead_id, outreach_id=o.id)
    db.commit()
    return _outreach_out(o)


class SuppressionIn(BaseModel):
    name: str
    address: str | None = None
    reason: str | None = None


@router.post("/suppressions")
def add_suppression(body: SuppressionIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    h = suppression_hash(body.name, body.address)
    if db.scalar(select(Suppression).where(Suppression.match_hash == h)) is None:
        db.add(Suppression(match_hash=h, label=body.name[:300], reason=body.reason))
    audit(db, user, "suppression.add", None, None)
    db.commit()
    return {"ok": True}

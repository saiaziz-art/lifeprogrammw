"""Drafting workflow: build verified facts, generate, edit, regenerate, reject, and the
daily "prepare up to N drafts" step. Nothing here sends anything."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from plot.models import AuditLog, Contact, Evidence, InboundMessage, Lead, LeadEvent, Outreach, Site, TitleDocument, utcnow
from plot.outreach import compliance as C
from plot.outreach import writer as W
from plot.preferences import load_preferences

SENSITIVE_SETTINGS = {"conservation-area": "conservation area", "national-park": "National Park", "area-of-outstanding-natural-beauty": "National Landscape"}


class DraftError(ValueError):
    pass


def _api_key(db: Session) -> str | None:
    from plot.sources.registry import load_secrets

    return load_secrets(db).get("anthropic_api_key")


def _first_line(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").split(",")[0].split("\n")[0]).strip().lower()


def confirmed_claims(buyer) -> dict[str, bool]:
    """Claims the validator may allow because you confirmed them in your buyer profile."""
    if not buyer.facts_confirmed:
        return {}
    stmt = " ".join(x for x in (buyer.funding_statement, buyer.local_connection) if x)
    out = {k: any(re.search(p, stmt, re.I) for p in pats) for k, pats in W.UNCONFIRMED_CLAIMS.items() if k in ("cash", "funding", "speed", "local")}
    out["funding"] = out.get("funding") or bool(buyer.funding_statement)
    out["local"] = out.get("local") or bool(buyer.local_connection)
    return out


def build_facts(db: Session, lead: Lead, contact: Contact, channel: str, kind: str, *, reference: str, parent: Outreach | None = None, inbound: InboundMessage | None = None, include_price: bool = False) -> tuple[W.Facts, dict[str, bool]]:
    prefs, _ = load_preferences(db)
    b = prefs.buyer
    site = db.get(Site, lead.site_id)
    doc = db.get(TitleDocument, contact.source_document_id) if contact.source_document_id else None
    ex = (doc.extracted if doc else None) or {}
    an = lead.analysis or {}
    prop_ref = ex.get("property_address") or site.address or None
    place = W.town_from_address(ex.get("property_address"))
    if not place:
        pc = db.scalar(select(Evidence).where(Evidence.site_id == site.id, Evidence.kind == "postcode", Evidence.superseded_at.is_(None)))
        if pc is not None:
            par = (pc.data or {}).get("parish") or ""
            place = re.sub(r",?\s*\(?unparished area\)?", "", par).strip() or (pc.data or {}).get("admin_ward")
    if not place and an.get("area"):
        place = re.sub(r"\s+(fringe|north|south|east|west|edge)$", "", an["area"], flags=re.I)
    home = bool(ex.get("proprietor_address") and ex.get("property_address") and _first_line(ex["proprietor_address"]) == _first_line(ex["property_address"]))
    acc, m = an.get("access") or {}, an.get("metrics") or {}
    obs = []
    if acc.get("position") == "side_garden":
        obs.append("side_garden")
    if acc.get("corner"):
        obs.append("corner")
    if acc.get("type") == "backland_strip":
        obs.append("backland")
    if (m.get("parcel_vs_neighbours") or 0) >= 1.6:
        obs.append("generous")
    datasets = {c.get("dataset") for c in an.get("constraints", [])}
    datasets |= {e.kind.removeprefix("pd:") for e in db.scalars(select(Evidence).where(Evidence.site_id == site.id, Evidence.kind.in_([f"pd:{k}" for k in SENSITIVE_SETTINGS]), Evidence.superseded_at.is_(None))).all()}
    setting = next((v for k, v in SENSITIVE_SETTINGS.items() if k in datasets), None)
    sig = set(an.get("signals") or lead.signals or [])
    hist = "withdrawn" if "residential_application_withdrawn" in sig else "lapsed" if "residential_permission_possibly_lapsed" in sig else "refused" if "residential_application_refused" in sig else None
    prev_on = None
    if kind == "follow_up":
        first = db.scalar(select(Outreach).where(Outreach.lead_id == lead.id, Outreach.kind == "initial", Outreach.status.in_(("sent", "posted"))).order_by(Outreach.sent_at).limit(1))
        if first and first.sent_at:
            prev_on = f"{first.sent_at.day} {first.sent_at:%B %Y}"
    confirmed = b.facts_confirmed
    facts = W.Facts(
        channel=channel,
        kind=kind,
        persona=b.persona,
        recipient_kind=contact.kind,
        recipient_name=contact.name,
        salutation=contact.salutation or "",
        opportunity_type=lead.opportunity_type,
        place=place,
        property_ref=prop_ref,
        property_is_home=home,
        title_number=ex.get("title_number") or (doc.title_number if doc else None),
        first_name=b.first_name or "",
        last_name=b.last_name,
        partner_term=b.partner_term if b.persona == "couple" else None,
        returning_to_uk=bool(b.returning_to_uk),
        local_connection=b.local_connection if confirmed else None,
        funding_statement=b.funding_statement if confirmed else None,
        agent_name=b.agent_name if b.persona == "represented" else None,
        agent_company=b.agent_company if b.persona == "represented" else None,
        reply_email=b.reply_email,
        reply_phone=b.reply_phone,
        reply_postal_address=b.reply_postal_address,
        observations=obs,
        road_name=acc.get("road_name"),
        sensitive_setting=setting,
        planning_history=hist,
        reference=reference,
        retention_until=f"{contact.retention_until.day} {contact.retention_until:%B %Y}" if contact.retention_until else None,
        previous_sent_on=prev_on,
        reply_category=inbound.category if inbound else None,
        reply_excerpt=(inbound.body[:400] if inbound else None),
        include_price=include_price,
    )
    return facts, confirmed_claims(b)


def facts_for(db: Session, o: Outreach) -> tuple[W.Facts, dict[str, bool]]:
    lead = db.get(Lead, o.lead_id)
    contact = db.get(Contact, o.contact_id) if o.contact_id else None
    if contact is None:
        contact = Contact(lead_id=lead.id, kind=o.recipient_type or "unknown", name=o.recipient_name or "", source="manual")
    inbound = db.scalar(select(InboundMessage).where(InboundMessage.suggested_reply_id == o.id)) if o.kind == "reply" else None
    ref = (o.generator_meta or {}).get("reference") or f"PLOT-{lead.id:04d}"
    f, conf = build_facts(db, lead, contact, o.channel, o.kind, reference=ref, inbound=inbound, include_price=bool((o.generator_meta or {}).get("include_price")))
    return f, conf


def _reference(db: Session, lead: Lead) -> str:
    n = db.scalar(select(func.count()).select_from(Outreach).where(Outreach.lead_id == lead.id)) or 0
    return f"PLOT-{lead.id:04d}-{n + 1}"


def _stage(db: Session, lead: Lead, stage: str, actor: str, note: str | None = None) -> None:
    from plot.offmarket.agent import STAGE_ORDER, set_stage

    if STAGE_ORDER.get(stage, 0) > STAGE_ORDER.get(lead.stage, 0) and lead.stage != "closed":
        set_stage(db, lead, stage, actor, note)


def default_channel(contact: Contact) -> str:
    from plot.offmarket.score import contact_method

    return "email" if contact_method(contact.kind, contact.email_basis) == "email_or_post" and contact.email else "post"


def create_draft(db: Session, lead: Lead, actor: str, *, channel: str | None = None, kind: str = "initial", instructions: str | None = None, parent: Outreach | None = None, inbound: InboundMessage | None = None, auto: bool = False, client=None) -> Outreach:
    from plot.offmarket.agent import primary_contact

    contact = primary_contact(db, lead)
    if contact is None:
        raise DraftError("verify the owner from an official copy of the register before drafting")
    prefs, _ = load_preferences(db)
    channel = channel or default_channel(contact)
    if channel not in ("post", "email"):
        raise DraftError("channel must be post or email")
    # supersede earlier unsent drafts of the same kind for this lead
    previous = db.scalars(select(Outreach).where(Outreach.lead_id == lead.id, Outreach.kind == kind, Outreach.status.in_(("draft", "needs_changes", "needs_compliance_review", "awaiting_approval", "approved")))).all()
    for p in previous:
        p.status = "superseded"
    n_prev = db.scalar(select(func.count()).select_from(Outreach).where(Outreach.lead_id == lead.id, Outreach.kind == kind)) or 0
    ref = _reference(db, lead)
    facts, confirmed = build_facts(db, lead, contact, channel, kind, reference=ref, parent=parent, inbound=inbound)
    seed = W.seed_for(lead.id, contact.id, kind, n_prev, instructions or "")
    d = W.generate(facts, seed=seed, api_key=_api_key(db), instructions=instructions, buyer_confirmed=confirmed, client=client)
    o = Outreach(
        lead_id=lead.id,
        contact_id=contact.id,
        channel=channel,
        recipient_type=contact.kind,
        recipient_name=contact.name,
        recipient_address="\n".join(list(contact.address_lines or []) + ([contact.postcode] if contact.postcode else [])),
        recipient_email=contact.email if channel == "email" else None,
        subject=d.subject,
        body=d.body,
        footer=d.footer,
        kind=kind,
        parent_id=parent.id if parent else None,
        persona=prefs.buyer.persona,
        generator=d.generator,
        generator_meta=d.meta | {"reference": ref, "instructions": instructions, "auto": auto, "regeneration": n_prev},
        word_count=W.message_words(d.body),
        postage_estimate_gbp=prefs.outreach.postage_estimate_gbp if channel == "post" else None,
        status="draft",
        compliance={},
    )
    db.add(o)
    db.flush()
    if inbound is not None:
        inbound.suggested_reply_id = o.id
    C.apply(db, o)
    db.add(AuditLog(actor=actor, action="outreach.drafted", entity="outreach", entity_id=str(o.id), detail={"lead_id": lead.id, "kind": kind, "channel": channel, "generator": d.generator, "status": o.status}))
    if kind == "initial":
        _stage(db, lead, "draft_prepared", actor, f"Draft {ref} prepared ({d.generator})")
        if o.status == "awaiting_approval":
            _stage(db, lead, "awaiting_approval", actor, "Draft passed checks and awaits your approval")
    db.flush()
    return o


def edit_draft(db: Session, o: Outreach, actor: str, *, subject: str | None = None, body: str | None = None, include_price: bool | None = None) -> Outreach:
    if o.status in C.TERMINAL or o.status in ("sending", "delivery_unknown"):
        raise DraftError(f"cannot edit a message that is {o.status.replace('_', ' ')}")
    was_approved = o.status == "approved"
    if subject is not None:
        o.subject = subject.strip()[:300]
    if body is not None:
        o.body = body.replace("\r\n", "\n").strip()
    if include_price is not None:
        o.generator_meta = (o.generator_meta or {}) | {"include_price": include_price}
    o.word_count = W.message_words(o.body)
    o.generator = "manual" if o.generator in (None, "manual") else o.generator
    o.generator_meta = (o.generator_meta or {}) | {"edited_by_you": True}
    if was_approved:
        o.approved_hash = o.approved_at = o.approved_by = None
        db.add(AuditLog(actor=actor, action="outreach.approval_cleared", entity="outreach", entity_id=str(o.id), detail={"reason": "edited"}))
    o.status = "draft"
    o.pdf = None
    C.apply(db, o)
    db.add(AuditLog(actor=actor, action="outreach.edited", entity="outreach", entity_id=str(o.id), detail={"content_hash": o.content_hash}))
    return o


def regenerate(db: Session, o: Outreach, actor: str, instructions: str | None = None) -> Outreach:
    if o.status in C.TERMINAL or o.status in ("sending", "delivery_unknown"):
        raise DraftError(f"cannot regenerate a message that is {o.status.replace('_', ' ')}")
    lead = db.get(Lead, o.lead_id)
    parent = db.get(Outreach, o.parent_id) if o.parent_id else None
    inbound = db.scalar(select(InboundMessage).where(InboundMessage.suggested_reply_id == o.id))
    return create_draft(db, lead, actor, channel=o.channel, kind=o.kind, instructions=instructions, parent=parent, inbound=inbound)


def reject(db: Session, o: Outreach, actor: str, reason: str | None) -> None:
    if o.status in C.TERMINAL or o.status in ("sending", "delivery_unknown"):
        raise DraftError(f"cannot reject a message that is {o.status.replace('_', ' ')}")
    o.status = "rejected"
    o.rejected_reason = (reason or "").strip() or None
    o.approved_hash = None
    db.add(AuditLog(actor=actor, action="outreach.rejected", entity="outreach", entity_id=str(o.id), detail={"reason": o.rejected_reason}))
    db.flush()


def prepare_drafts(db: Session, prefs=None, actor: str = "system", client=None) -> dict[str, Any]:
    """Daily: up to N drafts for the strongest eligible, verified owners, plus due follow-ups.
    Drafts wait for individual approval; nothing is sent."""
    from plot.offmarket.agent import followup_due, latest_draft, primary_contact

    prefs = prefs or load_preferences(db)[0]
    out: dict[str, Any] = {"created": [], "skipped": {}}
    if not prefs.buyer.facts_confirmed:
        out["skipped"]["buyer_profile_unconfirmed"] = "Confirm your buyer profile before drafts can be prepared"
        return out
    today = utcnow().date()
    made_today = sum(1 for o in db.scalars(select(Outreach).where(Outreach.created_at >= utcnow().replace(hour=0, minute=0, second=0, microsecond=0))).all() if (o.generator_meta or {}).get("auto"))
    room = max(0, prefs.outreach.daily_draft_limit - made_today)
    if room == 0:
        out["skipped"]["daily_limit"] = f"Daily draft limit ({prefs.outreach.daily_draft_limit}) reached"
        return out
    # follow-ups first (already in conversation), then new first approaches by score
    for lead in db.scalars(select(Lead).where(Lead.stage.in_(("contacted", "awaiting_response")))).all():
        if room == 0:
            break
        due = followup_due(db, lead, prefs.outreach.follow_up_after_days, prefs.outreach.max_follow_ups)
        if due and due <= today:
            o = create_draft(db, lead, actor, kind="follow_up", auto=True, client=client)
            out["created"].append({"outreach_id": o.id, "lead_id": lead.id, "kind": "follow_up", "status": o.status})
            room -= 1
    leads = db.scalars(select(Lead).where(Lead.stage == "owner_verified", Lead.acq_fatal.is_(False)).order_by(Lead.acq_score_total.desc().nullslast())).all()
    for lead in leads:
        if room == 0:
            break
        conf = ((lead.acq_score or {}).get("confidence") or {}).get("label")
        if conf == "low":
            out["skipped"].setdefault("low_confidence", []).append(lead.id)
            continue
        if latest_draft(db, lead) is not None or primary_contact(db, lead) is None:
            continue
        o = create_draft(db, lead, actor, kind="initial", auto=True, client=client)
        out["created"].append({"outreach_id": o.id, "lead_id": lead.id, "kind": "initial", "status": o.status})
        room -= 1
    return out


def lead_event(db: Session, lead: Lead, actor: str, note: str) -> None:
    db.add(LeadEvent(lead_id=lead.id, from_stage=lead.stage, to_stage=lead.stage, actor=actor, note=note))

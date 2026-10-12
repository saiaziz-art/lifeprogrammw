"""Reply intelligence: categorise owner replies, respect opt-outs immediately, suggest replies
for your approval and prepare a negotiation brief when an owner asks about price.

Categories: interested | possibly_interested | wants_information | requests_price |
not_interested | requests_no_contact | unknown. A request for no further contact always wins.
Decline reasons are about the deal (price, privacy, planning, not selling, timing) — owners
are never profiled by personal characteristics.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from plot.models import Assessment, AuditLog, Contact, InboundMessage, Lead, Outreach, Suppression, utcnow
from plot.outreach import compliance as C

CATEGORIES = ["interested", "possibly_interested", "wants_information", "requests_price", "not_interested", "requests_no_contact", "unknown"]
DECLINE_REASONS = ["not_selling", "price_expectations", "privacy", "planning_concerns", "timing", "family_reasons_unspecified", "other"]

RULES: list[tuple[str, list[str]]] = [
    ("requests_no_contact", [r"\bstop\b(?! by)", r"\bremove (me|us|my|our)\b", r"\bdo ?n[o']t (contact|write|email|call)", r"\bnot? (to )?contact (me|us) again", r"\bno further contact\b", r"\bunsubscribe\b", r"\bleave (me|us) alone\b", r"\bnever (contact|write)", r"\bstop (writing|contacting|emailing)", r"\bopt(ing)? out\b", r"\bharass", r"\bdelete (my|our) (details|data|information)\b"]),
    ("not_interested", [r"\bnot interested\b", r"\bno,? thank(s| you)\b", r"\bnot (for sale|selling)\b", r"\b(wouldn'?t|would not|won'?t|will not) (consider|sell)\b", r"\bno intention of selling\b", r"\bnot something (we|i)('d| would) consider\b", r"\bplease don'?t\b", r"\bthe answer is no\b", r"\bnot at this time\b"]),
    ("requests_price", [r"\bhow much\b", r"\bwhat (price|figure|sum)\b", r"\bwhat would you (pay|offer)\b", r"\bmake (me|us) an offer\b", r"\b(your|an) offer\b", r"\bvaluation\b", r"\bwhat('s| is) it worth\b", r"\bprice in mind\b"]),
    ("wants_information", [r"\bmore (information|details)\b", r"\btell (me|us) more\b", r"\bwhich (part|bit|area)\b", r"\bhow (big|large|much land)\b", r"\bwhat exactly\b", r"\bwhat (would|do) you (want|need|have in mind)\b", r"\bsend (me|us) (a plan|details)\b", r"\bwho are you\b"]),
    ("interested", [r"\bhappy to (discuss|talk|chat|meet)\b", r"\bwould (consider|be open)\b", r"\b(i'?m|we'?re|am|are) interested\b", r"\bopen to (it|the idea|discuss)", r"\blet'?s (talk|chat|meet)\b", r"\b(call|ring|phone) me\b", r"\bcome (and|to) (see|look|have a look)\b", r"\bpop round\b", r"\bdefinitely interested\b", r"\byes\b"]),
    ("possibly_interested", [r"\bmight\b", r"\bmaybe\b", r"\bpossibly\b", r"\bdepends\b", r"\bnot sure\b", r"\bin (the )?future\b", r"\bperhaps\b", r"\bnot (right )?now\b"]),
]
DECLINE_RULES: list[tuple[str, list[str]]] = [
    ("price_expectations", [r"\bprice\b", r"\bworth (more|a lot)\b", r"\bmoney\b", r"\bnot enough\b"]),
    ("privacy", [r"\bprivacy\b", r"\boverlook", r"\bview\b", r"\bneighbours?\b", r"\bquiet\b", r"\bgarden (is|means)\b"]),
    ("planning_concerns", [r"\bplanning\b", r"\bcouncil\b", r"\bpermission\b", r"\bgreen belt\b"]),
    ("timing", [r"\bnot (right )?now\b", r"\bin (a few|some) years\b", r"\blater\b", r"\bmaybe one day\b"]),
    ("not_selling", [r"\bnot (for sale|selling)\b", r"\bno intention\b", r"\bstaying\b", r"\bkeep (it|the)\b", r"\bnever sell\b"]),
]


def classify(text: str) -> dict[str, Any]:
    t = (text or "").lower()
    for cat, pats in RULES:
        hits = [m.group(0) for p in pats if (m := re.search(p, t))]
        if hits:
            if cat == "interested" and hits == ["yes"] and len(t) > 300:
                continue  # a lone "yes" in a long message is weak evidence
            conf = 0.9 if cat in ("requests_no_contact",) else min(0.85, 0.55 + 0.15 * len(hits))
            out = {"category": cat, "confidence": round(conf, 2), "evidence": ", ".join(f"“{h}”" for h in hits[:3]), "decline_reason": None}
            if cat in ("not_interested", "requests_no_contact"):
                out["decline_reason"] = decline_reason(t)
            return out
    return {"category": "unknown", "confidence": 0.0, "evidence": "No clear signal — please read and categorise", "decline_reason": None}


def decline_reason(t: str) -> str | None:
    for reason, pats in DECLINE_RULES:
        if any(re.search(p, t) for p in pats):
            return reason
    return None


AI_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "evidence_quote": {"type": "string"},
        "decline_reason": {"type": "string", "enum": DECLINE_REASONS + ["none"]},
    },
    "required": ["category", "evidence_quote", "decline_reason"],
    "additionalProperties": False,
}


def classify_ai(text: str, api_key: str, client=None) -> dict[str, Any] | None:
    """Optional second opinion. The quote must appear verbatim in the reply or it is discarded."""
    from plot.ai.extract import MODEL

    try:
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=400,
            system="Categorise a landowner's reply to a polite enquiry about buying land. If they ask for no further contact in any way, choose requests_no_contact. Quote the decisive words verbatim. Decline reasons describe the deal only, never personal characteristics.",
            messages=[{"role": "user", "content": text[:4000]}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": AI_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        data = json.loads(next(b.text for b in resp.content if getattr(b, "type", "") == "text"))
    except Exception:  # noqa: BLE001 — rules remain authoritative
        return None
    q = data.get("evidence_quote", "").strip()
    if not q or q.lower() not in text.lower():
        return None
    return {"category": data["category"], "confidence": 0.8, "evidence": f"“{q}”", "decline_reason": None if data["decline_reason"] == "none" else data["decline_reason"]}


def record_inbound(db: Session, lead_id: int, *, channel: str, body: str, actor: str, from_name: str | None = None, from_address: str | None = None, subject: str | None = None, received_at: dt.datetime | None = None, provider_message_id: str | None = None, outreach_id: int | None = None, client=None) -> InboundMessage:
    from plot.outreach.drafting import _api_key

    if outreach_id is None:
        last = db.scalar(select(Outreach).where(Outreach.lead_id == lead_id, Outreach.status.in_(("sent", "posted"))).order_by(Outreach.sent_at.desc()).limit(1))
        outreach_id = last.id if last else None
    res = classify(body)
    source = "rules"
    if res["category"] != "requests_no_contact":
        key = _api_key(db)
        if key or client is not None:
            ai = classify_ai(body, key or "", client)
            if ai and ai["category"] == "requests_no_contact":
                res, source = ai, "ai"  # err on the side of respecting an opt-out
            elif ai and res["category"] == "unknown":
                res, source = ai, "ai"
    msg = InboundMessage(
        lead_id=lead_id,
        outreach_id=outreach_id,
        channel=channel,
        from_name=from_name,
        from_address=from_address,
        subject=subject,
        body=body,
        received_at=received_at or utcnow(),
        provider_message_id=provider_message_id,
        category=res["category"],
        category_source=source,
        category_confidence=res["confidence"],
        evidence=res["evidence"],
        decline_reason=res.get("decline_reason"),
    )
    db.add(msg)
    db.flush()
    db.add(AuditLog(actor=actor, action="reply.received", entity="lead", entity_id=str(lead_id), detail={"inbound_id": msg.id, "category": msg.category, "source": source}))
    apply_category(db, msg, actor)
    return msg


def apply_category(db: Session, msg: InboundMessage, actor: str) -> None:
    """Act on the (possibly re-)categorised reply. Opt-outs take effect immediately."""
    from plot.offmarket.agent import STAGE_ORDER, set_stage
    from plot.outreach.drafting import create_draft

    lead = db.get(Lead, msg.lead_id)
    contacts = db.scalars(select(Contact).where(Contact.lead_id == lead.id)).all()
    if msg.category in ("requests_no_contact", "not_interested"):
        for c in contacts:
            C.add_suppression(db, c, reason="Owner asked not to be contacted" if msg.category == "requests_no_contact" else "Owner declined", source="reply", actor=actor)
        for o in db.scalars(select(Outreach).where(Outreach.lead_id == lead.id, Outreach.status.in_(("draft", "needs_changes", "needs_compliance_review", "awaiting_approval", "approved")))).all():
            o.status = "withdrawn"
            o.review_reason = "Withdrawn automatically: the owner " + ("asked for no further contact" if msg.category == "requests_no_contact" else "declined")
        lead.decline_reason = "requests_no_contact" if msg.category == "requests_no_contact" else (msg.decline_reason or "not_selling")
        lead.closed_reason = f"Owner {'asked for no further contact' if msg.category == 'requests_no_contact' else 'declined'} ({msg.received_at:%d %b %Y})"
        set_stage(db, lead, "closed", actor, lead.closed_reason)
        msg.handled = msg.category == "requests_no_contact"  # nothing to do — and no reply will be drafted
        db.flush()
        return
    if msg.category in ("interested", "possibly_interested", "wants_information", "requests_price"):
        if STAGE_ORDER.get(lead.stage, 0) < STAGE_ORDER["interested"]:
            set_stage(db, lead, "interested", actor, f"Owner replied: {msg.category.replace('_', ' ')}")
        if msg.suggested_reply_id is None:
            parent = db.get(Outreach, msg.outreach_id) if msg.outreach_id else None
            channel = "email" if (msg.channel == "email" and msg.from_address) else "post"
            if channel == "email":
                _record_reply_address(db, lead, msg, actor)
            try:
                create_draft(db, lead, actor, channel=channel, kind="reply", parent=parent, inbound=msg)
            except ValueError:
                pass  # e.g. no verified contact: the reply is still shown for you to handle
    db.flush()


def _record_reply_address(db: Session, lead: Lead, msg: InboundMessage, actor: str) -> None:
    """An owner who emails you has initiated contact by email: record that as the basis."""
    from plot.offmarket.agent import primary_contact

    c = primary_contact(db, lead)
    if c is None or not msg.from_address:
        return
    if c.email != msg.from_address or c.email_basis != "owner_initiated":
        c.email = msg.from_address
        c.email_basis = "owner_initiated"
        c.notes = (c.notes or "") + f" Email recorded from the owner's reply on {msg.received_at:%d %b %Y}."
        db.add(AuditLog(actor=actor, action="contact.email_from_reply", entity="contact", entity_id=str(c.id), detail={"inbound_id": msg.id}))


def recategorise(db: Session, msg: InboundMessage, category: str, actor: str, decline: str | None = None) -> InboundMessage:
    if category not in CATEGORIES:
        raise ValueError("unknown category")
    if msg.category == "requests_no_contact" and category != "requests_no_contact":
        raise ValueError("an opt-out cannot be reversed here; the owner must ask to hear from you again")
    if msg.category == "not_interested" and category not in ("not_interested", "requests_no_contact"):
        # you judged the decline was misread: lift the automatic suppression and reopen
        lead = db.get(Lead, msg.lead_id)
        for c in db.scalars(select(Contact).where(Contact.lead_id == lead.id)).all():
            for _, h in C.contact_keys(c):
                for s in db.scalars(select(Suppression).where(Suppression.match_hash == h, Suppression.source == "reply", Suppression.reason == "Owner declined")).all():
                    db.delete(s)
        lead.decline_reason = lead.closed_reason = None
        from plot.offmarket.agent import set_stage

        set_stage(db, lead, "awaiting_response", actor, "Reopened: you re-categorised the owner's reply")
    msg.category, msg.category_source, msg.category_confidence = category, "user", 1.0
    if decline:
        msg.decline_reason = decline
    db.add(AuditLog(actor=actor, action="reply.recategorised", entity="inbound", entity_id=str(msg.id), detail={"category": category}))
    apply_category(db, msg, actor)
    return msg


def negotiation_brief(db: Session, lead: Lead) -> dict[str, Any]:
    """Preliminary, private brief for you — never sent and never an offer."""
    a = db.scalar(select(Assessment).where(Assessment.site_id == lead.site_id, Assessment.is_current.is_(True)))
    if a is None:
        return {"available": False, "note": "No feasibility assessment yet — run one before discussing figures."}
    r = a.residual or {}
    mv = r.get("market_value") or {}
    sens = sorted(r.get("sensitivity") or [], key=lambda s: s["delta"])[:3]
    unknowns = [i for i in (a.site_risk or {}).get("items", []) if i.get("status") in ("unknown", "requires_investigation")]
    walk = r.get("max_recommended_offer")
    prudent = r.get("prudent_offer")
    opening = int(prudent * 0.85 // 1000 * 1000) if prudent else None
    an = lead.analysis or {}
    return {
        "available": True,
        "private": True,
        "walk_away_gbp": walk,
        "prudent_offer_gbp": prudent,
        "suggested_opening_range_gbp": [opening, prudent] if opening and prudent else None,
        "basis": f"Residual land value against your £{r.get('budget', 0):,.0f} all-in budget (base case), after build, fees, contingency, finance and SDLT.",
        "comparables": mv if mv.get("available") else {"available": False, "note": mv.get("note") or "No reliable comparable sales evidence."},
        "biggest_risks": [{"factor": s["factor"], "reduces_value_by_gbp": -s["delta"]} for s in sens],
        "unknowns_to_resolve": [i.get("title") for i in unknowns][:6],
        "structures": [
            "Conditional contract: exchange now, complete only on satisfactory planning permission (owner keeps the land if refused).",
            "Option agreement: a modest option fee for a fixed period while you apply for planning; price set now or by formula.",
            "Overage/clawback: share of uplift if a larger scheme is later permitted — can bridge a gap in price expectations.",
        ],
        "questions_for_owner": [
            "Exactly which land would be included (a sketch on a plan helps)?",
            "How access would work and whether any rights of way or shared drains are involved.",
            "Whether they'd accept a sale conditional on planning permission, and their preferred timing.",
            "Whether a mortgage lender would need to consent to releasing part of the title.",
        ] + (["Any covenants on the title that would restrict a new dwelling."] if an.get("type") == "garden_subdivision" else []),
        "rules": "Never send a figure without your explicit approval; any figure should be 'subject to contract, survey and planning'.",
        "generated_at": utcnow().isoformat(),
    }


DECLINE_LABELS = {
    "not_selling": "Unwilling to sell",
    "price_expectations": "Price expectations",
    "privacy": "Loss of privacy / amenity",
    "planning_concerns": "Planning concerns",
    "timing": "Not the right time",
    "family_reasons_unspecified": "Personal reasons (not recorded)",
    "requests_no_contact": "Asked for no further contact",
    "other": "Other",
}


def decline_feedback(db: Session) -> dict[str, Any]:
    """Aggregate decline reasons by opportunity type — used to tune qualification and wording."""
    rows = db.execute(select(Lead.opportunity_type, Lead.decline_reason).where(Lead.decline_reason.is_not(None))).all()
    by_reason: dict[str, int] = {}
    by_type: dict[str, dict[str, int]] = {}
    for typ, reason in rows:
        by_reason[reason] = by_reason.get(reason, 0) + 1
        by_type.setdefault(typ, {})[reason] = by_type.setdefault(typ, {}).get(reason, 0) + 1
    tips = []
    if by_reason.get("privacy", 0) >= 2:
        tips.append("Privacy is a common concern: favour plots with their own frontage and good separation; mention a single-storey design where suitable.")
    if by_reason.get("price_expectations", 0) >= 2:
        tips.append("Price expectations recur: consider conditional or option structures earlier in conversations.")
    if by_reason.get("planning_concerns", 0) >= 2:
        tips.append("Planning concerns recur: prioritise sites screened as 'plausible route' or better.")
    return {"by_reason": {DECLINE_LABELS.get(k, k): v for k, v in by_reason.items()}, "by_type": by_type, "tips": tips}

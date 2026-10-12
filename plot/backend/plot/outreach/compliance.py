"""UK GDPR / PECR safeguards, hash-bound approval and the send gate.

Owner outreach is treated as potentially direct marketing:
- post to a verified registered proprietor, relying on documented legitimate interests;
- email to an individual (incl. sole traders and most partnerships) only when the owner
  wrote first or consent is recorded — a published address is never permission;
- email to a company / public body (corporate subscriber) with identity and opt-out;
- anything uncertain (personal representatives, trustees, unclear recipient type) goes to
  manual compliance review instead of being sendable.

Approval binds the exact content to the exact recipient: approved_hash = H(content, recipient).
Any edit to either clears approval; the send gate recomputes both before anything leaves.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from plot.models import AuditLog, Contact, InboundMessage, Lead, MailAccount, Outreach, Suppression, TitleDocument, utcnow
from plot.preferences import load_preferences

TERMINAL = ("sent", "posted", "rejected", "withdrawn", "superseded", "failed")
PENDING = ("draft", "needs_changes", "needs_compliance_review", "awaiting_approval", "approved", "sending")
EMAIL_INDIVIDUAL_BASES = ("owner_initiated", "consent_recorded")
CORPORATE = ("company", "public_body")
REVIEW_KINDS = ("personal_representatives", "trustees", "charity", "partnership")


class ComplianceError(ValueError):
    pass


def _n(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _pc(s: str | None) -> str:
    return re.sub(r"\s+", "", (s or "").upper())


def sha(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def content_hash(o: Outreach) -> str:
    return sha("content", o.channel, o.subject or "", o.body or "", o.footer or "")


def recipient_hash_for(channel: str, contact: Contact | None, *, name: str | None = None, address: str | None = None, email: str | None = None) -> str:
    if contact is not None:
        name, address = contact.name, "\n".join(list(contact.address_lines or []) + ([contact.postcode] if contact.postcode else []))
        email = contact.email if channel == "email" else None
    return sha("recipient", channel, _n(name), [_n(x) for x in (address or "").splitlines() if x.strip()], _n(email) if channel == "email" else "")


def approval_hash(content: str, recipient: str) -> str:
    return sha("approval", content, recipient)


def suppression_keys(name: str | None, address_lines: list[str] | None, postcode: str | None, email: str | None) -> list[tuple[str, str]]:
    keys = []
    first = _n((address_lines or [""])[0]) if address_lines else ""
    pc = _pc(postcode)
    if name and (first or pc):
        keys.append(("name_address", sha("na", _n(name), first, pc)))
    if first and pc:
        keys.append(("address", sha("a", first, pc)))
    if email:
        keys.append(("email", sha("e", _n(email))))
    return keys


def contact_keys(c: Contact) -> list[tuple[str, str]]:
    return suppression_keys(c.name, list(c.address_lines or []), c.postcode, c.email)


def suppressed(db: Session, c: Contact) -> Suppression | None:
    hashes = [h for _, h in contact_keys(c)]
    if not hashes:
        return None
    return db.scalar(select(Suppression).where(Suppression.match_hash.in_(hashes)).limit(1))


def add_suppression(db: Session, c: Contact, reason: str, source: str, actor: str, *, include_address: bool = True) -> int:
    """Permanent do-not-contact. Stores one-way hashes plus a minimal label."""
    n = 0
    label = f"{(c.name or '?')[:1]}. … — {c.postcode or (c.email or '').split('@')[-1]}"
    for kind, h in contact_keys(c):
        if kind == "address" and not include_address:
            continue
        if db.scalar(select(Suppression).where(Suppression.match_hash == h)) is None:
            db.add(Suppression(match_hash=h, kind=kind, label=label, reason=reason, source=source))
            n += 1
    db.add(AuditLog(actor=actor, action="suppression.added", entity="contact", entity_id=str(c.id), detail={"reason": reason, "source": source, "entries": n}))
    db.flush()
    return n


# --------------------------------------------------------------------------- checks


def _check(code: str, label: str, status: str, detail: str = "") -> dict[str, str]:
    return {"code": code, "label": label, "status": status, "detail": detail}


def evaluate(db: Session, o: Outreach, *, at_send: bool = False) -> dict[str, Any]:
    from plot.outreach.writer import PRICE_RE

    prefs, _ = load_preferences(db)
    b, cfg = prefs.buyer, prefs.outreach
    lead = db.get(Lead, o.lead_id)
    c = db.get(Contact, o.contact_id) if o.contact_id else None
    checks: list[dict[str, str]] = []
    cleared = (o.compliance or {}).get("review_cleared")

    # 1. who is writing
    ok_identity = b.facts_confirmed and bool(b.first_name) and bool(b.reply_postal_address or b.reply_email) and (b.persona != "represented" or bool(b.agent_name))
    checks.append(_check("buyer_profile", "Your identity and buyer facts are confirmed", "pass" if ok_identity else "fail", "" if ok_identity else "Confirm your buyer profile (name, persona, reply address/email) in Settings → Off-market"))

    # 2. recipient verified from an official source
    if c is None:
        checks.append(_check("recipient_verified", "Recipient verified", "fail", "No verified recipient"))
    else:
        doc_ok = True
        if c.source == "title_register":
            doc = db.get(TitleDocument, c.source_document_id) if c.source_document_id else None
            doc_ok = doc is not None and doc.status == "verified"
        ok = c.verified and doc_ok and lead.ownership_status == "verified" if c.source == "title_register" else c.verified
        checks.append(_check("recipient_verified", "Recipient verified from the register (or by the owner)", "pass" if ok else "fail", f"Source: {c.source.replace('_', ' ')}" + ("" if ok else " — not verified")))

    # 3. recipient type & channel eligibility
    kind = (c.kind if c else "unknown") or "unknown"
    if kind == "unknown":
        checks.append(_check("recipient_type", "Recipient type known", "fail", "Recipient type unknown — record whether this is an individual, company or other body"))
    elif kind in REVIEW_KINDS:
        checks.append(_check("recipient_type", "Recipient type", "pass" if cleared else "review", f"{kind.replace('_', ' ')} — sensitive or unclear; manual compliance review required"))
    else:
        checks.append(_check("recipient_type", "Recipient type", "pass", kind.replace("_", " ")))
    if o.channel == "email":
        if not (c and c.email):
            checks.append(_check("channel", "Email permitted", "fail", "No email address for this recipient"))
        elif kind in CORPORATE:
            ok = c.email_basis in ("corporate_subscriber", *EMAIL_INDIVIDUAL_BASES)
            checks.append(_check("channel", "Email permitted (corporate subscriber)", "pass" if ok else "review", "" if ok else "Confirm this is a corporate address and record the basis"))
        elif c.email_basis in EMAIL_INDIVIDUAL_BASES:
            checks.append(_check("channel", "Email permitted (individual)", "pass", f"Basis: {c.email_basis.replace('_', ' ')}"))
        else:
            checks.append(_check("channel", "Email permitted (individual)", "fail", "Unsolicited marketing email to an individual needs prior consent or that they wrote to you first. Publication of an address is not permission. Send a letter instead."))
        acct = db.scalar(select(MailAccount).where(MailAccount.status == "connected").limit(1))
        if acct is None:
            checks.append(_check("mailbox", "Mailbox connected", "fail" if at_send else "warn", "Connect Gmail or Outlook in Admin → Mail to send email"))
    else:
        has_addr = bool(c and c.address_lines and c.postcode)
        checks.append(_check("channel", "Postal address", "pass" if has_addr else "fail", "" if has_addr else "Recipient postal address incomplete"))
        if kind not in CORPORATE:
            lia = bool(cfg.lia_summary and len(cfg.lia_summary) > 80)
            checks.append(_check("lawful_basis", "Legitimate interests assessment recorded", "pass" if lia else "fail", "" if lia else "Record your legitimate interests assessment in Settings → Off-market"))

    # 4. do-not-contact & lead state
    sup = suppressed(db, c) if c else None
    checks.append(_check("suppression", "Not on the do-not-contact list", "fail" if sup else "pass", f"Suppressed: {sup.reason}" if sup else ""))
    opted_out = db.scalar(select(func.count()).select_from(InboundMessage).where(InboundMessage.lead_id == lead.id, InboundMessage.category == "requests_no_contact"))
    if opted_out or lead.decline_reason == "requests_no_contact":
        checks.append(_check("lead_state", "Owner has not opted out", "fail", "The owner asked not to be contacted"))
    elif (lead.decline_reason or lead.stage == "closed") and o.kind != "reply":
        checks.append(_check("lead_state", "Owner has not declined", "fail", f"Declined/closed: {lead.decline_reason or lead.closed_reason or 'closed'}"))
    else:
        checks.append(_check("lead_state", "Owner has not declined or opted out", "pass"))

    # 5. frequency & follow-up rules (replies to an owner who wrote are exempt from frequency)
    if c is not None and o.kind in ("initial", "follow_up"):
        sent = db.scalars(select(Outreach).where(Outreach.id != o.id, Outreach.status.in_(("sent", "posted", "sending", "delivery_unknown")), or_(Outreach.contact_id == c.id, Outreach.recipient_hash == o.recipient_hash))).all()
        now = utcnow()
        recent = [x for x in sent if x.sent_at and (now - x.sent_at).days < cfg.min_days_between_contacts]
        year = [x for x in sent if x.sent_at and (now - x.sent_at).days < 365]
        if any(x.status in ("sending", "delivery_unknown") for x in sent):
            checks.append(_check("frequency", "Contact frequency", "fail", "An earlier message is still sending or its delivery is unconfirmed"))
        elif recent:
            checks.append(_check("frequency", "Contact frequency", "fail", f"Contacted {min((now - x.sent_at).days for x in recent)} days ago (minimum {cfg.min_days_between_contacts})"))
        elif len(year) >= cfg.max_contacts_per_year:
            checks.append(_check("frequency", "Contact frequency", "fail", f"Already contacted {len(year)} times in 12 months (maximum {cfg.max_contacts_per_year})"))
        else:
            checks.append(_check("frequency", "Contact frequency", "pass", f"{len(year)} contact(s) in the last 12 months"))
        initials = [x for x in sent if x.kind == "initial"]
        if o.kind == "initial" and initials:
            checks.append(_check("duplicate", "Not a repeat first approach", "fail", "This owner has already been sent a first approach"))
        if o.kind == "follow_up":
            inbound = db.scalar(select(func.count()).select_from(InboundMessage).where(InboundMessage.lead_id == lead.id))
            prior_fu = [x for x in sent if x.kind == "follow_up"]
            if not initials:
                checks.append(_check("follow_up", "Follow-up rules", "fail", "No first approach has been sent"))
            elif inbound:
                checks.append(_check("follow_up", "Follow-up rules", "fail", "The owner has replied — answer the reply instead"))
            elif len(prior_fu) >= cfg.max_follow_ups:
                checks.append(_check("follow_up", "Follow-up rules", "fail", f"Maximum of {cfg.max_follow_ups} follow-up reached"))
            else:
                days = (utcnow() - min(x.sent_at for x in initials if x.sent_at)).days
                ok = days >= cfg.follow_up_after_days
                checks.append(_check("follow_up", "Follow-up rules", "pass" if ok else "fail", f"{days} days since the first approach (minimum {cfg.follow_up_after_days})"))
    dup = db.scalar(select(Outreach).where(Outreach.id != o.id, Outreach.recipient_hash == o.recipient_hash, Outreach.status.in_(("awaiting_approval", "approved", "sending"))).limit(1))
    if dup is not None:
        checks.append(_check("duplicate_pending", "No other pending message to this recipient", "fail", f"Message #{dup.id} to the same recipient is {dup.status.replace('_', ' ')}"))

    # 6. retention
    if c is not None:
        ok = c.retention_until is None or c.retention_until >= utcnow().date()
        checks.append(_check("retention", "Within the data-retention period", "pass" if ok else "fail", f"Retain until {c.retention_until:%d %b %Y}" if c.retention_until else "No retention date set"))

    # 7. sensitive title circumstances
    if c is not None and c.source_document_id:
        doc = db.get(TitleDocument, c.source_document_id)
        if doc is not None and (doc.extracted or {}).get("personal_representatives"):
            checks.append(_check("sensitive", "Sensitive circumstances", "pass" if cleared else "review", "Title held by personal representatives — consider whether contact is appropriate now"))

    # 8. content
    from plot.outreach.drafting import facts_for

    facts, confirmed = facts_for(db, o)
    from plot.outreach.writer import Draft, blocking, validate

    issues = validate(Draft(o.subject or "", o.body or "", o.footer or "", o.generator or "manual"), facts, buyer_confirmed=confirmed)
    blk = blocking(issues)
    checks.append(_check("content", "Message passes the writing and honesty rules", "fail" if blk else "pass", "; ".join(i["message"] for i in blk) if blk else (f"{len(issues)} suggestion(s)" if issues else "")))
    if PRICE_RE.search(f"{o.subject}\n{o.body}") and o.kind != "reply":
        checks.append(_check("price", "No price in a first approach", "fail", "Remove the figure — prices are only discussed once the owner asks, and only with your explicit approval"))

    statuses = {x["status"] for x in checks}
    verdict = "blocked" if "fail" in statuses else ("manual_review" if "review" in statuses else "pass")
    return {"verdict": verdict, "checks": checks, "issues": issues, "checked_at": utcnow().isoformat(), "review_cleared": cleared}


def apply(db: Session, o: Outreach) -> dict[str, Any]:
    """Recompute hashes, re-run checks and set the draft status accordingly (never beyond awaiting_approval)."""
    if o.status in TERMINAL or o.status in ("approved", "sending", "delivery_unknown"):
        return o.compliance
    o.content_hash = content_hash(o)
    c = db.get(Contact, o.contact_id) if o.contact_id else None
    o.recipient_hash = recipient_hash_for(o.channel, c, name=o.recipient_name, address=o.recipient_address, email=o.recipient_email)
    res = evaluate(db, o)
    o.compliance = res
    if res["verdict"] == "pass":
        o.status, o.review_reason = "awaiting_approval", None
    elif res["verdict"] == "manual_review":
        o.status = "needs_compliance_review"
        o.review_reason = "; ".join(x["detail"] or x["label"] for x in res["checks"] if x["status"] == "review")
    else:
        o.status = "needs_changes"
        o.review_reason = "; ".join(f"{x['label']}: {x['detail']}" for x in res["checks"] if x["status"] == "fail")
    db.flush()
    return res


def clear_review(db: Session, o: Outreach, actor: str, note: str) -> dict[str, Any]:
    if o.status != "needs_compliance_review":
        raise ComplianceError("only drafts in manual compliance review can be cleared")
    if len((note or "").strip()) < 15:
        raise ComplianceError("record why contact is appropriate (at least a sentence)")
    o.compliance = (o.compliance or {}) | {"review_cleared": {"by": actor, "at": utcnow().isoformat(), "note": note.strip()}}
    db.add(AuditLog(actor=actor, action="outreach.review_cleared", entity="outreach", entity_id=str(o.id), detail={"note": note.strip()}))
    return apply(db, o)


def approve(db: Session, o: Outreach, actor: str, *, confirm_recipient_hash: str, confirm_price: bool = False) -> None:
    """Approve one message for one recipient. There is deliberately no bulk variant."""
    from plot.outreach.writer import PRICE_RE

    if o.status != "awaiting_approval":
        raise ComplianceError(f"cannot approve a message that is {o.status.replace('_', ' ')}")
    res = apply(db, o)
    if res["verdict"] != "pass" or o.status != "awaiting_approval":
        raise ComplianceError("checks no longer pass: " + (o.review_reason or "see compliance panel"))
    if confirm_recipient_hash != o.recipient_hash:
        raise ComplianceError("recipient confirmation does not match the current recipient — review the recipient and try again")
    if PRICE_RE.search(f"{o.subject}\n{o.body}") and not confirm_price:
        raise ComplianceError("this message contains a figure — tick the price confirmation to approve it")
    o.approved_hash = approval_hash(o.content_hash, o.recipient_hash)
    o.status = "approved"
    o.approved_at = utcnow()
    o.approved_by = actor
    o.generator_meta = (o.generator_meta or {}) | {"price_confirmed": bool(confirm_price)}
    db.add(AuditLog(actor=actor, action="outreach.approved", entity="outreach", entity_id=str(o.id), detail={"approved_hash": o.approved_hash, "recipient_hash": o.recipient_hash, "channel": o.channel, "kind": o.kind}))
    db.flush()


def assert_sendable(db: Session, o: Outreach) -> None:
    """The send gate: exact approved content, exact approved recipient, checks still pass."""
    if o.status != "approved" or not o.approved_hash:
        raise ComplianceError(f"message is {o.status.replace('_', ' ')}, not approved")
    if content_hash(o) != o.content_hash:
        raise ComplianceError("content changed after approval")
    c = db.get(Contact, o.contact_id) if o.contact_id else None
    rh = recipient_hash_for(o.channel, c, name=o.recipient_name, address=o.recipient_address, email=o.recipient_email)
    if rh != o.recipient_hash:
        raise ComplianceError("recipient changed after approval")
    if approval_hash(o.content_hash, o.recipient_hash) != o.approved_hash:
        raise ComplianceError("approval does not match this content and recipient")
    res = evaluate(db, o, at_send=True)
    if res["verdict"] != "pass":
        raise ComplianceError("checks no longer pass: " + "; ".join(f"{x['label']}: {x['detail']}" for x in res["checks"] if x["status"] in ("fail", "review")))


def invalidate_for_contact(db: Session, contact: Contact, actor: str) -> int:
    """A recipient detail changed: every unsent message to them loses its approval."""
    n = 0
    for o in db.scalars(select(Outreach).where(Outreach.contact_id == contact.id, Outreach.status.in_(PENDING))).all():
        if o.status == "sending":
            continue
        o.recipient_name = contact.name
        o.recipient_address = "\n".join(list(contact.address_lines or []) + ([contact.postcode] if contact.postcode else []))
        o.recipient_email = contact.email if o.channel == "email" else None
        if o.status == "approved":
            o.status, o.approved_hash, o.approved_at, o.approved_by = "draft", None, None, None
            db.add(AuditLog(actor=actor, action="outreach.approval_cleared", entity="outreach", entity_id=str(o.id), detail={"reason": "recipient changed"}))
        apply(db, o)
        n += 1
    return n


def retention_sweep(db: Session, today: dt.date | None = None) -> int:
    """Delete personal data past its retention date unless a conversation is live."""
    today = today or utcnow().date()
    n = 0
    for c in db.scalars(select(Contact).where(Contact.retention_until.is_not(None), Contact.retention_until < today)).all():
        lead = db.get(Lead, c.lead_id)
        if lead and lead.stage in ("interested", "feasibility_discussion", "negotiation", "under_offer"):
            continue
        c.name = "[deleted after retention period]"
        c.salutation = None
        c.address_lines = []
        c.email = None
        c.verified = False
        c.notes = f"Personal data deleted {today:%d %b %Y} (retention)."
        for o in db.scalars(select(Outreach).where(Outreach.contact_id == c.id)).all():
            o.recipient_name, o.recipient_address, o.recipient_email, o.pdf = "[deleted]", None, None, None
            if o.status in PENDING:
                o.status = "withdrawn"
        n += 1
    for d in db.scalars(select(TitleDocument).where(TitleDocument.retention_until.is_not(None), TitleDocument.retention_until < today)).all():
        lead = db.get(Lead, d.lead_id) if d.lead_id else None
        if lead and lead.stage in ("interested", "feasibility_discussion", "negotiation", "under_offer"):
            continue
        d.content, d.text = b"", None
        d.extracted = {"deleted": True, "title_number": d.title_number}
        n += 1
    db.flush()
    return n

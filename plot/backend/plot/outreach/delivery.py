"""Delivery: printable letters (manual post or an authorised postal API) and email through a
connected mailbox. Every path goes through the send gate, records what actually happened,
and never resends automatically.
"""

from __future__ import annotations

import datetime as dt
import email.utils
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from plot.config import get_settings
from plot.models import AuditLog, InboundMessage, Lead, MailAccount, Outreach, utcnow
from plot.outreach import compliance as C
from plot.outreach import letters, mail
from plot.preferences import load_preferences
from plot.security.crypto import get_secret

STANNP_URL = "https://api-eu1.stannp.com/v1/letters/create"


class DeliveryError(RuntimeError):
    pass


def _sender(db: Session) -> dict[str, Any]:
    b = load_preferences(db)[0].buyer
    name = " ".join(x for x in (b.first_name, b.last_name) if x)
    if b.persona == "represented" and b.agent_name:
        name = f"{b.agent_name}{(', ' + b.agent_company) if b.agent_company else ''} (for {name})"
    return {"name": name, "address": b.reply_postal_address, "email": b.reply_email, "phone": b.reply_phone, "display": name}


def letter_pdf(db: Session, o: Outreach, *, final: bool) -> bytes:
    """Draft PDFs carry a watermark; the final PDF exists only for an approved letter."""
    if o.channel != "post":
        raise DeliveryError("not a letter")
    if final:
        if o.status == "approved":
            C.assert_sendable(db, o)
        if o.status not in ("approved", "posted"):
            raise DeliveryError("the printable letter is available once you approve it")
        if o.pdf is None:
            s = _sender(db)
            when = (o.approved_at or utcnow()).date()
            o.pdf = letters.render_letter(o, sender_name=s["name"], sender_address=s["address"], sender_email=s["email"], sender_phone=s["phone"], letter_date=when, draft=False)
            o.delivery_detail = (o.delivery_detail or {}) | {"pdf_sha256": letters.pdf_sha(o.pdf), "pdf_bound_to": o.approved_hash}
            db.flush()
        return o.pdf
    s = _sender(db)
    return letters.render_letter(o, sender_name=s["name"], sender_address=s["address"], sender_email=s["email"], sender_phone=s["phone"], letter_date=utcnow().date(), draft=True)


def _contacted(db: Session, o: Outreach, actor: str, note: str) -> None:
    from plot.offmarket.agent import STAGE_ORDER, set_stage

    lead = db.get(Lead, o.lead_id)
    lead.last_contact_at = o.sent_at
    if o.kind in ("initial", "follow_up") and STAGE_ORDER.get(lead.stage, 0) < STAGE_ORDER["contacted"]:
        set_stage(db, lead, "contacted", actor, note)
    elif o.kind == "follow_up":
        from plot.outreach.drafting import lead_event

        lead_event(db, lead, actor, note)


def mark_posted(db: Session, o: Outreach, actor: str, *, posted_on: dt.date, postage_paid_gbp: float | None = None) -> Outreach:
    """You posted the approved letter yourself. Recorded as self-declared (no tracking)."""
    if o.channel != "post":
        raise DeliveryError("not a letter")
    C.assert_sendable(db, o)
    if posted_on > utcnow().date() or posted_on < (o.approved_at or utcnow()).date() - dt.timedelta(days=1):
        raise DeliveryError("posting date must be between approval and today")
    pdf = letter_pdf(db, o, final=True)
    o.status = "posted"
    o.provider = "manual_post"
    o.delivery_status = "posted_by_you"
    now = utcnow()
    o.sent_at = now if posted_on == now.date() else dt.datetime.combine(posted_on, dt.time(12, 0), tzinfo=dt.UTC)
    o.delivery_detail = (o.delivery_detail or {}) | {"declared_by": actor, "postage_paid_gbp": postage_paid_gbp, "pdf_sha256": letters.pdf_sha(pdf), "note": "Self-declared posting; Royal Mail delivery is not tracked."}
    db.add(AuditLog(actor=actor, action="outreach.posted", entity="outreach", entity_id=str(o.id), detail={"posted_on": posted_on.isoformat(), "approved_hash": o.approved_hash}))
    _contacted(db, o, actor, f"Letter {o.generator_meta.get('reference', o.id)} posted by you on {posted_on:%d %b %Y}")
    db.flush()
    return o


def submit_stannp(db: Session, o: Outreach, actor: str, *, confirm_cost_gbp: float, transport: httpx.BaseTransport | None = None) -> Outreach:
    """Submit an approved letter to Stannp (authorised UK print-and-post service).
    In test mode Stannp produces a proof only: nothing is printed or posted, and the letter stays approved."""
    prefs, _ = load_preferences(db)
    if get_settings().is_sandbox:
        raise DeliveryError("Postal submission is disabled in the sandbox environment")
    if prefs.outreach.postal_provider != "stannp":
        raise DeliveryError("Stannp is not selected as the postal provider (Settings → Off-market)")
    key = get_secret(db, "stannp_api_key")
    if not key:
        raise DeliveryError("Set stannp_api_key in Admin → Integrations")
    C.assert_sendable(db, o)
    if confirm_cost_gbp is None or confirm_cost_gbp < 0:
        raise DeliveryError("confirm the postage cost before submission")
    test = prefs.outreach.postal_provider_test_mode
    if not test and o.idempotency_key:
        raise DeliveryError("this letter has already been submitted once")
    pdf = letter_pdf(db, o, final=True)
    lines = (o.recipient_address or "").splitlines()
    pc = lines[-1] if lines else ""
    addr = lines[:-1]
    data = {
        "test": "true" if test else "false",
        "recipient[company]" if o.recipient_type in ("company", "public_body") else "recipient[lastname]": o.recipient_name,
        "recipient[address1]": addr[0] if addr else "",
        "recipient[address2]": addr[1] if len(addr) > 2 else "",
        "recipient[city]": addr[-1] if len(addr) > 1 else "",
        "recipient[postcode]": pc,
        "recipient[country]": "GB",
        "duplex": "false",
        "clearzone": "true",
    }
    if not test:
        o.idempotency_key = C.sha("stannp", o.id, o.approved_hash)[:64]
        o.status = "sending"
        o.send_attempted_at = utcnow()
        db.commit()
    try:
        with httpx.Client(timeout=60, transport=transport) as c:
            r = c.post(STANNP_URL, auth=(key, ""), data=data, files={"file": (f"letter-{o.id}.pdf", pdf, "application/pdf")})
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text[:300]}
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        if test:
            raise DeliveryError(f"Stannp test submission failed: {exc}") from exc
        o.status = "delivery_unknown"
        o.delivery_detail = (o.delivery_detail or {}) | {"provider": "stannp", "error": str(exc), "action": "Check your Stannp dashboard before doing anything else"}
        db.add(AuditLog(actor=actor, action="outreach.delivery_unknown", entity="outreach", entity_id=str(o.id), detail={"error": str(exc)}))
        db.flush()
        return o
    ok = r.status_code < 400 and body.get("success") is True
    d = body.get("data") or {}
    if test:
        o.delivery_detail = (o.delivery_detail or {}) | {"stannp_test": {"ok": ok, "at": utcnow().isoformat(), "proof_pdf": d.get("pdf"), "cost": d.get("cost"), "status": d.get("status"), "http": r.status_code, "error": None if ok else str(body)[:300]}}
        db.add(AuditLog(actor=actor, action="outreach.postal_test", entity="outreach", entity_id=str(o.id), detail={"ok": ok}))
        db.flush()
        return o
    if not ok:
        o.status = "failed"
        o.delivery_status = "provider_rejected"
        o.delivery_detail = (o.delivery_detail or {}) | {"provider": "stannp", "http": r.status_code, "error": str(body)[:500]}
    else:
        o.status = "posted"
        o.provider = "stannp"
        o.provider_message_id = f"stannp:{d.get('id')}"
        o.delivery_status = "submitted_to_provider"
        o.sent_at = utcnow()
        o.postage_estimate_gbp = float(d.get("cost") or confirm_cost_gbp)
        o.delivery_detail = (o.delivery_detail or {}) | {"provider": "stannp", "stannp_id": d.get("id"), "status": d.get("status"), "cost": d.get("cost"), "confirmed_cost_gbp": confirm_cost_gbp}
        _contacted(db, o, actor, f"Letter {o.generator_meta.get('reference', o.id)} submitted to Stannp (id {d.get('id')})")
    db.add(AuditLog(actor=actor, action=f"outreach.{o.status}", entity="outreach", entity_id=str(o.id), detail={"provider": "stannp", "http": r.status_code}))
    db.flush()
    return o


def _mime_for(db: Session, o: Outreach, acct: MailAccount):
    s = _sender(db)
    parent = db.get(Outreach, o.parent_id) if o.parent_id else None
    in_reply_to = (parent.delivery_detail or {}).get("internet_message_id") if parent is not None else None
    domain = acct.email_address.split("@")[-1]
    msg_id = f"<plot.{(o.idempotency_key or C.sha('pre', o.id))[:32]}@{domain}>"
    text = f"{o.body}\n\n--\n{o.footer or ''}".strip()
    m = mail.build_mime(from_name=s["display"], from_addr=acct.email_address, to_name=o.recipient_name, to_addr=o.recipient_email or "", subject=o.subject or "", text=text, message_id=msg_id, reference=o.generator_meta.get("reference", str(o.id)), in_reply_to=in_reply_to)
    thread = parent.provider_thread_id if parent and parent.provider == acct.provider else None
    return m, thread


def create_mailbox_draft(db: Session, o: Outreach, actor: str, *, transport: httpx.BaseTransport | None = None) -> Outreach:
    """Put the draft in your own mailbox (Drafts folder) for reference. This contacts no one."""
    if o.channel != "email":
        raise DeliveryError("not an email")
    if o.status not in ("awaiting_approval", "approved"):
        raise DeliveryError("only drafts that pass the checks can be copied to your mailbox")
    acct = mail.connected_account(db)
    if acct is None:
        raise DeliveryError("connect Gmail or Outlook first (Admin → Mail)")
    m, thread = _mime_for(db, o, acct)
    with mail.Mailbox(db, acct, transport) as mb:
        r = mb.create_draft(m, thread)
    o.provider = "gmail" if acct.provider == "google" else "outlook"
    o.provider_draft_id = r["draft_id"]
    db.add(AuditLog(actor=actor, action="outreach.mailbox_draft", entity="outreach", entity_id=str(o.id), detail={"provider": acct.provider, "draft_id": r["draft_id"]}))
    db.flush()
    return o


def send_email(db: Session, o: Outreach, actor: str, *, transport: httpx.BaseTransport | None = None) -> Outreach:
    """Send exactly the approved content to exactly the approved recipient. Commits before calling
    the provider so a crash can never cause a silent resend."""
    if o.channel != "email":
        raise DeliveryError("not an email")
    if get_settings().is_sandbox:
        raise DeliveryError("Email sending is disabled in the sandbox environment")
    C.assert_sendable(db, o)
    if o.idempotency_key:
        raise DeliveryError("a send has already been attempted for this approval — reconcile it instead")
    acct = mail.connected_account(db)
    if acct is None:
        raise DeliveryError("connect Gmail or Outlook first (Admin → Mail)")
    attempt = int((o.delivery_detail or {}).get("attempt", 0)) + 1
    o.idempotency_key = C.sha("email", o.id, o.approved_hash, attempt)[:64]
    o.status = "sending"
    o.send_attempted_at = utcnow()
    o.provider = "gmail" if acct.provider == "google" else "outlook"
    m, thread = _mime_for(db, o, acct)
    o.delivery_detail = (o.delivery_detail or {}) | {"attempt": attempt, "internet_message_id": m["Message-ID"], "mailbox": acct.email_address}
    db.add(AuditLog(actor=actor, action="outreach.sending", entity="outreach", entity_id=str(o.id), detail={"approved_hash": o.approved_hash, "provider": acct.provider}))
    db.commit()
    try:
        with mail.Mailbox(db, acct, transport) as mb:
            r = mb.send(m, thread)
    except mail.MailError as exc:
        o.status = "delivery_unknown" if exc.ambiguous else "failed"
        o.delivery_status = "unknown" if exc.ambiguous else "provider_rejected"
        o.delivery_detail = (o.delivery_detail or {}) | {"error": str(exc), "action": "Use 'Check mailbox' to find out whether it was sent" if exc.ambiguous else "Fix the problem, then return the message for approval"}
        db.add(AuditLog(actor=actor, action=f"outreach.{o.status}", entity="outreach", entity_id=str(o.id), detail={"error": str(exc)}))
        db.commit()
        return o
    _record_sent(db, o, actor, r)
    db.commit()
    return o


def _record_sent(db: Session, o: Outreach, actor: str, r: dict[str, Any]) -> None:
    o.status = "sent"
    o.provider_message_id = str(r["message_id"])
    o.provider_thread_id = r.get("thread_id")
    o.delivery_status = "accepted_by_provider"
    o.sent_at = utcnow()
    o.delivery_detail = (o.delivery_detail or {}) | {"provider_response": {k: v for k, v in r.items() if k != "message_id"}}
    db.add(AuditLog(actor=actor, action="outreach.sent", entity="outreach", entity_id=str(o.id), detail={"provider_message_id": o.provider_message_id, "thread": o.provider_thread_id}))
    _contacted(db, o, actor, f"Email {o.generator_meta.get('reference', o.id)} accepted by {o.provider} (id {o.provider_message_id})")


def reconcile_email(db: Session, o: Outreach, actor: str, *, transport: httpx.BaseTransport | None = None) -> Outreach:
    """For an ambiguous send: look for our Message-ID in the mailbox."""
    if o.status != "delivery_unknown" or o.channel != "email":
        raise DeliveryError("nothing to reconcile")
    acct = mail.connected_account(db)
    if acct is None:
        raise DeliveryError("connect the mailbox first")
    mid = (o.delivery_detail or {}).get("internet_message_id")
    with mail.Mailbox(db, acct, transport) as mb:
        found = mb.find_by_message_id(mid) if mid else None
    if found:
        _record_sent(db, o, actor, found)
        o.delivery_detail = o.delivery_detail | {"reconciled": True}
    else:
        o.delivery_detail = (o.delivery_detail or {}) | {"reconcile_checked_at": utcnow().isoformat(), "found": False}
    db.flush()
    return o


def release_unsent(db: Session, o: Outreach, actor: str, note: str) -> Outreach:
    """You confirmed (e.g. in your Sent folder / provider dashboard) that it did not go out."""
    if o.status not in ("delivery_unknown", "failed"):
        raise DeliveryError("only failed or unconfirmed sends can be released")
    if len((note or "").strip()) < 10:
        raise DeliveryError("say how you confirmed it was not sent")
    o.idempotency_key = None
    o.approved_hash = o.approved_at = o.approved_by = None
    o.status = "draft"
    o.delivery_detail = (o.delivery_detail or {}) | {"released": {"by": actor, "at": utcnow().isoformat(), "note": note}}
    db.add(AuditLog(actor=actor, action="outreach.released", entity="outreach", entity_id=str(o.id), detail={"note": note}))
    C.apply(db, o)
    return o


def sync_replies(db: Session, *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Look for replies in the threads of messages we sent. Read-only on the mailbox."""
    from plot.outreach.replies import record_inbound

    out = {"checked": 0, "new": 0, "bounces": 0, "errors": []}
    for acct in db.scalars(select(MailAccount).where(MailAccount.status == "connected")).all():
        prov = "gmail" if acct.provider == "google" else "outlook"
        since = utcnow() - dt.timedelta(days=120)
        sent = db.scalars(select(Outreach).where(Outreach.provider == prov, Outreach.status == "sent", Outreach.provider_thread_id.is_not(None), Outreach.sent_at >= since)).all()
        try:
            with mail.Mailbox(db, acct, transport) as mb:
                for o in sent:
                    out["checked"] += 1
                    for m in mb.thread_messages(o.provider_thread_id):
                        frm = m["from"].lower()
                        if acct.email_address.lower() in frm or "SENT" in m.get("labels", []):
                            continue
                        if db.scalar(select(InboundMessage).where(InboundMessage.provider_message_id == m["id"])) is not None:
                            continue
                        if "mailer-daemon" in frm or "postmaster" in frm:
                            o.delivery_status = "bounced"
                            o.delivery_detail = (o.delivery_detail or {}) | {"bounce": mail.strip_quoted(m["body"])[:500]}
                            out["bounces"] += 1
                            continue
                        name, addr = email.utils.parseaddr(m["from"])
                        record_inbound(db, o.lead_id, channel="email", body=mail.strip_quoted(m["body"]) or m["body"], from_name=name, from_address=addr, subject=m.get("subject"), received_at=m["date"], provider_message_id=m["id"], outreach_id=o.id, actor="system")
                        out["new"] += 1
            acct.last_sync_at = utcnow()
            acct.last_error = None
        except Exception as exc:  # noqa: BLE001 — recorded on the account and in the run
            acct.last_error = f"{type(exc).__name__}: {exc}"
            out["errors"].append(f"{acct.email_address}: {acct.last_error}")
    db.flush()
    return out

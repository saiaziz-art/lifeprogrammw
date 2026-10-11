"""Digest and alert delivery (SMTP email and/or webhook such as ntfy). Every attempt is
recorded; nothing is reported as sent unless the transport accepted it."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

import httpx
from sqlalchemy.orm import Session

from plot.models import AuditLog, Digest, utcnow
from plot.sources.registry import load_secrets


def _smtp_ready(secrets: dict) -> bool:
    return all(secrets.get(k) for k in ("smtp_host", "smtp_user", "smtp_password", "smtp_from"))


def send_email(secrets: dict, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = secrets["smtp_from"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    port = int(secrets.get("smtp_port") or 465)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(secrets["smtp_host"], port, context=ctx, timeout=30) as s:
            s.login(secrets["smtp_user"], secrets["smtp_password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(secrets["smtp_host"], port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(secrets["smtp_user"], secrets["smtp_password"])
            s.send_message(msg)


def send_webhook(url: str, title: str, body: str) -> None:
    r = httpx.post(url, content=body.encode(), headers={"Title": title[:200], "Content-Type": "text/plain; charset=utf-8"}, timeout=20)
    r.raise_for_status()


def deliver_digest(db: Session, digest: Digest, prefs, secrets: dict) -> None:
    if not digest.items:
        digest.delivery_status = "nothing_to_send"
        return
    channels = []
    errors = []
    subject = f"PLOT: {len(digest.items)} opportunit{'y' if len(digest.items) == 1 else 'ies'} today"
    if _smtp_ready(secrets) and prefs.notifications.email_to:
        try:
            send_email(secrets, prefs.notifications.email_to, subject, digest.body_text)
            channels.append("email")
        except Exception as e:  # noqa: BLE001
            errors.append(f"email: {type(e).__name__}: {e}")
    if secrets.get("notify_webhook_url"):
        try:
            send_webhook(secrets["notify_webhook_url"], subject, digest.body_text)
            channels.append("webhook")
        except Exception as e:  # noqa: BLE001
            errors.append(f"webhook: {type(e).__name__}: {e}")
    if channels:
        digest.delivery_status = "sent"
        digest.delivered_at = utcnow()
        digest.delivery_error = "; ".join(errors) or None
    elif errors:
        digest.delivery_status = "failed"
        digest.delivery_error = "; ".join(errors)
    else:
        digest.delivery_status = "not_configured"
        digest.delivery_error = "No SMTP credentials + recipient, and no webhook configured (Admin → Integrations)."
    db.add(AuditLog(actor="system", action=f"digest.{digest.delivery_status}", entity="digest", entity_id=str(digest.id), detail={"channels": channels, "errors": errors}))


def send_alert(db: Session, subject: str, body: str) -> str:
    """Best-effort operational alert; returns delivery status and always writes an audit row."""
    from plot.preferences import load_preferences

    secrets = load_secrets(db)
    prefs, _ = load_preferences(db)
    status, errors = "not_configured", []
    if prefs.notifications.notify_on_critical_alerts:
        if _smtp_ready(secrets) and prefs.notifications.email_to:
            try:
                send_email(secrets, prefs.notifications.email_to, subject, body)
                status = "sent"
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
                status = "failed"
        if secrets.get("notify_webhook_url"):
            try:
                send_webhook(secrets["notify_webhook_url"], subject, body)
                status = "sent"
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
                status = status if status == "sent" else "failed"
    db.add(AuditLog(actor="system", action=f"alert.{status}", entity="alert", detail={"subject": subject, "body": body[:2000], "errors": errors}))
    return status

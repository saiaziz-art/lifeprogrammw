"""End-to-end: the off-market acquisition agent, exercised through the real daily run and the API.

Discover a candidate → verify parcel & ownership evidence → assess feasibility → draft a
personalised letter → approve → generate the printable letter → record posting → manage the
reply; plus the email path through a (mocked) Gmail mailbox, an opt-out, and the guarantees
that nothing is ever sent without individual approval.

All owners, addresses and registers are clearly labelled TEST DATA (plot/sandbox.py). The
public-data APIs and Gmail are mocked at the HTTP layer; nothing leaves the machine.
"""

from __future__ import annotations

import base64
import datetime as dt
import email
import email.policy
import io
import json
import re

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import func, select

from plot.api.main import _login_attempts, app
from plot.jobs.pipeline import run_sourcing
from plot.models import AuditLog, Contact, Digest, InboundMessage, Lead, Outreach, Run, Suppression
from plot.offmarket import agent
from plot.outreach import writer
from plot.sandbox import REGISTER_GARDEN, REGISTER_PADDOCK, register_pdf, street_area
from plot.security.crypto import set_secret
from tests import mocks

H = {"X-Requested-With": "plot"}
PW = "correct horse battery staple"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"


@pytest.fixture()
def client(db):
    _login_attempts.clear()
    c = TestClient(app)
    assert c.post("/api/auth/setup", json={"email": "owner@test.local", "password": PW}, headers=H).status_code == 200
    return c


@pytest.fixture()
def synthetic_area(monkeypatch):
    """The discovery engine runs for real; only its input layers come from the synthetic street."""
    monkeypatch.setattr(agent.OffMarketDiscovery, "area_loader", staticmethod(lambda db, lon, lat, r, overpass_url=None: street_area()))


def _set_area_and_buyer(client, *, confirmed: bool) -> None:
    p = client.get("/api/preferences").json()["preferences"]
    p["prospecting_areas"] = [{"name": "TEST sandbox street", "lon": -1.7156, "lat": 50.8955, "radius_m": 400, "enabled": True, "types": ["garden_subdivision", "edge_paddock", "outbuilding", "replacement", "derelict", "planning_history"]}]
    p["buyer"] |= {"persona": "couple", "first_name": "Sai", "last_name": "Testbuyer", "partner_term": "wife", "returning_to_uk": True, "reply_email": "sai.test@example.invalid", "reply_postal_address": "TEST ADDRESS, 1 Example Street, Testville TE1 0AA", "facts_confirmed": confirmed}
    assert client.put("/api/preferences", json=p, headers=H).status_code == 200


def _daily_run(db):
    with respx.mock(assert_all_called=False) as router:
        mocks.install(router)
        run = run_sourcing(db, trigger="test", max_enrich=30)
    db.expire_all()
    return db.get(Run, run.id)


def _lead_for(db, title_id: str) -> Lead:
    return next(l for l in db.scalars(select(Lead)).all() if (l.analysis or {}).get("title_id") == title_id)


def _upload(client, lead_id: int, text: str, name: str) -> dict:
    r = client.post(f"/api/offmarket/leads/{lead_id}/titles", files={"file": (name, register_pdf(text), "application/pdf")}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def _approve(client, o: dict, **extra) -> httpx.Response:
    return client.post(f"/api/offmarket/outreach/{o['id']}/approve", json={"confirm_recipient_hash": o["recipient_hash"], **extra}, headers=H)


def test_letter_workflow_end_to_end(client, db, synthetic_area):
    _set_area_and_buyer(client, confirmed=False)

    # 1. the 07:00 run discovers and assesses candidates, but drafts and sends nothing
    run = _daily_run(db)
    disc = next(s for s in run.steps if s["name"] == "retrieve")["detail"]["sources"]["offmarket.discovery"]
    assert disc["status"] == "ok" and disc["new"] >= 3
    wide, paddock = _lead_for(db, "WIDE"), _lead_for(db, "PADDOCK")
    assert wide.opportunity_type == "garden_subdivision" and paddock.opportunity_type == "edge_paddock"
    assert wide.stage == "feasibility_screened" and wide.ownership_status == "title_purchase_needed"
    assert wide.acq_score_total and wide.acq_score["confidence"]["label"] != "high"  # planning & ownership unverified
    assert "official copy" in wide.next_action
    assert db.scalar(select(func.count()).select_from(Outreach)) == 0
    digest = db.scalar(select(Digest).order_by(Digest.id.desc()).limit(1))
    assert "OFF-MARKET ACQUISITION" in digest.body_text and digest.briefing["new_prospects"] >= 3
    assert "Nothing is ever sent automatically." in digest.body_text

    # 2. no drafting before the owner is verified
    assert client.post(f"/api/offmarket/leads/{wide.id}/drafts", json={}, headers=H).status_code == 422

    # 3. upload the official copy (mock register PDF) → parsed, then you verify it
    doc = _upload(client, wide.id, REGISTER_GARDEN, "TEST-register.pdf")
    assert doc["status"] == "parsed" and doc["title_number"] == "TT123456" and doc["tenure"] == "freehold"
    ex = doc["extracted"]
    assert [p["name"] for p in ex["proprietors"]] == ["Alex Sandbox-Test", "Sam Sandbox-Test"]
    assert ex["charges"] and ex["covenants"] and ex["restrictions"]
    r = client.post(f"/api/offmarket/titles/{doc['id']}/verify", json={"confirm_matches_land": True, "confirm_details_checked": False}, headers=H)
    assert r.status_code == 422  # both confirmations required
    c = client.post(f"/api/offmarket/titles/{doc['id']}/verify", json={"confirm_matches_land": True, "confirm_details_checked": True}, headers=H).json()
    assert c["verified"] and c["kind"] == "individual" and c["salutation"] == "Alex and Sam" and c["postcode"] == "TE1 1ST"
    assert c["address_lines"] == ["12 Test Road", "Testville"]  # postcode is stored separately, not repeated as a line
    d = client.get(f"/api/offmarket/leads/{wide.id}").json()
    assert d["stage"] == "owner_verified" and d["ownership_status"] == "verified"
    assert d["contact_method"] == "post"

    # 4. buyer facts unconfirmed → the draft cannot reach approval
    o = client.post(f"/api/offmarket/leads/{wide.id}/drafts", json={}, headers=H).json()
    assert o["status"] == "needs_changes" and "buyer" in o["review_reason"].lower()
    _set_area_and_buyer(client, confirmed=True)

    # 5. a personalised letter, checked and waiting for approval
    o = client.post(f"/api/offmarket/leads/{wide.id}/drafts", json={}, headers=H).json()
    assert o["status"] == "awaiting_approval", o["review_reason"]
    assert o["channel"] == "post" and o["generator"] == "composer" and o["kind"] == "initial"
    body = o["body"]
    assert body.startswith("Dear Alex and Sam,") and body.rstrip().endswith("Sai")
    assert "My wife and I" in body and "12 Test Road" in body
    assert 80 <= writer.message_words(body) <= 140
    low = body.lower()
    for bad in writer.BANNED_PHRASES + ["cash buyer", "£", "guarantee"]:
        assert bad not in low
    assert body.count("?") <= 1
    assert "HM Land Registry" in o["footer"] and o["reference"] in o["footer"] and "ico.org.uk" in o["footer"]
    assert all(ch["status"] == "pass" for ch in o["compliance"]["checks"] if ch["status"] != "warn")
    assert db.scalar(select(func.count()).select_from(Outreach).where(Outreach.status.in_(("sent", "posted")))) == 0

    # an email to this private individual is not allowed (no consent, owner didn't write first)
    # (even if their address were published somewhere — publication is not permission)
    assert client.patch(f"/api/offmarket/contacts/{c['id']}", json={"email": "alex@sandbox-test.example.invalid"}, headers=H).status_code == 200
    oe = client.post(f"/api/offmarket/leads/{wide.id}/drafts", json={"channel": "email"}, headers=H).json()
    assert oe["status"] == "needs_changes" and "consent" in oe["review_reason"].lower()
    o = client.post(f"/api/offmarket/leads/{wide.id}/drafts", json={"channel": "post", "instructions": "a little shorter"}, headers=H).json()
    assert o["status"] == "awaiting_approval"

    # 6. draft PDF is watermarked; the final letter does not exist before approval
    pdf = client.get(f"/api/offmarket/outreach/{o['id']}/pdf").content
    assert "DRAFT" in "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    assert client.get(f"/api/offmarket/outreach/{o['id']}/pdf?final=1").status_code == 409

    # 7. approval binds this exact content to this exact recipient
    bad = dict(o, recipient_hash="0" * 64)
    assert _approve(client, bad).status_code == 409
    assert client.get("/api/openapi.json").json()["paths"].keys().isdisjoint({p for p in client.get("/api/openapi.json").json()["paths"] if re.search(r"bulk|approve-all|send-all", p)})
    a = _approve(client, o).json()
    assert a["status"] == "approved" and a["approved_by"] == "owner@test.local"
    # editing after approval clears it
    edited = client.patch(f"/api/offmarket/outreach/{o['id']}", json={"body": a["body"].replace("Dear Alex and Sam,", "Dear Alex and Sam,\n")}, headers=H).json()
    assert edited["status"] == "awaiting_approval" and edited["approved_at"] is None
    assert client.post(f"/api/offmarket/outreach/{o['id']}/mark-posted", json={"posted_on": dt.date.today().isoformat()}, headers=H).status_code == 409
    a = _approve(client, edited).json()
    assert a["status"] == "approved"
    assert a["recipient_address"] == "12 Test Road\nTestville\nTE1 1ST"

    # 8. the real printable letter
    final = client.get(f"/api/offmarket/outreach/{o['id']}/pdf?final=1")
    assert final.status_code == 200 and final.headers["content-type"] == "application/pdf"
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(final.content)).pages)
    assert "DRAFT" not in text and "Alex Sandbox-Test" in text and text.count("TE1 1ST") == 1 and "HM Land Registry" in text and a["reference"] in text

    # 9. you post it and record that — the lead moves to Contacted
    p = client.post(f"/api/offmarket/outreach/{o['id']}/mark-posted", json={"posted_on": dt.date.today().isoformat(), "postage_paid_gbp": 0.87}, headers=H).json()
    assert p["status"] == "posted" and p["delivery_status"] == "posted_by_you"
    assert client.post(f"/api/offmarket/outreach/{o['id']}/mark-posted", json={"posted_on": dt.date.today().isoformat()}, headers=H).status_code == 409
    d = client.get(f"/api/offmarket/leads/{wide.id}").json()
    assert d["stage"] == "contacted"
    again = client.post(f"/api/offmarket/leads/{wide.id}/drafts", json={}, headers=H).json()
    assert again["status"] == "needs_changes"  # a second first approach to the same owner is blocked
    client.post(f"/api/offmarket/outreach/{again['id']}/reject", json={"instructions": "not needed"}, headers=H)
    fu = client.post(f"/api/offmarket/leads/{wide.id}/drafts", json={"kind": "follow_up"}, headers=H).json()
    assert fu["status"] == "needs_changes" and "days" in fu["review_reason"]  # too early for the one follow-up
    assert "-1" not in fu["review_reason"] and "Contacted 0 days ago" in fu["review_reason"]  # posted today reads as 0 days, never negative
    client.post(f"/api/offmarket/outreach/{fu['id']}/reject", json={}, headers=H)

    # 10. the owner writes back; reply categorised, suggested reply prepared for approval
    r = client.post(f"/api/offmarket/leads/{wide.id}/replies", json={"channel": "post", "received_on": dt.date.today().isoformat(), "body": "Thank you for your letter. We would consider it and are happy to discuss — please give us a ring."}, headers=H).json()
    assert r["category"] == "interested" and r["suggested_reply_id"]
    d = client.get(f"/api/offmarket/leads/{wide.id}").json()
    assert d["stage"] == "interested" and d["brief"]["available"] is not None
    sug = client.get(f"/api/offmarket/outreach/{r['suggested_reply_id']}").json()
    assert sug["kind"] == "reply" and sug["status"] == "awaiting_approval" and "£" not in sug["body"]
    assert re.search(r"which part of the garden", sug["body"])

    # 11. a price request produces a private negotiation brief, never a figure in a draft
    r2 = client.post(f"/api/offmarket/leads/{wide.id}/replies", json={"channel": "phone_note", "received_on": dt.date.today().isoformat(), "body": "How much would you pay for it?"}, headers=H).json()
    assert r2["category"] == "requests_price"
    brief = client.get(f"/api/offmarket/leads/{wide.id}/brief").json()
    assert brief["available"] and brief["private"] and "Never send a figure" in brief["rules"]
    sug2 = client.get(f"/api/offmarket/outreach/{r2['suggested_reply_id']}").json()
    assert not writer.PRICE_RE.search(sug2["body"])

    # nothing was ever sent automatically; every outbound record has an individual approval
    db.expire_all()
    for x in db.scalars(select(Outreach).where(Outreach.status.in_(("sent", "posted")))).all():
        assert x.approved_by == "owner@test.local" and x.approved_hash
    actions = [a.action for a in db.scalars(select(AuditLog)).all()]
    assert "outreach.approved" in actions and "outreach.posted" in actions and "owner.verified" in actions

    # 12. next morning: the daily run never contacts anyone
    before = db.scalar(select(func.count()).select_from(Outreach).where(Outreach.status.in_(("sent", "posted"))))
    run2 = _daily_run(db)
    assert db.scalar(select(func.count()).select_from(Outreach).where(Outreach.status.in_(("sent", "posted")))) == before
    brief = db.scalars(select(Run)).all()[-1]
    assert run2.status in ("partial", "succeeded")


def _gmail_routes(router, sent: list):
    router.post("https://oauth2.googleapis.com/token").mock(return_value=httpx.Response(200, json={"access_token": "TEST-access", "refresh_token": "TEST-refresh", "expires_in": 3600, "scope": "gmail.compose gmail.readonly", "token_type": "Bearer"}))
    router.get(f"{GMAIL}/profile").mock(return_value=httpx.Response(200, json={"emailAddress": "sai.test@example.invalid"}))

    def send(request: httpx.Request):
        payload = json.loads(request.content)
        sent.append(email.message_from_bytes(base64.urlsafe_b64decode(payload["raw"]), policy=email.policy.default))
        return httpx.Response(200, json={"id": f"gm-{len(sent)}", "threadId": "th-1", "labelIds": ["SENT"]})

    router.post(f"{GMAIL}/messages/send").mock(side_effect=send)


def _owner_reply(text: str) -> dict:
    data = base64.urlsafe_b64encode(text.encode()).decode()
    return {
        "messages": [
            {"id": "gm-1", "threadId": "th-1", "labelIds": ["SENT"], "internalDate": "1760000000000", "payload": {"mimeType": "text/plain", "headers": [{"name": "From", "value": "Sai Testbuyer <sai.test@example.invalid>"}], "body": {"data": ""}}},
            {"id": "gm-owner-1", "threadId": "th-1", "labelIds": ["INBOX"], "internalDate": "1760100000000", "payload": {"mimeType": "text/plain", "headers": [{"name": "From", "value": "Sandbox Test Land <estates@sandbox-test.example.invalid>"}, {"name": "Subject", "value": "Re: land"}], "body": {"data": data}}},
        ]
    }


def test_email_workflow_with_connected_mailbox_and_opt_out(client, db, synthetic_area):
    _set_area_and_buyer(client, confirmed=True)
    _daily_run(db)
    paddock = _lead_for(db, "PADDOCK")
    doc = _upload(client, paddock.id, REGISTER_PADDOCK, "TEST-register-paddock.pdf")
    assert doc["extracted"]["proprietors"][0]["kind"] == "company"
    c = client.post(f"/api/offmarket/titles/{doc['id']}/verify", json={"confirm_matches_land": True, "confirm_details_checked": True}, headers=H).json()
    assert c["kind"] == "company"

    # a corporate subscriber may be emailed (identity + opt-out still required)
    r = client.patch(f"/api/offmarket/contacts/{c['id']}", json={"email": "estates@sandbox-test.example.invalid", "email_basis": "corporate_subscriber"}, headers=H)
    assert r.status_code == 200
    o = client.post(f"/api/offmarket/leads/{paddock.id}/drafts", json={"channel": "email"}, headers=H).json()
    assert o["status"] == "awaiting_approval", o["review_reason"]
    assert o["body"].startswith("Dear Sir or Madam,") and "home" in o["body"]
    a = _approve(client, o).json()
    assert a["status"] == "approved"
    # no mailbox → cannot send
    assert client.post(f"/api/offmarket/outreach/{o['id']}/send", headers=H).status_code == 409

    # connect Gmail through the real OAuth code path (provider endpoints mocked)
    set_secret(db, "google_client_id", "TEST-client-id.apps.googleusercontent.com", "test")
    set_secret(db, "google_client_secret", "TEST-secret", "test")
    db.commit()
    start = client.post("/api/mail/oauth/start", json={"provider": "google"}, headers=H).json()["url"]
    assert "code_challenge_method=S256" in start and "gmail.compose" in start
    state = re.search(r"state=([^&]+)", start).group(1)
    sent: list = []
    with respx.mock(assert_all_called=False) as router:
        _gmail_routes(router, sent)
        cb = client.get(f"/api/mail/oauth/callback?state={state}&code=TEST-code", follow_redirects=False)
        assert cb.status_code == 303 and "connected=1" in cb.headers["location"]
        # the approval was bound before the mailbox existed; content and recipient are unchanged so it stands
        s1 = client.post(f"/api/offmarket/outreach/{o['id']}/send", headers=H).json()
        assert s1["status"] == "sent" and s1["provider_message_id"] == "gm-1" and s1["delivery_status"] == "accepted_by_provider"
        assert client.post(f"/api/offmarket/outreach/{o['id']}/send", headers=H).status_code == 409  # no resend
    assert len(sent) == 1
    m = sent[0]
    assert m["To"].endswith("<estates@sandbox-test.example.invalid>") and m["Subject"] == o["subject"]
    text = m.get_content()
    assert o["body"].split("\n")[2][:40] in text and "If you would prefer not to receive further messages" in text
    d = client.get(f"/api/offmarket/leads/{paddock.id}").json()
    assert d["stage"] == "contacted"

    # the owner replies asking not to be contacted → immediate suppression, lead closed, nothing drafted
    with respx.mock(assert_all_called=False) as router:
        _gmail_routes(router, sent)
        router.get(f"{GMAIL}/threads/th-1").mock(return_value=httpx.Response(200, json=_owner_reply("Please remove us from your list and do not contact us again.\n\nOn Mon, Sai wrote:\n> Dear Sir or Madam")))
        out = client.post("/api/offmarket/sync-replies", headers=H).json()
        assert out["new"] == 1 and not out["errors"]
        assert client.post("/api/offmarket/sync-replies", headers=H).json()["new"] == 0  # deduplicated
    db.expire_all()
    msg = db.scalar(select(InboundMessage).where(InboundMessage.lead_id == paddock.id))
    assert msg.category == "requests_no_contact" and msg.suggested_reply_id is None and "On Mon" not in msg.body
    lead = db.get(Lead, paddock.id)
    assert lead.stage == "closed" and lead.decline_reason == "requests_no_contact"
    assert db.scalar(select(func.count()).select_from(Suppression)) >= 2
    assert client.post(f"/api/offmarket/leads/{paddock.id}/drafts", json={"kind": "follow_up"}, headers=H).status_code == 422
    assert client.patch(f"/api/offmarket/leads/{paddock.id}", json={"stage": "feasibility_screened"}, headers=H).status_code == 422
    dash = client.get("/api/offmarket/dashboard").json()
    assert dash["kpis"]["emails_sent"] == 1 and dash["kpis"]["owners_contacted"] == 1 and dash["kpis"]["response_rate"] == 1.0
    assert dash["declines"]["by_reason"].get("Asked for no further contact") == 1


def test_ambiguous_send_is_never_retried_blindly(client, db, synthetic_area):
    _set_area_and_buyer(client, confirmed=True)
    _daily_run(db)
    paddock = _lead_for(db, "PADDOCK")
    doc = _upload(client, paddock.id, REGISTER_PADDOCK, "TEST-register-paddock.pdf")
    c = client.post(f"/api/offmarket/titles/{doc['id']}/verify", json={"confirm_matches_land": True, "confirm_details_checked": True}, headers=H).json()
    client.patch(f"/api/offmarket/contacts/{c['id']}", json={"email": "estates@sandbox-test.example.invalid", "email_basis": "corporate_subscriber"}, headers=H)
    o = client.post(f"/api/offmarket/leads/{paddock.id}/drafts", json={"channel": "email"}, headers=H).json()
    _approve(client, o)
    set_secret(db, "google_client_id", "TEST-client-id", "test")
    db.commit()
    state = re.search(r"state=([^&]+)", client.post("/api/mail/oauth/start", json={"provider": "google"}, headers=H).json()["url"]).group(1)
    with respx.mock(assert_all_called=False) as router:
        _gmail_routes(router, [])
        client.get(f"/api/mail/oauth/callback?state={state}&code=TEST-code", follow_redirects=False)
        router.post(f"{GMAIL}/messages/send").mock(side_effect=httpx.ReadTimeout("timed out"))
        s = client.post(f"/api/offmarket/outreach/{o['id']}/send", headers=H).json()
        assert s["status"] == "delivery_unknown"
        assert client.post(f"/api/offmarket/outreach/{o['id']}/send", headers=H).status_code == 409
        mid = s["delivery_detail"]["internet_message_id"]
        router.get(f"{GMAIL}/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": "gm-9", "threadId": "th-9"}]}))
        rec = client.post(f"/api/offmarket/outreach/{o['id']}/reconcile", headers=H).json()
        assert rec["status"] == "sent" and rec["provider_message_id"] == "gm-9"
        assert router.calls.last.request.url.params["q"] == f"rfc822msgid:{mid}"


def test_suppression_and_contact_change_invalidate_approval(client, db, synthetic_area):
    _set_area_and_buyer(client, confirmed=True)
    _daily_run(db)
    wide = _lead_for(db, "WIDE")
    doc = _upload(client, wide.id, REGISTER_GARDEN, "TEST-register.pdf")
    c = client.post(f"/api/offmarket/titles/{doc['id']}/verify", json={"confirm_matches_land": True, "confirm_details_checked": True}, headers=H).json()
    o = client.post(f"/api/offmarket/leads/{wide.id}/drafts", json={}, headers=H).json()
    assert _approve(client, o).json()["status"] == "approved"
    # changing the recipient after approval clears it
    client.patch(f"/api/offmarket/contacts/{c['id']}", json={"salutation": "Alex"}, headers=H)
    after = client.get(f"/api/offmarket/outreach/{o['id']}").json()
    assert after["status"] != "approved"
    # a do-not-contact entry blocks approval entirely
    client.post("/api/offmarket/suppressions", json={"address_lines": ["12 Test Road"], "postcode": "TE1 1ST", "reason": "TEST: never write to this address"}, headers=H)
    after = client.get(f"/api/offmarket/outreach/{o['id']}").json()
    assert after["status"] == "needs_changes" and "do-not-contact" in after["review_reason"].lower()
    assert _approve(client, after).status_code == 409


def test_sandbox_refuses_mailbox_and_postal(db, monkeypatch):
    from plot.config import get_settings
    from plot.outreach import mail

    monkeypatch.setattr(get_settings(), "env", "sandbox")
    with pytest.raises(mail.MailError):
        mail.start_oauth(db, "google")
    assert get_settings().is_sandbox
    db.rollback()
    _ = Contact  # sandbox contacts are created only by plot.sandbox.seed (clearly labelled)

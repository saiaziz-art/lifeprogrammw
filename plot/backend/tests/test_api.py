"""API: authentication, CSRF, encrypted credentials, workflow gates and validation."""

from __future__ import annotations

import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select

from plot.api.main import _login_attempts, app
from plot.jobs.pipeline import run_sourcing
from plot.models import Lead, Secret
from tests import mocks

H = {"X-Requested-With": "plot"}
PW = "correct horse battery staple"


@pytest.fixture()
def client(db):
    _login_attempts.clear()
    c = TestClient(app)
    r = c.post("/api/auth/setup", json={"email": "owner@test.local", "password": PW}, headers=H)
    assert r.status_code == 200
    return c


def test_auth_required_and_csrf(db):
    c = TestClient(app)
    assert c.get("/api/opportunities").status_code == 401
    assert c.post("/api/auth/setup", json={"email": "a@b.c", "password": PW}).status_code == 403  # no CSRF header
    assert c.post("/api/auth/setup", json={"email": "a@b.c", "password": "short"}, headers=H).status_code == 422
    assert c.post("/api/auth/setup", json={"email": "a@b.c", "password": PW}, headers=H).status_code == 200
    assert c.post("/api/auth/setup", json={"email": "x@b.c", "password": PW}, headers=H).status_code == 409
    c2 = TestClient(app)
    assert c2.post("/api/auth/login", json={"email": "a@b.c", "password": "wrong password!"}, headers=H).status_code == 401
    assert c2.post("/api/auth/login", json={"email": "a@b.c", "password": PW}, headers=H).status_code == 200
    assert c2.get("/api/auth/me").json()["email"] == "a@b.c"
    assert c2.post("/api/auth/logout", headers=H).status_code == 200
    assert c2.get("/api/opportunities").status_code == 401


def test_secrets_are_encrypted_and_never_returned(client, db):
    r = client.put("/api/admin/secrets/ofcom_api_key", json={"value": "super-secret-value"}, headers=H)
    assert r.status_code == 200
    listing = client.get("/api/admin/secrets").json()
    assert {"key": "ofcom_api_key", "label": "Ofcom API subscription key", "status": "set"} in listing
    assert "super-secret-value" not in client.get("/api/admin/secrets").text
    row = db.scalar(select(Secret).where(Secret.key == "ofcom_api_key"))
    assert b"super-secret-value" not in row.ciphertext


def test_opportunities_dossier_and_overrides(client, db):
    with respx.mock(assert_all_called=False) as router:
        mocks.install(router)
        run_sourcing(db, trigger="test", max_enrich=10)
    opp = client.get("/api/opportunities").json()
    assert opp["total"] >= 4
    row = opp["items"][0]
    assert {"score", "residual_land_value", "max_offer", "confidence", "provenance", "glyph"} <= set(row)
    d = client.get(f"/api/sites/{row['id']}").json()
    assert d["assessment"]["costs"]["categories"][0]["name"].startswith("Land acquisition")
    assert d["site"]["provenance"][0]["source_id"]
    # override needs a reason and a valid value
    assert client.post(f"/api/sites/{row['id']}/overrides", json={"field": "planning.class", "value": "nonsense", "reason": "x y z"}, headers=H).status_code == 422
    assert client.post(f"/api/sites/{row['id']}/overrides", json={"field": "evil.field", "value": 1, "reason": "x y z"}, headers=H).status_code == 422
    r = client.post(f"/api/sites/{row['id']}/overrides", json={"field": "planning.class", "value": "plausible_route", "reason": "pre-app advice positive"}, headers=H)
    assert r.status_code == 200
    d2 = client.get(f"/api/sites/{row['id']}").json()
    assert d2["assessment"]["planning"]["classification"] == "plausible_route"
    # map & dashboard
    assert client.get("/api/map/sites").json()["points"]["features"]
    dash = client.get("/api/dashboard").json()
    assert dash["last_run"]["status"] == "partial" and dash["last_successful_run"] is None
    assert any("source(s) failing" in a["title"] for a in dash["alerts"])


def test_lead_stage_gates_and_outreach_compliance(client, db):
    with respx.mock(assert_all_called=False) as router:
        mocks.install(router)
        run_sourcing(db, trigger="test", max_enrich=10)
    lead = db.scalar(select(Lead).where(Lead.stage == "ownership_research_needed"))
    url = f"/api/leads/{lead.id}"
    r = client.patch(url, json={"stage": "owner_verified"}, headers=H)
    assert r.status_code == 422 and "record the owner" in str(r.json())
    assert client.post(f"{url}/outreach", json={"sender_name": "Me", "sender_contact": "my address"}, headers=H).status_code == 422
    owner = {"name": "Example Owner", "owner_type": "individual", "address": "1 Test Road", "source": "TEST: official copy title XX0000", "source_date": "2026-10-01"}
    assert client.patch(url, json={"owner": owner, "stage": "owner_verified"}, headers=H).status_code == 200
    o = client.post(f"{url}/outreach", json={"sender_name": "Me", "sender_contact": "my address"}, headers=H).json()
    assert "HM Land Registry title register" in o["body"] and o["status"] == "draft"
    # email to an individual is blocked by PECR check
    oe = client.post(f"{url}/outreach", json={"channel": "email", "sender_name": "Me", "sender_contact": "my address"}, headers=H).json()
    assert next(c for c in oe["compliance"]["checks"] if c["code"] == "pecr_channel")["ok"] is False
    # removing the privacy paragraph fails a blocking check
    stripped = o["body"].split("How I obtained")[0]
    r = client.patch(f"/api/outreach/{o['id']}", json={"body": stripped, "action": "approve", "lia_confirmed": True}, headers=H)
    assert r.status_code == 422
    r = client.patch(f"/api/outreach/{o['id']}", json={"body": o["body"], "action": "approve", "lia_confirmed": True}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert client.patch(url, json={"stage": "outreach_approved"}, headers=H).status_code == 200
    assert client.patch(url, json={"stage": "owner_contacted"}, headers=H).status_code == 422  # not yet sent
    assert client.patch(f"/api/outreach/{o['id']}", json={"action": "mark_sent"}, headers=H).json()["status"] == "sent"
    assert client.patch(url, json={"stage": "owner_contacted"}, headers=H).status_code == 200
    assert client.patch(url, json={"stage": "interest_confirmed"}, headers=H).status_code == 422
    assert client.patch(url, json={"add_evidence": {"kind": "owner_reply", "summary": "Owner wrote back: open to selling the paddock", "date": "2026-10-20"}, "stage": "interest_confirmed"}, headers=H).status_code == 200
    # suppression list blocks future letters to the same person
    assert client.post("/api/suppressions", json={"name": "Example Owner", "address": "1 Test Road"}, headers=H).status_code == 200


def test_preferences_validation_and_import(client, db):
    p = client.get("/api/preferences").json()["preferences"]
    p["weights"]["planning"] = 50
    assert client.put("/api/preferences", json=p, headers=H).status_code == 422
    p["weights"]["planning"] = 30
    p["budget"]["total_all_in_gbp"] = 450_000
    r = client.put("/api/preferences", json=p, headers=H)
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["reassess_job"]
    bad = client.post("/api/import/listing", json={"title": "Plot", "lat": 48.0, "lon": -1.6}, headers=H)
    assert bad.status_code == 422
    ok = client.post("/api/import/listing", json={"title": "Plot (test)", "lat": 50.79, "lon": -1.60, "asking_price": 80_000, "area_value": 0.2, "area_unit": "acres", "url": "https://example.test/listing/1"}, headers=H)
    assert ok.status_code == 200 and ok.json()["enrichment_job"]
    site = client.get(f"/api/sites/{ok.json()['site_id']}").json()
    assert site["site"]["asking_price"] == 80_000 and round(site["site"]["area_m2"]) == 809
    assert site["assessment"]["residual"]["asking_price"] == 80_000
    # boundary drawing validated
    bad_poly = {"geometry": {"type": "Polygon", "coordinates": [[[-1.6, 50.79], [-1.6, 50.79], [-1.6, 50.79], [-1.6, 50.79]]]}}
    assert client.post(f"/api/sites/{ok.json()['site_id']}/boundary", json=bad_poly, headers=H).status_code == 422
    good_poly = {"geometry": {"type": "Polygon", "coordinates": [[[-1.6, 50.79], [-1.5996, 50.79], [-1.5996, 50.7903], [-1.6, 50.7903], [-1.6, 50.79]]]}}
    r = client.post(f"/api/sites/{ok.json()['site_id']}/boundary", json=good_poly, headers=H)
    assert r.status_code == 200 and 800 < r.json()["area_m2"] < 1100


def test_design_evaluate_is_fast_and_complete(client):
    import time

    t = time.monotonic()
    r = client.post("/api/design/evaluate", json={"concept": "cabin_40", "method": "modular", "site_area_m2": 500}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["design"]["fit"]["method"] == "area_only" and len(body["comparison"]) == 4
    assert time.monotonic() - t < 5

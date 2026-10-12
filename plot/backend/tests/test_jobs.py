"""Scheduler timing (incl. DST), durable queue semantics, and the full daily pipeline."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import respx
from sqlalchemy import func, select

from plot.db import new_session
from plot.jobs import queue
from plot.jobs.pipeline import run_sourcing
from plot.jobs.scheduler import ensure_default_schedules, latest_fire_time, next_fire_time, tick
from plot.models import Assessment, AuditLog, Digest, Evidence, Job, Lead, Listing, ListingEvent, Run, Schedule, Site, Source, SourceRun
from plot.sources.base import CandidateRecord
from tests import mocks

UTC = dt.UTC
LON = ZoneInfo("Europe/London")


# ----------------------------------------------------------------------------- scheduler


def test_0700_london_across_dst():
    winter = latest_fire_time("0 7 * * *", "Europe/London", dt.datetime(2026, 1, 15, 9, 0, tzinfo=UTC))
    summer = latest_fire_time("0 7 * * *", "Europe/London", dt.datetime(2026, 7, 15, 9, 0, tzinfo=UTC))
    assert winter == dt.datetime(2026, 1, 15, 7, 0, tzinfo=UTC)
    assert summer == dt.datetime(2026, 7, 15, 6, 0, tzinfo=UTC)  # 07:00 BST
    # the morning the clocks go forward (29 Mar 2026) still fires at 07:00 local
    spring = next_fire_time("0 7 * * *", "Europe/London", dt.datetime(2026, 3, 28, 12, 0, tzinfo=UTC))
    assert spring.astimezone(LON).hour == 7 and spring == dt.datetime(2026, 3, 29, 6, 0, tzinfo=UTC)
    autumn = next_fire_time("0 7 * * *", "Europe/London", dt.datetime(2026, 10, 24, 12, 0, tzinfo=UTC))
    assert autumn.astimezone(LON).hour == 7 and autumn == dt.datetime(2026, 10, 25, 7, 0, tzinfo=UTC)


def test_tick_enqueues_once_per_fire_time(db):
    ensure_default_schedules(db)
    sch = db.scalar(select(Schedule))
    sch.created_at = dt.datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()
    t = dt.datetime(2026, 10, 12, 6, 0, 30, tzinfo=UTC)  # 07:00:30 BST
    assert len(tick(db, t)) == 1
    assert tick(db, t + dt.timedelta(seconds=30)) == []  # idempotent
    db.commit()
    job = db.scalar(select(Job))
    assert job.type == "daily_sourcing" and job.scheduled_for == dt.datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
    assert len(tick(db, t + dt.timedelta(days=1))) == 1  # next morning


def test_missed_run_outside_grace_is_recorded_not_run(db):
    ensure_default_schedules(db)
    sch = db.scalar(select(Schedule))
    sch.created_at = dt.datetime(2026, 1, 1, tzinfo=UTC)
    sch.last_enqueued_for = dt.datetime(2026, 10, 10, 6, 0, tzinfo=UTC)
    db.commit()
    assert tick(db, dt.datetime(2026, 10, 11, 22, 0, tzinfo=UTC)) == []  # 16 h late > 8 h grace
    db.commit()
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "schedule.missed")) == 1


# ----------------------------------------------------------------------------- queue


def test_queue_claim_retry_and_failure(db):
    j = queue.enqueue(db, "noop", {"a": 1}, max_attempts=2)
    db.commit()
    s1, s2 = new_session(), new_session()
    got = queue.claim(s1, "w1")
    assert got.id == j.id and got.status == "running" and got.attempts == 1
    assert queue.claim(s2, "w2") is None  # SKIP LOCKED / already running
    queue.fail(s1, got, RuntimeError("boom"))
    s1.refresh(got)
    assert got.status == "queued" and got.run_after > dt.datetime.now(UTC)
    got.run_after = dt.datetime.now(UTC) - dt.timedelta(seconds=1)
    s1.commit()
    again = queue.claim(s2, "w2")
    assert again.attempts == 2
    queue.fail(s2, again, RuntimeError("boom again"))
    s2.refresh(again)
    assert again.status == "failed" and "boom again" in again.last_error
    s1.close()
    s2.close()


def test_dedupe_key_and_stale_reaper(db):
    a = queue.enqueue(db, "noop", dedupe_key="k1")
    b = queue.enqueue(db, "noop", dedupe_key="k1")
    assert a is not None and b is None
    db.commit()
    s = new_session()
    j = queue.claim(s, "w1")
    j.heartbeat_at = dt.datetime.now(UTC) - dt.timedelta(hours=2)
    s.commit()
    assert queue.reap_stale(s, 30) == 1
    s.refresh(j)
    assert j.status == "queued" and "stopped heartbeating" in j.last_error
    s.close()


# ----------------------------------------------------------------------------- full pipeline


def test_daily_run_end_to_end_with_partial_failures(db):
    with respx.mock(assert_all_called=False) as router:
        mocks.install(router)
        run = run_sourcing(db, trigger="test", max_enrich=20)
    db.expire_all()
    run = db.get(Run, run.id)
    assert run.status == "partial"  # EA, BGS, HMLR, NaPTAN blocked → reported, not hidden
    assert [s["name"] for s in run.steps] == ["prepare", "retrieve", "changes", "public_data", "dedupe", "screen", "enrich", "assess", "acquisition", "rank", "digest", "deliver"]
    srs = {s.source_id: s for s in db.scalars(select(SourceRun).where(SourceRun.run_id == run.id)).all()}
    assert srs["planning_data.brownfield"].status == "ok" and srs["planning_data.brownfield"].records_new == 2
    assert srs["github.digital_land_snapshots"].status == "ok"
    assert srs["dft.naptan_rail"].status == "failed" and srs["dft.naptan_rail"].error_class == "network_blocked"
    assert srs["portal.rightmove"].status == "blocked"
    assert srs["email.alerts"].status == "not_configured"
    assert srs["planning_data.constraints"].status == "ok"
    assert srs["ea.flood"].status == "failed"
    # sites: 2 from planning.data + 3 active NFDC snapshot rows (1 ended row skipped)
    assert db.scalar(select(func.count()).select_from(Site)) == 5
    s1 = db.scalar(select(Site).join(Evidence, Evidence.site_id == Site.id).where(Evidence.source_ref == "1700001"))
    assert s1.lpa_name == "Test LPA" and s1.boundary_source == "inspire" and not s1.stale
    lead = db.scalar(select(Lead).where(Lead.site_id == s1.id))
    assert lead.ownership_status == "title_purchase_needed"  # INSPIRE matched; owner still unknown
    assert lead.stage == "feasibility_screened" and lead.acq_score_total is not None
    assert "official copy" in lead.next_action
    acq = next(s for s in run.steps if s["name"] == "acquisition")
    assert acq["detail"]["drafts"]["created"] == []  # buyer profile unconfirmed → nothing drafted, nothing sent
    # discovery needs building footprints: with OSM blocked and no OS import, it fails honestly
    assert srs["offmarket.discovery"].status == "failed" and srs["offmarket.discovery"].error_class == "network_blocked"
    assert db.scalar(select(func.count()).select_from(Assessment).where(Assessment.is_current.is_(True))) >= 4
    assert db.scalar(select(func.count()).select_from(Listing)) == 0  # register records are never listings
    digest = db.scalar(select(Digest))
    assert digest.delivery_status == "not_configured" and digest.items
    assert db.get(Source, "dft.naptan_rail").consecutive_failures == 1

    # second run: no duplicates, failures counted, unchanged assessments skipped
    with respx.mock(assert_all_called=False) as router:
        mocks.install(router)
        run2 = run_sourcing(db, trigger="test", max_enrich=20)
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Site)) == 5
    assert db.get(Source, "dft.naptan_rail").consecutive_failures == 2
    assess_step = next(s for s in db.get(Run, run2.id).steps if s["name"] == "assess")
    assert assess_step["detail"]["unchanged"] >= 3
    assert any(a.action == "alert.not_configured" for a in db.scalars(select(AuditLog)).all())


def test_run_failure_is_recorded_honestly(db, monkeypatch):
    import plot.jobs.pipeline as pl

    def explode(*a, **k):
        raise RuntimeError("simulated crash in dedupe")

    monkeypatch.setattr(pl, "_dedupe", explode)
    with respx.mock(assert_all_called=False) as router:
        mocks.install(router)
        try:
            run_sourcing(db, trigger="test", max_enrich=5)
        except RuntimeError:
            pass
    db.expire_all()
    run = db.scalar(select(Run).order_by(Run.id.desc()))
    assert run.status == "failed" and "simulated crash" in run.error
    assert next(s for s in run.steps if s["name"] == "dedupe")["status"] == "failed"


def test_listing_price_change_and_withdrawal_detection(db):
    from plot.ingest import mark_missing, upsert

    rec = CandidateRecord(source_id="email.alerts", external_ref="rightmove:1", record_type="listing", lon=-1.6, lat=50.79, title="Plot", asking_price=100_000, signals=[])
    site, o1 = upsert(db, rec, None)
    rec.asking_price = 90_000
    _, o2 = upsert(db, rec, None)
    assert (o1, o2) == ("new", "changed")
    for _ in range(3):
        mark_missing(db, "email.alerts", set(), None)
    db.commit()
    events = [e.event for e in db.scalars(select(ListingEvent).order_by(ListingEvent.id)).all()]
    assert events == ["new", "price_change", "withdrawn"]
    _, o3 = upsert(db, rec, None)
    assert o3 == "changed" and db.scalar(select(ListingEvent).order_by(ListingEvent.id.desc())).event == "relisted"

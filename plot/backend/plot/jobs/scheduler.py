"""Persistent cron scheduler (a separate process — not a browser timer).

Schedules live in the `schedules` table (default: daily sourcing at 07:00 Europe/London).
Every tick computes the most recent fire time ≤ now in the schedule's own time zone (so
DST changes keep it at 07:00 local) and enqueues one job per fire time, idempotently, via a
dedupe key. If the scheduler was down at 07:00 it catches up on start-up within a grace
window; older misses are recorded, not silently run.
"""

from __future__ import annotations

import datetime as dt
import logging
import signal
import time
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.orm import Session

from plot.config import get_settings
from plot.db import session_scope
from plot.jobs.heartbeat import beat
from plot.jobs.queue import enqueue
from plot.models import AuditLog, Schedule, utcnow

log = logging.getLogger("plot.scheduler")

DEFAULT_SCHEDULES = [
    {"name": "daily_sourcing", "job_type": "daily_sourcing", "payload": {"trigger": "schedule"}},
]


def ensure_default_schedules(db: Session) -> None:
    s = get_settings()
    for d in DEFAULT_SCHEDULES:
        row = db.scalar(select(Schedule).where(Schedule.name == d["name"]))
        if row is None:
            db.add(Schedule(name=d["name"], cron=s.daily_cron, timezone=s.daily_timezone, job_type=d["job_type"], payload=d["payload"], enabled=True))
    db.flush()


def latest_fire_time(cron: str, tz: str, now_utc: dt.datetime) -> dt.datetime:
    """Most recent scheduled time ≤ now, as an aware UTC datetime."""
    local_now = now_utc.astimezone(ZoneInfo(tz))
    it = croniter(cron, local_now + dt.timedelta(seconds=1))
    prev = it.get_prev(dt.datetime)
    if prev.tzinfo is None:
        prev = prev.replace(tzinfo=ZoneInfo(tz))
    return prev.astimezone(dt.UTC)


def next_fire_time(cron: str, tz: str, now_utc: dt.datetime) -> dt.datetime:
    local_now = now_utc.astimezone(ZoneInfo(tz))
    nxt = croniter(cron, local_now).get_next(dt.datetime)
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=ZoneInfo(tz))
    return nxt.astimezone(dt.UTC)


def tick(db: Session, now_utc: dt.datetime | None = None) -> list[int]:
    now_utc = now_utc or utcnow()
    grace = dt.timedelta(hours=get_settings().scheduler_misfire_grace_hours)
    enqueued: list[int] = []
    for sch in db.scalars(select(Schedule).where(Schedule.enabled.is_(True))).all():
        fire = latest_fire_time(sch.cron, sch.timezone, now_utc)
        if sch.last_enqueued_for is not None and fire <= sch.last_enqueued_for:
            continue
        if sch.last_enqueued_for is None and now_utc - fire > dt.timedelta(minutes=5) and sch.created_at and sch.created_at > fire:
            # schedule created after this fire time: wait for the next one
            sch.last_enqueued_for = fire
            continue
        if now_utc - fire > grace:
            db.add(AuditLog(actor="scheduler", action="schedule.missed", entity="schedule", entity_id=str(sch.id), detail={"fire_time": fire.isoformat(), "noticed_at": now_utc.isoformat(), "reason": "scheduler was not running within the grace window"}))
            sch.last_enqueued_for = fire
            continue
        job = enqueue(db, sch.job_type, sch.payload | {"scheduled_for": fire.isoformat()}, dedupe_key=f"schedule:{sch.id}:{fire.isoformat()}", schedule_id=sch.id, scheduled_for=fire)
        sch.last_enqueued_for = fire
        if job is not None:
            enqueued.append(job.id)
            log.info("enqueued %s job %s for %s", sch.name, job.id, fire)
    db.flush()
    return enqueued


def run_forever() -> None:  # pragma: no cover - process loop
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    with session_scope() as db:
        ensure_default_schedules(db)
    log.info("scheduler started")
    while not stop["flag"]:
        try:
            with session_scope() as db:
                ids = tick(db)
                beat(db, "scheduler", enqueued=ids, tick_seconds=get_settings().scheduler_tick_seconds)
        except Exception:  # noqa: BLE001 - keep the scheduler alive; error is logged
            log.exception("scheduler tick failed")
        for _ in range(get_settings().scheduler_tick_seconds):
            if stop["flag"]:
                break
            time.sleep(1)
    log.info("scheduler stopped")

"""Durable Postgres job queue (SELECT … FOR UPDATE SKIP LOCKED).

- Jobs survive restarts; a dedupe_key makes scheduled enqueues idempotent.
- Failed attempts are retried with exponential backoff up to max_attempts, then 'failed'.
- A worker heartbeats while running; jobs whose heartbeat stops (crash/kill) are requeued by
  the reaper so nothing is left 'running' forever.
"""

from __future__ import annotations

import datetime as dt
import traceback

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from plot.models import Job, utcnow


def enqueue(
    db: Session,
    type_: str,
    payload: dict | None = None,
    *,
    run_after: dt.datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 3,
    schedule_id: int | None = None,
    scheduled_for: dt.datetime | None = None,
) -> Job | None:
    stmt = (
        insert(Job)
        .values(
            type=type_,
            payload=payload or {},
            status="queued",
            attempts=0,
            max_attempts=max_attempts,
            run_after=run_after or utcnow(),
            dedupe_key=dedupe_key,
            schedule_id=schedule_id,
            scheduled_for=scheduled_for,
            result={},
            created_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(Job.id)
    )
    job_id = db.execute(stmt).scalar()
    db.flush()
    return db.get(Job, job_id) if job_id else None


def claim(db: Session, worker_id: str, types: list[str] | None = None) -> Job | None:
    cond = "AND type = ANY(:types)" if types else ""
    row = db.execute(
        text(
            f"""
            UPDATE jobs SET status='running', locked_by=:w, locked_at=now(), heartbeat_at=now(),
                   started_at=COALESCE(started_at, now()), attempts=attempts+1
            WHERE id = (
                SELECT id FROM jobs WHERE status='queued' AND run_after <= now() {cond}
                ORDER BY run_after, id FOR UPDATE SKIP LOCKED LIMIT 1)
            RETURNING id"""
        ),
        {"w": worker_id, "types": types},
    ).first()
    db.commit()
    return db.get(Job, row.id) if row else None


def heartbeat(db: Session, job_id: int) -> None:
    db.execute(update(Job).where(Job.id == job_id).values(heartbeat_at=utcnow()))
    db.commit()


def complete(db: Session, job: Job, result: dict) -> None:
    job.status = "succeeded"
    job.finished_at = utcnow()
    job.result = result
    job.last_error = None
    job.locked_by = None
    db.commit()


def fail(db: Session, job: Job, exc: BaseException) -> None:
    job.last_error = "".join(traceback.format_exception(exc))[-4000:]
    job.locked_by = None
    if job.attempts < job.max_attempts:
        job.status = "queued"
        job.run_after = utcnow() + dt.timedelta(minutes=2 ** job.attempts)
    else:
        job.status = "failed"
        job.finished_at = utcnow()
    db.commit()


def reap_stale(db: Session, stale_minutes: int) -> int:
    cutoff = utcnow() - dt.timedelta(minutes=stale_minutes)
    rows = db.scalars(select(Job).where(Job.status == "running", Job.heartbeat_at < cutoff)).all()
    for j in rows:
        j.last_error = f"worker {j.locked_by} stopped heartbeating (presumed crashed) at {j.heartbeat_at:%Y-%m-%d %H:%M} UTC"
        j.locked_by = None
        if j.attempts < j.max_attempts:
            j.status = "queued"
            j.run_after = utcnow()
        else:
            j.status = "failed"
            j.finished_at = utcnow()
    db.commit()
    return len(rows)

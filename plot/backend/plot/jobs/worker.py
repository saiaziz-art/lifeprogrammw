"""Background worker process: claims queued jobs, heartbeats while running, retries on failure."""

from __future__ import annotations

import base64
import logging
import os
import signal
import socket
import threading
import time

from sqlalchemy import select

from plot.config import get_settings
from plot.db import new_session, session_scope
from plot.jobs import queue
from plot.jobs.heartbeat import beat

log = logging.getLogger("plot.worker")


def handle_daily_sourcing(db, payload: dict, job_id: int) -> dict:
    from plot.jobs.pipeline import run_sourcing

    run = run_sourcing(db, trigger=payload.get("trigger", "schedule"), job_id=job_id, max_enrich=int(payload.get("max_enrich", 150)))
    return {"run_id": run.id, "status": run.status, "summary": run.summary}


def handle_reassess_all(db, payload: dict, job_id: int) -> dict:
    from plot.engine.assess import assess_site, rerank
    from plot.engine.costs.rates import load_rates
    from plot.models import Site
    from plot.preferences import load_preferences

    prefs, v = load_preferences(db)
    rc = load_rates(db)
    n = 0
    for s in db.scalars(select(Site).where(Site.excluded.is_(False))).all():
        if assess_site(db, s, prefs, v, force=bool(payload.get("force")), rates_cache=rc) is not None:
            n += 1
    rerank(db)
    db.commit()
    return {"reassessed": n}


def handle_enrich_site(db, payload: dict, job_id: int) -> dict:
    from plot.engine.assess import assess_site, rerank
    from plot.jobs.pipeline import _enrich
    from plot.models import Run, Site, utcnow
    from plot.preferences import enabled_bboxes, load_preferences
    from plot.sources.registry import load_secrets, sync_sources

    sync_sources(db)
    prefs, v = load_preferences(db)
    site = db.get(Site, int(payload["site_id"]))
    run = Run(job_id=job_id, kind="enrich_site", trigger="manual", status="running", steps=[], summary={"site_id": site.id})
    db.add(run)
    db.commit()
    site.enriched_at = None
    db.commit()
    stats = _enrich(db, run, prefs, load_secrets(db), enabled_bboxes(prefs), only_new=True, run_started=run.started_at, limit=1)
    assess_site(db, site, prefs, v, run_id=run.id, force=True)
    rerank(db)
    run.status = "partial" if stats["failed_sources"] else "succeeded"
    run.summary = {"site_id": site.id, "failed_sources": stats["failed_sources"], "errors": {k: v2["last_error"] for k, v2 in stats["source_stats"].items() if v2["failed"]}}
    run.finished_at = utcnow()
    db.commit()
    return run.summary


def handle_ingest_eml(db, payload: dict, job_id: int) -> dict:
    from plot.engine.assess import assess_site, rerank
    from plot.ingest import upsert
    from plot.models import Run, Source, utcnow
    from plot.preferences import enabled_bboxes, load_preferences
    from plot.sources.base import RunContext
    from plot.sources.email_alerts import ingest_raw_messages
    from plot.sources.registry import load_secrets, sync_sources

    sync_sources(db)
    prefs, v = load_preferences(db)
    raws = [base64.b64decode(x) for x in payload["emls"]]
    run = Run(job_id=job_id, kind="ingest_eml", trigger="manual", status="running", steps=[], summary={})
    db.add(run)
    db.commit()
    src = db.get(Source, "email.upload")
    ctx = RunContext(db=db, config=src.config or {}, search_bboxes=enabled_bboxes(prefs), secrets=load_secrets(db), now=utcnow(), run_id=run.id)
    sites, new = [], 0
    for rec in ingest_raw_messages(ctx, raws, "email.upload"):
        site, outcome = upsert(db, rec, run.id)
        sites.append(site)
        new += outcome == "new"
    db.commit()
    for s in sites:
        assess_site(db, s, prefs, v, run_id=run.id)
    rerank(db)
    run.status = "succeeded"
    run.summary = {"listings_located": len(sites), "new": new}
    run.finished_at = utcnow()
    db.commit()
    return run.summary


def handle_offmarket_discovery(db, payload: dict, job_id: int) -> dict:
    """Discovery only (no contact): analyse prospecting areas, enrich and assess new prospects, score."""
    from plot.engine.assess import assess_site, rerank
    from plot.engine.costs.rates import load_rates
    from plot.ingest import upsert
    from plot.jobs.pipeline import _enrich, _record_source
    from plot.models import Run, Site, Source, utcnow
    from plot.offmarket import agent
    from plot.preferences import enabled_bboxes, load_preferences
    from plot.sources.base import RunContext, SourceError
    from plot.sources.http import classify
    from plot.sources.registry import load_secrets, sync_sources

    sync_sources(db)
    prefs, v = load_preferences(db)
    run = Run(job_id=job_id, kind="offmarket_discovery", trigger=payload.get("trigger", "manual"), status="running", steps=[], summary={})
    db.add(run)
    db.commit()
    src = db.get(Source, agent.SOURCE_ID)
    secrets = load_secrets(db)
    ctx = RunContext(db=db, config=src.config or {}, search_bboxes=enabled_bboxes(prefs), secrets=secrets, now=utcnow(), run_id=run.id)
    adapter = agent.OffMarketDiscovery()
    started = utcnow()
    counts = {"fetched": 0, "new": 0, "changed": 0}
    try:
        with db.begin_nested():
            for rec in adapter.discover(ctx):
                counts["fetched"] += 1
                _, outcome = upsert(db, rec, run.id)
                if outcome in ("new", "changed"):
                    counts[outcome] += 1
        _record_source(db, run, agent.SOURCE_ID, "degraded" if adapter.degraded_note else "ok", fetched=counts["fetched"], new=counts["new"], changed=counts["changed"], started=started, note=adapter.degraded_note)
        status = "partial" if adapter.degraded_note else "succeeded"
    except Exception as exc:  # noqa: BLE001 — honest failure record
        err = exc if isinstance(exc, SourceError) else classify(exc)
        _record_source(db, run, agent.SOURCE_ID, "failed", err=err, started=started)
        status = "failed"
        run.error = str(err.args[0])
    db.commit()
    stats = _enrich(db, run, prefs, secrets, enabled_bboxes(prefs), only_new=True, run_started=run.started_at, limit=40)
    rc = load_rates(db)
    for s in db.scalars(select(Site).where(Site.excluded.is_(False), Site.first_seen_at >= run.started_at)).all():
        assess_site(db, s, prefs, v, run_id=run.id, rates_cache=rc)
    agent.sync_leads(db, run.id)
    scored = agent.rescore_all(db)
    rerank(db)
    run.status = status if not stats["failed_sources"] or status == "failed" else "partial"
    run.summary = counts | {"areas": adapter.area_stats, "prospects_scored": scored, "enrichment_failures": stats["failed_sources"]}
    run.finished_at = utcnow()
    db.commit()
    return run.summary


def handle_sync_replies(db, payload: dict, job_id: int) -> dict:
    from plot.outreach.delivery import sync_replies

    out = sync_replies(db)
    db.commit()
    return out


HANDLERS = {
    "offmarket_discovery": handle_offmarket_discovery,
    "sync_replies": handle_sync_replies,
    "daily_sourcing": handle_daily_sourcing,
    "reassess_all": handle_reassess_all,
    "enrich_site": handle_enrich_site,
    "ingest_eml": handle_ingest_eml,
}


def run_one(worker_id: str) -> bool:
    """Claim and execute a single job. Returns False if nothing was queued."""
    db = new_session()
    try:
        job = queue.claim(db, worker_id)
        if job is None:
            return False
        handler = HANDLERS.get(job.type)
        stop = threading.Event()

        def beat() -> None:
            while not stop.wait(30):
                with session_scope() as hb:
                    queue.heartbeat(hb, job.id)

        t = threading.Thread(target=beat, daemon=True)
        t.start()
        try:
            if handler is None:
                raise RuntimeError(f"no handler for job type {job.type!r}")
            work = new_session()
            try:
                result = handler(work, job.payload or {}, job.id)
            finally:
                work.close()
            job = db.merge(job)
            queue.complete(db, job, result)
            log.info("job %s (%s) succeeded: %s", job.id, job.type, result)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            job = db.merge(job)
            queue.fail(db, job, exc)
            log.exception("job %s (%s) failed (attempt %s/%s)", job.id, job.type, job.attempts, job.max_attempts)
        finally:
            stop.set()
        return True
    finally:
        db.close()


def run_forever() -> None:  # pragma: no cover - process loop
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    s = get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("worker %s started", worker_id)
    last_reap = 0.0
    while not stop["flag"]:
        if time.monotonic() - last_reap > 60:
            with session_scope() as db:
                n = queue.reap_stale(db, s.worker_stale_lock_minutes)
                if n:
                    log.warning("requeued %s stale job(s)", n)
            last_reap = time.monotonic()
        try:
            with session_scope() as db:
                beat(db, f"worker:{worker_id}", poll_seconds=s.worker_poll_seconds)
            busy = run_one(worker_id)
        except Exception:  # noqa: BLE001
            log.exception("worker loop error")
            busy = False
        if not busy:
            time.sleep(s.worker_poll_seconds)
    log.info("worker stopped")

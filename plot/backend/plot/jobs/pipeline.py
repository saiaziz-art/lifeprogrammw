"""The daily sourcing workflow (steps 1–10), with per-step and per-source run history.

Each step commits as it goes, so a crash leaves an accurate partial record and the run is
marked failed by the worker. Status semantics:
- succeeded: every enabled, configured live source returned data without error;
- partial:   the workflow completed but one or more sources failed or degraded;
- failed:    the workflow itself raised.
Sources blocked by terms or missing credentials are reported, not counted as failures.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any

from geoalchemy2.shape import from_shape, to_shape
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from plot import geo
from plot.engine.assess import assess_site, rerank
from plot.engine.costs.rates import load_rates, seed_rates
from plot.ingest import address_tokens, mark_missing, upsert
from plot.jobs.notify import deliver_digest, send_alert
from plot.models import Assessment, Digest, Evidence, Lead, LeadEvent, Listing, ListingEvent, Run, Site, Source, SourceRun, utcnow
from plot.offmarket import agent as offmarket
from plot.outreach.drafting import prepare_drafts
from plot.preferences import enabled_bboxes, load_preferences, point_in_search_area
from plot.sources.base import DiscoverySource, EnrichmentSource, RunContext, SiteContext, SourceError
from plot.sources.http import classify
from plot.sources.registry import DISCOVERY, ENRICHMENT, load_secrets, sync_sources

log = logging.getLogger("plot.pipeline")
NON_FAILURE = {"blocked_by_terms", "not_configured"}


class Recorder:
    def __init__(self, db: Session, run: Run):
        self.db, self.run = db, run
        self._t = 0.0

    def start(self, name: str, label: str) -> None:
        self._t = time.monotonic()
        self.run.steps = list(self.run.steps) + [{"name": name, "label": label, "status": "running", "started_at": utcnow().isoformat()}]
        self.db.commit()

    def finish(self, status: str, **detail: Any) -> None:
        steps = list(self.run.steps)
        steps[-1] = steps[-1] | {"status": status, "finished_at": utcnow().isoformat(), "seconds": round(time.monotonic() - self._t, 2), "detail": detail}
        self.run.steps = steps
        self.db.commit()


def _source_row(db: Session, sid: str) -> Source:
    return db.get(Source, sid)


def _record_source(db: Session, run: Run, sid: str, status: str, *, fetched=0, new=0, changed=0, err: SourceError | None = None, started: dt.datetime | None = None, note: str | None = None) -> SourceRun:
    now = utcnow()
    sr = SourceRun(
        run_id=run.id,
        source_id=sid,
        started_at=started or now,
        finished_at=now,
        status=status,
        records_fetched=fetched,
        records_new=new,
        records_changed=changed,
        http_status=err.http_status if err else None,
        error_class=err.error_class if err else None,
        error_message=(str(err.args[0]) if err else note),
    )
    db.add(sr)
    src = _source_row(db, sid)
    src.last_attempt_at = now
    src.last_status = status
    if status in ("ok", "degraded"):
        src.last_success_at = now
        src.consecutive_failures = 0
        src.last_error = None if status == "ok" else (note or (str(err.args[0]) if err else None))
        if new or changed:
            src.last_data_change_at = now
    elif status == "failed":
        src.consecutive_failures = (src.consecutive_failures or 0) + 1
        src.last_error = str(err.args[0]) if err else note
    else:
        src.last_error = str(err.args[0]) if err else note
    db.flush()
    return sr


def run_sourcing(db: Session, *, trigger: str = "manual", job_id: int | None = None, max_enrich: int = 150, now: dt.datetime | None = None) -> Run:
    run = Run(job_id=job_id, kind="daily_sourcing", trigger=trigger, status="running", steps=[], summary={}, started_at=now or utcnow())
    db.add(run)
    db.commit()
    rec = Recorder(db, run)
    try:
        _execute(db, run, rec, max_enrich)
    except Exception as exc:  # noqa: BLE001 — recorded, then re-raised for the job queue
        db.rollback()
        run = db.get(Run, run.id)
        steps = list(run.steps)
        if steps and steps[-1].get("status") == "running":
            steps[-1] = steps[-1] | {"status": "failed", "finished_at": utcnow().isoformat(), "detail": {"error": f"{type(exc).__name__}: {exc}"}}
        run.steps = steps
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = utcnow()
        db.commit()
        send_alert(db, f"PLOT sourcing run #{run.id} FAILED", f"The daily run failed: {run.error}")
        raise
    return run


def _execute(db: Session, run: Run, rec: Recorder, max_enrich: int) -> None:
    rec.start("prepare", "Load preferences, sources and cost assumptions")
    sync_sources(db)
    seed_rates(db)
    prefs, prefs_version = load_preferences(db)
    secrets = load_secrets(db)
    rates_cache = load_rates(db)
    bboxes = enabled_bboxes(prefs)
    rec.finish("ok", search_areas=len(bboxes), preferences_version=prefs_version)

    # 1 — retrieve new records ------------------------------------------------------
    rec.start("retrieve", "1. Retrieve new records from connected sources")
    results: dict[str, dict] = {}
    for cls in DISCOVERY:
        meta = cls.meta
        src = _source_row(db, meta.id)
        started = utcnow()
        if not src.enabled:
            _record_source(db, run, meta.id, "disabled", started=started)
            continue
        adapter: DiscoverySource = cls()
        ctx = RunContext(db=db, config=src.config or {}, search_bboxes=bboxes, secrets=secrets, now=utcnow(), run_id=run.id)
        if getattr(adapter, "reference_only", False) and src.last_success_at and utcnow() - src.last_success_at < dt.timedelta(days=7):
            _record_source(db, run, meta.id, "skipped", started=started, note="reference data refreshed within 7 days")
            continue
        counts = {"fetched": 0, "new": 0, "changed": 0}
        seen: set[str] = set()
        try:
            adapter.check_ready(ctx)
            # transient HTTP failures are already retried with backoff inside PublicHttp
            with db.begin_nested():
                for r in adapter.discover(ctx):
                    counts["fetched"] += 1
                    seen.add(r.external_ref)
                    _, outcome = upsert(db, r, run.id)
                    if outcome in ("new", "changed"):
                        counts[outcome] += 1
                if getattr(adapter, "reference_only", False):
                    counts["fetched"] = getattr(adapter, "records_fetched", 0)
            if meta.kind != "email" and not getattr(adapter, "reference_only", False):
                mark_missing(db, meta.id, seen, run.id)
            degraded = getattr(adapter, "degraded_note", None)
            _record_source(db, run, meta.id, "degraded" if degraded else "ok", fetched=counts["fetched"], new=counts["new"], changed=counts["changed"], started=started, note=degraded)
            results[meta.id] = {"status": "degraded" if degraded else "ok"} | counts
            if getattr(adapter, "area_stats", None):
                results[meta.id]["areas"] = adapter.area_stats
        except SourceError as err:
            status = "blocked" if err.error_class == "blocked_by_terms" else "not_configured" if err.error_class == "not_configured" else "failed"
            _record_source(db, run, meta.id, status, err=err, started=started)
            results[meta.id] = {"status": status, "error": str(err.args[0])}
            if getattr(adapter, "area_stats", None):
                results[meta.id]["areas"] = adapter.area_stats
        except Exception as exc:  # noqa: BLE001
            err = classify(exc)
            _record_source(db, run, meta.id, "failed", err=err, started=started)
            results[meta.id] = {"status": "failed", "error": str(err.args[0])}
        db.commit()
    rec.finish("ok", sources=results)

    # 2 — refresh & detect price/status changes ---------------------------------------
    rec.start("changes", "2. Refresh existing opportunities and detect price/status changes")
    events = db.execute(
        select(ListingEvent.event, func.count()).where(ListingEvent.run_id == run.id).group_by(ListingEvent.event)
    ).all()
    rec.finish("ok", listing_events={e: n for e, n in events})

    # 3 — public-data refresh for already-known sites (stale evidence) ----------------
    rec.start("public_data", "3. Ingest newly available planning and public-data updates")
    stats_known = _enrich(db, run, prefs, secrets, bboxes, only_new=False, run_started=run.started_at, limit=max_enrich // 2)
    rec.finish("ok" if not stats_known["failed_sources"] else "degraded", **stats_known)

    # 4 — dedupe --------------------------------------------------------------------
    rec.start("dedupe", "4. Deduplicate parcels and listings")
    merged = _dedupe(db)
    db.commit()
    rec.finish("ok", merged=merged)

    # 5 — screening -----------------------------------------------------------------
    rec.start("screen", "5. Preliminary eligibility screening")
    screened = _screen(db, prefs, post_enrichment=False)
    db.commit()
    rec.finish("ok", **screened)

    # 6 — enrich promising prospects ---------------------------------------------------
    rec.start("enrich", "6. Enrich promising prospects (planning & environmental)")
    stats_new = _enrich(db, run, prefs, secrets, bboxes, only_new=True, run_started=run.started_at, limit=max_enrich)
    post = _screen(db, prefs, post_enrichment=True)
    db.commit()
    rec.finish("ok" if not stats_new["failed_sources"] else "degraded", **stats_new, post_screen=post)

    # per-source enrichment health
    all_failed = {**stats_known["source_stats"], **stats_new["source_stats"]}
    for sid, st in all_failed.items():
        if st["calls"] == 0 and not st.get("not_configured"):
            continue
        if st.get("not_configured"):
            _record_source(db, run, sid, "not_configured", note=st["not_configured"])
        elif st["failed"] == 0:
            _record_source(db, run, sid, "ok", fetched=st["items"], new=st["items"], note=None)
        elif st["failed"] < st["calls"]:
            _record_source(db, run, sid, "degraded", fetched=st["items"], note=f"{st['failed']} of {st['calls']} site lookups failed: {st['last_error']}")
        else:
            _record_source(db, run, sid, "failed", err=SourceError(st["last_class"] or "failed", f"all {st['calls']} site lookups failed: {st['last_error']}"))
    db.commit()

    # 7 — recalculate feasibility -------------------------------------------------------
    rec.start("assess", "7. Recalculate feasibility and economics where inputs changed")
    n_new = n_same = 0
    for site in db.scalars(select(Site).where(Site.excluded.is_(False))).all():
        if assess_site(db, site, prefs, prefs_version, run_id=run.id, rates_cache=rates_cache) is None:
            n_same += 1
        else:
            n_new += 1
    db.commit()
    rec.finish("ok", reassessed=n_new, unchanged=n_same)

    # 7b — off-market acquisition (research and drafting only; never contacts anyone) ----
    rec.start("acquisition", "7b. Score off-market prospects and prepare drafts for your approval (never sends)")
    synced = offmarket.sync_leads(db, run.id)
    from plot.outreach.compliance import retention_sweep
    from plot.outreach.delivery import sync_replies

    deleted = retention_sweep(db)
    replies = sync_replies(db)
    scored = offmarket.rescore_all(db)
    drafts = prepare_drafts(db, prefs, actor="system")
    db.commit()
    rec.finish("ok" if not replies["errors"] else "degraded", analyses_updated=synced, prospects_scored=scored, drafts=drafts, replies=replies, retention_deleted=deleted)

    # 8 — rank ---------------------------------------------------------------------
    rec.start("rank", "8. Rank opportunities")
    rerank(db)
    db.commit()
    rec.finish("ok", ranked=db.scalar(select(func.count()).select_from(Assessment).where(Assessment.is_current.is_(True))))

    # 9 — digest -------------------------------------------------------------------
    rec.start("digest", "9. Produce daily digest (top new / materially improved)")
    digest = _digest(db, run, prefs)
    db.commit()
    rec.finish("ok", items=len(digest.items))

    # 10 — deliver -----------------------------------------------------------------
    rec.start("deliver", "10. Deliver email / app notification")
    deliver_digest(db, digest, prefs, secrets)
    db.commit()
    rec.finish("ok" if digest.delivery_status in ("sent", "nothing_to_send") else ("skipped" if digest.delivery_status == "not_configured" else "failed"), delivery_status=digest.delivery_status, error=digest.delivery_error)

    # finalise
    srs = db.scalars(select(SourceRun).where(SourceRun.run_id == run.id)).all()
    failing = sorted({s.source_id for s in srs if s.status in ("failed", "degraded")})
    run.status = "partial" if failing else "succeeded"
    run.finished_at = utcnow()
    run.summary = {
        "failing_sources": failing,
        "records_fetched": sum(s.records_fetched for s in srs if s.source_id in results),
        "records_new": sum(s.records_new for s in srs if s.source_id in results),
        "sites_active": db.scalar(select(func.count()).select_from(Site).where(Site.excluded.is_(False))),
        "digest_items": len(digest.items),
        "data_refreshed": any(s.status == "ok" and s.records_fetched > 0 for s in srs),
    }
    db.commit()
    alerts = [s for s in db.scalars(select(Source).where(Source.consecutive_failures >= 2)).all()]
    if alerts:
        body = "\n".join(f"- {s.name}: failing {s.consecutive_failures} runs in a row — {s.last_error}" for s in alerts)
        send_alert(db, f"PLOT: {len(alerts)} source(s) repeatedly failing", body)
        db.commit()


# ------------------------------------------------------------------------- enrichment


def _site_ctx(site: Site) -> SiteContext:
    p = to_shape(site.point)
    return SiteContext(site_id=site.id, lon=p.x, lat=p.y, boundary_wkt=to_shape(site.boundary).wkt if site.boundary is not None else None, area_m2=site.area_m2, postcode=site.postcode, address=site.address)


def _enrich(db: Session, run: Run, prefs, secrets, bboxes, *, only_new: bool, run_started: dt.datetime, limit: int) -> dict[str, Any]:
    q = select(Site).where(Site.excluded.is_(False))
    if only_new:
        q = q.where((Site.enriched_at.is_(None)) | (Site.last_changed_at > Site.enriched_at))
    else:
        q = q.where(Site.enriched_at.is_not(None))
    sites = db.scalars(q.order_by(Site.first_seen_at.desc()).limit(limit)).all()
    stats: dict[str, dict] = {}
    broken: set[str] = set()
    for cls in ENRICHMENT:
        stats[cls.meta.id] = {"calls": 0, "failed": 0, "items": 0, "last_error": None, "last_class": None}
    for site in sites:
        ctx_site = _site_ctx(site)
        for cls in ENRICHMENT:
            sid = cls.meta.id
            st = stats[sid]
            src = _source_row(db, sid)
            if not src.enabled or sid in broken:
                continue
            adapter: EnrichmentSource = cls()
            ctx = RunContext(db=db, config=src.config or {}, search_bboxes=bboxes, secrets=secrets, now=utcnow(), run_id=run.id)
            try:
                adapter.check_ready(ctx)
            except SourceError as e:
                st["not_configured"] = str(e.args[0])
                broken.add(sid)
                continue
            latest = db.scalar(select(func.max(Evidence.retrieved_at)).where(Evidence.site_id == site.id, Evidence.source_id == sid, Evidence.superseded_at.is_(None)))
            if latest and utcnow() - latest < dt.timedelta(days=cls.refresh_days) and not only_new:
                continue
            if latest and only_new and site.enriched_at and latest >= site.last_changed_at:
                continue
            st["calls"] += 1
            try:
                with db.begin_nested():
                    items = adapter.enrich(ctx, ctx_site)
                    _store_evidence(db, site.id, sid, items)
                st["items"] += len(items)
                st["consecutive_fail"] = 0
            except Exception as exc:  # noqa: BLE001
                err = classify(exc)
                st["failed"] += 1
                st["last_error"] = str(err.args[0])
                st["last_class"] = err.error_class
                st["consecutive_fail"] = st.get("consecutive_fail", 0) + 1
                if err.error_class in ("network_blocked", "network_unreachable", "forbidden", "not_found", "all_endpoints_failed") and st["consecutive_fail"] >= 3:
                    broken.add(sid)  # circuit breaker: stop hammering a dead endpoint this run
                    st["last_error"] += " (circuit opened after 3 consecutive failures)"
        _apply_derived(db, site)
        site.enriched_at = utcnow()
        db.commit()
    failed = sorted(s for s, v in stats.items() if v["failed"])
    return {"sites": len(sites), "failed_sources": failed, "source_stats": stats}


def _store_evidence(db: Session, site_id: int, source_id: str, items) -> None:
    now = utcnow()
    keep: set[int] = set()
    for it in items:
        fp = it.fingerprint(source_id)
        row = db.scalar(select(Evidence).where(Evidence.site_id == site_id, Evidence.fingerprint == fp))
        if row is None:
            row = Evidence(site_id=site_id, topic=it.topic, kind=it.kind, source_id=source_id, source_url=it.source_url, source_ref=it.source_ref, title=it.title[:500], summary=it.summary, data=it.data, data_date=it.data_date, status=it.status, confidence=it.confidence, licence=it.licence, attribution=it.attribution, fingerprint=fp, retrieved_at=now)
            db.add(row)
            db.flush()
        else:
            row.retrieved_at = now
            row.superseded_at = None
            row.status = it.status
        keep.add(row.id)
    for old in db.scalars(select(Evidence).where(Evidence.site_id == site_id, Evidence.source_id == source_id, Evidence.superseded_at.is_(None), ~Evidence.kind.startswith("source-record:"))).all():
        if old.id not in keep:
            old.superseded_at = now
    db.flush()


def _apply_derived(db: Session, site: Site) -> None:
    ev = {e.kind: e for e in db.scalars(select(Evidence).where(Evidence.site_id == site.id, Evidence.superseded_at.is_(None))).all()}
    lpa = next((e for k, e in ev.items() if k == "pd:local-planning-authority"), None)
    pc = ev.get("postcode")
    if lpa is not None:
        site.lpa_name = lpa.data.get("name")
    elif pc is not None and not site.lpa_name:
        site.lpa_name = pc.data.get("admin_district")
    if pc is not None:
        site.county = pc.data.get("admin_county") or site.county
        if not site.postcode:
            site.postcode = pc.data.get("postcode")
    # adopt an INSPIRE extent only when the site point is exact (not a postcode/place centroid)
    loc_quality = (site.provenance or [{}])[0].get("location_quality", "exact")
    polys = [e for e in ev.values() if e.kind == "inspire-polygon" and e.data.get("boundary_wkt")]
    if site.boundary is None and loc_quality == "exact" and len(polys) == 1:
        g = geo.parse_wkt(polys[0].data["boundary_wkt"])
        if g is not None:
            a = geo.area_m2(g)
            if site.area_m2 is None or 0.5 <= a / site.area_m2 <= 2.0:
                site.boundary = from_shape(g, srid=4326)
                site.boundary_source = "inspire"
                if site.area_m2 is None:
                    site.area_m2 = round(a)
                    site.area_source = "INSPIRE indicative extent"
    # auto-advance off-market leads only as far as evidence supports
    lead = db.scalar(select(Lead).where(Lead.site_id == site.id))
    if lead is not None and polys and lead.ownership_status in ("unknown", "inspire_matched"):
        ids = ", ".join(str(p.data.get("inspire_id")) for p in polys[:3])
        lead.ownership_status = "title_purchase_needed"
        lead.analysis = (lead.analysis or {}) | {"inspire_ids": [p.data.get("inspire_id") for p in polys[:5]]}
        db.add(LeadEvent(lead_id=lead.id, from_stage=lead.stage, to_stage=lead.stage, actor="system", note=f"INSPIRE title extent(s) matched: {ids}. Owner identity requires an official copy of the register (HM Land Registry)."))


def _dedupe(db: Session) -> int:
    merged = 0
    sites = db.scalars(select(Site).where(Site.excluded.is_(False)).order_by(Site.first_seen_at)).all()
    for s in sites:
        if s.excluded:
            continue
        others = db.scalars(select(Site).where(Site.id != s.id, Site.excluded.is_(False), Site.first_seen_at >= s.first_seen_at, Site.point.ST_DWithin(s.point, 0.0003))).all()
        for o in others:
            ta, tb = address_tokens(s.address or s.headline), address_tokens(o.address or o.headline)
            if ta and tb and not (ta & tb):
                continue
            for l in db.scalars(select(Listing).where(Listing.site_id == o.id)).all():
                l.site_id = s.id
            for e in db.scalars(select(Evidence).where(Evidence.site_id == o.id, Evidence.kind.startswith("source-record:"))).all():
                e.site_id = s.id
            s.provenance = list(s.provenance or []) + list(o.provenance or [])
            if o.origin == "on_market":
                s.origin = "on_market"
            o.excluded = True
            o.exclusion_reason = f"duplicate of site #{s.id}"
            s.last_changed_at = utcnow()
            merged += 1
    db.flush()
    return merged


def _screen(db: Session, prefs, post_enrichment: bool) -> dict[str, int]:
    ex = prefs.exclusions
    counts = {"excluded": 0, "included": 0}
    for s in db.scalars(select(Site)).all():
        if s.exclusion_reason and (s.exclusion_reason.startswith("duplicate") or s.exclusion_reason.startswith("user:")):
            continue
        p = to_shape(s.point)
        reasons = []
        if not point_in_search_area(prefs, p.x, p.y):
            reasons.append("outside enabled search areas")
        if s.area_m2 is not None and s.area_m2 < ex.min_site_area_m2:
            reasons.append(f"site area {s.area_m2:,.0f} m² below minimum {ex.min_site_area_m2:,.0f} m²")
        if s.area_m2 is not None and s.area_m2 > ex.max_site_area_m2:
            reasons.append(f"site area {s.area_m2:,.0f} m² above maximum {ex.max_site_area_m2:,.0f} m²")
        if s.lpa_name and s.lpa_name in ex.excluded_lpas:
            reasons.append(f"LPA {s.lpa_name} excluded")
        text = " ".join(filter(None, [s.headline] + [f"{l.title or ''} {l.description or ''}" for l in s.listings])).lower()
        for kw in ex.excluded_keywords:
            if kw.lower() in text:
                reasons.append(f"keyword '{kw}'")
        prices = [l.asking_price for l in s.listings if l.asking_price]
        if ex.max_asking_price_gbp and prices and min(prices) > ex.max_asking_price_gbp:
            reasons.append(f"asking price above £{ex.max_asking_price_gbp:,}")
        if post_enrichment and ex.exclude_flood_zone_3:
            fz = db.scalar(select(Evidence).where(Evidence.site_id == s.id, Evidence.kind == "ea:flood_zone", Evidence.superseded_at.is_(None)))
            if fz is not None and str(fz.data.get("zone", "")).startswith("3"):
                reasons.append("Flood Zone 3")
        if reasons:
            if not s.excluded:
                counts["excluded"] += 1
            s.excluded = True
            s.exclusion_reason = "; ".join(reasons)
        elif s.excluded:
            s.excluded = False
            s.exclusion_reason = None
            counts["included"] += 1
    db.flush()
    return counts


def _digest(db: Session, run: Run, prefs) -> Digest:
    n = prefs.notifications.digest_size
    rows = db.execute(
        select(Assessment, Site).join(Site, Site.id == Assessment.site_id).where(Assessment.is_current.is_(True), Site.excluded.is_(False), Assessment.fatal.is_(False))
    ).all()
    items = []
    for a, s in rows:
        reason = None
        if s.first_seen_at >= run.started_at:
            reason = "new"
        else:
            prev = db.scalar(select(Assessment).where(Assessment.site_id == s.id, Assessment.is_current.is_(False)).order_by(Assessment.created_at.desc()).limit(1))
            if prev is not None and a.run_id == run.id and a.total_score - prev.total_score >= 5:
                reason = f"score up {a.total_score - prev.total_score:.0f} points"
            cut = db.scalar(
                select(func.count()).select_from(ListingEvent).join(Listing, Listing.id == ListingEvent.listing_id).where(Listing.site_id == s.id, ListingEvent.run_id == run.id, ListingEvent.event == "price_change")
            )
            if cut:
                reason = (reason + "; " if reason else "") + "price changed"
        if reason:
            items.append({"site_id": s.id, "headline": s.headline, "score": a.total_score, "rank": a.rank, "reason": reason, "budget_fit": a.explanation.get("budget_fit"), "next_action": a.explanation.get("next_action"), "confidence": (a.explanation.get("confidence") or {}).get("label"), "stale": s.stale})
    items.sort(key=lambda i: (i["stale"], -(i["score"] or 0)))
    items = items[:n]
    lines = [f"PLOT daily digest — {utcnow():%A %d %B %Y}", ""]
    if not items:
        lines.append("No genuinely new or materially improved opportunities today.")
    for i, it in enumerate(items, 1):
        lines += [f"{i}. {it['headline']}  (score {it['score']:.0f}/100, {it['reason']}, data confidence {it['confidence']}{', HISTORIC RECORD' if it['stale'] else ''})", f"   {it['budget_fit']}", f"   Next: {it['next_action']}", ""]
    brief = offmarket.briefing(db, run.started_at)
    lines += offmarket.briefing_text(brief)
    has_news = bool(items) or offmarket.briefing_has_news(brief)
    d = Digest(run_id=run.id, digest_date=utcnow().date(), items=items, briefing=brief, body_text="\n".join(lines), delivery_status="nothing_to_send" if not has_news else "not_configured")
    db.add(d)
    db.flush()
    return d


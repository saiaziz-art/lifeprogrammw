"""Dashboard, preferences, Design Studio and the administrator screen."""

from __future__ import annotations

import datetime as dt
import statistics
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from plot.api.deps import audit, current_user
from plot.api.serialize import rows_for
from plot.db import get_db
from plot.engine.assess import compute
from plot.engine.costs.rates import load_rates
from plot.engine.design.concepts import CONCEPTS, METHODS, design_option
from plot.engine.facts import SiteFacts, load_facts
from plot.jobs.queue import enqueue
from plot.jobs.scheduler import next_fire_time
from plot.models import (
    AuditLog,
    CostRate,
    Digest,
    Evidence,
    Expense,
    IngestInbox,
    Job,
    Lead,
    Run,
    Schedule,
    ServiceHeartbeat,
    Site,
    Source,
    SourceRun,
    User,
    utcnow,
)
from plot.preferences import PreferencesModel, load_preferences, save_preferences
from plot.security.crypto import SecretsUnavailable, delete_secret, secret_status, set_secret
from plot.sources.registry import SECRET_KEYS, adapter_by_id

router = APIRouter()


def _run_out(r: Run, source_runs: list[SourceRun] | None = None) -> dict:
    out = {"id": r.id, "kind": r.kind, "trigger": r.trigger, "status": r.status, "started_at": r.started_at.isoformat(), "finished_at": r.finished_at and r.finished_at.isoformat(), "steps": r.steps, "summary": r.summary, "error": r.error, "job_id": r.job_id}
    if source_runs is not None:
        out["sources"] = [{"source_id": s.source_id, "status": s.status, "records_fetched": s.records_fetched, "records_new": s.records_new, "records_changed": s.records_changed, "http_status": s.http_status, "error_class": s.error_class, "error_message": s.error_message, "finished_at": s.finished_at and s.finished_at.isoformat()} for s in source_runs]
    return out


# ---------------------------------------------------------------------------- dashboard


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)) -> dict[str, Any]:
    prefs, _ = load_preferences(db)
    last_run = db.scalar(select(Run).where(Run.kind == "daily_sourcing").order_by(Run.started_at.desc()).limit(1))
    last_ok = db.scalar(select(Run).where(Run.kind == "daily_sourcing", Run.status == "succeeded").order_by(Run.started_at.desc()).limit(1))
    last_completed = db.scalar(select(Run).where(Run.kind == "daily_sourcing", Run.status.in_(("succeeded", "partial"))).order_by(Run.started_at.desc()).limit(1))
    last_refresh = db.scalar(select(func.max(SourceRun.finished_at)).where(SourceRun.status.in_(("ok", "degraded")), SourceRun.records_fetched > 0))
    sites = db.scalars(select(Site).where(Site.excluded.is_(False))).all()
    rows = rows_for(db, sites)
    today0 = dt.datetime.combine(dt.date.today(), dt.time(), tzinfo=dt.UTC)
    viable = [r for r in rows if not r["score"]["fatal"] and r["score"]["total"] is not None]
    viable.sort(key=lambda r: r["score"]["total"], reverse=True)
    new_today = [r for r in rows if r["first_seen_at"] >= today0.isoformat()]
    reductions = [r for r in rows if r["price_reductions"]]
    off_market = sorted([r for r in rows if r["origin"] in ("off_market", "register") and not r["score"]["fatal"]], key=lambda r: r["score"]["total"] or 0, reverse=True)
    residuals = [r["residual_land_value"] for r in rows if r["residual_land_value"] is not None]
    within = [r for r in rows if r["within_budget"]]
    capacity = [r for r in rows if r["asking_price"] is None and (r["residual_land_value"] or -1) > 0 and not r["score"]["fatal"]]
    alerts = []
    failing = db.scalars(select(Source).where(Source.enabled.is_(True), Source.last_status.in_(("failed", "degraded")))).all()
    by_cause: dict[str, list[Source]] = {}
    for s in failing:
        cause = "network egress blocked (HTTP 403 from proxy)" if "egress proxy" in (s.last_error or "") else (s.last_error or "unknown error")[:160]
        by_cause.setdefault(cause, []).append(s)
    for cause, srcs in by_cause.items():
        repeated = any(s.consecutive_failures >= 2 for s in srcs)
        alerts.append({"level": "error" if repeated else "warning", "title": f"{len(srcs)} source(s) failing: {cause}", "detail": ", ".join(s.name for s in srcs), "link": "/admin"})
    if last_run is None:
        alerts.append({"level": "warning", "title": "No sourcing run has been executed yet", "detail": "Start the worker and scheduler, or run one from Admin.", "link": "/admin"})
    hb = db.get(ServiceHeartbeat, "scheduler")
    if hb is None or utcnow() - hb.at > dt.timedelta(minutes=10):
        alerts.append({"level": "error", "title": "Scheduler is not running", "detail": f"Last heartbeat: {hb.at.isoformat() if hb else 'never'}. The 07:00 run will not happen until it is started.", "link": "/admin"})
    stale = sum(1 for r in rows if r["stale"])
    if stale:
        alerts.append({"level": "info", "title": f"{stale} site(s) come from historic records", "detail": "Their status may have changed since publication — verify before acting.", "link": "/deals"})
    digest = db.scalar(select(Digest).order_by(Digest.created_at.desc()).limit(1))
    if digest and digest.delivery_status == "not_configured":
        alerts.append({"level": "info", "title": "Digest delivery not configured", "detail": digest.delivery_error, "link": "/admin"})
    sch = db.scalar(select(Schedule).where(Schedule.name == "daily_sourcing"))
    return {
        "budget": prefs.budget.total_all_in_gbp,
        "last_run": _run_out(last_run) if last_run else None,
        "last_successful_run": _run_out(last_ok) if last_ok else None,
        "last_completed_run": _run_out(last_completed) if last_completed else None,
        "last_data_refresh": last_refresh.isoformat() if last_refresh else None,
        "next_run": next_fire_time(sch.cron, sch.timezone, utcnow()).isoformat() if sch and sch.enabled else None,
        "counts": {"active": len(rows), "viable": len(viable), "fatal": sum(1 for r in rows if r["score"]["fatal"]), "on_market": sum(1 for r in rows if r["origin"] == "on_market"), "off_market": len(off_market), "within_budget": len(within), "priced": sum(1 for r in rows if r["asking_price"] is not None), "unpriced_with_capacity": len(capacity), "stale": stale},
        "strongest": viable[:6],
        "new_today": new_today[:8],
        "price_reductions": reductions[:6],
        "off_market": off_market[:6],
        "affordability": {"median_residual": int(statistics.median(residuals)) if residuals else None, "best_residual": max(residuals) if residuals else None, "within_budget": len(within), "assessed": len(residuals)},
        "alerts": alerts,
        "digest": None if digest is None else {"date": digest.digest_date.isoformat(), "items": digest.items, "delivery_status": digest.delivery_status, "delivery_error": digest.delivery_error},
    }


# ---------------------------------------------------------------------------- preferences


@router.get("/preferences")
def get_prefs(db: Session = Depends(get_db)) -> dict:
    prefs, v = load_preferences(db)
    db.commit()
    return {"version": v, "preferences": prefs.model_dump(mode="json")}


@router.put("/preferences")
def put_prefs(body: dict[str, Any], db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    try:
        prefs = PreferencesModel.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e
    v = save_preferences(db, prefs, user.email)
    audit(db, user, "preferences.save", "preferences", v)
    j = enqueue(db, "reassess_all", {}, dedupe_key=f"reassess:prefs:{v}")
    db.commit()
    return {"version": v, "reassess_job": j.id if j else None}


# ---------------------------------------------------------------------------- design studio


@router.get("/design/catalogue")
def design_catalogue() -> dict:
    return {
        "concepts": [c.__dict__ for c in CONCEPTS.values()],
        "methods": [{"key": m.key, "name": m.name, "notes": m.notes, "design_for_cost": m.design_for_cost, "programme_months": m.programme_months} for m in METHODS.values()],
        "specifications": ["essential", "good", "high"],
    }


class DesignIn(BaseModel):
    site_id: int | None = None
    concept: str = "small_65"
    method: str = "timber_frame"
    gia_m2: float | None = Field(None, ge=25, le=250)
    storeys: float | None = Field(None, ge=1, le=2)
    specification: str | None = None
    include_solar: bool | None = None
    include_rainwater: bool | None = None
    land_price: int | None = Field(None, ge=0)
    budget: int | None = Field(None, ge=50_000)
    site_area_m2: float | None = Field(None, ge=50, le=200_000)


@router.post("/design/evaluate")
def design_evaluate(body: DesignIn, db: Session = Depends(get_db)) -> dict:
    if body.concept not in CONCEPTS or body.method not in METHODS:
        raise HTTPException(422, "unknown concept or method")
    prefs, _ = load_preferences(db)
    p = prefs.model_copy(deep=True)
    if body.specification:
        p.house.specification = body.specification  # type: ignore[assignment]
    if body.include_solar is not None:
        p.house.want_solar_pv = body.include_solar
    if body.include_rainwater is not None:
        p.house.want_rainwater_harvesting = body.include_rainwater
    if body.budget:
        p.budget.total_all_in_gbp = body.budget
    p.house.preferred_construction = [body.method]
    rates, meta = load_rates(db)
    if body.site_id:
        site = db.get(Site, body.site_id)
        if site is None:
            raise HTTPException(404)
        facts = load_facts(db, site)
    else:
        facts = SiteFacts(0, -1.6, 50.8, None, "none", body.site_area_m2, "entered in Design Studio" if body.site_area_m2 else None, None, None, None, "plot", "manual", "Generic plot (no site)", False, [], None, [], [], {}, None, None, dt.date.today())
    if body.land_price is not None:
        facts.overrides["listing.asking_price"] = {"value": body.land_price, "reason": "Design Studio scenario"}
    out = compute(facts, p, rates, meta, design={"concept": body.concept, "method": body.method, "gia_m2": body.gia_m2, "storeys": body.storeys})
    alt = []
    for mk in METHODS:
        if mk == "conversion" and facts.category not in ("barn", "renovation", "building"):
            continue
        p2 = p.model_copy(deep=True)
        p2.house.preferred_construction = [mk]
        o2 = compute(facts, p2, rates, meta, design={"concept": body.concept, "method": mk, "gia_m2": body.gia_m2, "storeys": body.storeys}, light=True)
        alt.append({"method": mk, "name": METHODS[mk].name, "total_base": o2["residual"]["total_project_cost"]["base"], "non_land_base": o2["residual"]["non_land_cost"]["base"], "residual": o2["residual"]["residual_land_value"], "programme_months": o2["design"]["option"]["programme_months"], "cost_per_m2": o2["residual"]["cost_per_m2"]})
    return {"design": out["design"], "residual": out["residual"], "costs": out["costs"], "comparison": alt, "site_risk_drivers": out["site_risk"]["drivers"], "planning_class": out["planning"]["classification_label"], "fatal_flags": out["score"]["fatal_flags"]}


@router.get("/design/option")
def design_option_preview(concept: str = "small_65", method: str = "timber_frame", gia_m2: float | None = None, storeys: float | None = None) -> dict:
    if concept not in CONCEPTS or method not in METHODS:
        raise HTTPException(422, "unknown concept or method")
    return design_option(concept, method, gia_m2, storeys)


# ---------------------------------------------------------------------------- admin


@router.get("/admin/overview")
def admin_overview(db: Session = Depends(get_db)) -> dict:
    hbs = db.scalars(select(ServiceHeartbeat)).all()
    now = utcnow()
    schedules = []
    for s in db.scalars(select(Schedule)).all():
        schedules.append({"id": s.id, "name": s.name, "cron": s.cron, "timezone": s.timezone, "enabled": s.enabled, "job_type": s.job_type, "last_enqueued_for": s.last_enqueued_for and s.last_enqueued_for.isoformat(), "next_fire": next_fire_time(s.cron, s.timezone, now).isoformat() if s.enabled else None, "next_fire_local": next_fire_time(s.cron, s.timezone, now).astimezone(ZoneInfo(s.timezone)).isoformat() if s.enabled else None})
    return {
        "now": now.isoformat(),
        "services": [{"name": h.name, "last_seen": h.at.isoformat(), "seconds_ago": int((now - h.at).total_seconds()), "alive": (now - h.at) < dt.timedelta(minutes=3), "detail": h.detail} for h in hbs],
        "schedules": schedules,
        "jobs": [{"id": j.id, "type": j.type, "status": j.status, "attempts": j.attempts, "max_attempts": j.max_attempts, "created_at": j.created_at.isoformat(), "started_at": j.started_at and j.started_at.isoformat(), "finished_at": j.finished_at and j.finished_at.isoformat(), "scheduled_for": j.scheduled_for and j.scheduled_for.isoformat(), "last_error": (j.last_error or "")[-600:] or None, "result": j.result} for j in db.scalars(select(Job).order_by(Job.id.desc()).limit(25)).all()],
    }


@router.get("/admin/sources")
def admin_sources(db: Session = Depends(get_db)) -> list[dict]:
    out = []
    for s in db.scalars(select(Source).order_by(Source.category, Source.id)).all():
        a = adapter_by_id(s.id)
        recent = db.scalars(select(SourceRun).where(SourceRun.source_id == s.id).order_by(SourceRun.id.desc()).limit(7)).all()
        out.append({
            "id": s.id, "name": s.name, "category": s.category, "kind": s.kind, "enabled": s.enabled, "licence": s.licence, "attribution": s.attribution,
            "terms_url": s.terms_url, "docs_url": s.docs_url, "notes": s.notes, "config": s.config,
            "required_secrets": list(a.meta.required_secrets) if a else [],
            "last_attempt_at": s.last_attempt_at and s.last_attempt_at.isoformat(), "last_success_at": s.last_success_at and s.last_success_at.isoformat(),
            "last_data_change_at": s.last_data_change_at and s.last_data_change_at.isoformat(), "last_status": s.last_status, "last_error": s.last_error,
            "consecutive_failures": s.consecutive_failures,
            "history": [{"run_id": r.run_id, "status": r.status, "fetched": r.records_fetched, "error_class": r.error_class, "at": r.finished_at and r.finished_at.isoformat()} for r in recent],
        })
    return out


class SourcePatch(BaseModel):
    enabled: bool | None = None
    config: dict[str, Any] | None = None


@router.patch("/admin/sources/{source_id}")
def patch_source(source_id: str, body: SourcePatch, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    s = db.get(Source, source_id)
    if s is None:
        raise HTTPException(404)
    if body.enabled is not None:
        s.enabled = body.enabled
    if body.config is not None:
        s.config = body.config
    audit(db, user, "source.update", "source", source_id, enabled=body.enabled)
    db.commit()
    return {"ok": True}


@router.get("/admin/runs")
def admin_runs(db: Session = Depends(get_db), limit: int = 20) -> list[dict]:
    return [_run_out(r) for r in db.scalars(select(Run).order_by(Run.id.desc()).limit(limit)).all()]


@router.get("/admin/runs/{run_id}")
def admin_run(run_id: int, db: Session = Depends(get_db)) -> dict:
    r = db.get(Run, run_id)
    if r is None:
        raise HTTPException(404)
    return _run_out(r, db.scalars(select(SourceRun).where(SourceRun.run_id == run_id).order_by(SourceRun.id)).all())


@router.post("/admin/runs")
def admin_run_now(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    j = enqueue(db, "daily_sourcing", {"trigger": "manual"}, max_attempts=1)
    audit(db, user, "run.enqueue", "job", j.id if j else None)
    db.commit()
    return {"queued_job": j.id if j else None, "note": "A worker process picks this up within a few seconds if running."}


class SchedulePatch(BaseModel):
    cron: str | None = None
    timezone: str | None = None
    enabled: bool | None = None


@router.patch("/admin/schedules/{schedule_id}")
def patch_schedule(schedule_id: int, body: SchedulePatch, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    s = db.get(Schedule, schedule_id)
    if s is None:
        raise HTTPException(404)
    if body.cron is not None:
        if not croniter.is_valid(body.cron):
            raise HTTPException(422, "invalid cron expression")
        s.cron = body.cron
    if body.timezone is not None:
        try:
            ZoneInfo(body.timezone)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, "unknown time zone") from exc
        s.timezone = body.timezone
    if body.enabled is not None:
        s.enabled = body.enabled
    audit(db, user, "schedule.update", "schedule", schedule_id, cron=s.cron, tz=s.timezone, enabled=s.enabled)
    db.commit()
    return {"ok": True, "next_fire": next_fire_time(s.cron, s.timezone, utcnow()).isoformat()}


@router.get("/admin/secrets")
def admin_secrets(db: Session = Depends(get_db)) -> list[dict]:
    return [{"key": k, "label": v, "status": secret_status(db, k)} for k, v in SECRET_KEYS.items()]


class SecretIn(BaseModel):
    value: str = Field(min_length=1, max_length=4000)


@router.put("/admin/secrets/{key}")
def put_secret(key: str, body: SecretIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    if key not in SECRET_KEYS:
        raise HTTPException(404, "unknown credential key")
    try:
        set_secret(db, key, body.value, user.email)
    except SecretsUnavailable as e:
        raise HTTPException(503, str(e)) from e
    audit(db, user, "secret.set", "secret", key)
    db.commit()
    return {"ok": True, "status": "set"}


@router.delete("/admin/secrets/{key}")
def remove_secret(key: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    delete_secret(db, key)
    audit(db, user, "secret.delete", "secret", key)
    db.commit()
    return {"ok": True}


@router.get("/admin/cost-rates")
def cost_rates(db: Session = Depends(get_db)) -> list[dict]:
    return [{"key": r.key, "category": r.category, "description": r.description, "unit": r.unit, "low": r.low, "base": r.base, "high": r.high, "basis": r.basis, "source": r.source, "source_date": r.source_date.isoformat(), "user_override": r.user_override} for r in db.scalars(select(CostRate).order_by(CostRate.category, CostRate.key)).all()]


class RateIn(BaseModel):
    low: float = Field(ge=0)
    base: float = Field(ge=0)
    high: float = Field(ge=0)
    source: str = Field(min_length=3, max_length=1000)
    source_date: dt.date


@router.put("/admin/cost-rates/{key}")
def put_rate(key: str, body: RateIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    r = db.get(CostRate, key)
    if r is None:
        raise HTTPException(404)
    if not body.low <= body.base <= body.high:
        raise HTTPException(422, "rates must satisfy low ≤ base ≤ high")
    r.low, r.base, r.high, r.source, r.source_date, r.user_override = body.low, body.base, body.high, body.source, body.source_date, True
    audit(db, user, "cost_rate.update", "cost_rate", key, low=body.low, base=body.base, high=body.high, source=body.source)
    enqueue(db, "reassess_all", {}, dedupe_key=f"reassess:rate:{key}:{utcnow().timestamp():.0f}")
    db.commit()
    return {"ok": True}


@router.get("/admin/expenses")
def expenses(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(Expense).order_by(Expense.incurred_at.desc())).all()
    return {"total_gbp": round(sum(e.amount_gbp for e in rows), 2), "items": [{"id": e.id, "category": e.category, "description": e.description, "amount_gbp": e.amount_gbp, "incurred_at": e.incurred_at.isoformat(), "site_id": e.site_id} for e in rows]}


class ExpenseIn(BaseModel):
    category: str = Field(min_length=2, max_length=60)
    description: str = Field(min_length=2, max_length=1000)
    amount_gbp: float = Field(ge=0)
    site_id: int | None = None


@router.post("/admin/expenses")
def add_expense(body: ExpenseIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    db.add(Expense(category=body.category, description=body.description, amount_gbp=body.amount_gbp, site_id=body.site_id))
    audit(db, user, "expense.add", "site", body.site_id, amount=body.amount_gbp)
    db.commit()
    return {"ok": True}


@router.get("/admin/missing")
def missing_information(db: Session = Depends(get_db)) -> dict:
    sites = db.scalars(select(Site).where(Site.excluded.is_(False))).all()
    ids = [s.id for s in sites]
    topics: dict[int, set[str]] = {}
    for sid, topic in db.execute(select(Evidence.site_id, Evidence.topic).where(Evidence.site_id.in_(ids), Evidence.superseded_at.is_(None))).all() if ids else []:
        topics.setdefault(sid, set()).add(topic)
    need = ("flood", "ground", "location", "heritage")
    rows = []
    for s in sites:
        have = topics.get(s.id, set())
        gaps = []
        if s.boundary is None:
            gaps.append("boundary")
        if s.area_m2 is None:
            gaps.append("site area")
        if not any(l.asking_price for l in s.listings) and s.origin == "on_market":
            gaps.append("asking price")
        gaps += [f"{t} data" for t in need if t not in have]
        if s.stale:
            gaps.append("current status (historic record)")
        if gaps:
            rows.append({"site_id": s.id, "headline": s.headline, "gaps": gaps})
    unconfigured = [{"id": s.id, "name": s.name, "status": s.last_status, "error": s.last_error} for s in db.scalars(select(Source).where(Source.last_status.in_(("not_configured", "failed", "degraded")))).all()]
    inbox_n = db.scalar(select(func.count()).select_from(IngestInbox).where(IngestInbox.dismissed.is_(False), IngestInbox.resolved_site_id.is_(None)))
    return {"sites": rows, "sources": unconfigured, "inbox_unlocated": inbox_n, "leads_awaiting_owner_research": db.scalar(select(func.count()).select_from(Lead).where(Lead.ownership_status == "title_purchase_needed"))}


@router.get("/admin/audit")
def audit_log(db: Session = Depends(get_db), limit: int = 100) -> list[dict]:
    return [{"at": a.at.isoformat(), "actor": a.actor, "action": a.action, "entity": a.entity, "entity_id": a.entity_id, "detail": a.detail} for a in db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()]



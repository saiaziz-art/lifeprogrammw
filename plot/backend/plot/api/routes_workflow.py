"""Acquisition pipeline for shortlisted sites (on- and off-market).

Off-market leads, ownership verification and owner outreach live in routes_offmarket.py.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from plot.api.deps import audit, current_user
from plot.api.serialize import rows_for
from plot.db import get_db
from plot.models import PIPELINE_STAGES, Assessment, PipelineItem, Site, User

router = APIRouter()

STANDARD_CHECKLIST = [
    ("planning_verify", "Verify planning status/history on the LPA register"),
    ("pre_app", "Pre-application advice from the LPA"),
    ("title", "Title register & plan: access rights, covenants, easements, ransom strips"),
    ("searches", "Local authority, drainage & water, environmental searches"),
    ("site_visit", "Site visit: access, slope, trees, neighbours, noise, light"),
    ("topo", "Topographical survey"),
    ("utilities", "DNO electricity estimate; water & sewer quotes; broadband enquiry"),
    ("ground", "Ground investigation & percolation test"),
    ("ecology", "Preliminary ecological appraisal (seasonal surveys if triggered)"),
    ("flood", "Flood risk assessment (if flood/surface-water risk)"),
    ("trees", "Arboricultural survey (if trees near works)"),
    ("design_cost", "Architect concept + independent cost check"),
    ("finance", "Self-build mortgage agreement in principle"),
    ("cil", "CIL self-build exemption (submit before commencement)"),
    ("insurance", "Site insurance & structural warranty quotes"),
    ("offer", "Offer strategy: max offer, conditional/option terms, solicitor briefed"),
]


# ---------------------------------------------------------------------------- pipeline


@router.get("/pipeline")
def pipeline(db: Session = Depends(get_db)) -> dict:
    items = db.scalars(select(PipelineItem)).all()
    sites = {s.id: s for s in db.scalars(select(Site).where(Site.id.in_([i.site_id for i in items]))).all()} if items else {}
    rows = {r["id"]: r for r in rows_for(db, list(sites.values()))}
    return {
        "stages": PIPELINE_STAGES,
        "items": [{"id": i.id, "site_id": i.site_id, "stage": i.stage, "notes": i.notes, "checklist": i.checklist, "updated_at": i.updated_at.isoformat(), "site": rows.get(i.site_id)} for i in items],
    }


class PipelineIn(BaseModel):
    site_id: int


@router.post("/pipeline")
def add_pipeline(body: PipelineIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    if db.get(Site, body.site_id) is None:
        raise HTTPException(404)
    existing = db.scalar(select(PipelineItem).where(PipelineItem.site_id == body.site_id))
    if existing:
        return {"id": existing.id}
    a = db.scalar(select(Assessment).where(Assessment.site_id == body.site_id, Assessment.is_current.is_(True)))
    checklist = {k: {"label": lbl, "done": False, "note": ""} for k, lbl in STANDARD_CHECKLIST}
    if a:
        for i, act in enumerate((a.planning or {}).get("actions", [])[:6]):
            checklist[f"planning_action_{i}"] = {"label": act, "done": False, "note": "", "from": "planning screen"}
        for it in (a.site_risk or {}).get("items", []):
            if it.get("action") and it.get("severity") in ("high", "medium"):
                checklist[f"risk_{it['code']}"] = {"label": it["action"], "done": False, "note": "", "from": f"site risk: {it['title']}"}
        offer = (a.residual or {}).get("max_recommended_offer")
        if offer is not None:
            checklist["offer"]["label"] += f" (current max recommended offer £{offer:,})"
    item = PipelineItem(site_id=body.site_id, stage="shortlist", checklist=checklist)
    db.add(item)
    audit(db, user, "pipeline.add", "site", body.site_id)
    db.commit()
    return {"id": item.id}


class PipelinePatch(BaseModel):
    stage: Literal[tuple(PIPELINE_STAGES)] | None = None  # type: ignore[valid-type]
    notes: str | None = Field(None, max_length=20_000)
    checklist: dict[str, Any] | None = None


@router.patch("/pipeline/{item_id}")
def patch_pipeline(item_id: int, body: PipelinePatch, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    it = db.get(PipelineItem, item_id)
    if it is None:
        raise HTTPException(404)
    if body.stage:
        audit(db, user, "pipeline.stage", "site", it.site_id, frm=it.stage, to=body.stage)
        it.stage = body.stage
    if body.notes is not None:
        it.notes = body.notes
    if body.checklist is not None:
        it.checklist = body.checklist
    db.commit()
    return {"ok": True}


@router.delete("/pipeline/{item_id}")
def delete_pipeline(item_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    it = db.get(PipelineItem, item_id)
    if it is None:
        raise HTTPException(404)
    db.delete(it)
    audit(db, user, "pipeline.remove", "site", it.site_id)
    db.commit()
    return {"ok": True}

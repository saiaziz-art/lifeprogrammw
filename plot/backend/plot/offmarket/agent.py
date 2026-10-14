"""Daily off-market acquisition agent: discover → screen → score → brief.

The agent may research, qualify and (in plot.outreach) draft. It never contacts anyone:
nothing in this module or the daily run sends a message, and drafts wait for your
individual approval.

Discovery runs as an ordinary discovery source (`offmarket.discovery`) inside the 07:00
workflow, so its outcome is recorded in run history like any other source — including
"failed: network blocked" when the public layers cannot be reached.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable
from typing import Any

import shapely
from geoalchemy2.shape import to_shape
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from plot import geo
from plot.models import Assessment, Contact, Evidence, InboundMessage, Lead, LeadEvent, Outreach, Site, TitleDocument, utcnow
from plot.offmarket.analyse import Analyser, AreaData, Candidate
from plot.offmarket.geodata import OSM_ATTRIBUTION, OVERPASS_DEFAULT, area_data
from plot.offmarket.score import TYPE_LABEL, acquisition_score, contact_method, next_action, owner_benefits
from plot.preferences import ProspectingArea, load_preferences
from plot.sources.base import CandidateRecord, DiscoverySource, RunContext, SourceError, SourceMeta
from plot.sources.http import classify

SOURCE_ID = "offmarket.discovery"
EVIDENCE_KIND = f"source-record:{SOURCE_ID}"
CATEGORY = {"garden_subdivision": "plot", "edge_paddock": "land", "outbuilding": "building", "replacement": "renovation", "derelict": "building", "planning_history": "land"}
HEADLINE = {
    "garden_subdivision": "Possible garden plot",
    "edge_paddock": "Paddock at the settlement edge",
    "outbuilding": "Outbuilding / yard",
    "replacement": "Small dwelling on a generous plot",
    "derelict": "Derelict or disused building",
    "planning_history": "Site with past residential applications",
}
CAVEATS = [
    "Indicative only: the new-plot outline is a geometric test, not a design or a planning judgement.",
    "Title extents are HM Land Registry INSPIRE index polygons — indicative, not legal boundaries.",
    "Ownership is NOT known from this analysis. A house inside the same polygon does not prove the garden belongs to it; buy the official copy of the register.",
    "Building identification is inferred from footprint data and may be wrong.",
]
DOWNGRADE_MARK = "Downgraded:"
ACTIVE_DRAFT = ("draft", "needs_changes", "needs_compliance_review", "awaiting_approval", "approved")
STAGE_ORDER = {s: i for i, s in enumerate(["discovered", "feasibility_screened", "owner_verified", "draft_prepared", "awaiting_approval", "contacted", "awaiting_response", "interested", "feasibility_discussion", "negotiation", "under_offer", "completed", "closed"])}


# --------------------------------------------------------------------------- discovery source


def _wkt(g) -> str | None:
    return None if g is None else shapely.to_wkt(geo.from_bng(g), rounding_precision=7)


def analysis_dict(c: Candidate, area: ProspectingArea, sources: dict[str, str]) -> dict[str, Any]:
    return {
        "type": c.type,
        "title_id": c.title_id,
        "title_id_kind": "HM Land Registry INSPIRE ID",
        "area": area.name,
        "metrics": c.metrics,
        "access": c.access,
        "constraints": c.constraints,
        "flags": c.flags,
        "signals": c.signals,
        "sources": sources,
        "geometry": {"title": _wkt(c.title_geom), "plot": _wkt(c.plot_geom), "house": _wkt(c.house_geom), "access": _wkt(c.access_geom)},
        "caveats": CAVEATS,
        "analysed_at": utcnow().isoformat(),
    }


def prelim_rank(c: Candidate) -> float:
    """Rank before any site assessment: acquisition score with a neutral planning prior."""
    s = acquisition_score(c.type, {"metrics": c.metrics, "access": c.access, "constraints": c.constraints, "flags": c.flags, "signals": c.signals, "sources": {"buildings": "y"}}, None, "title_purchase_needed", None, None)
    penalty = sum(8 for f in c.flags if f.get("severity") == "major") + sum(3 for f in c.flags if f.get("severity") == "moderate")
    return s["total"] - penalty - 2 * len(c.constraints)


def candidate_record(c: Candidate, area: ProspectingArea, sources: dict[str, str]) -> CandidateRecord:
    plot84 = geo.from_bng(c.plot_geom)
    pt = plot84.representative_point()
    road = c.access.get("road_name")
    headline = f"{HEADLINE.get(c.type, c.type)}{' off ' + road if road else ''} ({area.name})"
    m = c.metrics
    desc = f"{TYPE_LABEL.get(c.type, c.type)}. Indicative plot ~{m.get('plot_area_m2')} m² within a ~{m.get('parcel_area_m2')} m² registered title extent; access: {c.access.get('type', 'unknown').replace('_', ' ')}."
    return CandidateRecord(
        source_id=SOURCE_ID,
        external_ref=f"{c.title_id}:{c.type}",
        record_type="off_market_signal",
        lon=round(pt.x, 7),
        lat=round(pt.y, 7),
        title=headline,
        category=CATEGORY.get(c.type, "land"),
        boundary_wkt=plot84.wkt,
        area_m2=float(round(c.plot_geom.area)),
        description=desc,
        publisher="PLOT off-market analysis",
        licence="Derived from OGL v3.0 data (HM Land Registry INSPIRE, planning.data.gov.uk, OS) and/or ODbL (OpenStreetMap)",
        attribution=f"Contains HM Land Registry data © Crown copyright and database right; contains OS data © Crown copyright and database right; {OSM_ATTRIBUTION} where OSM was used",
        confidence="low",
        signals=list(c.signals),
        raw={"opportunity_type": c.type, "boundary_source": "indicative_plot", "location_quality": "exact", "analysis": analysis_dict(c, area, sources)},
    )


def select_candidates(cands: list[Candidate], limit: int) -> list[Candidate]:
    """Highly selective: drop anything with a fatal flag, then keep the best few."""
    usable = [c for c in cands if not any(f.get("severity") == "fatal" for f in c.flags)]
    return sorted(usable, key=prelim_rank, reverse=True)[:limit]


class OffMarketDiscovery(DiscoverySource):
    meta = SourceMeta(
        id=SOURCE_ID,
        name="Off-market discovery (garden plots, paddocks, outbuildings, replacement, derelict, planning history)",
        category="off_market",
        kind="live_api",
        licence="Derived from OGL v3.0 and ODbL open data",
        attribution="HM Land Registry INSPIRE polygons, planning.data.gov.uk, OS OpenMap Local or © OpenStreetMap contributors",
        docs_url="https://www.planning.data.gov.uk/dataset/title-boundary",
        notes=(
            "Analyses each prospecting area (Preferences → Off-market) for overlooked plots using title extents, building "
            "footprints, roads and constraints. Discovery never contacts anyone; ownership always needs an official copy."
        ),
        default_config={"overpass_url": OVERPASS_DEFAULT, "max_candidates_per_area": 8},
    )
    area_loader: Callable[..., AreaData] = staticmethod(area_data)

    def __init__(self) -> None:
        self.degraded_note: str | None = None
        self.area_stats: list[dict[str, Any]] = []

    def discover(self, ctx: RunContext) -> Iterable[CandidateRecord]:
        prefs, _ = load_preferences(ctx.db)
        areas = [a for a in prefs.prospecting_areas if a.enabled]
        limit = int(ctx.config.get("max_candidates_per_area", 8))
        errors: list[tuple[str, SourceError]] = []
        for area in areas:
            try:
                data = self.area_loader(ctx.db, area.lon, area.lat, area.radius_m, overpass_url=ctx.config.get("overpass_url", OVERPASS_DEFAULT))
            except Exception as exc:  # noqa: BLE001 — classified and reported, other areas continue
                err = exc if isinstance(exc, SourceError) else classify(exc)
                errors.append((area.name, err))
                self.area_stats.append({"area": area.name, "status": "failed", "error": str(err.args[0])})
                continue
            cands = Analyser(data, area.types).run()
            chosen = select_candidates(cands, limit)
            self.area_stats.append({"area": area.name, "status": "ok", "titles": len(data.titles), "buildings": len(data.buildings), "candidates": len(cands), "kept": len(chosen)})
            for c in chosen:
                yield candidate_record(c, area, data.sources)
        if errors and len(errors) == len(areas):
            raise errors[0][1]
        if errors:
            self.degraded_note = "; ".join(f"{n}: {e.args[0]}" for n, e in errors)


# --------------------------------------------------------------------------- leads


def sync_leads(db: Session, run_id: int | None = None) -> int:
    """Copy discovery analysis onto leads (keeping ownership research already done)."""
    n = 0
    rows = db.execute(select(Evidence, Lead).join(Lead, Lead.site_id == Evidence.site_id).where(Evidence.kind == EVIDENCE_KIND)).all()
    for ev, lead in rows:
        an = (ev.data or {}).get("analysis")
        if not an:
            continue
        cur = lead.analysis or {}
        if cur.get("type") not in (None, an["type"]):
            continue  # a register site or another analysis already describes this lead
        keep = {k: cur[k] for k in ("inspire_ids",) if k in cur}
        if cur.get("analysed_at") != an.get("analysed_at"):
            lead.analysis = an | keep
            n += 1
        if lead.opportunity_type in ("user_identified", "register_site") and cur.get("type") is None and lead.stage == "discovered":
            lead.opportunity_type = an["type"]
        if lead.discovered_run_id is None:
            lead.discovered_run_id = run_id
        if lead.ownership_status in ("unknown", "inspire_matched"):
            lead.ownership_status = "title_purchase_needed"
            db.add(LeadEvent(lead_id=lead.id, from_stage=lead.stage, to_stage=lead.stage, actor="system", note=f"Registered title extent {an['title_id']} (INSPIRE). Owner identity requires an official copy of the register from HM Land Registry."))
    db.flush()
    return n


def _assessment_dict(a: Assessment | None) -> dict[str, Any] | None:
    if a is None:
        return None
    return {"planning": a.planning, "residual": a.residual, "design": a.design, "score": a.score}


def verified_title(db: Session, lead: Lead) -> TitleDocument | None:
    return db.scalar(select(TitleDocument).where(TitleDocument.lead_id == lead.id, TitleDocument.status == "verified").order_by(TitleDocument.verified_at.desc()).limit(1))


def primary_contact(db: Session, lead: Lead) -> Contact | None:
    return db.scalar(select(Contact).where(Contact.lead_id == lead.id, Contact.verified.is_(True)).order_by(Contact.id).limit(1))


def latest_draft(db: Session, lead: Lead) -> Outreach | None:
    return db.scalar(select(Outreach).where(Outreach.lead_id == lead.id, Outreach.status.in_(ACTIVE_DRAFT)).order_by(Outreach.created_at.desc()).limit(1))


def followup_due(db: Session, lead: Lead, after_days: int, max_follow_ups: int) -> dt.date | None:
    """Date an optional follow-up becomes due: one only, never after a reply or opt-out."""
    if max_follow_ups < 1 or lead.stage not in ("contacted", "awaiting_response") or lead.decline_reason:
        return None
    if db.scalar(select(func.count()).select_from(InboundMessage).where(InboundMessage.lead_id == lead.id)):
        return None
    sent = db.scalars(select(Outreach).where(Outreach.lead_id == lead.id, Outreach.status.in_(("sent", "posted")))).all()
    if not sent or any(o.kind == "follow_up" for o in sent) or latest_draft(db, lead) is not None:
        return None
    first = min(o.sent_at for o in sent if o.sent_at)
    return (first + dt.timedelta(days=after_days)).date()


def set_stage(db: Session, lead: Lead, stage: str, actor: str, note: str | None = None) -> None:
    if lead.stage == stage:
        return
    db.add(LeadEvent(lead_id=lead.id, from_stage=lead.stage, to_stage=stage, actor=actor, note=note))
    lead.stage = stage
    lead.stage_changed_at = utcnow()


def rescore_lead(db: Session, lead: Lead, prefs=None) -> None:
    prefs = prefs or load_preferences(db)[0]
    a = db.scalar(select(Assessment).where(Assessment.site_id == lead.site_id, Assessment.is_current.is_(True)))
    title = verified_title(db, lead)
    contact = primary_contact(db, lead)
    s = acquisition_score(lead.opportunity_type, lead.analysis or {}, _assessment_dict(a), lead.ownership_status, (title.extracted if title else None), contact.kind if contact else None)
    prev_total, prev_fatal = lead.acq_score_total, lead.acq_fatal
    if prev_total is not None and (s["fatal"] and not prev_fatal or prev_total - s["total"] >= 10):
        why = next((f["title"] for f in s["flags"] if f.get("severity") == "fatal"), None) or f"score {prev_total:.0f} → {s['total']:.0f}"
        db.add(LeadEvent(lead_id=lead.id, from_stage=lead.stage, to_stage=lead.stage, actor="system", note=f"{DOWNGRADE_MARK} {why}"))
    lead.acq_score = s
    lead.acq_score_total = s["total"]
    lead.acq_confidence = s["confidence"]["score"]
    lead.acq_fatal = s["fatal"]
    lead.benefits = owner_benefits(lead.opportunity_type, lead.analysis or {})
    lead.contact_method = contact_method(contact.kind if contact else None, contact.email_basis if contact else None)
    if a is not None and lead.stage == "discovered":
        set_stage(db, lead, "feasibility_screened", "system", f"Feasibility screened: {a.planning.get('classification_label', 'planning screened')}; acquisition score {s['total']:.0f}/100 ({s['confidence']['label']} confidence)")
    draft = latest_draft(db, lead)
    inbound = db.scalar(select(InboundMessage).where(InboundMessage.lead_id == lead.id, InboundMessage.handled.is_(False)).order_by(InboundMessage.received_at.desc()).limit(1))
    due = followup_due(db, lead, prefs.outreach.follow_up_after_days, prefs.outreach.max_follow_ups)
    lead.next_action = next_action({"stage": lead.stage, "ownership_status": lead.ownership_status, "analysis": lead.analysis or {}, "acq_fatal": lead.acq_fatal, "closed_reason": lead.closed_reason}, draft.status if draft else None, inbound.category if inbound else None, due.isoformat() if due else None)


def rescore_all(db: Session) -> int:
    prefs, _ = load_preferences(db)
    leads = db.scalars(select(Lead).where(Lead.stage != "closed")).all()
    for lead in leads:
        rescore_lead(db, lead, prefs)
    db.flush()
    return len(leads)


# --------------------------------------------------------------------------- daily briefing


def briefing(db: Session, since: dt.datetime) -> dict[str, Any]:
    """Real counts only — never simulated engagement."""
    new_leads = db.execute(
        select(Lead, Site).join(Site, Site.id == Lead.site_id).where(Lead.created_at >= since, Site.excluded.is_(False)).order_by(Lead.acq_score_total.desc().nullslast())
    ).all()
    top = []
    for lead, site in new_leads[:5]:
        conf = (lead.acq_score or {}).get("confidence", {})
        top.append({"lead_id": lead.id, "headline": site.headline, "type": TYPE_LABEL.get(lead.opportunity_type, lead.opportunity_type), "score": lead.acq_score_total, "confidence": conf.get("label"), "cap_reason": conf.get("cap_reason"), "next_action": lead.next_action})

    def count(q) -> int:
        return int(db.scalar(q) or 0)

    week = utcnow() - dt.timedelta(days=7)
    out = {
        "new_prospects": len(new_leads),
        "top_new": top,
        "awaiting_official_copy": count(select(func.count()).select_from(Lead).where(Lead.ownership_status == "title_purchase_needed", Lead.stage.in_(("discovered", "feasibility_screened")), Lead.acq_fatal.is_(False))),
        "drafts_awaiting_approval": count(select(func.count()).select_from(Outreach).where(Outreach.status == "awaiting_approval")),
        "drafts_needing_review": count(select(func.count()).select_from(Outreach).where(Outreach.status.in_(("needs_changes", "needs_compliance_review")))),
        "replies_to_handle": count(select(func.count()).select_from(InboundMessage).where(InboundMessage.handled.is_(False))),
        "sent_last_7_days": count(select(func.count()).select_from(Outreach).where(Outreach.status == "sent", Outreach.sent_at >= week)),
        "posted_last_7_days": count(select(func.count()).select_from(Outreach).where(Outreach.status == "posted", Outreach.sent_at >= week)),
        "followups_due": [],
        "positive_replies": count(select(func.count()).select_from(InboundMessage).where(InboundMessage.received_at >= since, InboundMessage.category.in_(("interested", "possibly_interested", "wants_information", "requests_price")))),
        "downgraded": [e.note.removeprefix(DOWNGRADE_MARK).strip() for e in db.scalars(select(LeadEvent).where(LeadEvent.at >= since, LeadEvent.note.startswith(DOWNGRADE_MARK))).all()],
        "promising_new": sum(1 for lead, _ in new_leads if not lead.acq_fatal and (lead.acq_score or {}).get("confidence", {}).get("label") in ("medium", "high")),
    }
    prefs, _ = load_preferences(db)
    today = utcnow().date()
    for lead in db.scalars(select(Lead).where(Lead.stage.in_(("contacted", "awaiting_response")))).all():
        due = followup_due(db, lead, prefs.outreach.follow_up_after_days, prefs.outreach.max_follow_ups)
        if due and due <= today:
            out["followups_due"].append(lead.id)
    out["headline"] = briefing_headline(out)
    return out


def _n(k: int, one: str, many: str) -> str:
    words = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"]
    return f"{words[k] if k < len(words) else k} {one if k == 1 else many}"


def briefing_headline(b: dict[str, Any]) -> str:
    parts = []
    if b["new_prospects"]:
        parts.append(_n(b["promising_new"], "promising off-market site", "promising off-market sites") + f" found ({b['new_prospects']} analysed).")
    if b["drafts_awaiting_approval"]:
        parts.append(_n(b["drafts_awaiting_approval"], "owner letter", "owner letters") + " ready for review.")
    if b["positive_replies"]:
        parts.append(_n(b["positive_replies"], "owner has", "owners have") + " replied with interest or questions.")
    elif b["replies_to_handle"]:
        parts.append(_n(b["replies_to_handle"], "reply needs", "replies need") + " your attention.")
    if b["downgraded"]:
        parts.append(_n(len(b["downgraded"]), "opportunity has", "opportunities have") + " been downgraded.")
    if b["followups_due"]:
        parts.append(_n(len(b["followups_due"]), "optional follow-up is", "optional follow-ups are") + " due.")
    return " ".join(parts) or "No movement in the off-market pipeline today."


def briefing_has_news(b: dict[str, Any]) -> bool:
    return bool(b.get("new_prospects") or b.get("drafts_awaiting_approval") or b.get("replies_to_handle") or b.get("followups_due") or b.get("downgraded"))


def briefing_text(b: dict[str, Any]) -> list[str]:
    lines = ["", "OFF-MARKET ACQUISITION", b.get("headline", ""), f"New prospects analysed today: {b['new_prospects']}"]
    for i, t in enumerate(b["top_new"], 1):
        cap = f" — confidence capped: {t['cap_reason']}" if t.get("cap_reason") else ""
        lines.append(f"  {i}. {t['headline']} — {t['type']}, {t['score'] or 0:.0f}/100, {t['confidence']} confidence{cap}")
    lines += [
        f"Official copies to buy before any contact: {b['awaiting_official_copy']}",
        f"Drafts waiting for your approval: {b['drafts_awaiting_approval']} (needing changes/compliance review: {b['drafts_needing_review']})",
        f"Owner replies to handle: {b['replies_to_handle']}",
        *[f"Downgraded: {d}" for d in b.get("downgraded", [])],
        f"Optional follow-ups due (each needs your approval): {len(b['followups_due'])}",
        f"Actually sent in the last 7 days: {b['sent_last_7_days']} email(s), {b['posted_last_7_days']} letter(s) marked posted",
        "Nothing is ever sent automatically.",
    ]
    return lines


def lead_geometry(db: Session, lead: Lead) -> dict[str, Any]:
    site = db.get(Site, lead.site_id)
    g = (lead.analysis or {}).get("geometry") or {}
    out = {k: (geo.geojson(geo.parse_wkt(v)) if v else None) for k, v in g.items()}
    if not out.get("plot") and site is not None and site.boundary is not None:
        out["plot"] = geo.geojson(to_shape(site.boundary))
    if site is not None:
        p = to_shape(site.point)
        out["point"] = [p.x, p.y]
    return out

"""Off-Market Acquisition Score (/100) with a separate evidence-confidence rating.

  Residential planning potential          30
  Physical subdivision / redevelopment    25
  Total development economics             20
  Ownership & acquisition practicality    15
  Suitability for your future home        10

A parcel that looks outstanding but has uncertain access or no credible planning route is
capped at medium/low confidence and carries flags explaining why.
"""

from __future__ import annotations

from typing import Any

PLANNING_BASE = {"permission_verified": 28, "approval_exists_review": 24, "plausible_route": 18, "speculative_high_risk": 9, "significant_obstacle": 2}
ACCESS_POINTS = {"independent_frontage": 8, "classified_road_frontage": 5, "private_lane": 3, "backland_strip": 3, "unknown": 2, "none_identified": 0}

TYPE_LABEL = {
    "garden_subdivision": "Garden plot (possible second dwelling)",
    "edge_paddock": "Paddock / yard at the settlement edge",
    "outbuilding": "Outbuilding or yard (conversion / replacement)",
    "replacement": "Small dwelling on a generous plot (replacement)",
    "derelict": "Derelict or disused building",
    "planning_history": "Site with past residential applications",
    "register_site": "Brownfield register site",
    "user_identified": "Site you identified",
}


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def acquisition_score(lead_type: str, analysis: dict[str, Any], assessment: dict[str, Any] | None, ownership_status: str, title: dict[str, Any] | None, contact_kind: str | None) -> dict[str, Any]:
    m = analysis.get("metrics", {})
    acc = analysis.get("access", {})
    flags = list(analysis.get("flags", []))
    cons = {c["dataset"] for c in analysis.get("constraints", [])}
    notes: dict[str, list[str]] = {k: [] for k in ("planning", "physical", "economics", "ownership", "home")}

    # -- planning (30)
    pl = (assessment or {}).get("planning") or {}
    cls = pl.get("classification")
    if cls:
        p = PLANNING_BASE.get(cls, 9)
        notes["planning"].append(f"Screening class: {pl.get('classification_label')}")
    else:
        p = 9
        notes["planning"].append("Planning constraints not yet checked — neutral-low prior")
    if lead_type == "garden_subdivision":
        if acc.get("type") == "independent_frontage" and m.get("in_built_up_area"):
            p += 4
            notes["planning"].append("Infill within a built-up area with its own frontage")
        if acc.get("type") == "backland_strip":
            p -= 4
            notes["planning"].append("Backland/tandem plots are often resisted")
    if lead_type == "edge_paddock" and not m.get("in_built_up_area"):
        p -= 2
        notes["planning"].append("Outside the built-up area: countryside policies likely")
    if lead_type == "planning_history":
        sig = set(analysis.get("signals", []))
        if "residential_application_refused" in sig:
            p -= 3
            notes["planning"].append("A residential application here was refused — reasons need reading")
        if sig & {"residential_application_withdrawn", "residential_permission_possibly_lapsed"}:
            p += 1
            notes["planning"].append("Earlier residential interest (withdrawn/possibly lapsed)")
    for ds, pen, why in (("conservation-area", 2, "Conservation area"), ("tree-preservation-zone", 1, "Tree Preservation Order"), ("tree", 1, "Protected tree"), ("green-belt", 4, "Green Belt"), ("national-park", 4, "National Park"), ("area-of-outstanding-natural-beauty", 1, "National Landscape")):
        if ds in cons:
            p -= pen
            notes["planning"].append(f"{why} affects the plot")
    planning = clamp(p, 0, 30)

    # -- physical (25)
    ph = ACCESS_POINTS.get(acc.get("type", "unknown"), 2)
    notes["physical"].append(f"Access: {acc.get('type', 'unknown').replace('_', ' ')}" + (f", {acc.get('frontage_m')} m frontage" if acc.get("frontage_m") else ""))
    area = m.get("plot_area_m2") or (assessment or {}).get("design", {}).get("fit", {}).get("site_area_m2") or 0
    ph += 6 if area >= 300 else 4.5 if area >= 250 else 3 if area >= 200 else 1
    if lead_type == "garden_subdivision":
        r = m.get("retained_garden_ratio") or 0
        ph += 4 if r >= 0.5 else 2.5 if r >= 0.35 else 0
        sep = m.get("separation_to_house_m") or 0
        ph += 4 if sep >= 21 else 3 if sep >= 14 else 1.5 if sep >= 10 else 0
        ch = m.get("plot_vs_neighbours")
        ph += 1.5 if ch is None else 3 if ch >= 0.8 else 1.5 if ch >= 0.6 else 0
        notes["physical"].append(f"Owner keeps ~{m.get('retained_garden_m2')} m² garden; ~{sep} m to the existing house")
    else:
        ph += 7
    fit = (assessment or {}).get("design", {}).get("fit", {})
    if fit.get("fits") is False:
        ph -= 6
        notes["physical"].append("House concept does not fit the plot")
    if "flood-risk-zone" in cons:
        ph -= 3
        notes["physical"].append("Flood zone affects the plot")
    physical = clamp(ph, 0, 25)

    # -- economics (20)
    res = (assessment or {}).get("residual") or {}
    rlv = res.get("residual_land_value")
    if rlv is None:
        econ = 6.0
        notes["economics"].append("Not yet costed")
    else:
        econ = clamp(rlv / 120_000 * 20, 0, 20)
        notes["economics"].append(f"Maximum viable land price £{max(0, rlv):,.0f}")
        mv = res.get("market_value") or {}
        if mv.get("available") and mv.get("comparables_median"):
            notes["economics"].append(f"Local sold-house median £{mv['comparables_median']:,} (indicative)")
    economics = econ

    # -- ownership (15)
    ow = {"verified": 6, "inferred_ccod": 4, "title_purchase_needed": 3, "inspire_matched": 3}.get(ownership_status, 1)
    notes["ownership"].append({"verified": "Owner verified from the title register", "inferred_ccod": "Company owner suggested by HMLR CCOD (confirm)", "title_purchase_needed": "Registered title found — owner needs an official copy (~£7)", "inspire_matched": "Registered title found"}.get(ownership_status, "Ownership not yet researched"))
    retains = lead_type in ("garden_subdivision",)
    ow += 3 if retains else 2 if lead_type in ("edge_paddock", "outbuilding", "derelict", "planning_history") else 0
    if retains:
        notes["ownership"].append("Owner could keep their home")
    ow += 2  # single registered title (INSPIRE-derived parcels)
    t = title or {}
    if t.get("charges"):
        ow -= 1
        notes["ownership"].append("Registered charge: lender consent needed to release part")
    if t.get("covenants"):
        ow -= 2
        notes["ownership"].append("Covenants on the title need review")
    if t.get("easements"):
        ow -= 1
        notes["ownership"].append("Rights/easements affect the title")
    if t.get("personal_representatives"):
        ow -= 1
        flags.append({"code": "personal_representatives", "severity": "review", "title": "Title held by personal representatives — sensitive; manual review before any contact"})
    if contact_kind == "company":
        ow += 1
    if contact_kind in ("individual", "company", "public_body", "charity"):
        ow += 1
    ownership = clamp(ow, 0, 15)

    # -- home suitability (10)
    loc = ((assessment or {}).get("score") or {}).get("components", {}).get("location", {})
    home = (loc.get("score", 7.5) / max(1, loc.get("max", 15))) * 7
    home += 3 if fit.get("fits") is True else 2 if fit.get("fits") is None else 0
    home = clamp(home, 0, 10)

    # -- fatal/blocking flags from the site assessment
    for f in ((assessment or {}).get("score") or {}).get("fatal_flags", []):
        flags.append({"code": f["code"], "severity": "fatal", "title": f["title"]})
    fatal = any(f.get("severity") == "fatal" for f in flags)
    total = round(planning + physical + economics + ownership + home, 1)

    # -- confidence (separate from attractiveness)
    conf = 0.1
    conf += 0.25 if pl.get("checks", {}).get("national_designations") == "checked" else 0
    conf += 0.15 if analysis.get("sources", {}).get("buildings") else 0
    conf += 0.25 if ownership_status == "verified" else 0.1 if ownership_status in ("title_purchase_needed", "inferred_ccod", "inspire_matched") else 0
    conf += 0.1 if (res.get("market_value") or {}).get("available") else 0
    conf += 0.05 if lead_type == "garden_subdivision" and m.get("neighbour_median_plot_m2") else 0
    cap_reason = None
    if acc.get("type", "unknown") in ("backland_strip", "private_lane", "none_identified", "unknown"):
        conf = min(conf, 0.45)
        cap_reason = "access uncertain"
    if cls in (None, "speculative_high_risk", "significant_obstacle"):
        conf = min(conf, 0.45)
        cap_reason = cap_reason or "no credible planning route established"
    conf = round(clamp(conf, 0.05, 0.95), 2)
    label = "high" if conf >= 0.7 else "medium" if conf >= 0.45 else "low"
    return {
        "total": total,
        "components": {
            "planning": {"score": round(planning, 1), "max": 30, "notes": notes["planning"]},
            "physical": {"score": round(physical, 1), "max": 25, "notes": notes["physical"]},
            "economics": {"score": round(economics, 1), "max": 20, "notes": notes["economics"]},
            "ownership": {"score": round(ownership, 1), "max": 15, "notes": notes["ownership"]},
            "home": {"score": round(home, 1), "max": 10, "notes": []},
        },
        "flags": flags,
        "fatal": fatal,
        "confidence": {"score": conf, "label": label, "cap_reason": cap_reason},
        "type_label": TYPE_LABEL.get(lead_type, lead_type),
    }


def owner_benefits(lead_type: str, analysis: dict[str, Any]) -> list[str]:
    """Objectively plausible benefits, framed as possibilities — never assumptions about the owner."""
    m = analysis.get("metrics", {})
    common = [
        "The owner could explore an offer with no obligation to proceed.",
        "A sale subject to planning permission (or an option) would mean nothing completes unless consent is granted.",
        "A direct private sale avoids the effort of marketing an unconventional parcel.",
    ]
    if lead_type == "garden_subdivision":
        return [
            "This could potentially allow the owner to keep their home while releasing value from part of the garden.",
            f"They would retain around {m.get('retained_garden_m2', '?')} m² of garden" + (" — the plot is much larger than its neighbours" if (m.get("parcel_vs_neighbours") or 0) >= 1.6 else "") + ".",
            "A smaller garden would mean less ground to look after, if that were ever a consideration for them.",
        ] + common[:2]
    if lead_type in ("edge_paddock", "outbuilding", "derelict"):
        return [
            "Land or a building with no apparent current use could be sold on its own, while the owner keeps the rest of their holding.",
            "Selling part rather than all of a holding is possible if they prefer.",
        ] + common
    if lead_type == "replacement":
        return ["An off-market sale of the whole property, without a public marketing campaign or chain-dependent timing."] + common[:2]
    if lead_type == "planning_history":
        return ["Earlier planning interest suggests the owner may already have considered development; a buyer could take on the planning work."] + common
    return common


def contact_method(contact_kind: str | None, email_basis: str | None) -> str:
    if contact_kind in (None, "unknown"):
        return "none_until_owner_verified"
    if contact_kind in ("personal_representatives", "trustees"):
        return "manual_review"
    if contact_kind in ("company", "public_body") and email_basis == "corporate_subscriber":
        return "email_or_post"
    if email_basis in ("owner_initiated", "consent_recorded"):
        return "email_or_post"
    return "post"


def next_action(lead: dict[str, Any], has_draft: str | None, last_inbound: str | None, followup_due: str | None) -> str:
    st, os_ = lead.get("stage"), lead.get("ownership_status")
    if lead.get("acq_fatal"):
        return "Deprioritise: a fatal constraint or access problem was found"
    if last_inbound:
        return f"Reply needed: owner response categorised as '{last_inbound.replace('_', ' ')}'"
    if os_ in ("unknown", "inspire_matched", "title_purchase_needed", "inferred_ccod") and st in ("discovered", "feasibility_screened"):
        ids = lead.get("analysis", {}).get("inspire_ids") or ([lead["analysis"]["title_id"]] if lead.get("analysis", {}).get("title_id") else [])
        return "Buy the official copy of the register for this parcel (HM Land Registry, about £7)" + (f" — INSPIRE ID {ids[0]}" if ids else "") + ", then upload it here"
    if has_draft == "awaiting_approval":
        return "Review, edit or approve the draft letter"
    if has_draft in ("needs_changes", "needs_compliance_review"):
        return "Draft needs attention before it can be approved"
    if st == "owner_verified":
        return "Generate a personalised draft letter"
    if followup_due:
        return f"Optional polite follow-up due {followup_due} (requires your approval)"
    if st in ("contacted", "awaiting_response"):
        return "Awaiting the owner's reply"
    return "Review the prospect"

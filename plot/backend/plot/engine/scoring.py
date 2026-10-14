"""Transparent opportunity score (default /100: planning 30, financial 25, physical 20,
location 15, discount & hidden potential 10), fatal-flag override, and a separate data
confidence measure. Weights come from preferences.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from plot.engine.facts import SiteFacts


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def financial_score(res: dict[str, Any], budget: float, weight: float) -> tuple[float, list[str]]:
    notes = []
    r = res["residual_land_value"]
    asking = res.get("asking_price")
    if r < 0:
        notes.append(f"Even with free land the project exceeds budget by £{-r:,.0f}.")
        return 0.0, notes
    if asking is not None:
        margin = r - asking
        s = 0.5 + 0.5 * clamp(margin / (0.15 * budget), -1, 1)
        notes.append(f"Residual £{r:,.0f} vs asking £{asking:,.0f} ({'+' if margin >= 0 else '−'}£{abs(margin):,.0f}).")
    else:
        s = 0.5 * clamp(r / (0.25 * budget), 0, 1)
        notes.append(f"No asking price: residual land capacity £{r:,.0f} (score capped at half until a price is known).")
    return round(s * weight, 1), notes


def location_score(f: SiteFacts, prefs, weight: float) -> tuple[float, dict[str, Any]]:
    parts: dict[str, Any] = {}
    # rail (0..5)
    rail = f.nearest_rail
    if rail:
        km = rail["distance_m"] / 1000
        rs = 5 if km <= 3 else 4 if km <= 6 else 3 if km <= 10 else 1.5 if km <= 15 else 0
        lr = f.nearest_london_rail
        if prefs.location.london_rail_station_bonus and lr and lr["distance_m"] <= 12_000:
            rs = min(5, rs + 1)
        if km > prefs.location.max_distance_to_rail_station_km:
            rs = min(rs, 1.5)
        parts["rail"] = {"score": rs, "max": 5, "detail": f"{rail['name']} {km:.1f} km" + (f"; London-direct: {lr['name']} {lr['distance_m'] / 1000:.1f} km (default list — verify)" if lr else ""), "status": "confirmed"}
    else:
        parts["rail"] = {"score": 2.5, "max": 5, "detail": "Station data not loaded — neutral prior", "status": "unknown"}
    # towns & privacy (0..4, 0..2)
    in_bua = bool(f.onsite("built-up-area"))
    bua_d = 0.0 if in_bua else f.nearest_distance("built-up-area")
    if bua_d is None:
        parts["town_access"] = {"score": 2.0, "max": 4, "detail": "Settlement proximity not checked — neutral prior", "status": "unknown"}
        parts["privacy"] = {"score": 1.0, "max": 2, "detail": "Unknown", "status": "unknown"}
    else:
        km = bua_d / 1000
        if in_bua:
            ta, pv = 3.0, 0.5
        elif km <= 3:
            ta, pv = 4.0, 1.5
        elif km <= prefs.location.max_distance_to_town_km:
            ta, pv = 2.5, 2.0
        else:
            ta, pv = 1.0, 2.0
        if (f.area_m2 or 0) > 1500 and not in_bua:
            pv = 2.0
        if not prefs.location.prefer_rural:
            ta, pv = (4.0, 1.0) if in_bua else (ta, pv)
        parts["town_access"] = {"score": ta, "max": 4, "detail": "Within built-up area" if in_bua else f"{km:.1f} km to nearest built-up area", "status": "estimated"}
        parts["privacy"] = {"score": pv, "max": 2, "detail": "Rural setting" if not in_bua else "Settlement setting", "status": "estimated"}
    # landscape (0..4)
    ls = 1.0
    det = []
    if f.onsite("national-park") or f.onsite("area-of-outstanding-natural-beauty"):
        ls += 2.0
        det.append("protected landscape")
    if f.onsite("heritage-coast"):
        ls += 0.5
        det.append("heritage coast")
    aw = f.nearest_distance("ancient-woodland")
    if aw is not None and aw <= 500:
        ls += 0.5
        det.append("ancient woodland nearby")
    if not in_bua and bua_d is not None:
        ls += 0.5
        det.append("countryside")
    status = "estimated" if f.constraints_checked() else "unknown"
    if not f.constraints_checked():
        ls = 2.0
        det = ["landscape data not loaded — neutral prior"]
    parts["landscape"] = {"score": min(4.0, ls), "max": 4, "detail": ", ".join(det) or "no designated landscape", "status": status}
    raw = sum(p["score"] for p in parts.values())
    return round(raw / 15 * weight, 1), parts


def discount_score(f: SiteFacts, res: dict[str, Any], weight: float, today: dt.date) -> tuple[float, list[str]]:
    pts = 0.0
    why: list[str] = []
    asking, r = res.get("asking_price"), res["residual_land_value"]
    if asking is not None and r > 0:
        disc = (r - asking) / r
        if disc > 0:
            pts += min(4.0, disc * 10)
            why.append(f"Asking price {disc * 100:.0f}% below residual value")
    reductions = 0
    for l in f.listings:
        for ev in l.events:
            if ev["event"] == "price_change" and (ev["new"].get("asking_price") or 0) < (ev["old"].get("asking_price") or 0):
                reductions += 1
            if ev["event"] == "relisted":
                pts += 0.5
                why.append("Relisted")
        days = (today - dt.date.fromisoformat(l.first_seen_at[:10])).days
        if days > 120 and l.status == "available":
            pts += 1.0
            why.append(f"On market {days} days")
    if reductions:
        pts += min(2.0, reductions)
        why.append(f"{reductions} price reduction(s)")
    sig = set(f.signals)
    if "has_permission" in sig:
        pts += 2.0
        why.append("Register records a residential permission")
    elif "allocated_or_suitable_no_permission" in sig:
        pts += 1.5
        why.append("LPA lists site as suitable for housing (brownfield register)")
    if f.origin == "off_market" or f.origin == "register":
        pts += 1.0
        why.append("Not openly advertised — less competition (availability unproven)")
    return round(min(10.0, pts) * weight / 10, 1), why


def data_confidence(f: SiteFacts, planning_conf: float, risk_conf: float, has_price: bool) -> dict[str, Any]:
    comps = {
        "planning": planning_conf,
        "site_risk": risk_conf,
        "boundary": 0.8 if f.boundary_wkt else (0.4 if f.area_m2 else 0.0),
        "price": 0.8 if has_price else 0.2,
        "freshness": 0.2 if f.stale else 0.9,
    }
    score = round(sum(comps.values()) / len(comps), 2)
    label = "high" if score >= 0.7 else "medium" if score >= 0.45 else "low"
    return {"score": score, "label": label, "components": comps}

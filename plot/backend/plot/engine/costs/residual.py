"""Residual land valuation, acquisition scenarios and sensitivity.

Maximum viable land offer = all-in budget − every other project cost (statutory costs,
finance, contingency, inflation), solved exactly because SDLT and finance depend on the land
price. A negative residual means the project exceeds budget even with free land.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

from plot.engine.costs.model import CostInputs, build_cost_plan
from plot.engine.costs.rates import Rate


def total_at(inp: CostInputs, rates, meta, land: float, which: str = "base") -> float:
    plan = build_cost_plan(inp, rates, meta, land)
    if which == "high_rss":
        return plan["totals"]["high_rss"]
    return plan["totals"][which]


def solve_residual(inp: CostInputs, rates: dict[str, Rate], meta: dict[str, dict], budget: float, which: str = "base") -> float:
    t0 = total_at(inp, rates, meta, 0.0, which)
    if t0 >= budget:
        return budget - t0  # negative: shortfall with free land
    lo, hi = 0.0, budget
    for _ in range(40):
        mid = (lo + hi) / 2
        if total_at(inp, rates, meta, mid, which) > budget:
            hi = mid
        else:
            lo = mid
        if hi - lo < 50:
            break
    return lo


def evaluate(inp: CostInputs, rates, meta, budget: float, asking: int | None, market: dict[str, Any] | None, tolerance_pct: float, light: bool = False) -> dict[str, Any]:
    res_base = solve_residual(inp, rates, meta, budget, "base")
    res_prudent = res_base if light else solve_residual(inp, rates, meta, budget, "high_rss")
    land_for_plan = asking if asking is not None else max(0.0, res_base)
    plan = build_cost_plan(inp, rates, meta, land_for_plan)

    scenarios = {}
    for sc in () if light else ("buy_now", "subject_to_planning", "option"):
        s_inp = replace(inp, scenario=sc)
        r = solve_residual(s_inp, rates, meta, budget, "base")
        sp = build_cost_plan(s_inp, rates, meta, asking if asking is not None else max(0.0, r))
        scenarios[sc] = {
            "residual_land_value": round(r),
            "total_base": sp["totals"]["base"],
            "total_high_rss": sp["totals"]["high_rss"],
            "notes": {
                "buy_now": "Unconditional purchase: you carry planning risk and holding costs while the application runs. Only sensible with an existing permission or very strong route.",
                "subject_to_planning": "Conditional contract: completion only if acceptable permission is granted. No land holding cost during planning; sellers may want a higher price or a deadline.",
                "option": f"Option: pay a non-refundable fee (~{inp.option_fee_pct * 100:.0f}% assumed) for the right to buy at an agreed price within a period; you fund planning at risk. Needs specialist legal advice.",
            }[sc],
        }

    # sensitivity: re-solve with one driver stressed at a time
    sens = []

    def stress(label: str, mod) -> None:
        s_inp = mod(copy.deepcopy(inp))
        r = solve_residual(s_inp, rates, meta, budget, "base")
        sens.append({"factor": label, "residual": round(r), "delta": round(r - res_base)})

    def ground(i: CostInputs) -> CostInputs:
        i.drivers.foundation_case = "engineered"
        i.drivers.foundation_known = True
        return i

    def services(i: CostInputs) -> CostInputs:
        i.drivers.services_remote = True
        i.drivers.drainage = "off_mains"
        i.drivers.infiltration_doubt = True
        return i

    if not light:
        stress("Ground needs piled/raft foundations", ground)
        stress("Services remote + poor infiltration", services)
        stress("Construction inflation +2%/yr", lambda i: replace(i, inflation_pa=i.inflation_pa + 2))
        stress("Planning takes 8 months longer", lambda i: replace(i, planning_months=(i.planning_months[0] + 8, i.planning_months[1] + 8)))
        stress("Build rates +10%", lambda i: replace(i, region_factor=i.region_factor * 1.10))
    sens.sort(key=lambda s: s["delta"])

    out: dict[str, Any] = {
        "budget": budget,
        "residual_land_value": round(res_base),
        "max_recommended_offer": int(max(0, res_base) // 1000 * 1000),
        "prudent_offer": int(max(0, res_prudent) // 1000 * 1000),
        "asking_price": asking,
        "headroom_vs_asking": round(res_base - asking) if asking is not None else None,
        "total_project_cost": plan["totals"],
        "over_under_budget_base": round(plan["totals"]["base"] - budget),
        # only meaningful against a real price; with no price the land figure IS the residual
        "within_budget": (plan["totals"]["base"] <= budget * (1 + tolerance_pct / 100)) if asking is not None else None,
        "non_land_cost": plan["non_land"],
        "cost_per_m2": plan["cost_per_m2_base"],
        "contingency_base": round(sum(l["base"] for l in plan["lines"] if l["key"].startswith("contingency"))),
        "scenarios": scenarios,
        "sensitivity": sens,
        "plan": plan,
        "land_price_used": round(land_for_plan),
        "land_price_basis": "asking price" if asking is not None else "residual (no asking price)",
    }
    out["market_value"] = market_value(market, inp.gia_m2)
    return out


def market_value(market: dict[str, Any] | None, gia: float) -> dict[str, Any]:
    if not market or market.get("n_houses", 0) < 3:
        return {"available": False, "note": "Insufficient comparable sales evidence; no finished value estimated."}
    note = "Median of nearby house sales (HM Land Registry), not adjusted for size, age or quality. A small new home may sell below the local median."
    out = {"available": True, "comparables_median": market.get("house_median"), "p25": market.get("house_p25"), "p75": market.get("house_p75"), "n": market.get("n_houses"), "note": note}
    if market.get("median_floor_area_m2"):
        vpm2 = market["house_median"] / market["median_floor_area_m2"]
        out["indicative_value_per_m2"] = round(vpm2)
        out["indicative_value_for_gia"] = round(vpm2 * gia)
        out["note"] += " £/m² uses median EPC floor area nearby — indicative only."
    return out

"""Quantity-surveyor-style development cost plan (16 categories, low / base / high).

Every line shows quantity × rate, the rate's basis/source/date, and whether you overrode it.
'high' is the sum of adverse outcomes on every line (a stress case); 'high_rss' combines line
variances (root-sum-square) as a more realistic upper estimate. Land price is an input so the
same plan drives residual valuation.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from plot.engine.costs.rates import Rate
from plot.engine.costs.sdlt import sdlt
from plot.engine.site_risk import CostDrivers

CATEGORIES = {
    1: "Land acquisition & transaction taxes",
    2: "Legal fees, searches & surveys",
    3: "Planning, architecture & professional fees",
    4: "Ecology, geotechnical & specialist investigations",
    5: "Demolition & clearance",
    6: "Earthworks, foundations & retaining structures",
    7: "House manufacture / construction",
    8: "Transport, cranage & installation",
    9: "Electricity & utility connections",
    10: "Water supply, wastewater & drainage",
    11: "Roads, parking & access",
    12: "External works & landscaping",
    13: "Planning obligations, ecology mitigation & levies",
    14: "Insurance, financing & holding costs",
    15: "VAT",
    16: "Inflation & contingency",
}
CONSTRUCTION_CATS = (5, 6, 7, 8, 9, 10, 11, 12)


@dataclass
class Line:
    cat: int
    key: str
    label: str
    qty: float
    unit: str
    low: float
    base: float
    high: float
    basis: str = ""
    source: str = ""
    source_date: str = ""
    user_override: bool = False
    note: str = ""


@dataclass
class CostInputs:
    gia_m2: float
    method_key: str
    storeys: float
    dims: dict[str, float]
    modules: int | None
    build_months: dict[str, float]
    spec: str
    bathrooms: int
    include_solar: bool
    include_rainwater: bool
    drivers: CostDrivers
    obligations: list[dict[str, Any]]
    site_area_m2: float | None
    perimeter_m: float | None
    existing_dwelling: bool
    planning_months: tuple[float, float]
    purchase_months: float
    budget: float
    cash_available: float
    use_mortgage: bool
    mortgage_rate: float
    arrangement_fee: float
    inflation_pa: float
    build_contingency_pct: float
    conversion_contingency_pct: float
    region_factor: float
    sdlt_additional_dwelling: bool = False
    sdlt_non_resident: bool = False
    sdlt_first_time_buyer: bool = False
    scenario: str = "buy_now"  # buy_now | subject_to_planning | option
    option_fee_pct: float = 0.02
    overrides: dict[str, float] = field(default_factory=dict)


SPEC = {"essential": 0.88, "good": 1.0, "high": 1.22}


class Plan:
    def __init__(self, rates: dict[str, Rate], meta: dict[str, dict], overrides: dict[str, float]):
        self.rates, self.meta, self.overrides = rates, meta, overrides
        self.lines: list[Line] = []

    def add(self, cat: int, key: str, label: str, qty: float = 1.0, factor: float = 1.0, unit: str | None = None, note: str = "", rate_key: str | None = None) -> Line | None:
        rk = rate_key or key
        r = self.rates[rk]
        lo, ba, hi = (r.low * qty * factor, r.base * qty * factor, r.high * qty * factor)
        ov = False
        if f"cost.{key}" in self.overrides:
            v = float(self.overrides[f"cost.{key}"])
            scale = v / ba if ba else 1.0
            lo, ba, hi = lo * scale if ba else v, v, hi * scale if ba else v
            ov = True
        m = self.meta.get(rk, {})
        line = Line(cat, key, label, round(qty, 2), unit or r.unit, lo, ba, hi, r.basis, m.get("source", ""), m.get("source_date", ""), ov or bool(m.get("user_override")), note)
        self.lines.append(line)
        return line

    def raw(self, cat: int, key: str, label: str, low: float, base: float, high: float, unit: str = "£", note: str = "", basis: str = "computed") -> Line:
        line = Line(cat, key, label, 1, unit, low, base, high, basis, "PLOT computation", "", False, note)
        self.lines.append(line)
        return line

    def total(self, cats=None, which="base") -> float:
        return sum(getattr(l, which) for l in self.lines if cats is None or l.cat in cats)


def build_cost_plan(inp: CostInputs, rates: dict[str, Rate], meta: dict[str, dict], land_price: float) -> dict[str, Any]:
    p = Plan(rates, meta, inp.overrides)
    rf = inp.region_factor
    spec = SPEC.get(inp.spec, 1.0)
    d = inp.drivers
    fp = inp.dims["footprint_m2"]
    gia = inp.gia_m2
    is_conv = inp.method_key == "conversion"
    site_area = inp.site_area_m2 or max(fp * 4, 400.0)
    perimeter = min(inp.perimeter_m or 4 * math.sqrt(site_area), 300.0)

    # 7 — construction
    p.add(7, f"build.{inp.method_key}", f"{inp.method_key.replace('_', ' ').title()} construction", gia, rf * spec)
    p.add(7, "pkg.kitchen", "Kitchen", 1, spec)
    p.add(7, "pkg.bathroom", "Bathrooms / shower rooms", inp.bathrooms, spec)
    p.add(7, "pkg.heating", "Heat pump heating & hot water", 1)
    p.add(7, "pkg.electrical", "Electrical installation", 1, rf)
    if inp.spec == "high":
        p.add(7, "pkg.mvhr", "MVHR ventilation", 1)
    if inp.include_solar:
        p.add(7, "pkg.solar_pv", "Solar PV", 1)
    if inp.include_rainwater:
        p.add(7, "pkg.rainwater", "Rainwater harvesting (non-potable)", 1)
    build_sub = p.total([7])
    prelim_factor = 0.5 if inp.method_key == "modular" else 1.0
    r = rates["prelims.pct"]
    p.raw(7, "prelims", "Preliminaries", r.low * build_sub * prelim_factor, r.base * build_sub * prelim_factor, r.high * build_sub * prelim_factor, "% of build", basis=r.basis)

    # 8 — transport & crane
    if inp.method_key == "modular":
        n = inp.modules or max(1, math.ceil(gia / 45))
        p.add(8, "transport.module", f"Module transport ({n} modules)", n)
        p.add(8, "crane.day", "Crane & installation", max(1, math.ceil(n / 3)), unit="days")
    elif inp.method_key == "sips":
        p.add(8, "crane.day", "Crane for SIPs panels", 1, unit="days")

    # 5 — demolition & clearance
    if d.demolition_m2:
        p.add(5, "demo.m2", "Demolition / strip-out", d.demolition_m2, rf, note="Area estimated — measure on site")
    if d.asbestos_risk:
        p.add(5, "asbestos.removal", "Asbestos removal allowance", 1)
    p.add(5, "clearance.m2", "Site clearance", min(site_area, 3000), rf)

    # 6 — earthworks & foundations
    if inp.method_key == "modular" and d.foundation_case == "standard":
        p.add(6, "found.modular_piles", "Screw piles / pads for modules", fp, rf)
    else:
        key = {"standard": "found.standard", "deep": "found.deep", "engineered": "found.engineered"}[d.foundation_case]
        p.add(6, key, f"Foundations & slab ({d.foundation_case} case)", fp, rf, note="" if d.foundation_known else "Ground not screened — case assumed")
        if d.foundation_case == "engineered":
            p.add(6, "found.mobilisation", "Piling mobilisation", 1)
    spoil = fp * 0.3 + inp.dims["perimeter_m"] * 0.6 * (1.0 if d.foundation_case == "standard" else 1.8)
    p.add(6, "earth.muckaway", "Excavation & spoil disposal", spoil, rf, unit="m³", note="Topsoil strip 300 mm + foundation spoil")
    p.add(6, "retaining.lm", "Retaining walls (high case only until topo survey)", 15)

    # 9 — electricity & utilities
    remote = bool(d.services_remote)
    p.add(9, "elec.remote" if remote else "elec.near", "Electricity connection" + (" (long rural run)" if remote else ""), 1, note="" if d.services_remote is not None else "Network position unknown")
    p.add(9, "telecoms", "Fibre/telecoms connection", 1)
    p.add(9, "temp.supplies", "Temporary supplies during build", 1)

    # 10 — water & drainage
    p.add(10, "water.remote" if remote else "water.near", "Water connection" + (" / mains extension" if remote else ""), 1)
    if d.drainage == "mains":
        p.add(10, "foul.mains", "Foul sewer connection", 1)
    elif d.drainage == "off_mains":
        p.add(10, "foul.ptp_poor_ground" if d.infiltration_doubt else "foul.ptp", "Package treatment plant + drainage field" + (" (poor infiltration)" if d.infiltration_doubt else ""), 1)
    else:
        # unknown: mains as low case, off-mains as high, midpoint as base
        r1, r2 = rates["foul.mains"], rates["foul.ptp"]
        p.raw(10, "foul.unknown", "Foul drainage (route unknown)", r1.low, (r1.base + r2.base) / 2, r2.high, note="Mains vs off-mains not established")
    p.add(10, "surface.suds", "Surface water soakaways/SuDS", 1)

    # 11 — access
    drive = inp.drivers.long_access_m * 3.2 + 30
    p.add(11, "access.driveway_m2", "Driveway & parking", drive, rf, unit="m²")
    if d.new_access:
        p.add(11, "access.new_crossing", "New vehicular access / splays", 1)

    # 12 — externals
    garden = max(0.0, min(site_area - fp - drive, 400.0))
    p.add(12, "ext.landscape_m2", "Landscaping & paths", garden, rf, unit="m²")
    p.add(12, "ext.fencing_lm", "Boundary treatment", perimeter, rf, unit="m")

    construction = p.total(CONSTRUCTION_CATS)
    building_works = p.total([6, 7, 8])

    # 3 — professional fees
    design_pct = rates["fee.design_pct"]
    df = 0.6 if inp.method_key == "modular" else 1.0
    p.raw(3, "fee.design", "Architect / designer", design_pct.low * building_works * df, design_pct.base * building_works * df, design_pct.high * building_works * df, basis=design_pct.basis)
    p.add(3, "fee.structural", "Structural engineer")
    p.add(3, "fee.planning_app", "Planning application fee")
    p.add(3, "fee.planning_consultant", "Planning consultant / pre-app")
    p.add(3, "fee.building_control", "Building control")
    w = rates["fee.warranty_pct"]
    p.raw(3, "fee.warranty", "10-year structural warranty", w.low * building_works, w.base * building_works, w.high * building_works, basis=w.basis)
    p.add(3, "fee.sap_air", "SAP / EPC / air test")

    # 2 — legal & surveys
    p.add(2, "legal.purchase", "Conveyancing")
    p.add(2, "legal.searches", "Searches")
    p.add(2, "valuation.land", "Valuation / land survey")
    p.add(2, "survey.topo", "Topographical survey")
    if inp.scenario == "subject_to_planning":
        p.add(2, "legal.conditional_extra", "Conditional contract legal costs")
    if inp.scenario == "option":
        p.add(2, "legal.option", "Option agreement legal costs")

    # 4 — investigations
    p.add(4, "survey.ecology", "Preliminary ecological appraisal")
    if d.ecology_protected_species:
        p.add(4, "survey.protected_species", "Protected species surveys")
    if d.tree_survey:
        p.add(4, "survey.trees", "Tree survey")
    if d.fra_needed:
        p.add(4, "survey.fra", "Flood risk assessment")
    p.add(4, "survey.gi", "Ground investigation")
    if d.drainage != "mains":
        p.add(4, "survey.percolation", "Percolation test")
    p.add(4, "survey.contam_p1", "Contamination desk study")
    if d.contamination_phase2:
        p.add(4, "survey.contam_p2", "Contamination Phase 2")
    if d.asbestos_risk:
        p.add(4, "survey.asbestos", "Asbestos survey")
    if d.archaeology:
        p.add(4, "survey.archaeology", "Archaeology")
    if is_conv:
        p.add(4, "survey.structural", "Structural survey of existing building")

    # 13 — obligations
    obl = {o["code"]: o for o in inp.obligations}
    if obl.get("cil", {}).get("applies") in ("yes", "likely"):
        p.add(13, "obligations.cil_rate", "CIL (base assumes self-build exemption claimed)", gia, unit="m²")
    if obl.get("habitats", {}).get("applies") in ("yes", "likely"):
        p.add(13, "obligations.habitats", "Habitats recreation mitigation")
    elif obl.get("habitats", {}).get("applies") in ("unknown", "possible"):
        r = rates["obligations.habitats"]
        p.raw(13, "obligations.habitats", "Habitats mitigation (not established)", 0, 0, r.high, note="Zone not confirmed")
    if obl.get("nutrient_neutrality", {}).get("applies") == "yes":
        p.add(13, "obligations.nutrient", "Nutrient neutrality mitigation")
    elif "nutrient_neutrality" not in obl:
        r = rates["obligations.nutrient"]
        p.raw(13, "obligations.nutrient", "Nutrient neutrality (catchment not checked)", 0, 0, r.high)
    if obl.get("bng", {}).get("applies") in ("yes", "unknown", "possible"):
        f = 1.0 if obl["bng"]["applies"] == "yes" else 0.5
        p.add(13, "obligations.bng", "Biodiversity net gain", 1, f)

    # 16 — inflation & contingency (computed on construction-stage costs)
    b_low, b_base, b_high = inp.build_months["low"], inp.build_months["base"], inp.build_months["high"]
    pm_base, pm_high = inp.planning_months
    months_mid = {
        "low": inp.purchase_months + pm_base * 0.75 + b_low / 2,
        "base": inp.purchase_months + pm_base + b_base / 2,
        "high": inp.purchase_months + pm_high + b_high / 2,
    }
    r_infl = {"low": max(0.0, inp.inflation_pa - 1.0), "base": inp.inflation_pa, "high": inp.inflation_pa + 1.5}
    cons = {k: p.total(CONSTRUCTION_CATS, k) for k in ("low", "base", "high")}
    infl = {k: cons[k] * ((1 + r_infl[k] / 100) ** (months_mid[k] / 12) - 1) for k in cons}
    p.raw(16, "inflation", f"Construction inflation to mid-build (~{months_mid['base']:.0f} months at {inp.inflation_pa}%/yr)", infl["low"], infl["base"], infl["high"], basis="Compound inflation on construction-stage costs")
    pct = (inp.conversion_contingency_pct if is_conv else inp.build_contingency_pct) / 100
    p.raw(16, "contingency.build", f"Construction contingency ({pct * 100:.1f}%)", pct * (cons["low"] + infl["low"]), pct * (cons["base"] + infl["base"]), pct * (cons["high"] + infl["high"]), basis="Preference setting")
    if not d.foundation_known:
        r = rates["risk.unknown_ground_pct"]
        g = {k: p.total([6], k) for k in ("low", "base", "high")}
        p.raw(16, "contingency.ground", "Unknown-ground contingency", r.low * g["low"], r.base * g["base"], r.high * g["high"], basis=r.basis)

    # 15 — VAT
    fees = {k: p.total([3], k) + sum(getattr(l, k) for l in p.lines if l.cat in (2, 4) and not l.key.startswith("legal.")) for k in ("low", "base", "high")}
    legal = {k: sum(getattr(l, k) for l in p.lines if l.key.startswith("legal.") or l.key == "valuation.land") for k in ("low", "base", "high")}
    p.raw(15, "vat.fees", "VAT at 20% on professional fees & surveys (not reclaimable)", 0.2 * (fees["low"] + legal["low"]), 0.2 * (fees["base"] + legal["base"]), 0.2 * (fees["high"] + legal["high"]), basis="Professional services are standard-rated")
    if is_conv:
        c7 = {k: p.total([7], k) for k in ("low", "base", "high")}
        p.raw(15, "vat.conversion", "VAT at 5% on qualifying conversion works (reduced rate)", 0.05 * c7["low"], 0.05 * c7["base"], 0.05 * c7["high"], basis="Reduced rate for non-residential → residential conversions; DIY scheme may reclaim some materials VAT")
    else:
        p.add(15, "vat.white_goods_allowance", "VAT on non-zero-rated items (appliances etc.)")
    ext = {k: p.total([12], k) for k in ("low", "base", "high")}
    p.raw(15, "vat.externals", "VAT on landscaping if outside the build contract (50% assumed)", 0, 0.2 * 0.5 * ext["base"], 0.2 * ext["high"], basis="Landscaping often standard-rated; DIY scheme reclaim possible on materials")

    # 1 — land & SDLT
    def land_lines(price: float) -> None:
        p.lines = [l for l in p.lines if l.cat != 1]
        if inp.scenario == "option":
            fee = price * inp.option_fee_pct
            p.raw(1, "option_fee", f"Option fee ({inp.option_fee_pct * 100:.1f}%, typically non-refundable)", fee, fee, fee, basis="Scenario input")
        p.raw(1, "land", "Land purchase price", price, price, price, basis="Asking price or offer")
        tax = sdlt(price, residential=inp.existing_dwelling, additional_dwelling=inp.sdlt_additional_dwelling, non_resident=inp.sdlt_non_resident, first_time_buyer=inp.sdlt_first_time_buyer)
        p.raw(1, "sdlt", f"SDLT ({tax.regime.replace('_', ' ')})", tax.tax, tax.tax, tax.tax, basis="; ".join(tax.notes))

    land_lines(land_price)

    # 14 — insurance, finance, holding (iterate: finance depends on total)
    p.add(14, "insurance.site", "Self-build site insurance", max(1.0, (b_base + 2) / 12))
    holding_months = (pm_base if inp.scenario == "buy_now" else 0) + b_base
    for _ in range(3):
        p.lines = [l for l in p.lines if l.key not in ("finance.interest", "finance.fee", "finance.valuations", "holding.land")]
        totals = {k: p.total(None, k) for k in ("low", "base", "high")}
        out = {}
        for k in ("low", "base", "high"):
            borrow = max(0.0, totals[k] - inp.cash_available)
            months_b = {"low": b_low, "base": b_base, "high": b_high}[k]
            land_borrow = max(0.0, land_price - inp.cash_available)
            plan_m = {"low": pm_base * 0.75, "base": pm_base, "high": pm_high}[k] if inp.scenario == "buy_now" else 0
            interest = inp.mortgage_rate / 100 * (0.5 * borrow * months_b / 12 + land_borrow * plan_m / 12) if inp.use_mortgage else 0.0
            out[k] = (borrow, interest)
        if inp.use_mortgage and out["base"][0] > 0:
            p.raw(14, "finance.interest", "Self-build mortgage interest (stage-release, avg 50% drawn)", out["low"][1], out["base"][1], out["high"][1], basis=f"{inp.mortgage_rate}% on borrowing beyond £{inp.cash_available:,.0f} cash")
            p.raw(14, "finance.fee", "Mortgage arrangement fee", inp.arrangement_fee, inp.arrangement_fee, inp.arrangement_fee)
            p.add(14, "finance.valuation_stage", "Lender stage valuations")
        if inp.scenario == "buy_now":
            p.raw(14, "holding.land", f"Holding land during planning (~{pm_base:.0f} months: insurance, security, maintenance)", 100 * pm_base * 0.75, 150 * pm_base, 250 * pm_high)

    totals = {k: round(p.total(None, k)) for k in ("low", "base", "high")}
    non_land = {k: totals[k] - round(sum(getattr(l, k) for l in p.lines if l.key in ("land", "option_fee"))) for k in totals}
    rss_hi = math.sqrt(sum((l.high - l.base) ** 2 for l in p.lines))
    rss_lo = math.sqrt(sum((l.base - l.low) ** 2 for l in p.lines))
    borrow_base = max(0.0, totals["base"] - inp.cash_available)
    cats = []
    for no, name in CATEGORIES.items():
        ls = [l for l in p.lines if l.cat == no]
        cats.append({"no": no, "name": name, "low": round(sum(l.low for l in ls)), "base": round(sum(l.base for l in ls)), "high": round(sum(l.high for l in ls)), "lines": len(ls)})
    warnings = []
    if borrow_base > 0 and not inp.use_mortgage:
        warnings.append(f"Funding gap of £{borrow_base:,.0f} beyond available cash and no mortgage assumed.")
    if inp.use_mortgage and land_price > 0 and land_price - inp.cash_available > land_price * 0.5:
        warnings.append("Land borrowing would exceed a typical 50% LTV on land.")
    return {
        "scenario": inp.scenario,
        "land_price": round(land_price),
        "lines": [asdict(l) | {"low": round(l.low), "base": round(l.base), "high": round(l.high)} for l in p.lines],
        "categories": cats,
        "totals": totals | {"high_rss": round(totals["base"] + rss_hi), "low_rss": round(totals["base"] - rss_lo)},
        "non_land": non_land,
        "construction_base": round(construction),
        "cost_per_m2_base": round(totals["base"] / gia) if gia else None,
        "programme": {"purchase_months": inp.purchase_months, "planning_months": {"base": pm_base, "high": pm_high}, "build_months": inp.build_months, "holding_months_base": holding_months},
        "borrowing_base": round(borrow_base),
        "warnings": warnings,
        "assumptions": [
            f"GIA {gia:.0f} m², {inp.method_key.replace('_', ' ')}, spec '{inp.spec}', location factor {rf}",
            f"Foundation case: {d.foundation_case}{'' if d.foundation_known else ' (assumed — ground not screened)'}; drainage: {d.drainage}; services remote: {d.services_remote}",
            "Fees and surveys exclude VAT (VAT shown in category 15); new-build construction assumed zero-rated with a VAT-registered builder.",
            "Rates are PLOT screening defaults unless marked as overridden — replace with quotes.",
        ],
    }

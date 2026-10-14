"""Default unit-cost library (date-stamped, editable in Admin → Cost assumptions).

IMPORTANT: these defaults are PLOT planning assumptions for screening, compiled for the
South of England at Q3 2026 price levels. They are NOT market-verified quotes or BCIS data
(BCIS is a paid licence). Each rate carries its basis; replace them with QS/contractor
quotes or licensed indices as soon as you have them — the cost model records which rates
were user-overridden.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from plot.models import CostRate

RATES_DATE = dt.date(2026, 10, 1)
DEFAULT_SOURCE = "PLOT default assumption (screening) — not a quotation; replace with QS/contractor/supplier quotes"


@dataclass(frozen=True)
class Rate:
    key: str
    category: str
    description: str
    unit: str
    low: float
    base: float
    high: float
    basis: str
    regional: bool = True


R = Rate
DEFAULT_RATES: list[Rate] = [
    # --- house construction (shell + internal finishes; excludes packages, foundations, externals)
    R("build.timber_frame", "construction", "Timber-frame house: frame, envelope, windows, internal finishes", "£/m² GIA", 1450, 1700, 2050, "Shell + internal finishes only (kitchen, bathrooms, heating, electrics, prelims priced separately); good-spec one-off builds, southern England."),
    R("build.sips", "construction", "SIPs house: panels, envelope, windows, internal finishes", "£/m² GIA", 1500, 1750, 2100, "As timber frame; panels typically priced higher, faster on site."),
    R("build.modular", "construction", "Volumetric modular: factory-built, delivered finished internally", "£/m² GIA", 1800, 2150, 2700, "Factory-built volumes; excludes transport/crane, foundations, connections and the separately priced packages."),
    R("build.masonry", "construction", "Masonry house: blockwork, render/brick, roof, finishes", "£/m² GIA", 1400, 1650, 2000, "Main-contractor masonry one-off build (shell + finishes)."),
    R("build.conversion", "construction", "Conversion/refurbishment of existing building", "£/m² GIA", 1300, 1900, 2900, "Highly condition-dependent; structural survey essential."),
    R("pkg.kitchen", "construction", "Kitchen (units, worktops, appliances)", "£ each", 5000, 9000, 20000, "Small-home kitchen supply & fit incl. appliances."),
    R("pkg.bathroom", "construction", "Bathroom / shower room fit-out", "£ each", 5000, 9000, 16000, "Sanitaryware, tiling, plumbing."),
    R("pkg.heating", "construction", "Air-source heat pump, cylinder, underfloor/rads", "£ each", 8000, 11000, 16000, "Self-build/custom-build may be eligible for Boiler Upgrade Scheme grant (verify) — not deducted."),
    R("pkg.electrical", "construction", "Electrical installation & consumer unit", "£ each", 4000, 6500, 10000, "Small dwelling."),
    R("pkg.mvhr", "construction", "MVHR ventilation (high spec only)", "£ each", 3000, 5000, 8000, "Applied when specification = high."),
    R("pkg.solar_pv", "construction", "Solar PV ~4 kWp (optional battery excluded)", "£ each", 4500, 6500, 10000, "Applied when solar is in the brief."),
    R("pkg.rainwater", "construction", "Rainwater harvesting for non-potable use (WC/laundry/garden)", "£ each", 2500, 4500, 7500, "Tank, pump, filtration, dual pipework."),
    R("prelims.pct", "construction", "Preliminaries (site set-up, welfare, scaffold, management)", "% of build", 0.04, 0.06, 0.10, "Site-specific preliminaries beyond those already in the £/m² rates (welfare, scaffold, management); halved for modular."),
    R("transport.module", "construction", "Module transport per volume", "£ each", 1500, 2500, 4000, "Per module, regional haul."),
    R("crane.day", "construction", "Mobile crane day incl. operator & lift plan", "£/day", 2500, 4500, 8000, "Modular/SIPs installation."),
    R("vat.white_goods_allowance", "construction", "Standard-rated items within a zero-rated new build (appliances etc.)", "£", 600, 1200, 2500, "VAT on items excluded from zero-rating."),
    # --- groundworks
    R("found.standard", "groundworks", "Strip/trench-fill foundations + insulated ground slab", "£/m² footprint", 180, 250, 340, "Good ground (gravels/limestone)."),
    R("found.deep", "groundworks", "Deep trench-fill / heave protection (clay & trees)", "£/m² footprint", 260, 360, 500, "NHBC 4.2 depths."),
    R("found.engineered", "groundworks", "Piled or raft foundation incl. ground beams", "£/m² footprint", 380, 520, 750, "Soft/made ground, landslide-prone."),
    R("found.mobilisation", "groundworks", "Piling rig mobilisation / specialist", "£", 0, 6000, 12000, "Engineered case only."),
    R("found.modular_piles", "groundworks", "Screw piles / pads for modular units", "£/m² footprint", 120, 180, 260, "Lightweight modular on good ground."),
    R("earth.muckaway", "groundworks", "Excavation & disposal of inert spoil", "£/m³", 35, 55, 90, "Hazardous spoil far higher."),
    R("retaining.lm", "groundworks", "Retaining wall (if slope)", "£/m", 0, 0, 1200, "Only in high case until topo survey."),
    R("demo.m2", "site_prep", "Demolition of existing structure", "£/m² GIA", 40, 70, 120, "Excludes asbestos."),
    R("asbestos.removal", "site_prep", "Asbestos removal allowance", "£", 0, 3000, 15000, "Pre-2000 structures."),
    R("clearance.m2", "site_prep", "Vegetation/site clearance", "£/m² site", 1, 3, 8, "Overgrown plots higher."),
    # --- services & drainage
    R("elec.near", "services", "Electricity connection — network nearby", "£", 1500, 3500, 8000, "DNO budget estimate typical range."),
    R("elec.remote", "services", "Electricity connection — long rural run", "£", 4000, 12000, 45000, "Extensions/transformer upgrades."),
    R("water.near", "services", "Water connection incl. infrastructure charge", "£", 1800, 3500, 8000, "Single new connection."),
    R("water.remote", "services", "Water mains extension / requisition", "£", 5000, 15000, 40000, "Or borehole + treatment as alternative."),
    R("foul.mains", "services", "Foul sewer connection incl. infrastructure charge", "£", 1500, 4000, 9000, "Gravity connection to adjacent sewer."),
    R("foul.ptp", "services", "Package treatment plant + drainage field", "£", 7000, 12000, 22000, "Plus percolation testing."),
    R("foul.ptp_poor_ground", "services", "Off-mains where infiltration is poor (discharge permit / extended field)", "£", 10000, 18000, 30000, "Clay ground."),
    R("surface.suds", "services", "Surface water soakaways/SuDS", "£", 1500, 4000, 9000, "Attenuation if infiltration fails."),
    R("telecoms", "services", "Fibre/telecoms connection contribution", "£", 0, 1500, 6000, "Single-plot contributions vary."),
    R("temp.supplies", "services", "Temporary builder's supplies during build", "£", 500, 1000, 2000, ""),
    # --- access & externals
    R("access.driveway_m2", "externals", "Driveway / parking (permeable)", "£/m²", 40, 80, 140, ""),
    R("access.new_crossing", "externals", "New vehicular access / dropped kerb / splays", "£", 1500, 6000, 25000, "S184 licence; S278 if works to a classified road."),
    R("ext.landscape_m2", "externals", "Landscaping & paths", "£/m² garden", 8, 20, 50, "Garden area (capped at 400 m²)."),
    R("ext.fencing_lm", "externals", "Boundary fencing/hedging", "£/m", 25, 45, 90, "Site perimeter (capped)."),
    # --- fees & surveys (ex VAT)
    R("fee.design_pct", "fees", "Architect/designer (to planning + building regs)", "% of building works", 0.05, 0.08, 0.12, "Lower where modular supplier provides design."),
    R("fee.structural", "fees", "Structural engineer", "£", 1500, 3000, 6000, ""),
    R("fee.planning_app", "fees", "Planning application fee (1 dwelling, full)", "£", 578, 600, 700, "£578 from Dec 2024, indexed annually from April 2025 — verify current fee."),
    R("fee.planning_consultant", "fees", "Planning consultant / pre-application advice", "£", 500, 3000, 9000, ""),
    R("fee.building_control", "fees", "Building control", "£", 1200, 1800, 3000, ""),
    R("fee.warranty_pct", "fees", "10-year structural warranty", "% of building works", 0.010, 0.015, 0.020, "Needed for most mortgages/resale."),
    R("fee.sap_air", "fees", "SAP/EPC calculations & air test", "£", 500, 900, 1500, ""),
    R("survey.topo", "surveys", "Topographical survey", "£", 600, 1000, 2000, ""),
    R("survey.ecology", "surveys", "Preliminary ecological appraisal", "£", 600, 1200, 2500, ""),
    R("survey.protected_species", "surveys", "Protected species surveys (bats/reptiles/GCN)", "£", 0, 1500, 6000, "Seasonal."),
    R("survey.trees", "surveys", "BS5837 tree survey", "£", 500, 900, 1800, ""),
    R("survey.fra", "surveys", "Flood risk assessment", "£", 1500, 2500, 6000, "Where flood risk identified."),
    R("survey.gi", "surveys", "Ground investigation (trial pits/boreholes + lab)", "£", 1500, 3000, 7000, ""),
    R("survey.percolation", "surveys", "Percolation test", "£", 300, 600, 1000, "Off-mains drainage."),
    R("survey.contam_p1", "surveys", "Contamination Phase 1 desk study", "£", 300, 600, 1200, ""),
    R("survey.contam_p2", "surveys", "Contamination Phase 2 intrusive", "£", 2000, 5000, 12000, "Brownfield."),
    R("survey.asbestos", "surveys", "Demolition/refurbishment asbestos survey", "£", 400, 700, 1200, ""),
    R("survey.archaeology", "surveys", "Archaeology desk-based assessment / watching brief", "£", 800, 1500, 5000, ""),
    R("survey.structural", "surveys", "Structural survey of existing building", "£", 600, 1200, 2500, "Conversions."),
    # --- acquisition
    R("legal.purchase", "acquisition", "Conveyancing (land purchase)", "£", 1200, 1800, 3000, ""),
    R("legal.searches", "acquisition", "Searches (local, drainage, environmental)", "£", 400, 600, 900, ""),
    R("legal.conditional_extra", "acquisition", "Extra legal cost: conditional contract", "£", 800, 1500, 3000, ""),
    R("legal.option", "acquisition", "Option agreement legal costs (both sides typical)", "£", 2000, 4000, 8000, "Buyer often pays seller's reasonable costs."),
    R("valuation.land", "acquisition", "RICS valuation / land survey", "£", 500, 900, 1500, ""),
    # --- obligations
    R("obligations.cil_rate", "obligations", "CIL rate if self-build exemption NOT secured", "£/m² GIA", 0, 0, 220, "LPA-specific; base assumes self-build exemption claimed correctly."),
    R("obligations.habitats", "obligations", "Habitats recreation mitigation per dwelling", "£", 300, 1500, 6000, "SAMM/HIP/Bird Aware/New Forest schemes."),
    R("obligations.nutrient", "obligations", "Nutrient neutrality mitigation per dwelling", "£", 2000, 6000, 15000, "Credits vary by catchment & drainage."),
    R("obligations.bng", "obligations", "Biodiversity net gain (if not exempt)", "£", 1000, 5000, 15000, "On-site enhancement or off-site units."),
    # --- insurance, finance, contingency
    R("insurance.site", "finance", "Self-build site insurance (12 months)", "£", 600, 1000, 1800, ""),
    R("finance.valuation_stage", "finance", "Lender stage valuations (×4)", "£", 400, 600, 1000, ""),
    R("risk.unknown_ground_pct", "contingency", "Unknown-ground contingency on groundworks", "% groundworks", 0.0, 0.15, 0.30, "Applied when geology not screened."),
]


def rates_version(rates: dict[str, Rate]) -> str:
    payload = json.dumps({k: [v.low, v.base, v.high] for k, v in sorted(rates.items())})
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def seed_rates(db: Session) -> int:
    existing = {r.key for r in db.scalars(select(CostRate)).all()}
    n = 0
    for r in DEFAULT_RATES:
        if r.key in existing:
            continue
        db.add(CostRate(key=r.key, category=r.category, description=r.description, unit=r.unit, low=r.low, base=r.base, high=r.high, basis=r.basis, source=DEFAULT_SOURCE, source_date=RATES_DATE, regional=r.regional))
        n += 1
    db.flush()
    return n


def load_rates(db: Session | None) -> tuple[dict[str, Rate], dict[str, dict]]:
    """Returns (rates, meta) — DB rows override defaults; meta records source/date/override."""
    rates = {r.key: r for r in DEFAULT_RATES}
    meta = {r.key: {"source": DEFAULT_SOURCE, "source_date": RATES_DATE.isoformat(), "user_override": False} for r in DEFAULT_RATES}
    if db is not None:
        for row in db.scalars(select(CostRate)).all():
            rates[row.key] = Rate(row.key, row.category, row.description, row.unit, row.low, row.base, row.high, row.basis, row.regional)
            meta[row.key] = {"source": row.source, "source_date": row.source_date.isoformat(), "user_override": row.user_override}
    return rates, meta

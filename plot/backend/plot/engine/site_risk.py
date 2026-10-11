"""Environmental, ground, drainage, utilities, access and site-preparation screening.

Outputs (1) human-readable risk items, each Confirmed / Estimated / Unknown / Requires
Investigation, and (2) cost drivers consumed by the cost model (foundation case, drainage
type, service-run assumptions...). Engineering conclusions are screening-level only: no
foundation or drainage design is specified without site investigation.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from plot.engine.facts import SiteFacts
from plot.engine.planning.england import AGRI_BUILDING, EXISTING_DWELLING

SEV_ORDER = {"fatal": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# lithology keyword → (hazard code, severity, explanation)
GEOLOGY_RULES: list[tuple[re.Pattern[str], str, str, str]] = [
    (re.compile(r"PEAT", re.I), "compressible", "high", "Peat: highly compressible, high water table — piled or raft foundations likely."),
    (re.compile(r"ALLUVIUM|TIDAL FLAT|MARINE BEACH", re.I), "soft_alluvium", "high", "Alluvium: soft/compressible soils and shallow groundwater — engineered foundations likely."),
    (re.compile(r"MADE GROUND|ARTIFICIAL|INFILLED GROUND|LANDSCAPED GROUND|WORKED GROUND", re.I), "made_ground", "high", "Artificial ground: variable bearing and possible contamination."),
    (re.compile(r"LANDSLIDE|LANDSLIP|MASS MOVEMENT", re.I), "landslide", "high", "Mapped mass-movement deposits: slope instability — specialist geotechnical assessment essential."),
    (re.compile(r"LONDON CLAY|GAULT|KIMMERIDGE CLAY|OXFORD CLAY|WEALD CLAY|WADHURST CLAY|BARTON CLAY|ATHERFIELD CLAY|LIAS|CHARMOUTH MUDSTONE|FROME CLAY|FULLER'S EARTH|AMPTHILL CLAY|WHITCHURCH SAND|LAMBETH GROUP|READING FORMATION|WEST WALTON|LONDON CLAY FORMATION", re.I), "shrink_swell", "medium", "Clay formation: shrink–swell potential; deeper foundations near trees (NHBC 4.2); clay may prevent soakaways."),
    (re.compile(r"\bCLAY\b|MUDSTONE", re.I), "clay_generic", "medium", "Clay-rich deposit: possible shrink–swell; infiltration likely poor."),
    (re.compile(r"CLAY-WITH-FLINTS", re.I), "clay_with_flints", "medium", "Clay-with-flints over chalk: variable, possible solution pipes."),
    (re.compile(r"CHALK|SEAFORD|LEWES NODULAR|NEWHAVEN|PORTSDOWN|ZIG ZAG|WEST MELBURY|HOLYWELL NODULAR|NEW PIT", re.I), "chalk", "medium", "Chalk: solution features possible; often in groundwater Source Protection Zones (limits drainage fields); groundwater flooding in some valleys."),
    (re.compile(r"HEAD\b", re.I), "head", "low", "Head deposits: variable, can be soft — confirm by trial pits."),
    (re.compile(r"RIVER TERRACE|SAND AND GRAVEL|GRAVEL|PLATEAU", re.I), "granular", "low", "Sand/gravel: generally good bearing and infiltration; check groundwater level."),
    (re.compile(r"PORTLAND|PURBECK|CORNBRASH|OOLITE|LIMESTONE", re.I), "limestone", "low", "Limestone: generally good bearing; minor dissolution possible."),
    (re.compile(r"GREENSAND|SANDSTONE|BRACKLESHAM|POOLE FORMATION|BRANKSOME|BOSCOMBE|SAND\b", re.I), "sand", "low", "Sandy formation: generally good bearing; check clay interbeds and slope stability."),
]


@dataclass
class RiskItem:
    code: str
    topic: str
    title: str
    status: str  # confirmed | estimated | unknown | requires_investigation
    severity: str  # fatal | high | medium | low | info
    detail: str
    action: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CostDrivers:
    foundation_case: str = "standard"  # standard | deep | engineered
    foundation_known: bool = False
    drainage: str = "unknown"  # mains | off_mains | unknown
    services_remote: bool | None = None  # long service runs likely
    demolition_m2: float = 0.0
    asbestos_risk: bool = False
    fra_needed: bool = False
    gi_needed: bool = True
    contamination_phase2: bool = False
    archaeology: bool = False
    tree_survey: bool = True
    ecology_protected_species: bool = False
    new_access: bool = True
    long_access_m: float = 15.0
    infiltration_doubt: bool = False
    radon_known: bool = False


@dataclass
class SiteRiskResult:
    items: list[RiskItem]
    drivers: CostDrivers
    score: float
    max_score: float
    confidence: float
    fatal: bool

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["items"] = sorted(d["items"], key=lambda i: SEV_ORDER.get(i["severity"], 9))
        return d


def _src(e) -> list[dict]:
    return [{"title": e.title, "url": e.source_url, "date": e.data_date, "source_id": e.source_id, "status": e.status}] if e else []


def assess_site_risk(f: SiteFacts, weight: float = 20, design_fit: dict | None = None) -> SiteRiskResult:
    items: list[RiskItem] = []
    d = CostDrivers()
    text = f.listing_text

    # ---------------------------------------------------------------- flood & water
    fz = f.first("ea:flood_zone")
    pd_fz = f.onsite("flood-risk-zone")
    if fz:
        z = str(fz.data.get("zone", "1")).lower()
        sev = {"1": "low", "2": "medium"}.get(z[:1], "high")
        if z == "3b":
            sev = "fatal"
        items.append(RiskItem("flood_rivers_sea", "flood", fz.title, "confirmed", sev, fz.summary or "", "Site-specific FRA" if sev != "low" else None, _src(fz)))
        d.fra_needed = sev != "low"
    elif pd_fz:
        lvl = str(max(e.data.get("flood-risk-level") or "0" for e in pd_fz))
        items.append(RiskItem("flood_rivers_sea", "flood", f"Flood Zone {lvl} (planning.data.gov.uk)", "confirmed", "high" if lvl == "3" else "medium", "Rivers & sea flood zone from the Planning Data platform.", "Site-specific FRA", _src(pd_fz[0])))
        d.fra_needed = True
    elif f.constraints_checked():
        items.append(RiskItem("flood_rivers_sea", "flood", "Flood Zone 1 (no Zone 2/3 recorded)", "estimated", "low", "No flood-risk-zone intersect on planning.data.gov.uk; EA dataset not yet checked directly.", None))
    else:
        items.append(RiskItem("flood_rivers_sea", "flood", "Rivers & sea flood risk not yet checked", "unknown", "info", "Flood data not retrieved.", "Re-run enrichment"))
    for key, label in (("ea:rofsw", "Surface water flood risk"), ("ea:recorded_outlines", "Historic flooding (recorded outlines)")):
        e = f.first(key)
        if e is None:
            items.append(RiskItem(key.split(":")[1], "flood", f"{label}: not checked", "unknown", "info", "Dataset not retrieved or endpoint unavailable.", "Check the EA long-term flood risk service"))
        elif e.data.get("hits"):
            items.append(RiskItem(key.split(":")[1], "flood", f"{label}: present at site", "confirmed", "medium", e.summary or "", "FRA and drainage strategy", _src(e)))
            d.fra_needed = True
        else:
            items.append(RiskItem(key.split(":")[1], "flood", f"{label}: none mapped", "confirmed", "low", "", None, _src(e)))
    items.append(RiskItem("groundwater", "flood", "Groundwater flooding & seasonal water table", "requires_investigation", "info", "Groundwater flood susceptibility is not openly mapped; measured seasonal groundwater depth needs monitoring (ideally winter) — distinct from flood-risk mapping.", "Install standpipes during ground investigation; monitor through winter"))

    # ---------------------------------------------------------------- ground
    geo_items = [e for e in f.items("bgs:") if e.data.get("units")]
    lith = " ".join(
        " ".join(str(v) for v in u.values())
        for e in geo_items
        for u in e.data.get("units", [])
    ).upper()
    hazards: list[tuple[str, str, str]] = []
    for pat, code, sev, expl in GEOLOGY_RULES:
        if pat.search(lith) and code not in [h[0] for h in hazards]:
            hazards.append((code, sev, expl))
    if geo_items:
        d.foundation_known = True
        codes = {h[0] for h in hazards}
        if codes & {"compressible", "soft_alluvium", "made_ground", "landslide"}:
            d.foundation_case = "engineered"
        elif codes & {"shrink_swell", "clay_generic", "clay_with_flints"}:
            d.foundation_case = "deep"
        if codes & {"shrink_swell", "clay_generic"}:
            d.infiltration_doubt = True
        for code, sev, expl in hazards:
            items.append(RiskItem(f"ground_{code}", "ground", expl.split(":")[0], "estimated", sev, expl + " (Inferred from mapped BGS geology, not ground investigation.)", "Trial pits / boreholes with lab testing", [s for e in geo_items for s in _src(e)][:2]))
        if not hazards:
            items.append(RiskItem("ground_unclassified", "ground", "Mapped geology without known screening hazards", "estimated", "low", lith[:300], None, [s for e in geo_items for s in _src(e)][:2]))
    else:
        items.append(RiskItem("ground_unknown", "ground", "Ground conditions not screened", "unknown", "info", "BGS geology not retrieved. Foundation costs carry an unknown-ground contingency.", "Re-run enrichment; plan a ground investigation"))
    if f.onsite("contaminated-land"):
        d.contamination_phase2 = True
        items.append(RiskItem("contamination_determined", "ground", "Determined contaminated land", "confirmed", "high", "Part 2A determination recorded.", "Phase 1 & 2 contamination assessment", _src(f.onsite("contaminated-land")[0])))
    elif f.category == "brownfield" or "brownfield_register" in f.signals:
        d.contamination_phase2 = True
        items.append(RiskItem("contamination_brownfield", "ground", "Previously developed land: contamination possible", "estimated", "medium", "Former uses (garages, depots, works) may leave contamination; planning conditions typically require investigation.", "Phase 1 desk study (then Phase 2 if indicated)"))
    items.append(RiskItem("radon", "ground", "Radon potential", "unknown", "info", "Indicative radon data is licensed (UKHSA/BGS). Building Regulations require protection in affected areas.", "Buy a UKHSA radon report for the address (~£5) or check ukradon.org"))
    items.append(RiskItem("mining", "ground", "Mining legacy", "estimated", "low", "Dorset and Hampshire lie outside coalfields; local stone/chalk workings and Purbeck stone/ball-clay mines exist — confirm with a mining search if near known workings.", "Conveyancing environmental + mining search"))
    items.append(RiskItem("gradient", "ground", "Site gradient", "unknown", "info", "No terrain model integrated yet; slope affects foundations, retaining walls and access.", "Topographical survey"))

    # ---------------------------------------------------------------- wastewater & water supply
    in_bua = bool(f.onsite("built-up-area"))
    bua_d = 0.0 if in_bua else f.nearest_distance("built-up-area")
    if bua_d is not None:
        d.services_remote = bua_d > 300
        if bua_d > 100 and bua_d <= 300:
            d.drainage = "unknown"
            items.append(RiskItem("foul_drainage", "utilities", "Sewer availability uncertain", "unknown", "medium", f"About {bua_d:.0f} m from a built-up area: a public sewer may or may not be reachable by gravity.", "Obtain sewer asset map; percolation test as fallback"))
        elif bua_d <= 100:
            d.drainage = "mains"
            items.append(RiskItem("foul_drainage", "utilities", "Public sewer probably nearby", "estimated", "low", "Within/adjacent to a built-up area. Sewer proximity is inferred, not confirmed.", "Obtain sewer asset map and connection quote (water company)"))
        else:
            d.drainage = "off_mains"
            sev = "medium" if not d.infiltration_doubt else "high"
            items.append(RiskItem("foul_drainage", "utilities", "Off-mains drainage likely (package treatment plant)", "estimated", sev, f"About {bua_d:.0f} m from the nearest built-up area. A package treatment plant needs a drainage field (percolation test) or a permitted discharge to a watercourse; septic tanks cannot discharge to watercourses. Groundwater Source Protection Zones restrict drainage fields." + (" Clay geology suggests poor infiltration." if d.infiltration_doubt else ""), "Percolation test (BS 6297); check General Binding Rules vs permit"))
        items.append(RiskItem("water_supply", "utilities", "Mains water: proximity inferred only", "estimated", "low" if not d.services_remote else "medium", "Connection availability and cost require a water company quote; long mains extensions can be costly.", "Request a new connection quote"))
    else:
        items.append(RiskItem("foul_drainage", "utilities", "Drainage route unknown", "unknown", "info", "Settlement proximity not checked.", "Obtain asset maps"))
    items.append(RiskItem("rainwater", "utilities", "Rainwater harvesting (non-potable only)", "estimated", "info", "Harvested rainwater can serve WCs, washing machine and garden. It does not replace a safe drinking-water supply: potable use needs treatment and Private Water Supplies Regulations compliance.", None))

    # ---------------------------------------------------------------- electricity, telecoms
    items.append(RiskItem("electricity", "utilities", "Electricity connection: " + ("long service run likely" if d.services_remote else "network proximity assumed"), "estimated", "medium" if d.services_remote else "low", "Distribution network proximity ≠ confirmed capacity. Rural single-phase connections can need overhead/underground extensions.", "Request a budget estimate from the DNO (SSEN for most of Dorset/Hampshire)"))
    bb = f.first("broadband")
    if bb:
        items.append(RiskItem("broadband", "utilities", bb.title, bb.status, "low", "Coverage at the nearest postcode; a new connection still needs an operator quote.", "Openreach / altnet new-connection enquiry", _src(bb)))
    else:
        items.append(RiskItem("broadband", "utilities", "Broadband & mobile coverage not checked", "unknown", "info", "Ofcom API key not configured.", "Check Ofcom coverage checker"))

    # ---------------------------------------------------------------- access
    d.new_access = True
    if d.services_remote:
        d.long_access_m = 60.0
    items.append(RiskItem("access", "access", "Vehicular access & visibility splays", "requires_investigation", "medium" if d.services_remote else "low", "Legal access to the highway (no ransom strip), visibility splays and highway authority consent need checking. Construction and crane/HGV access matter for modular builds.", "Check title plan for access rights; highways pre-app"))
    if "landlocked" in text or "no vehicular access" in text or "access via" in text and "right of way" in text:
        items.append(RiskItem("access_claim", "access", "Listing mentions restricted access", "requires_investigation", "high", "Access appears constrained per the advert.", "Solicitor review of rights of way"))

    # ---------------------------------------------------------------- site preparation
    if EXISTING_DWELLING.search(text) or AGRI_BUILDING.search(text) or f.category in ("renovation", "barn", "brownfield", "building"):
        d.asbestos_risk = True
        d.demolition_m2 = 80.0 if f.category != "brownfield" else 200.0
        items.append(RiskItem("demolition", "site_prep", "Existing structures: demolition/strip-out and asbestos risk", "estimated", "medium", "Pre-2000 buildings may contain asbestos (roof sheets, insulation). Demolition area estimated, not measured.", "Refurbishment/demolition asbestos survey"))
    if f.onsite("tree-preservation-zone") or f.onsite("tree") or f.nearest_distance("ancient-woodland") is not None and (f.nearest_distance("ancient-woodland") or 9e9) < 50:
        items.append(RiskItem("trees", "site_prep", "Protected or significant trees", "confirmed", "medium", "Root protection areas constrain footprint and foundations.", "BS5837 tree survey", _src(f.first("pd:tree-preservation-zone") or f.first("pd-near:ancient-woodland"))))
    else:
        items.append(RiskItem("trees", "site_prep", "Trees and hedgerows not surveyed", "unknown", "info", "Clearance needs, nesting-season timing and hedgerow regulations to be established.", "Site visit; tree survey if trees within ~15 m of works"))
    d.ecology_protected_species = d.asbestos_risk or (bua_d is not None and bua_d > 100)
    items.append(RiskItem("ecology", "site_prep", "Protected species", "requires_investigation", "medium" if d.ecology_protected_species else "low", "Bats (buildings, mature trees), reptiles (heathland edges), great crested newts (ponds within 250 m) are common survey triggers; surveys are seasonal.", "Preliminary Ecological Appraisal"))
    if f.nearest_distance("scheduled-monument") is not None and (f.nearest_distance("scheduled-monument") or 9e9) <= 500:
        d.archaeology = True
        items.append(RiskItem("archaeology", "site_prep", "Archaeological potential", "estimated", "low", "Designated monument nearby; check the county Historic Environment Record.", "HER search / desk-based assessment"))
    items.append(RiskItem("earthworks", "site_prep", "Earthworks, spoil disposal and buried obstructions", "unknown", "info", "Volumes estimated from footprint; old foundations, tanks and services on previously developed land can add cost.", "Topographic survey and trial pits"))

    # ---------------------------------------------------------------- design fit (physical)
    if design_fit and design_fit.get("fits") is False:
        items.append(RiskItem("design_fit", "site_prep", "House concept does not fit the site", "estimated", "high", design_fit.get("reason", ""), "Test alternative footprints in Design Studio"))

    # ---------------------------------------------------------------- score
    sev_pen = {"high": 6.0, "medium": 2.5, "low": 0.5}
    # Unknowns are priced at their expected (prior) penalty: missing data must not make a
    # site look better than a surveyed one. Universal unknowns (radon, gradient...) apply to
    # every site equally and are not scored.
    prior_pen = {"flood_rivers_sea": 2.0, "rofsw": 0.8, "recorded_outlines": 0.4, "ground_unknown": 2.0, "foul_drainage": 1.5, "broadband": 0.3}
    fatal = any(i.severity == "fatal" for i in items)
    pen = sum(prior_pen.get(i.code, 0.0) if i.status == "unknown" else sev_pen.get(i.severity, 0.0) for i in items)
    score = 0.0 if fatal else max(0.0, 20.0 - min(pen, 20.0))
    known = sum(1 for i in items if i.status in ("confirmed", "estimated"))
    confidence = round(min(0.9, 0.1 + 0.8 * known / max(1, len(items))), 2)
    return SiteRiskResult(items=items, drivers=d, score=round(score * weight / 20.0, 1), max_score=weight, confidence=confidence, fatal=fatal)

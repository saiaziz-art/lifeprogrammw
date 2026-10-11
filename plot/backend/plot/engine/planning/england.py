"""England planning screening rules.

Deterministic rules over sourced evidence. Every finding carries status (confirmed /
estimated / unknown / requires_investigation), the evidence it relies on, and the policy
basis. Policy references are cited at chapter/instrument level; check the current text.

What this does NOT know (and says so): LPA settlement boundaries, Local Plan policies and
allocations, the full planning history, CIL charging rates, highway standards. These are
listed as required checks rather than guessed.
"""

from __future__ import annotations

import datetime as dt
import re

from plot.engine.facts import EvidenceView, SiteFacts
from plot.engine.planning import base
from plot.engine.planning.base import CLASSES, Finding, Obligation, PlanningResult, Route

BNG_AREA_EXEMPTION_FROM = dt.date(2026, 8, 6)
BNG_AREA_EXEMPTION_M2 = 2_000  # 0.2 ha

CLAIMED_PERMISSION = re.compile(
    r"\b(with (?:full |outline |detailed )?planning|full planning (?:permission|consent|granted)|outline planning (?:permission|consent|granted)|planning (?:permission|consent) (?:granted|for|has been)|"
    r"planning approved|approved planning|benefits? from (?:full |outline )?planning|prior approval (?:granted|for)|class q (?:approval|consent|granted)|consented)\b",
    re.I,
)
NO_PERMISSION = re.compile(r"\b(no planning|without planning|subject to planning|stp\b|planning potential|hope value|development potential|may suit development)\b", re.I)
OVERAGE = re.compile(r"\b(overage|uplift clause|clawback)\b", re.I)
EXISTING_DWELLING = re.compile(r"\b(existing (?:dwelling|house|bungalow|cottage|chalet)|replacement dwelling|renovation project|in need of (?:complete |full )?(?:renovation|modernisation|refurbishment)|derelict (?:house|cottage|bungalow))\b", re.I)
AGRI_BUILDING = re.compile(r"\b(barns?|agricultural building|steel portal frame|dutch barn|class q|cattle shed|grain store|redundant farm building)\b", re.I)
RESIDENTIAL_APP = re.compile(r"\b(dwelling|house|bungalow|residential|home|chalet)\b", re.I)


def src(e: EvidenceView | None) -> list[dict]:
    if e is None:
        return []
    return [{"title": e.title, "url": e.source_url, "date": e.data_date, "source_id": e.source_id, "status": e.status}]


def _parse_date(v) -> dt.date | None:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


class England:
    code = "england"

    def assess(self, f: SiteFacts, prefs, weight: float = 30) -> PlanningResult:
        findings: list[Finding] = []
        routes: list[Route] = []
        obligations: list[Obligation] = []
        actions: list[str] = []
        checks: dict[str, str] = {}

        checked = f.constraints_checked()
        checks["national_designations"] = "checked" if checked else "not_checked"
        lpa = self._lpa(f)

        # ------------------------------------------------------------ hard constraints on site
        def onsite(ds: str) -> list[EvidenceView]:
            return f.onsite(ds)

        for ds, code, title, effect, policy, detail in [
            ("scheduled-monument", "scheduled_monument_onsite", "Scheduled monument on site", "fatal", "Ancient Monuments and Archaeological Areas Act 1979", "Works need Scheduled Monument Consent; a new dwelling is very unlikely to be acceptable."),
            ("site-of-special-scientific-interest", "sssi_onsite", "Site of Special Scientific Interest on site", "fatal", "Wildlife and Countryside Act 1981; NPPF ch.15", "Development damaging an SSSI is normally refused."),
            ("special-area-of-conservation", "sac_onsite", "Special Area of Conservation on site", "fatal", "Conservation of Habitats and Species Regulations 2017", "Habitats site — residential development on it is not realistic."),
            ("special-protection-area", "spa_onsite", "Special Protection Area on site", "fatal", "Conservation of Habitats and Species Regulations 2017", "Habitats site — residential development on it is not realistic."),
            ("ramsar", "ramsar_onsite", "Ramsar wetland on site", "fatal", "NPPF ch.15", "Treated as a habitats site."),
            ("ancient-woodland", "ancient_woodland_onsite", "Ancient woodland on site", "fatal", "NPPF ch.15 (loss only for wholly exceptional reasons)", "Loss of ancient woodland is refused save wholly exceptional reasons."),
            ("common-land-and-village-green", "common_land_onsite", "Common land / village green", "fatal", "Commons Act 2006", "Registered common land or green cannot be developed for housing."),
            ("coastal-change-management-area", "ccma_onsite", "Coastal change management area", "major", "NPPF ch.14", "New permanent dwellings are generally not appropriate in CCMAs."),
            ("contaminated-land", "contaminated_land_onsite", "Determined contaminated land (Part 2A)", "major", "Environmental Protection Act 1990 Part 2A", "Remediation and specialist investigation required before residential use."),
        ]:
            hits = onsite(ds)
            if hits:
                findings.append(Finding(code, title, effect, hits[0].status, detail, policy, src(hits[0])))

        # ------------------------------------------------------------ landscape designations
        np_hits = onsite("national-park")
        nl_hits = onsite("area-of-outstanding-natural-beauty")
        gb_hits = onsite("green-belt")
        ca_hits = onsite("conservation-area")
        lb_hits = onsite("listed-building") + onsite("listed-building-outline")
        whs_hits = onsite("world-heritage-site")
        a4_hits = onsite("article-4-direction-area")
        tpo_hits = onsite("tree-preservation-zone") + onsite("tree")
        hc_hits = onsite("heritage-coast")

        if np_hits:
            name = np_hits[0].data.get("name") or "National Park"
            detail = "Great weight to landscape and scenic beauty; new dwellings tightly restricted by the National Park Local Plan."
            if "new forest" in name.lower():
                detail += " New Forest NPA policy restricts new housing largely to the Defined Villages, with floorspace limits on replacement/extended dwellings — check the adopted Local Plan."
            findings.append(Finding("national_park", f"Within {name}", "major", np_hits[0].status, detail, "NPPF ch.15; National Park Local Plan", src(np_hits[0]), f"Read {name} Local Plan housing policies"))
        if nl_hits:
            findings.append(Finding("national_landscape", f"Within National Landscape: {nl_hits[0].data.get('name')}", "moderate", nl_hits[0].status, "Great weight to conserving landscape and scenic beauty; design and siting scrutinised; Class Q excluded.", "NPPF ch.15", src(nl_hits[0])))
        if hc_hits:
            findings.append(Finding("heritage_coast", "Heritage Coast", "moderate", hc_hits[0].status, "Development should be consistent with the special character of the Heritage Coast.", "NPPF ch.15", src(hc_hits[0])))
        if gb_hits:
            findings.append(Finding("green_belt", "Green Belt", "major", gb_hits[0].status, "New buildings are inappropriate unless an exception applies (e.g. limited infilling in villages, replacement building not materially larger, redevelopment of previously developed land, or 'grey belt' criteria).", "NPPF ch.13 (Dec 2024)", src(gb_hits[0]), "Assess Green Belt exceptions / grey belt status with a planning consultant"))
        if ca_hits:
            findings.append(Finding("conservation_area", f"Conservation area: {ca_hits[0].data.get('name')}", "moderate", ca_hits[0].status, "Development must preserve or enhance character; Class Q and some PD rights excluded; demolition controls apply.", "Planning (LB&CA) Act 1990 s.72; NPPF ch.16", src(ca_hits[0])))
        if lb_hits:
            grade = lb_hits[0].data.get("listed-building-grade")
            findings.append(Finding("listed_building_onsite", f"Listed building on/within site (grade {grade or '?'})", "major", lb_hits[0].status, "Listed building consent needed for works; demolition or substantial alteration rarely acceptable; Class Q excluded.", "Planning (LB&CA) Act 1990; NPPF ch.16", src(lb_hits[0])))
        if whs_hits:
            findings.append(Finding("world_heritage_site", "World Heritage Site", "major", whs_hits[0].status, "Outstanding universal value must be protected.", "NPPF ch.16", src(whs_hits[0])))
        if a4_hits:
            findings.append(Finding("article_4", "Article 4 direction", "moderate", a4_hits[0].status, f"Some permitted development rights removed: {a4_hits[0].data.get('permitted-development-rights') or 'see direction'}.", "GPDO 2015 art.4", src(a4_hits[0])))
        if tpo_hits:
            findings.append(Finding("tpo", "Tree Preservation Order on/at site", "moderate", tpo_hits[0].status, "Protected trees constrain layout, foundations and clearance; consent needed for works.", "TCPA 1990 Pt VIII", src(tpo_hits[0]), "Commission BS5837 tree survey"))
        elif checked:
            findings.append(Finding("tpo_unknown", "Tree Preservation Orders: not confirmed absent", "info", "unknown", "TPO data on planning.data.gov.uk covers only some LPAs.", None, [], "Check the LPA's TPO map"))

        # proximity: setting of heritage assets & ancient woodland buffer
        d_lb = f.nearest_distance("listed-building")
        if not lb_hits and d_lb is not None and d_lb <= 50:
            findings.append(Finding("listed_building_setting", f"Listed building {d_lb:.0f} m away", "minor", "confirmed", "Setting of the listed building is a material consideration.", "NPPF ch.16", src(f.first("pd-near:listed-building"))))
        d_aw = f.nearest_distance("ancient-woodland")
        if not onsite("ancient-woodland") and d_aw is not None and d_aw < 15:
            findings.append(Finding("ancient_woodland_buffer", f"Ancient woodland {d_aw:.0f} m away (<15 m buffer)", "major", "confirmed", "Natural England/Forestry Commission standing advice: minimum 15 m buffer; layout must avoid root protection zone.", "Standing advice (NE/FC)", src(f.first("pd-near:ancient-woodland"))))
        d_sm = f.nearest_distance("scheduled-monument")
        if not onsite("scheduled-monument") and d_sm is not None and d_sm <= 250:
            findings.append(Finding("archaeology_nearby", f"Scheduled monument {d_sm:.0f} m away", "minor", "confirmed", "Archaeological potential; desk-based assessment or watching brief may be conditioned.", "NPPF ch.16", src(f.first("pd-near:scheduled-monument"))))

        # ------------------------------------------------------------ habitats sites (distance-based policy)
        self._habitats(f, findings, obligations)

        # nutrient neutrality
        nn = onsite("nutrient-neutrality-catchment")
        if nn:
            obligations.append(Obligation("nutrient_neutrality", f"Nutrient neutrality ({nn[0].data.get('name')})", "yes", "New overnight accommodation in this catchment must demonstrate nutrient neutrality (mitigation credits or on-site measures). The Planning and Infrastructure Act 2025 introduces Nature Restoration Fund levies that may replace bespoke mitigation — check the LPA's current scheme.", "obligations.nutrient"))
            findings.append(Finding("nutrient_neutrality", "Nutrient neutrality catchment", "moderate", nn[0].status, "Adds mitigation cost and can delay determination; drainage choice (mains vs package plant) affects the nutrient budget.", "Habitats Regs 2017; Natural England advice", src(nn[0])))
        elif checked:
            obligations.append(Obligation("nutrient_neutrality", "Nutrient neutrality", "no", "Not within a mapped nutrient neutrality catchment.", None))

        # ------------------------------------------------------------ flood
        fz = f.first("ea:flood_zone")
        pd_fz = onsite("flood-risk-zone")
        zone = None
        if fz is not None:
            zone = str(fz.data.get("zone", "1")).lower()
            checks["flood_zone"] = "checked"
        elif pd_fz:
            zone = str(max(e.data.get("flood-risk-level") or "0" for e in pd_fz))
            checks["flood_zone"] = "checked (planning.data.gov.uk)"
        elif checked:
            zone = "1"
            checks["flood_zone"] = "checked (no zone 2/3 on planning.data.gov.uk)"
        else:
            checks["flood_zone"] = "not_checked"
        ev_fz = fz or (pd_fz[0] if pd_fz else None)
        if zone and zone.startswith("3"):
            effect = "fatal" if zone == "3b" else "major"
            findings.append(Finding("flood_zone_3", f"Flood Zone {zone.upper()}", effect, "confirmed", "Sequential test (and exception test) apply; a single new dwelling is frequently refused where reasonably available lower-risk sites exist. FZ3b (functional floodplain) excludes new dwellings.", "NPPF ch.14; PPG Flood risk", src(ev_fz), "Commission a site-specific flood risk assessment before any offer"))
        elif zone and zone.startswith("2"):
            findings.append(Finding("flood_zone_2", "Flood Zone 2", "moderate", "confirmed", "Sequential test applies; site-specific FRA required.", "NPPF ch.14", src(ev_fz), "Obtain FRA quote"))
        rofsw = f.first("ea:rofsw")
        if rofsw and rofsw.data.get("hits"):
            findings.append(Finding("surface_water", "Mapped surface water flood risk", "moderate", "confirmed", "Layout, floor levels and drainage must respond; FRA likely required.", "NPPF ch.14", src(rofsw)))

        # ------------------------------------------------------------ settlement & countryside
        in_bua = bool(onsite("built-up-area"))
        bua_d = 0.0 if in_bua else f.nearest_distance("built-up-area")
        if f.near_checked("built-up-area") or in_bua:
            checks["settlement_proxy"] = "checked (ONS built-up areas; LPA settlement boundary NOT checked)"
        else:
            checks["settlement_proxy"] = "not_checked"
        isolated = bua_d is not None and bua_d > 300
        if in_bua:
            findings.append(Finding("within_bua", "Within an ONS built-up area", "positive", "estimated", "Suggests the site may be within or near a settlement where infill housing is supported in principle — but the LPA's own settlement boundary decides.", "Local Plan settlement policy", src(onsite("built-up-area")[0]), "Confirm settlement boundary on the LPA policies map"))
        elif bua_d is not None:
            if isolated:
                findings.append(Finding("countryside_isolated", f"Countryside, {bua_d / 1000:.1f} km from nearest built-up area", "major", "estimated", "New isolated homes in the countryside are resisted unless a special circumstance applies (rural worker, heritage asset re-use, redundant building re-use, subdivision, or exceptional-quality design).", "NPPF para 84 (isolated homes)", src(f.first("pd-near:built-up-area"))))
            else:
                findings.append(Finding("edge_of_settlement", f"Outside but within {bua_d:.0f} m of a built-up area", "moderate", "estimated", "Not physically isolated, but probably outside the settlement boundary — countryside policies likely apply.", "Local Plan countryside policy", src(f.first("pd-near:built-up-area")), "Check settlement boundary and any emerging allocations"))

        # ------------------------------------------------------------ existing status & routes
        text = f.listing_text
        reg = f.raw_register or {}
        reg_status = (reg.get("planning_status") or reg.get("planning-permission-status") or "").lower()
        is_register = "brownfield_register" in f.signals or bool(onsite("brownfield-land")) or bool(reg)
        claimed = bool(CLAIMED_PERMISSION.search(text)) and not NO_PERMISSION.search(text)
        overage = bool(OVERAGE.search(text))
        existing_dwelling = f.category in ("renovation", "house") or bool(EXISTING_DWELLING.search(text))
        agri = f.category == "barn" or bool(AGRI_BUILDING.search(text))
        verified_override = f.overrides.get("planning.verified")

        approval_exists = False
        permission_verified = bool(verified_override)
        if verified_override:
            findings.append(Finding("permission_verified_by_user", "Permission / lawful use verified by you", "positive", "confirmed", f"Recorded reason: {verified_override.get('reason')}", None, []))

        if is_register:
            perm_date = _parse_date(reg.get("permission_date") or reg.get("planning-permission-date"))
            dwell = reg.get("min_net_dwellings") or reg.get("minimum-net-dwellings")
            if "permissioned" in reg_status and "not" not in reg_status:
                lapsed = perm_date is not None and perm_date + dt.timedelta(days=3 * 365) < f.today
                approval_exists = not lapsed
                findings.append(
                    Finding(
                        "register_permissioned",
                        f"Brownfield register: residential permission recorded ({reg.get('permission_type') or reg.get('planning-permission-type') or 'type n/a'}, {perm_date or 'date n/a'})",
                        "positive",
                        "confirmed" if not f.stale else "estimated",
                        (f"Permission for about {dwell} dwelling(s). " if dwell else "")
                        + ("Older than 3 years: may have lapsed unless lawfully commenced (or built out)." if lapsed else "Within a typical 3-year implementation period unless conditioned otherwise.")
                        + (" Register record is stale; status may have changed." if f.stale else ""),
                        "TCPA 1990 s.91; Brownfield Land Register Regs 2017",
                        [],
                        "Look up the permission on the LPA register: conditions, commencement, S106",
                    )
                )
                routes.append(Route("existing_permission", "Build out / vary existing permission", "strong" if approval_exists else "possible", "A single smaller home would need a fresh application or variation; LPA may resist under-use of an allocated brownfield site."))
            else:
                findings.append(Finding("register_suitable", "Brownfield register: LPA considers site suitable for housing", "positive", "confirmed" if not f.stale else "estimated", f"Previously developed land on the statutory register{f' (about {dwell} dwellings anticipated)' if dwell else ''}. Principle of housing supported, though a single dwelling may be seen as under-use.", "Brownfield Land Register Regs 2017", []))
                routes.append(Route("brownfield_allocation", "Brownfield register site — full application", "possible", "Housing principle accepted by the LPA; expect scrutiny of density and design."))
            if f.stale:
                actions.append("Register record is historic: confirm current status with the LPA before relying on it")

        if claimed:
            findings.append(Finding("claimed_permission", "Advertiser states planning permission/consent", "positive", "requires_investigation", "Claim taken from the listing text only. Verify reference, decision date, conditions, expiry and any S106 on the LPA register.", "TCPA 1990 s.91", [{"title": l.title, "url": l.url, "date": None, "source_id": l.source_id, "status": "estimated"} for l in f.listings[:1]], "Find the permission on the LPA public access register"))
            approval_exists = True
            routes.append(Route("existing_permission", "Existing permission (claimed)", "possible", "If verified and unexpired, the principle is established; conditions may still constrain design/cost."))
        if overage:
            findings.append(Finding("overage", "Overage / uplift clause mentioned", "moderate", "requires_investigation", "Overage would claw back value on planning gain or sale — reduces achievable value.", None, [], "Ask agent for overage terms"))

        if existing_dwelling:
            excl = bool(lb_hits)
            routes.append(Route("replacement_dwelling", "Replacement dwelling", "excluded" if excl else ("possible" if not gb_hits and not np_hits else "weak"), "Replacement of a lawful dwelling is usually acceptable if not materially larger; National Park/Green Belt floorspace limits apply; abandonment of residential use must be ruled out."))
            findings.append(Finding("existing_dwelling", "Existing dwelling indicated", "positive", "requires_investigation", "Confirm lawful residential use (council tax band, EPC, planning history) — this is the strongest route in countryside locations.", None, [], "Check council tax listing and planning history for lawful use"))

        if agri:
            class_q_excluded_by = [n for n, hit in [("National Park", np_hits), ("National Landscape", nl_hits), ("conservation area", ca_hits), ("listed building", lb_hits), ("World Heritage Site", whs_hits), ("SSSI", onsite("site-of-special-scientific-interest")), ("scheduled monument", onsite("scheduled-monument"))] if hit]
            if class_q_excluded_by:
                routes.append(Route("class_q", "Class Q agricultural-to-residential prior approval", "excluded", f"Excluded: {', '.join(class_q_excluded_by)}."))
            else:
                routes.append(Route("class_q", "Class Q agricultural-to-residential prior approval", "possible" if checked else "weak", "Up to 10 homes / 1,000 m² (max 150 m² each) since 21 May 2024; building must have been in agricultural use and be structurally capable of conversion without substantial rebuilding. Requires a structural survey and evidence of agricultural use."))
                actions.append("Commission a structural survey and gather evidence of agricultural use (Class Q)")
            routes.append(Route("conversion_full", "Full planning for conversion of a rural building", "possible", "Re-use of redundant buildings that enhances the setting is a countryside exception (NPPF para 84c)."))

        if in_bua and not routes:
            routes.append(Route("infill", "Infill/windfall dwelling within settlement", "possible", "Subject to the settlement boundary, character, amenity, parking and access."))
        if isolated and not existing_dwelling and not agri:
            routes.append(Route("para84e", "Exceptional-quality design (NPPF para 84e)", "weak", "Truly outstanding, innovative design that significantly enhances its setting — rare, costly, and design-review led."))

        # nearby applications (precedent), where the dataset has coverage
        apps = f.items("planning-application")
        res_apps = [a for a in apps if RESIDENTIAL_APP.search(a.title or "")]
        if res_apps:
            refused = [a for a in res_apps if "refus" in str(a.data.get("planning-decision") or "").lower()]
            approved = [a for a in res_apps if "approv" in str(a.data.get("planning-decision") or "").lower() or "grant" in str(a.data.get("planning-decision") or "").lower()]
            findings.append(Finding("nearby_precedent", f"Nearby residential applications: {len(approved)} approved, {len(refused)} refused", "info", "estimated", "Precedent is a material consideration only where circumstances are comparable.", None, [s for a in res_apps[:5] for s in src(a)]))
        checks["planning_history"] = "partial (planning.data.gov.uk alpha)" if f.first("planning-history-coverage") else "not_checked"
        actions.append(f"Search {lpa or 'the LPA'}'s planning register for history on and around the site")

        # ------------------------------------------------------------ obligations
        area = f.area_m2
        if f.today >= BNG_AREA_EXEMPTION_FROM:
            if area is not None and area <= BNG_AREA_EXEMPTION_M2:
                obligations.append(Obligation("bng", "Biodiversity net gain", "no", f"Site ≈{area:,.0f} m² is within the ≤0.2 ha area-based exemption in force from 6 Aug 2026 (as reported; confirm the regulations and how the LPA measures site area).", None))
            elif area is not None:
                obligations.append(Obligation("bng", "Biodiversity net gain (10%)", "yes", "Above 0.2 ha: mandatory 10% BNG using the small sites metric; on-site enhancement or off-site units.", "obligations.bng"))
            else:
                obligations.append(Obligation("bng", "Biodiversity net gain", "unknown", "Site area unknown; exempt only if ≤0.2 ha.", "obligations.bng"))
        else:
            obligations.append(Obligation("bng", "Biodiversity net gain", "possible", "Self-build/custom-build and de minimis exemptions may apply (pre-Aug 2026 regime).", "obligations.bng"))
        obligations.append(Obligation("cil", "Community Infrastructure Levy", "likely", "Most Dorset/Hampshire LPAs charge CIL. Self-build exemption is available if claimed BEFORE commencement and the home is occupied for 3 years — otherwise the full charge applies.", "obligations.cil"))
        obligations.append(Obligation("s106", "Section 106 / unilateral undertaking", "possible", "Single dwellings rarely carry S106 except for habitats mitigation or occupancy ties.", None))

        # ------------------------------------------------------------ classification
        fatal = [x for x in findings if x.effect == "fatal" and x.status in ("confirmed", "estimated")]
        major = [x for x in findings if x.effect == "major"]
        good_route = any(r.plausibility in ("strong", "possible") for r in routes)
        if fatal:
            cls = "significant_obstacle"
        elif permission_verified:
            cls = "permission_verified"
        elif approval_exists:
            cls = "approval_exists_review"
        elif len(major) >= 2 and not existing_dwelling:
            cls = "significant_obstacle"
        elif good_route and not (isolated and not (existing_dwelling or agri)) and len(major) <= 1:
            cls = "plausible_route"
        else:
            cls = "speculative_high_risk"
        if "planning.class" in f.overrides:
            cls = f.overrides["planning.class"]["value"]
            findings.append(Finding("class_override", "Planning class overridden by you", "info", "confirmed", f"Reason: {f.overrides['planning.class'].get('reason')}", None, []))

        base_pts = {"permission_verified": 28, "approval_exists_review": 23, "plausible_route": 17, "speculative_high_risk": 8, "significant_obstacle": 1}[cls]
        penalty = sum({"major": 2.5, "moderate": 1.2, "minor": 0.4}.get(x.effect, 0) for x in findings if x.effect != "fatal")
        if cls in ("permission_verified", "approval_exists_review"):
            penalty *= 0.5
        score = max(0.0, min(30.0, base_pts - min(penalty, 10)))
        score = score * weight / 30.0

        known = sum(1 for k in ("national_designations", "flood_zone", "settlement_proxy") if checks.get(k, "").startswith("checked"))
        confidence = round(0.15 + 0.2 * known + (0.15 if checks.get("planning_history", "").startswith("partial") else 0) + (0.1 if f.boundary_wkt else 0) - (0.15 if f.stale else 0), 2)
        confidence = max(0.05, min(0.95, confidence))
        if not checked:
            actions.insert(0, "Constraint data not yet retrieved — re-run enrichment when planning.data.gov.uk is reachable")
        actions.append("Pre-application advice from the LPA before committing to purchase")

        return PlanningResult(
            jurisdiction="england",
            classification=cls,
            classification_label=CLASSES[cls],
            score=round(score, 1),
            max_score=weight,
            findings=findings,
            routes=routes,
            obligations=obligations,
            lpa=lpa,
            confidence=confidence,
            checks=checks,
            actions=list(dict.fromkeys(actions)),
        )

    # ------------------------------------------------------------------ helpers

    def _lpa(self, f: SiteFacts) -> str | None:
        hit = f.onsite("local-planning-authority")
        if hit:
            return hit[0].data.get("name")
        pc = f.first("postcode")
        if pc:
            return pc.data.get("admin_district")
        return f.lpa_name

    def _habitats(self, f: SiteFacts, findings: list[Finding], obligations: list[Obligation]) -> None:
        if not f.near_checked("special-protection-area"):
            obligations.append(Obligation("habitats", "Habitats-site recreation mitigation", "unknown", "Distance to SPAs/SACs not yet checked.", "obligations.habitats"))
            return
        ev = f.first("pd-near:special-protection-area")

        def dist(*names: str) -> float | None:
            ds = [f.nearest_distance(d, n) for d in ("special-protection-area", "special-area-of-conservation", "ramsar") for n in names]
            ds = [d for d in ds if d is not None]
            return min(ds) if ds else None

        applied = False
        d = dist("dorset heath")
        if d is not None:
            if d < 400:
                findings.append(Finding("dorset_heaths_400m", f"Within 400 m of Dorset Heathlands ({d:.0f} m)", "fatal", "confirmed", "The Dorset Heathlands Planning Framework does not permit a net increase in dwellings within 400 m of protected heathland.", "Dorset Heathlands Planning Framework SPD; Habitats Regs 2017", src(ev)))
            elif d <= 5000:
                obligations.append(Obligation("habitats", "Dorset Heathlands mitigation (HIP/SAMM)", "yes", f"Site is {d / 1000:.1f} km from Dorset Heathlands: per-dwelling contribution required.", "obligations.habitats"))
                applied = True
        d = dist("thames basin")
        if d is not None:
            if d < 400:
                findings.append(Finding("tbh_400m", f"Within 400 m of Thames Basin Heaths SPA ({d:.0f} m)", "fatal", "confirmed", "No net new residential development within the 400 m exclusion zone.", "Thames Basin Heaths SPA Delivery Framework", src(ev)))
            elif d <= 5000:
                obligations.append(Obligation("habitats", "Thames Basin Heaths SANG + SAMM", "yes", f"{d / 1000:.1f} km from the SPA: SANG capacity and SAMM contribution required.", "obligations.habitats"))
                applied = True
        d = dist("new forest")
        if d is not None and d <= 13_800:
            obligations.append(Obligation("habitats", "New Forest recreation mitigation", "likely", f"{d / 1000:.1f} km from New Forest SPA/SAC/Ramsar: LPAs within the 13.8 km zone seek per-dwelling mitigation.", "obligations.habitats"))
            applied = True
        d = dist("solent and southampton", "portsmouth harbour", "chichester and langstone")
        if d is not None and d <= 5_600:
            obligations.append(Obligation("habitats", "Bird Aware Solent contribution", "yes", f"{d / 1000:.1f} km from Solent SPAs: per-dwelling contribution (5.6 km zone).", "obligations.habitats"))
            applied = True
        d = dist("poole harbour")
        if d is not None and d <= 5_000:
            obligations.append(Obligation("habitats", "Poole Harbour recreation mitigation", "likely", f"{d / 1000:.1f} km from Poole Harbour SPA.", "obligations.habitats"))
            applied = True
        any_d = min([x for x in [f.nearest_distance("special-protection-area"), f.nearest_distance("special-area-of-conservation"), f.nearest_distance("ramsar")] if x is not None], default=None)
        if any_d is not None and any_d < 400 and not any(x.effect == "fatal" for x in findings):
            findings.append(Finding("habitats_near", f"Habitats site {any_d:.0f} m away", "moderate", "confirmed", "Habitats Regulations Assessment screening likely.", "Habitats Regs 2017", src(ev)))
        if not applied:
            obligations.append(Obligation("habitats", "Habitats-site recreation mitigation", "possible", "No standard mitigation zone identified from distance checks; LPA-specific schemes may still apply.", "obligations.habitats"))


base.register(England())

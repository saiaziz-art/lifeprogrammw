"""Small-home concepts, construction methods and parametric concept layouts.

These are FEASIBILITY CONCEPTS for testing fit and cost — not architectural drawings,
structural designs or Building Regulations submissions. An architect, structural engineer
and building control body are required before any of this is built.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

WALL_T = 0.40  # external wall build-up (m) used for GEA from GIA


@dataclass(frozen=True)
class Concept:
    key: str
    name: str
    gia_m2: float
    bedrooms: int
    bathrooms: int
    storeys: float  # 1, 1.5, 2
    description: str


CONCEPTS: dict[str, Concept] = {
    "cabin_40": Concept("cabin_40", "Compact cabin", 40, 1, 1, 1, "One-bedroom single-storey cabin: open living/kitchen, shower room, double bedroom. Simple pitched or mono-pitch roof."),
    "small_65": Concept("small_65", "Practical small home", 65, 2, 1, 1, "Two-bedroom single-storey home with central wet core, south-facing living space and a study nook."),
    "larger_90": Concept("larger_90", "Larger permanent home", 90, 3, 2, 2, "Three-bedroom compact two-storey home on a small footprint, stacked wet services, flexible ground-floor study/bedroom."),
}


@dataclass(frozen=True)
class Method:
    key: str
    name: str
    rate_key: str
    programme_months: tuple[float, float, float]  # on-site (low/base/high) for ~65 m²
    offsite_months: float
    max_module_width_m: float | None
    notes: str
    design_for_cost: list[str] = field(default_factory=list)


METHODS: dict[str, Method] = {
    "timber_frame": Method("timber_frame", "Timber frame (open/closed panel)", "build.timber_frame", (5, 7, 10), 1.5, None, "Fast weathertight shell; good thermal performance; widely available in the South.", ["Panel sizes on a 600 mm grid", "Simple rectangular plan, single ridge", "Engineered joists to avoid internal load-bearing walls"]),
    "sips": Method("sips", "Structural insulated panels (SIPs)", "build.sips", (4, 6, 9), 2, None, "Very airtight; needs crane for panels; limits on later alterations.", ["Standard panel widths (1.2 m)", "Minimise openings in gable panels", "Prefabricated roof cassettes"]),
    "modular": Method("modular", "Volumetric modular / prefab", "build.modular", (1.5, 3, 5), 4, 4.2, "Factory-built volumes; road transport limits module width (~4.2 m) and needs crane/HGV access.", ["Module widths ≤4.2 m for road transport", "Wet rooms finished in factory", "Repeat module types"]),
    "masonry": Method("masonry", "Conventional masonry (block & render/brick)", "build.masonry", (7, 10, 13), 0, None, "Familiar to lenders and planners; slower; wet trades weather-dependent.", ["Coordinate openings to block dimensions", "Avoid steel transfer beams", "Trussed rafter roof"]),
    "conversion": Method("conversion", "Refurbishment / conversion of existing building", "build.conversion", (8, 11, 15), 0, None, "Cost highly sensitive to structural condition; VAT reduced-rate may apply to some conversions.", ["Retain structure where survey confirms capacity", "Insulate inside to keep character", "Group services near existing drainage"]),
}

SPEC_FACTOR = {"essential": 0.88, "good": 1.0, "high": 1.22}


def footprint_dims(gia_m2: float, storeys: float, aspect: float = 1.6, module_width: float | None = None) -> dict[str, float]:
    """Internal floor plate and external footprint for a simple rectangular plan."""
    plate = gia_m2 / (1.0 if storeys <= 1 else (1.7 if storeys < 2 else 2.0))
    if module_width:
        w_int = module_width - 2 * 0.15  # module walls thinner per side; joined modules share faces
        n = max(1, math.ceil(plate / (w_int * 12)))
        width_int = n * w_int
        length_int = plate / width_int
    else:
        width_int = math.sqrt(plate / aspect)
        length_int = plate / width_int
    w_ext = width_int + 2 * WALL_T
    l_ext = length_int + 2 * WALL_T
    return {
        "plate_m2": round(plate, 1),
        "internal_width_m": round(width_int, 2),
        "internal_length_m": round(length_int, 2),
        "external_width_m": round(w_ext, 2),
        "external_length_m": round(l_ext, 2),
        "footprint_m2": round(w_ext * l_ext, 1),
        "gea_m2": round(w_ext * l_ext * (1.0 if storeys <= 1 else (1.7 if storeys < 2 else 2.0)), 1),
        "perimeter_m": round(2 * (w_ext + l_ext), 1),
    }


def concept_layout(concept: Concept, dims: dict[str, float]) -> list[dict[str, Any]]:
    """Room rectangles (metres, internal plate origin bottom-left, long axis along x = east–west).

    Living spaces on the south (y=0) side, bedrooms north, wet rooms in one central zone.
    """
    L, W = dims["internal_length_m"], dims["internal_width_m"]
    rooms: list[dict[str, Any]] = []

    def room(name: str, x: float, y: float, w: float, h: float, kind: str) -> None:
        rooms.append({"name": name, "x": round(x, 2), "y": round(y, 2), "w": round(w, 2), "h": round(h, 2), "area_m2": round(w * h, 1), "kind": kind, "floor": 0})

    if concept.key == "cabin_40":
        room("Living / kitchen", 0, 0, L * 0.58, W, "living")
        room("Shower room", L * 0.58, 0, L * 0.17, W * 0.5, "wet")
        room("Store / utility", L * 0.58, W * 0.5, L * 0.17, W * 0.5, "service")
        room("Bedroom", L * 0.75, 0, L * 0.25, W, "bedroom")
    elif concept.key == "small_65":
        south = W * 0.55
        room("Living / dining / kitchen", 0, 0, L * 0.62, south, "living")
        room("Study nook", L * 0.62, 0, L * 0.38, south, "living")
        room("Bedroom 1", 0, south, L * 0.4, W - south, "bedroom")
        room("Bathroom", L * 0.4, south, L * 0.2, W - south, "wet")
        room("Utility / entrance", L * 0.6, south, L * 0.12, W - south, "service")
        room("Bedroom 2", L * 0.72, south, L * 0.28, W - south, "bedroom")
    else:  # larger_90, two storeys: ground living, first floor bedrooms
        south = W * 0.58
        room("Kitchen / dining / living", 0, 0, L * 0.7, south, "living")
        room("Study / bedroom 3", L * 0.7, 0, L * 0.3, south, "bedroom")
        room("WC / utility", 0, south, L * 0.3, W - south, "wet")
        room("Hall & stair", L * 0.3, south, L * 0.25, W - south, "circulation")
        room("Store", L * 0.55, south, L * 0.45, W - south, "service")
        for r in [
            ("Bedroom 1", 0, 0, L * 0.5, south, "bedroom"),
            ("Bedroom 2", L * 0.5, 0, L * 0.5, south, "bedroom"),
            ("Bathroom", 0, south, L * 0.3, W - south, "wet"),
            ("Landing & stair", L * 0.3, south, L * 0.25, W - south, "circulation"),
            ("En-suite / storage", L * 0.55, south, L * 0.45, W - south, "wet"),
        ]:
            room(*r)
            rooms[-1]["floor"] = 1
    return rooms


def design_option(concept_key: str, method_key: str, gia_override: float | None = None, storeys_override: float | None = None) -> dict[str, Any]:
    c = CONCEPTS[concept_key]
    m = METHODS[method_key]
    gia = gia_override or c.gia_m2
    storeys = storeys_override or c.storeys
    if gia != c.gia_m2 or storeys != c.storeys:
        c = Concept(c.key, c.name, gia, c.bedrooms, c.bathrooms, storeys, c.description)
    dims = footprint_dims(gia, storeys, module_width=m.max_module_width_m)
    modules = None
    if m.max_module_width_m:
        plate = dims["plate_m2"]
        modules = max(1, math.ceil(gia / ((m.max_module_width_m - 0.3) * 12)))
        if storeys > 1:
            modules = max(modules, 2 * math.ceil(plate / ((m.max_module_width_m - 0.3) * 12)))
    scale = (gia / 65.0) ** 0.6
    prog = tuple(round(p * scale, 1) for p in m.programme_months)
    return {
        "concept": asdict(c),
        "method": {"key": m.key, "name": m.name, "notes": m.notes, "design_for_cost": m.design_for_cost},
        "gia_m2": gia,
        "storeys": storeys,
        "dims": dims,
        "modules": modules,
        "programme_months": {"low": prog[0], "base": prog[1], "high": prog[2], "offsite_overlap": m.offsite_months},
        "rooms": concept_layout(c, dims),
        "disclaimer": "Feasibility concept only — not an architectural drawing, structural design or Building Regulations submission.",
        "professionals_required": [
            "Architect / architectural designer (design, planning drawings)",
            "Structural engineer (foundations, frame, any retained structure)",
            "Building control (local authority or registered approver)",
            "Drainage/percolation designer where off-mains",
            "SAP assessor (Part L / EPC) and air-tightness tester",
        ],
    }

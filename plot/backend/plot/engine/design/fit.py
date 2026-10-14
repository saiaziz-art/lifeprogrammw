"""Does a concept physically fit? Tests rotated footprints inside the setback zone.

Uses the site boundary when known (INSPIRE extent or user-drawn), otherwise falls back to
an area-only check with lower confidence. 'Fits' means geometric feasibility only — planning
acceptability (character, amenity, overlooking) is a separate professional judgement.
"""

from __future__ import annotations

import math
from typing import Any

from shapely import affinity
from shapely.geometry import Polygon, box, mapping

from plot import geo


def _rect(cx: float, cy: float, length: float, width: float, angle_deg: float) -> Polygon:
    r = box(cx - length / 2, cy - width / 2, cx + length / 2, cy + width / 2)
    return affinity.rotate(r, angle_deg, origin=(cx, cy))


def test_fit(
    boundary_wkt: str | None,
    area_m2: float | None,
    dims: dict[str, float],
    *,
    setback_m: float = 2.5,
    parking_spaces: int = 2,
    min_garden_m2: float = 80,
) -> dict[str, Any]:
    footprint = dims["footprint_m2"]
    L, W = dims["external_length_m"], dims["external_width_m"]
    parking = parking_spaces * 12.5 + 20  # bays + turning
    access_allow = 45.0

    boundary = geo.parse_wkt(boundary_wkt) if boundary_wkt else None
    if boundary is not None and boundary.geom_type in ("Polygon", "MultiPolygon"):
        site_bng = geo.to_bng(boundary)
        if site_bng.geom_type == "MultiPolygon":
            site_bng = max(site_bng.geoms, key=lambda g: g.area)
        c = site_bng.centroid
        local = affinity.translate(site_bng, -c.x, -c.y)
        buildable = local.buffer(-setback_m)
        site_area = local.area
        if buildable.is_empty:
            return {"fits": False, "method": "boundary", "reason": f"Nothing left after {setback_m} m setbacks.", "site_area_m2": round(site_area)}
        minx, miny, maxx, maxy = buildable.bounds
        best = None
        step = max(0.75, math.sqrt(buildable.area) / 30)  # ≤ ~30×30 samples per angle
        for angle in range(0, 180, 15):
            orient = math.cos(math.radians(angle)) ** 2  # 1.0 = long elevation faces due south
            y = miny
            while y <= maxy:
                x = minx
                while x <= maxx:
                    r = _rect(x, y, L, W, angle)
                    if buildable.contains(r):
                        north = (y - miny) / max(1e-6, (maxy - miny))  # house to the north → garden to the south
                        s = orient + 0.35 * north
                        if best is None or s > best[0]:
                            best = (s, r, angle)
                    x += step
                y += step
        if best is None:
            return {
                "fits": False,
                "method": "boundary",
                "reason": f"A {L:.1f} × {W:.1f} m footprint does not fit inside the {setback_m} m setback zone at any tested orientation.",
                "site_area_m2": round(site_area),
                "geometry": {"site": mapping(local), "buildable": mapping(buildable)},
            }
        _, rect, angle = best
        garden = site_area - footprint - parking - access_allow
        fits = garden >= min_garden_m2
        aspect = "south" if angle in (0, 15, 165) else ("south-west/south-east" if angle in (30, 150) else "east/west")
        return {
            "fits": fits,
            "method": "boundary",
            "reason": None if fits else f"Footprint fits but only ~{garden:.0f} m² remains for garden (target {min_garden_m2:.0f} m²).",
            "site_area_m2": round(site_area),
            "garden_m2": round(garden),
            "orientation_deg": angle,
            "main_aspect": aspect,
            "coverage_pct": round(100 * footprint / site_area, 1),
            "setback_m": setback_m,
            "geometry": {"site": mapping(local), "buildable": mapping(buildable), "house": mapping(rect)},
            "house_wgs84": mapping(geo.from_bng(affinity.translate(rect, c.x, c.y))),
            "confidence": 0.65,
        }
    if area_m2:
        needed = footprint + parking + access_allow + min_garden_m2
        # setbacks waste perimeter land on small plots; approximate square plot
        side = math.sqrt(area_m2)
        usable_side = side - 2 * setback_m
        geometric = usable_side >= W and usable_side * usable_side >= footprint
        fits = area_m2 >= needed and geometric
        return {
            "fits": fits,
            "method": "area_only",
            "reason": None if fits else f"Site area ≈{area_m2:,.0f} m² vs ≈{needed:,.0f} m² needed (house, parking, access, garden).",
            "site_area_m2": round(area_m2),
            "garden_m2": round(area_m2 - footprint - parking - access_allow),
            "coverage_pct": round(100 * footprint / area_m2, 1),
            "note": "Boundary unknown — fit estimated from area only; plot shape and access position could change this.",
            "confidence": 0.35,
        }
    return {"fits": None, "method": "unknown", "reason": "Neither boundary nor area known.", "confidence": 0.0}

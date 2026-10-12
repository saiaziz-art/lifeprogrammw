"""Pure geometry analysis of a prospecting area (British National Grid metres).

Inputs are lawful open layers: HM Land Registry INSPIRE title extents, building footprints
(OS OpenMap Local or OpenStreetMap), road centrelines, and planning constraint polygons.
Outputs are *candidates* — places where a second dwelling, a conversion or a small plot
might be physically and plausibly possible. Nothing here says who owns the land or that it
is for sale, and every inference (e.g. "this building is probably the house") is labelled.

Garden-subdivision tests: road frontage and realistic independent access, a notional new
plot that fits in the garden outside an 8 m amenity zone around the existing house, the
garden the owner would retain, separation/overlooking distance, plot size versus
neighbouring plots (character), trees/conservation constraints, and road class (highway
safety).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.strtree import STRtree

HOUSE_TAGS = {"house", "detached", "semidetached_house", "bungalow", "residential", "terrace", "yes", "farmhouse", "cottage", "dwelling"}
OUTBUILDING_TAGS = {"barn", "farm_auxiliary", "stable", "shed", "garage", "garages", "industrial", "warehouse", "commercial", "storage_tank", "greenhouse", "cowshed", "sty", "outbuilding", "workshop", "retail", "service"}
DERELICT_TAGS = {"ruins", "disused", "abandoned"}
MINOR_ROADS = {"tertiary", "unclassified", "residential", "living_street", "tertiary_link"}
CLASSIFIED_ROADS = {"primary", "secondary", "trunk", "primary_link", "secondary_link", "trunk_link"}
LANE_ROADS = {"service", "track"}

PLOT_SIZES = [(10.0, 24.0), (12.0, 25.0), (14.0, 28.0)]  # width × depth of a notional new plot
AMENITY_ZONE_M = 8.0
MIN_SEPARATION_M = 10.0
ACCESS_STRIP_HALF_WIDTH = 1.8  # 3.6 m driveway


@dataclass
class Feature:
    id: str
    geom: BaseGeometry
    tags: dict[str, Any] = field(default_factory=dict)


@dataclass
class AreaData:
    titles: list[Feature]
    buildings: list[Feature]
    roads: list[Feature]
    constraints: dict[str, list[Feature]] = field(default_factory=dict)
    built_up: list[Feature] = field(default_factory=list)
    applications: list[Feature] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)  # layer -> source label


@dataclass
class Candidate:
    type: str
    title_id: str
    title_geom: Polygon
    plot_geom: Polygon  # indicative new plot (or whole parcel)
    house_geom: Polygon | None
    access: dict[str, Any]
    metrics: dict[str, Any]
    constraints: list[dict[str, Any]]
    signals: list[str]
    flags: list[dict[str, Any]]
    prelim_score: float = 0.0
    access_geom: BaseGeometry | None = None

    def summary(self) -> dict[str, Any]:
        return {"type": self.type, "title_id": self.title_id, "access": self.access, "metrics": self.metrics, "constraints": self.constraints, "signals": self.signals, "flags": self.flags}


def _largest(g: BaseGeometry) -> Polygon | None:
    if isinstance(g, Polygon):
        return g
    if isinstance(g, MultiPolygon) and g.geoms:
        return max(g.geoms, key=lambda x: x.area)
    return None


def building_kind(b: Feature) -> str:
    tag = str(b.tags.get("building", "yes")).lower()
    a = b.geom.area
    if tag in DERELICT_TAGS or b.tags.get("ruins") == "yes" or b.tags.get("disused") == "yes" or b.tags.get("abandoned") == "yes" or "disused:building" in b.tags:
        return "derelict"
    if tag in OUTBUILDING_TAGS:
        return "outbuilding"
    if tag in HOUSE_TAGS and 40 <= a <= 450:
        return "house"
    if a < 40:
        return "minor"
    if a > 450:
        return "large"
    return "outbuilding"


def _bearing(line: LineString, near: Point) -> float:
    """Angle (degrees) of the road segment closest to `near`."""
    coords = list(line.coords)
    best, ang = 1e18, 0.0
    for (x1, y1), (x2, y2) in zip(coords, coords[1:], strict=False):
        seg = LineString([(x1, y1), (x2, y2)])
        d = seg.distance(near)
        if d < best:
            best, ang = d, math.degrees(math.atan2(y2 - y1, x2 - x1))
    return ang


def _rect(cx: float, cy: float, w: float, d: float, angle: float) -> Polygon:
    r = box(cx - w / 2, cy - d / 2, cx + w / 2, cy + d / 2)
    return affinity.rotate(r, angle, origin=(cx, cy))


class Analyser:
    def __init__(self, data: AreaData, types: list[str] | None = None):
        self.d = data
        self.types = set(types or ["garden_subdivision", "edge_paddock", "outbuilding", "replacement", "derelict", "planning_history"])
        self.b_tree = STRtree([b.geom for b in data.buildings]) if data.buildings else None
        self.r_tree = STRtree([r.geom for r in data.roads]) if data.roads else None
        self.t_tree = STRtree([t.geom for t in data.titles]) if data.titles else None
        self.bua = unary_union([f.geom for f in data.built_up]) if data.built_up else None
        self._plot_stats: dict[str, float] = {}

    # ---------------------------------------------------------------- helpers

    def buildings_in(self, poly: Polygon) -> list[Feature]:
        if not self.b_tree:
            return []
        out = []
        for i in self.b_tree.query(poly):
            b = self.d.buildings[int(i)]
            inter = b.geom.intersection(poly).area
            if b.geom.area > 0 and inter >= 0.5 * b.geom.area:
                out.append(b)
        return out

    def roads_near(self, poly: Polygon, dist: float = 15.0) -> list[Feature]:
        if not self.r_tree:
            return []
        return [self.d.roads[int(i)] for i in self.r_tree.query(poly.buffer(dist))]

    def in_bua(self, g: BaseGeometry) -> bool:
        return bool(self.bua is not None and self.bua.contains(g.centroid))

    def dist_bua(self, g: BaseGeometry) -> float | None:
        if self.bua is None:
            return None
        return 0.0 if self.bua.intersects(g) else float(self.bua.distance(g))

    def neighbour_median(self, poly: Polygon) -> float | None:
        if not self.t_tree:
            return None
        areas = []
        for i in self.t_tree.query(poly.buffer(120)):
            t = self.d.titles[int(i)]
            if t.geom.equals(poly):
                continue
            g = _largest(t.geom)
            if g is None or not 150 <= g.area <= 5000:
                continue
            if len([b for b in self.buildings_in(g) if building_kind(b) == "house"]) == 1:
                areas.append(g.area)
        return float(statistics.median(areas)) if len(areas) >= 4 else None

    def constraint_hits(self, g: BaseGeometry) -> list[dict[str, Any]]:
        hits = []
        for ds, feats in self.d.constraints.items():
            for f in feats:
                if f.geom.intersects(g):
                    hits.append({"dataset": ds, "name": f.tags.get("name"), "entity": f.tags.get("entity")})
        return hits

    def frontage(self, poly: Polygon) -> list[dict[str, Any]]:
        out = []
        ring = poly.exterior
        for r in self.roads_near(poly, 15):
            hw = str(r.tags.get("highway", ""))
            if hw in ("footway", "path", "cycleway", "bridleway", "steps", "pedestrian"):
                continue
            seg = ring.intersection(r.geom.buffer(12))
            if seg.is_empty:
                continue
            # count only the boundary that faces the road (within 2 m of its closest approach),
            # not the side boundaries shared with neighbours
            seg = ring.intersection(r.geom.buffer(seg.distance(r.geom) + 2.0))
            length = seg.length
            if length < 4:
                continue
            mid = seg.interpolate(0.5, normalized=True) if not seg.is_empty else poly.centroid
            out.append({"road_id": r.id, "name": r.tags.get("name"), "highway": hw, "length_m": round(length, 1), "geom": seg, "mid": mid, "road": r})
        out.sort(key=lambda f: (f["highway"] in MINOR_ROADS, f["length_m"]), reverse=True)
        return out

    # ---------------------------------------------------------------- analysis

    def run(self) -> list[Candidate]:
        cands: list[Candidate] = []
        for t in self.d.titles:
            g = _largest(t.geom)
            if g is None or not g.is_valid:
                continue
            c = self.analyse_title(t, g)
            if c is not None:
                cands.append(c)
        return cands

    def analyse_title(self, t: Feature, P: Polygon) -> Candidate | None:
        A = P.area
        if A < 250 or A > 30_000:
            return None
        inside = self.buildings_in(P)
        houses = [b for b in inside if building_kind(b) == "house"]
        others = [b for b in inside if building_kind(b) in ("outbuilding", "large")]
        derelict = [b for b in inside if building_kind(b) == "derelict"]
        front = self.frontage(P)
        bua_d = self.dist_bua(P)
        in_bua = self.in_bua(P)
        apps = [a for a in self.d.applications if P.contains(a.geom)]
        apps_res = [a for a in apps if a.tags.get("residential")]

        if "derelict" in self.types and (derelict or any(h["dataset"] == "heritage-at-risk" for h in self.constraint_hits(P))):
            return self._simple(t, P, "derelict", front, in_bua, bua_d, houses, signals=["derelict_or_at_risk_structure"], buildings=derelict)
        if "planning_history" in self.types and apps_res:
            sig = []
            for a in apps_res:
                dec = str(a.tags.get("decision") or "").lower()
                if "refus" in dec:
                    sig.append("residential_application_refused")
                elif "withdraw" in dec:
                    sig.append("residential_application_withdrawn")
                elif a.tags.get("possibly_lapsed"):
                    sig.append("residential_permission_possibly_lapsed")
            if sig:
                return self._simple(t, P, "planning_history", front, in_bua, bua_d, houses, signals=sorted(set(sig)), extra={"applications": [a.tags for a in apps_res][:5]})
        if len(houses) == 1 and "garden_subdivision" in self.types and 600 <= A <= 6000 and (in_bua or (bua_d is not None and bua_d <= 50)):
            c = self.garden(t, P, houses[0], others, front, in_bua, bua_d)
            if c is not None:
                return c
        if len(houses) == 1 and "replacement" in self.types and houses[0].geom.area <= 100 and A >= 1000:
            return self._simple(t, P, "replacement", front, in_bua, bua_d, houses, signals=["small_dwelling_on_generous_plot"])
        if not houses and "edge_paddock" in self.types and 600 <= A <= 20_000 and not in_bua and bua_d is not None and bua_d <= 60:
            cover = sum(b.geom.area for b in inside) / A
            if cover < 0.1 and front:
                return self._simple(t, P, "edge_paddock", front, in_bua, bua_d, houses, signals=["undeveloped_parcel_adjoining_settlement"])
        if "outbuilding" in self.types and others and (bua_d is not None and bua_d <= 500):
            big = [b for b in others if b.geom.area >= 80]
            if big and len(houses) <= 1:
                return self._simple(t, P, "outbuilding", front, in_bua, bua_d, houses, signals=["sizeable_non_residential_building"], buildings=big)
        return None

    def _access(self, front: list[dict[str, Any]]) -> dict[str, Any]:
        if not front:
            return {"type": "none_identified", "frontage_m": 0}
        f = front[0]
        names = {x["name"] for x in front if x["name"] and x["length_m"] >= 8}
        typ = "independent_frontage" if f["highway"] in MINOR_ROADS else ("classified_road_frontage" if f["highway"] in CLASSIFIED_ROADS else ("private_lane" if f["highway"] in LANE_ROADS else "independent_frontage"))
        return {"type": typ, "frontage_m": f["length_m"], "road_name": f["name"], "road_class": f["highway"], "corner": len(names) >= 2}

    def _simple(self, t, P, typ, front, in_bua, bua_d, houses, signals, buildings=None, extra=None) -> Candidate:
        acc = self._access(front)
        plot = P
        if typ == "edge_paddock" and front:
            p2 = self._place_on_frontage(P, P.buffer(-1.0), front[0], sizes=[(14.0, 28.0), (12.0, 25.0)])
            if p2 is not None:
                plot = p2
        metrics = {
            "parcel_area_m2": round(P.area),
            "plot_area_m2": round(plot.area),
            "in_built_up_area": in_bua,
            "distance_to_built_up_m": None if bua_d is None else round(bua_d),
            "house_footprint_m2": round(houses[0].geom.area) if houses else None,
            "buildings": [{"kind": building_kind(b), "area_m2": round(b.geom.area), "tag": b.tags.get("building")} for b in (buildings or [])][:5],
        } | (extra or {})
        flags = []
        if acc["type"] == "none_identified":
            flags.append({"code": "no_access", "severity": "fatal", "title": "No road frontage identified"})
        cons = self.constraint_hits(plot)
        return Candidate(typ, t.id, P, plot, houses[0].geom if houses else None, acc, metrics, cons, signals, flags)

    def _place_on_frontage(self, P: Polygon, free: BaseGeometry, f: dict[str, Any], sizes=PLOT_SIZES) -> Polygon | None:
        seg = f["geom"]
        if seg.is_empty:
            return None
        ang = _bearing(f["road"].geom, f["mid"])
        for w, d in sizes:
            steps = max(1, int(seg.length // 2))
            for i in range(steps + 1):
                pt = seg.interpolate(i / steps, normalized=True)
                # move inward (towards the parcel centroid side) by half the depth
                for sign in (1, -1):
                    nx = -math.sin(math.radians(ang)) * sign
                    ny = math.cos(math.radians(ang)) * sign
                    cx, cy = pt.x + nx * (d / 2 + 0.5), pt.y + ny * (d / 2 + 0.5)
                    r = _rect(cx, cy, w, d, ang)
                    if r.difference(free).area <= 0.02 * r.area:
                        return r
        return None

    def garden(self, t: Feature, P: Polygon, house: Feature, others: list[Feature], front, in_bua: bool, bua_d) -> Candidate | None:
        A = P.area
        H = house.geom
        median = self.neighbour_median(P)
        large_ratio = (A / median) if median else None
        amenity = H.buffer(AMENITY_ZONE_M)
        free = P.buffer(-1.0).difference(unary_union([amenity] + [o.geom.buffer(1.5) for o in others]))
        if free.is_empty or free.area < 200:
            return None
        plot, access_geom, access = None, None, None
        for f in front:
            if f["highway"] in CLASSIFIED_ROADS and len(front) > 1:
                continue  # prefer a minor road where there is a choice
            plot = self._place_on_frontage(P, free, f)
            if plot is not None:
                access = self._access([f] + [x for x in front if x is not f])
                access["position"] = "side_garden" if H.distance(f["geom"]) < 15 else "own_frontage"
                break
        if plot is None and front:
            # backland: plot in the rear garden reached by a 3.6 m strip from the frontage
            plot = self._place_anywhere(free)
            if plot is not None:
                found = self._access_strip(P, plot, H, others, front[0])
                if found is None:
                    return None
                line, access_geom = found
                access = self._access(front)
                access["type"] = "backland_strip"
                access["strip_length_m"] = round(line.length, 1)
        if plot is None:
            return None
        retained_garden = A - plot.area - H.area - (access_geom.area if access_geom is not None else 0)
        garden_before = A - H.area
        ratio = retained_garden / garden_before if garden_before > 0 else 0
        dwelling = _rect(plot.centroid.x, plot.centroid.y, 8.0, 10.0, 0)
        separation = float(dwelling.distance(H))
        flags = []
        if retained_garden < max(120.0, 0.35 * garden_before):
            return None  # would leave the owner too little garden — not worth proposing
        if separation < MIN_SEPARATION_M:
            return None
        signals = ["single_dwelling_large_plot"]
        if large_ratio and large_ratio >= 1.6:
            signals.append("garden_much_larger_than_neighbours")
        if access.get("corner"):
            signals.append("corner_plot")
        if access["type"] == "classified_road_frontage":
            flags.append({"code": "classified_road", "severity": "major", "title": "Access onto a classified road — highway authority likely to require visibility splays"})
        if access["type"] == "backland_strip":
            flags.append({"code": "backland", "severity": "major", "title": "Backland plot reached by an access strip — tandem development is often resisted"})
        character = (plot.area / median) if median else None
        if character is not None and character < 0.55:
            flags.append({"code": "character", "severity": "moderate", "title": f"New plot ~{character * 100:.0f}% of the typical neighbouring plot — possible character objection"})
        cons = self.constraint_hits(plot)
        metrics = {
            "parcel_area_m2": round(A),
            "house_footprint_m2": round(H.area),
            "plot_area_m2": round(plot.area),
            "plot_width_m": round(min(_rect_sides(plot)), 1),
            "plot_depth_m": round(max(_rect_sides(plot)), 1),
            "retained_garden_m2": round(retained_garden),
            "retained_garden_ratio": round(ratio, 2),
            "separation_to_house_m": round(separation, 1),
            "neighbour_median_plot_m2": round(median) if median else None,
            "parcel_vs_neighbours": round(large_ratio, 2) if large_ratio else None,
            "plot_vs_neighbours": round(character, 2) if character else None,
            "in_built_up_area": in_bua,
            "distance_to_built_up_m": None if bua_d is None else round(bua_d),
            "house_identification": "largest residential-sized building on the parcel (inferred from footprint data, not verified)",
        }
        return Candidate("garden_subdivision", t.id, P, plot, H, access, metrics, cons, signals, flags, access_geom=access_geom)

    def _access_strip(self, P: Polygon, plot: Polygon, house: BaseGeometry, others: list[Feature], f: dict[str, Any]) -> tuple[LineString, BaseGeometry] | None:
        """Shortest 3.6 m driveway from the road edge of the parcel to the new plot that stays
        inside the parcel and clear of the house (1 m) and outbuildings."""
        seg, road = f["geom"], f["road"].geom
        if seg.is_empty:
            return None
        dmin = seg.distance(road)
        blocked = unary_union([house.buffer(1.0)] + [o.geom.buffer(0.5) for o in others])
        n = max(2, int(seg.length))
        best: tuple[LineString, BaseGeometry] | None = None
        for i in range(n + 1):
            fp = seg.interpolate(i / n, normalized=True)
            if fp.distance(road) > dmin + 1.5:
                continue  # only the part of the boundary that actually meets the road
            a = plot.exterior.interpolate(plot.exterior.project(fp))
            line = LineString([a, fp])
            if line.length < 1:
                continue
            strip = line.buffer(ACCESS_STRIP_HALF_WIDTH, cap_style=2)
            if strip.intersects(blocked) or strip.difference(P.buffer(0.5)).area > 0.1 * strip.area:
                continue
            if best is None or line.length < best[0].length:
                best = (line, strip)
        return best

    def _place_anywhere(self, free: BaseGeometry) -> Polygon | None:
        fp = _largest(free) if not isinstance(free, Polygon) else free
        if fp is None:
            return None
        minx, miny, maxx, maxy = fp.bounds
        step = max(1.5, math.sqrt(fp.area) / 15)
        for w, d in PLOT_SIZES:
            for ang in (0, 30, 60, 90, 120, 150):
                y = miny
                while y <= maxy:
                    x = minx
                    while x <= maxx:
                        r = _rect(x, y, w, d, ang)
                        if fp.contains(r):
                            return r
                        x += step
                    y += step
        return None


def _rect_sides(p: Polygon) -> tuple[float, float]:
    c = list(p.minimum_rotated_rectangle.exterior.coords)
    return (LineString(c[0:2]).length, LineString(c[1:3]).length)

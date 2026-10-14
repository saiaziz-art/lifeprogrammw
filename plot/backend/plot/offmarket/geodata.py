"""Lawful geodata for a prospecting area.

- Title extents: HM Land Registry INSPIRE Index Polygons via planning.data.gov.uk
  (`title-boundary`), OGL + OS attribution.
- Buildings & roads, in order of preference:
  1. OS OpenMap – Local imported into `ref_features` (OGL; `plot import-os-openmap <gpkg>`),
  2. OpenStreetMap via the Overpass API (ODbL; attribution "© OpenStreetMap contributors").
- Constraints, built-up areas, heritage at risk and planning applications from
  planning.data.gov.uk.

Every layer records its source so candidates show what they were derived from.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any

from geoalchemy2.shape import from_shape
from shapely import wkb
from shapely.geometry import LineString, Polygon
from sqlalchemy import text
from sqlalchemy.orm import Session

from plot import geo
from plot.models import RefFeature, utcnow
from plot.offmarket.analyse import AreaData, Feature
from plot.sources.http import PublicHttp
from plot.sources.planning_data import PlanningDataClient

OVERPASS_DEFAULT = "https://overpass-api.de/api/interpreter"
OSM_ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0"
CONSTRAINT_DATASETS = [
    "conservation-area", "tree-preservation-zone", "tree", "listed-building-outline", "listed-building", "green-belt", "national-park",
    "area-of-outstanding-natural-beauty", "flood-risk-zone", "ancient-woodland", "site-of-special-scientific-interest",
    "special-protection-area", "special-area-of-conservation", "scheduled-monument", "heritage-at-risk", "article-4-direction-area",
]
RESIDENTIAL_WORDS = ("dwelling", "house", "bungalow", "residential", "chalet", "annexe", "home")


def fetch_titles(client: PlanningDataClient, circle_wkt: str) -> list[dict[str, Any]]:
    return client.search(["title-boundary"], geometry_wkt=circle_wkt, cache_ttl=dt.timedelta(days=30), max_pages=24)


def overpass_query(bbox: tuple[float, float, float, float]) -> str:
    w, s, e, n = bbox
    b = f"{s:.6f},{w:.6f},{n:.6f},{e:.6f}"
    return f"""[out:json][timeout:90];
(
  way["building"]({b});
  way["highway"~"^(trunk|primary|secondary|tertiary|unclassified|residential|living_street|service|track|primary_link|secondary_link|tertiary_link)$"]({b});
);
out tags geom;"""


def parse_overpass(data: dict[str, Any]) -> tuple[list[Feature], list[Feature]]:
    buildings, roads = [], []
    for el in data.get("elements", []):
        g = el.get("geometry")
        tags = el.get("tags", {})
        if not g or len(g) < 2:
            continue
        coords = [(p["lon"], p["lat"]) for p in g]
        if "building" in tags and len(coords) >= 4 and coords[0] == coords[-1]:
            poly = Polygon(coords)
            if poly.is_valid and poly.area > 0:
                buildings.append(Feature(f"osm:way/{el.get('id')}", geo.to_bng(poly), tags))
        elif "highway" in tags:
            roads.append(Feature(f"osm:way/{el.get('id')}", geo.to_bng(LineString(coords)), tags))
    return buildings, roads


def buildings_roads_from_db(db: Session, circle_wkt: str) -> tuple[list[Feature], list[Feature]]:
    rows = db.execute(
        text("SELECT dataset, ref, ST_AsBinary(geom) AS g, props FROM ref_features WHERE dataset IN ('os_building','os_road') AND ST_Intersects(geom, ST_GeomFromText(:w, 4326))"),
        {"w": circle_wkt},
    ).all()
    b, r = [], []
    for ds, ref, g, props in rows:
        shp = geo.to_bng(wkb.loads(bytes(g)))
        (b if ds == "os_building" else r).append(Feature(f"os:{ref}", shp, props or {}))
    return b, r


def area_data(db: Session, lon: float, lat: float, radius_m: int, *, overpass_url: str = OVERPASS_DEFAULT) -> AreaData:
    """Assemble all layers for one prospecting area. Raises SourceError if a core layer fails."""
    circle = geo.circle_wkt(lon, lat, radius_m, resolution=16)
    sources: dict[str, str] = {}
    with PublicHttp(db, timeout=120) as http:
        client = PlanningDataClient(http)
        titles_raw = fetch_titles(client, circle)
        titles = []
        for e in titles_raw:
            g = geo.parse_wkt(e.get("geometry"))
            if g is not None:
                titles.append(Feature(str(e.get("reference")), geo.to_bng(g), {"entity": e.get("entity")}))
        sources["titles"] = "HM Land Registry INSPIRE Index Polygons via planning.data.gov.uk (OGL; © Crown copyright, OS 100026316)"
        buildings, roads = buildings_roads_from_db(db, circle)
        if buildings:
            sources["buildings"] = "OS OpenMap – Local (OGL), imported"
        else:
            minx, miny, maxx, maxy = geo.parse_wkt(circle).bounds
            data = http.post_form_json(overpass_url, {"data": overpass_query((minx, miny, maxx, maxy))})
            buildings, roads = parse_overpass(data)
            sources["buildings"] = sources["roads"] = f"OpenStreetMap via Overpass ({OSM_ATTRIBUTION})"
        cons_raw = client.search(CONSTRAINT_DATASETS + ["built-up-area"], geometry_wkt=circle, cache_ttl=dt.timedelta(days=14), max_pages=10)
        constraints: dict[str, list[Feature]] = {}
        bua: list[Feature] = []
        for e in cons_raw:
            if e.get("end-date"):
                continue
            g = geo.parse_wkt(e.get("geometry")) or geo.parse_wkt(e.get("point"))
            if g is None:
                continue
            f = Feature(str(e.get("entity")), geo.to_bng(g if g.geom_type != "Point" else g.buffer(0.00004)), {"name": e.get("name"), "entity": e.get("entity")})
            if e.get("dataset") == "built-up-area":
                bua.append(f)
            else:
                constraints.setdefault(e["dataset"], []).append(f)
        sources["constraints"] = "planning.data.gov.uk (OGL; dataset attributions apply)"
        apps_raw = client.search(["planning-application"], geometry_wkt=circle, cache_ttl=dt.timedelta(days=3), max_pages=6, extra=[("exclude_field", "geometry")])
    apps = []
    today = dt.date.today()
    for e in apps_raw:
        pt = geo.parse_wkt(e.get("point"))
        if pt is None:
            continue
        desc = str(e.get("description") or "").lower()
        decided = e.get("decision-date") or ""
        lapsed = False
        if decided and "approv" in str(e.get("planning-decision") or "").lower():
            try:
                lapsed = (today - dt.date.fromisoformat(decided[:10])).days > 3 * 365
            except ValueError:
                lapsed = False
        apps.append(Feature(str(e.get("reference")), geo.to_bng(pt), {"reference": e.get("reference"), "description": e.get("description"), "decision": e.get("planning-decision"), "decision_date": decided, "residential": any(w in desc for w in RESIDENTIAL_WORDS), "possibly_lapsed": lapsed, "url": e.get("documentation-url")}))
    sources["applications"] = "planning.data.gov.uk planning-application (alpha, partial coverage)"
    return AreaData(titles=titles, buildings=buildings, roads=roads, constraints=constraints, built_up=bua, applications=apps, sources=sources)


# --------------------------------------------------------------------------- OS OpenMap Local import


def _gpkg_geom(blob: bytes):
    """Decode a GeoPackage geometry blob (header + WKB)."""
    if blob[:2] != b"GP":
        return wkb.loads(blob)
    flags = blob[3]
    env = (flags >> 1) & 0b111
    env_len = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(env, 0)
    return wkb.loads(blob[8 + env_len :])


def import_os_openmap(db: Session, path: str, bbox: tuple[float, float, float, float] | None = None) -> dict[str, int]:
    """Import Building and Road layers from an OS OpenMap – Local GeoPackage (EPSG:27700)."""
    con = sqlite3.connect(path)
    layers = {r[0]: r[1] for r in con.execute("SELECT table_name, data_type FROM gpkg_contents")}
    counts = {"os_building": 0, "os_road": 0}
    for table in layers:
        low = table.lower()
        ds = "os_building" if "building" in low else ("os_road" if low in ("road", "roads") or low.endswith("road") else None)
        if ds is None:
            continue
        geom_col = con.execute("SELECT column_name FROM gpkg_geometry_columns WHERE table_name=?", (table,)).fetchone()[0]
        cols = [c[1] for c in con.execute(f'PRAGMA table_info("{table}")')]
        name_col = next((c for c in cols if c.lower() in ("distname", "distinctivename", "name", "roadname")), None)
        class_col = next((c for c in cols if c.lower() in ("classification", "roadclassification", "featuredescription", "featurecode")), None)
        sel = ", ".join(f'"{c}"' for c in [geom_col, name_col, class_col] if c)
        for row in con.execute(f'SELECT {sel} FROM "{table}"'):
            g = _gpkg_geom(row[0])
            g84 = geo.from_bng(g)
            if bbox and not g84.intersects(Polygon.from_bounds(*bbox)):
                continue
            props: dict[str, Any] = {}
            if ds == "os_road":
                props = {"name": row[1] if name_col else None, "highway": _os_road_class(row[2] if class_col else None)}
            else:
                props = {"building": "yes"}
            db.add(RefFeature(dataset=ds, ref=f"{table}:{counts[ds]}", name=(props.get("name") or ds)[:300], geom=from_shape(g84, srid=4326), props=props, source_id=None, fetched_at=utcnow()))
            counts[ds] += 1
    db.flush()
    return counts


def _os_road_class(v: Any) -> str:
    s = str(v or "").lower()
    if "a road" in s or s.startswith("a "):
        return "primary"
    if "b road" in s:
        return "secondary"
    if "minor" in s or "local" in s:
        return "residential"
    if "private" in s:
        return "service"
    return "unclassified"



"""Geometry helpers. Metric operations are done in British National Grid (EPSG:27700).

Note: without the OSTN15 grid installed, pyproj uses a Helmert approximation for
WGS84<->OSGB36, accurate to a few metres — adequate for screening, not for boundaries.
"""

from __future__ import annotations

import math
from functools import lru_cache

import shapely
from pyproj import Transformer
from shapely import wkt as shapely_wkt
from shapely.geometry import Point, Polygon, box, mapping
from shapely.geometry.base import BaseGeometry


@lru_cache
def _to_bng() -> Transformer:
    return Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)


@lru_cache
def _from_bng() -> Transformer:
    return Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)


def to_bng(geom: BaseGeometry) -> BaseGeometry:
    return shapely.transform(geom, lambda x, y: _to_bng().transform(x, y), interleaved=False)


def from_bng(geom: BaseGeometry) -> BaseGeometry:
    return shapely.transform(geom, lambda x, y: _from_bng().transform(x, y), interleaved=False)


def bng_to_lonlat(x: float, y: float) -> tuple[float, float]:
    lon, lat = _from_bng().transform(x, y)
    return lon, lat


def lonlat_to_bng(lon: float, lat: float) -> tuple[float, float]:
    return _to_bng().transform(lon, lat)


def parse_wkt(text: str | None) -> BaseGeometry | None:
    if not text:
        return None
    try:
        g = shapely_wkt.loads(text)
    except Exception:  # noqa: BLE001 - malformed external WKT
        return None
    return g if not g.is_empty else None


def area_m2(geom_wgs84: BaseGeometry) -> float:
    return float(to_bng(geom_wgs84).area)


def distance_m(a: BaseGeometry, b: BaseGeometry) -> float:
    return float(to_bng(a).distance(to_bng(b)))


def buffer_m(geom_wgs84: BaseGeometry, metres: float, resolution: int = 8) -> BaseGeometry:
    return from_bng(to_bng(geom_wgs84).buffer(metres, resolution))


def circle_wkt(lon: float, lat: float, metres: float, resolution: int = 8) -> str:
    return buffer_m(Point(lon, lat), metres, resolution).wkt


def bbox_polygon(b: tuple[float, float, float, float]) -> Polygon:
    return box(*b)


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def geojson(geom: BaseGeometry) -> dict:
    return mapping(geom)


def simplify_for_display(geom: BaseGeometry, tolerance_m: float = 2.0) -> BaseGeometry:
    return from_bng(to_bng(geom).simplify(tolerance_m, preserve_topology=True))

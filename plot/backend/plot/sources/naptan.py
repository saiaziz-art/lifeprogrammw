"""DfT NaPTAN — national rail station locations (reference data, refreshed weekly).

API: https://naptan.api.dft.gov.uk/v1/access-nodes?dataFormat=csv&atcoAreaCodes=910
(ATCO area 910 = National Rail). Stop type RLY = rail station access area. OGL v3.0.
Stored in ref_features (dataset='rail_station') and queried with PostGIS KNN.

"Direct London service" is a user-maintained flag (Settings → Rail stations). PLOT ships
a default list of station NAMES on South Western Railway routes into London Waterloo,
marked as a default to verify; journey times are left blank rather than guessed.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable

from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import delete

from plot.models import RefFeature, utcnow
from plot.sources.base import CandidateRecord, DiscoverySource, RunContext, SourceError, SourceMeta
from plot.sources.http import PublicHttp

URL = "https://naptan.api.dft.gov.uk/v1/access-nodes"

DEFAULT_LONDON_DIRECT = [
    # South West Main Line / Weymouth line
    "Weymouth", "Upwey", "Dorchester South", "Moreton (Dorset)", "Wool", "Wareham", "Holton Heath", "Hamworthy",
    "Poole", "Parkstone (Dorset)", "Branksome", "Bournemouth", "Pokesdown", "Christchurch", "Hinton Admiral",
    "New Milton", "Sway", "Brockenhurst", "Beaulieu Road", "Ashurst New Forest", "Totton", "Southampton Central",
    "Southampton Airport Parkway", "Eastleigh", "Shawford", "Winchester", "Micheldever", "Basingstoke",
    # West of England line
    "Axminster", "Crewkerne", "Yeovil Junction", "Sherborne", "Templecombe", "Gillingham (Dorset)", "Tisbury",
    "Salisbury", "Grateley", "Andover", "Whitchurch (Hants)", "Overton",
    # Portsmouth Direct / Alton
    "Petersfield", "Liss", "Liphook", "Havant", "Alton",
    # Hampshire / Surrey border main line
    "Hook", "Winchfield", "Fleet", "Farnborough (Main)",
]


class NaptanRailStations(DiscoverySource):
    """Not an opportunity source: refreshes reference station locations during the daily run."""

    meta = SourceMeta(
        id="dft.naptan_rail",
        name="NaPTAN rail stations (DfT)",
        category="enrichment",
        kind="live_api",
        licence="Open Government Licence v3.0",
        attribution="Contains public sector information (NaPTAN, Department for Transport) licensed under the OGL v3.0.",
        terms_url="https://beta-naptan.dft.gov.uk/",
        docs_url="https://naptan.api.dft.gov.uk/swagger/index.html",
        notes="Reference data only. Journey times to London are not inferred.",
        default_config={"london_direct_names": DEFAULT_LONDON_DIRECT},
    )
    reference_only = True

    def discover(self, ctx: RunContext) -> Iterable[CandidateRecord]:
        with PublicHttp(ctx.db) as http:
            text = http.get_text(URL, params={"dataFormat": "csv", "atcoAreaCodes": "910"}, headers={"Accept": "text/csv"})
        stations = parse_naptan_csv(text)
        if not stations:
            raise SourceError("parse_error", "NaPTAN CSV contained no RLY rail station records")
        london = {n.lower() for n in ctx.config.get("london_direct_names", DEFAULT_LONDON_DIRECT)}
        ctx.db.execute(delete(RefFeature).where(RefFeature.dataset == "rail_station"))
        for s in stations:
            ctx.db.add(
                RefFeature(
                    dataset="rail_station",
                    ref=s["atco"],
                    name=s["name"],
                    geom=from_shape(Point(s["lon"], s["lat"]), srid=4326),
                    props={"london_direct": s["name"].lower() in london, "london_direct_basis": "PLOT default list — verify"},
                    source_id=self.meta.id,
                    fetched_at=utcnow(),
                )
            )
        ctx.db.flush()
        self.records_fetched = len(stations)
        return []


def parse_naptan_csv(text: str) -> list[dict]:
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("StopType") or "").strip() != "RLY":
            continue
        try:
            lon, lat = float(row["Longitude"]), float(row["Latitude"])
        except (KeyError, TypeError, ValueError):
            continue
        name = (row.get("CommonName") or "").replace(" Rail Station", "").strip()
        out.append({"atco": row.get("ATCOCode"), "name": name, "lon": lon, "lat": lat})
    return out

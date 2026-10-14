"""Brownfield register CSV ingestion (LPA-published files and archived snapshots).

Two uses:
1. Direct LPA register CSV URLs (statutory publication under the Town and Country Planning
   (Brownfield Land Register) Regulations 2017, OGL v3.0). Configure URLs in Admin.
2. Archived snapshots kept in the open-source digital-land/brownfield-land-collection
   repository on GitHub ("fixed" copies of LPA files). These are real records but can be
   years old, so every record is flagged stale with its LastUpdatedDate.

Handles both the 2017 statutory column names (GeoX/GeoY/SiteNameAddress...) and the
digital-land kebab-case names. Encodings seen in the wild include cp1252.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from collections.abc import Iterable
from typing import Any

from plot import geo
from plot.sources.base import CandidateRecord, DiscoverySource, RunContext, SourceMeta, SourceNotConfigured
from plot.sources.http import PublicHttp

OGL = "Open Government Licence v3.0"

DIGITAL_LAND_SNAPSHOTS = [
    {
        "publisher": "New Forest District Council",
        "url": "https://raw.githubusercontent.com/digital-land/brownfield-land-collection/main/fixed/45e645b8570f5dd8b7f8775e032047d98ff2b61de6ac4f14ebea3ef8c771acba.csv",
        "note": "Archived copy of NFDC's brownfield land register held in digital-land/brownfield-land-collection (records last updated 2020).",
    }
]

_COLS = {
    "ref": ["SiteReference", "Site Reference", "reference", "site"],
    "address": ["SiteNameAddress", "Site Name Address", "site-address", "SiteAddress", "name"],
    "x": ["GeoX", "geox", "Easting", "EASTING", "longitude"],
    "y": ["GeoY", "geoy", "Northing", "NORTHING", "latitude"],
    "crs": ["CoordinateReferenceSystem", "Coordinate Reference System", "CoordinateSystem"],
    "hectares": ["Hectares", "hectares", "AREA (HA)"],
    "ownership": ["OwnershipStatus", "Ownership Status", "ownership-status"],
    "planning_status": ["PlanningStatus", "Planning Status", "planning-permission-status"],
    "permission_type": ["PermissionType", "Permission Type", "planning-permission-type"],
    "permission_date": ["PermissionDate", "Permission Date", "planning-permission-date"],
    "min_dwellings": ["NetDwellingsRangeFrom", "MinNetDwellings", "Min Net Dwellings", "minimum-net-dwellings"],
    "max_dwellings": ["NetDwellingsRangeTo", "maximum-net-dwellings"],
    "notes": ["Notes", "notes", "DevelopmentDescription", "Development Description"],
    "plan_url": ["SiteplanURL", "SitePlanURL", "Siteplan URL", "site-plan-url"],
    "history": ["PlanningHistory", "Planning History", "planning-permission-history"],
    "first_added": ["FirstAddedDate", "start-date"],
    "last_updated": ["LastUpdatedDate", "entry-date"],
    "end_date": ["EndDate", "end-date"],
    "org": ["OrganisationLabel", "Organisation Label", "OrganisationName"],
}


def _get(row: dict[str, str], key: str) -> str | None:
    for c in _COLS[key]:
        v = row.get(c)
        if v not in (None, ""):
            return v.strip()
    return None


def _num(v: str | None) -> float | None:
    if v is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", v.replace(",", ""))
    return float(m.group()) if m and re.fullmatch(r"\s*-?\d+(?:\.\d+)?\s*", v.replace(",", "")) else None


def _date(v: str | None) -> dt.date | None:
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(v[:10], fmt).date()
        except ValueError:
            continue
    return None


def decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_register_csv(text: str, *, source_id: str, source_url: str, publisher: str | None, now: dt.date, stale_after_days: int = 540) -> list[CandidateRecord]:
    out: list[CandidateRecord] = []
    for row in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip(): (v or "") for k, v in row.items()}
        if _date(_get(row, "end_date")):
            continue  # removed from the register (typically built out)
        x, y = _num(_get(row, "x")), _num(_get(row, "y"))
        if x is None or y is None:
            continue
        crs = (_get(row, "crs") or "").upper()
        if abs(x) <= 180 and abs(y) <= 90 and "OSGB" not in crs:
            lon, lat = x, y
            if lat < lon:  # some files swap columns; GB latitudes are 49..61
                lon, lat = lat, lon
        else:
            lon, lat = geo.bng_to_lonlat(x, y)
        if not (-9 <= lon <= 3 and 49 <= lat <= 61):
            continue
        ref = _get(row, "ref") or f"{lon:.5f},{lat:.5f}"
        address = _get(row, "address") or ref
        hectares_raw = _get(row, "hectares")
        hectares = _num(hectares_raw)
        last_updated = _date(_get(row, "last_updated"))
        stale = last_updated is None or (now - last_updated).days > stale_after_days
        status = (_get(row, "planning_status") or "").lower()
        signals = ["brownfield_register"]
        if "permissioned" in status and "not" not in status:
            signals.append("has_permission")
        elif status:
            signals.append("allocated_or_suitable_no_permission")
        if stale:
            signals.append("stale_record")
        out.append(
            CandidateRecord(
                source_id=source_id,
                external_ref=ref,
                record_type="register_site",
                lon=lon,
                lat=lat,
                title=f"Brownfield register: {address}",
                category="brownfield",
                address=address,
                area_m2=hectares * 10_000 if hectares else None,
                description=_get(row, "notes"),
                url=source_url,
                data_date=last_updated,
                publisher=_get(row, "org") or publisher,
                licence=OGL,
                attribution=f"Contains public sector information published by {publisher or 'the local planning authority'}, licensed under the OGL v3.0.",
                confidence="low" if stale else "high",
                stale=stale,
                signals=signals,
                raw={
                    "site_reference": ref,
                    "ownership_status": _get(row, "ownership"),
                    "planning_status": _get(row, "planning_status"),
                    "permission_type": _get(row, "permission_type"),
                    "permission_date": _get(row, "permission_date"),
                    "min_net_dwellings": _get(row, "min_dwellings"),
                    "max_net_dwellings": _get(row, "max_dwellings"),
                    "hectares_as_published": hectares_raw,
                    "site_plan_url": _get(row, "plan_url"),
                    "planning_history": _get(row, "history"),
                    "first_added": _get(row, "first_added"),
                    "last_updated": _get(row, "last_updated"),
                },
            )
        )
    return out


class _CsvRegisterBase(DiscoverySource):
    def _files(self, ctx: RunContext) -> list[dict[str, Any]]:  # pragma: no cover - overridden
        raise NotImplementedError

    def discover(self, ctx: RunContext) -> Iterable[CandidateRecord]:
        files = self._files(ctx)
        with PublicHttp(ctx.db) as http:
            for f in files:
                resp = http.request("GET", f["url"], headers={"Accept": "text/csv,*/*"})
                text = decode_bytes(resp.content)
                recs = parse_register_csv(text, source_id=self.meta.id, source_url=f["url"], publisher=f.get("publisher"), now=ctx.now.date())
                for r in recs:
                    if any(a <= r.lon <= c and b <= r.lat <= d for a, b, c, d in ctx.search_bboxes):
                        yield r


class DigitalLandSnapshots(_CsvRegisterBase):
    meta = SourceMeta(
        id="github.digital_land_snapshots",
        name="Archived brownfield register snapshots (digital-land on GitHub)",
        category="off_market",
        kind="mirror",
        licence=OGL,
        attribution="Brownfield register data © the publishing local planning authority, OGL v3.0; archived by MHCLG digital-land.",
        terms_url="https://github.com/digital-land/brownfield-land-collection",
        docs_url="https://github.com/digital-land/brownfield-land-collection/tree/main/fixed",
        notes="Fallback when planning.data.gov.uk is unreachable. Records are historic (often 2020) and flagged stale.",
        default_config={"files": DIGITAL_LAND_SNAPSHOTS},
    )

    def _files(self, ctx: RunContext) -> list[dict[str, Any]]:
        return ctx.config.get("files", DIGITAL_LAND_SNAPSHOTS)


class LpaRegisterCsv(_CsvRegisterBase):
    meta = SourceMeta(
        id="lpa.brownfield_csv",
        name="LPA brownfield register CSVs (direct)",
        category="off_market",
        kind="live_api",
        licence=OGL,
        attribution="Contains public sector information licensed under the OGL v3.0.",
        notes="Add each LPA's published register CSV URL in Admin (config 'files': [{publisher, url}]).",
        default_config={"files": []},
    )

    def _files(self, ctx: RunContext) -> list[dict[str, Any]]:
        files = ctx.config.get("files") or []
        if not files:
            raise SourceNotConfigured("no LPA register CSV URLs configured")
        return files

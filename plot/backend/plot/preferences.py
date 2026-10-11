"""Editable acquisition strategy: geography, budget, finance, programme, house brief, weights.

The £400,000 figure is an ILLUSTRATIVE ALL-IN budget (land + every cost to completion).
It is never used as a land budget: the land figure is always derived by residual valuation.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from plot.models import Preferences, PreferencesHistory, utcnow


class SearchArea(BaseModel):
    name: str
    # [min_lon, min_lat, max_lon, max_lat] in WGS84
    bbox: tuple[float, float, float, float]
    enabled: bool = True
    note: str | None = None

    @model_validator(mode="after")
    def _check_bbox(self) -> SearchArea:
        a, b, c, d = self.bbox
        if not (-9 <= a < c <= 3 and 49 <= b < d <= 61):
            raise ValueError(f"bbox for {self.name} is not a valid GB extent")
        return self


class Budget(BaseModel):
    total_all_in_gbp: int = Field(400_000, ge=50_000, description="Illustrative all-in budget, not a land budget")
    over_budget_tolerance_pct: float = Field(5.0, ge=0, le=50)
    build_contingency_pct: float = Field(10.0, ge=0, le=40)
    conversion_contingency_pct: float = Field(17.5, ge=0, le=50)
    unknown_ground_contingency_pct: float = Field(5.0, ge=0, le=30)


class Finance(BaseModel):
    cash_available_gbp: int = 150_000
    use_self_build_mortgage: bool = True
    mortgage_rate_pct: float = 5.75
    mortgage_arrangement_fee_gbp: int = 1_500
    max_ltv_land_pct: float = 50.0
    max_ltv_build_pct: float = 75.0
    buyer_owns_other_residential_property: bool = False
    buyer_is_non_uk_resident: bool = False
    first_time_buyer: bool = False


class Programme(BaseModel):
    target_start: dt.date = Field(default_factory=lambda: dt.date.today() + dt.timedelta(days=90))
    purchase_months: int = 3
    planning_months_base: int = 6
    planning_months_high: int = 14
    construction_inflation_pct_pa: float = 3.5
    holding_cost_months_override: int | None = None


class HouseBrief(BaseModel):
    default_concept: Literal["cabin_40", "small_65", "larger_90"] = "small_65"
    bedrooms_min: int = 2
    min_private_garden_m2: int = 80
    parking_spaces: int = 2
    specification: Literal["essential", "good", "high"] = "good"
    preferred_construction: list[str] = Field(
        default_factory=lambda: ["timber_frame", "sips", "modular", "masonry", "conversion"]
    )
    single_storey_preferred: bool = False
    want_home_office: bool = True
    want_solar_pv: bool = True
    want_rainwater_harvesting: bool = True


class LocationPrefs(BaseModel):
    prefer_rural: bool = True
    max_distance_to_rail_station_km: float = 12.0
    london_rail_station_bonus: bool = True
    max_distance_to_town_km: float = 8.0
    privacy_importance: Literal["low", "medium", "high"] = "high"
    landscape_importance: Literal["low", "medium", "high"] = "high"


class Weights(BaseModel):
    planning: float = 30
    financial: float = 25
    physical: float = 20
    location: float = 15
    discount: float = 10

    @model_validator(mode="after")
    def _sum_100(self) -> Weights:
        total = self.planning + self.financial + self.physical + self.location + self.discount
        if abs(total - 100) > 0.01:
            raise ValueError(f"score weights must sum to 100 (got {total})")
        return self


class Exclusions(BaseModel):
    exclude_flood_zone_3: bool = True
    min_site_area_m2: float = 150
    max_site_area_m2: float = 40_000
    excluded_lpas: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=lambda: ["leasehold flat", "garage only", "mooring"])
    max_asking_price_gbp: int | None = None


class Notifications(BaseModel):
    email_to: str | None = None
    digest_size: int = 5
    notify_on_critical_alerts: bool = True


class PreferencesModel(BaseModel):
    search_areas: list[SearchArea]
    budget: Budget = Budget()
    finance: Finance = Finance()
    programme: Programme = Programme()
    house: HouseBrief = HouseBrief()
    location: LocationPrefs = LocationPrefs()
    weights: Weights = Weights()
    exclusions: Exclusions = Exclusions()
    notifications: Notifications = Notifications()
    region_cost_factor: float = Field(1.03, description="South of England location factor vs UK mean")


DEFAULT_SEARCH_AREAS = [
    SearchArea(name="Dorset (incl. BCP)", bbox=(-2.96, 50.51, -1.68, 51.08)),
    SearchArea(name="Hampshire", bbox=(-1.96, 50.70, -0.73, 51.39)),
    SearchArea(
        name="New Forest fringe",
        bbox=(-1.95, 50.72, -1.30, 51.00),
        note="Overlaps Hampshire/Dorset; NPA land has tight new-dwelling policy",
    ),
    SearchArea(name="South Wiltshire (Salisbury)", bbox=(-2.36, 50.95, -1.48, 51.25)),
    SearchArea(name="West Sussex west (Chichester)", bbox=(-0.95, 50.72, -0.60, 51.08)),
    SearchArea(name="South Somerset / East Devon border", bbox=(-3.20, 50.68, -2.40, 51.10), enabled=False),
]


def default_preferences() -> PreferencesModel:
    return PreferencesModel(search_areas=DEFAULT_SEARCH_AREAS)


def load_preferences(db: Session) -> tuple[PreferencesModel, int]:
    row = db.get(Preferences, 1)
    if row is None:
        prefs = default_preferences()
        row = Preferences(id=1, version=1, data=prefs.model_dump(mode="json"))
        db.add(row)
        db.flush()
        return prefs, 1
    return PreferencesModel.model_validate(row.data), row.version


def save_preferences(db: Session, prefs: PreferencesModel, actor: str) -> int:
    row = db.get(Preferences, 1)
    data = prefs.model_dump(mode="json")
    if row is None:
        row = Preferences(id=1, version=1, data=data)
        db.add(row)
    else:
        row.version += 1
        row.data = data
        row.updated_at = utcnow()
    db.add(PreferencesHistory(version=row.version, data=data, saved_by=actor))
    db.flush()
    return row.version


def enabled_bboxes(prefs: PreferencesModel) -> list[tuple[float, float, float, float]]:
    return [a.bbox for a in prefs.search_areas if a.enabled]


def point_in_search_area(prefs: PreferencesModel, lon: float, lat: float) -> bool:
    return any(a <= lon <= c and b <= lat <= d for a, b, c, d in enabled_bboxes(prefs))

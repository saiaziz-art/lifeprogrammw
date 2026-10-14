"""Stamp Duty Land Tax (England & Northern Ireland) — deterministic, date-aware.

Rates encoded (verify against GOV.UK before exchange):
- Residential from 1 April 2025: 0% ≤£125k, 2% to £250k, 5% to £925k, 10% to £1.5m, 12% above.
  +5% higher-rates surcharge on each band for additional dwellings (from 31 Oct 2024);
  +2% non-UK-resident surcharge. First-time buyer relief from 1 April 2025: 0% to £300k,
  5% to £500k, unavailable above £500k.
- Non-residential / mixed (bare land, agricultural land, a plot with no dwelling):
  0% ≤£150k, 2% to £250k, 5% above. No surcharges.

Land with no dwelling on it is normally non-residential for SDLT even when bought to build a
home; a plot with an existing (habitable) dwelling is residential. Edge cases (dwelling
uninhabitable, part-built house, mixed parcels) need advice — the result says which regime
was assumed.
"""

from __future__ import annotations

from dataclasses import dataclass

RES_BANDS = [(125_000, 0.0), (250_000, 0.02), (925_000, 0.05), (1_500_000, 0.10), (float("inf"), 0.12)]
FTB_BANDS = [(300_000, 0.0), (500_000, 0.05)]
NONRES_BANDS = [(150_000, 0.0), (250_000, 0.02), (float("inf"), 0.05)]


@dataclass
class SdltResult:
    tax: float
    regime: str
    breakdown: list[tuple[float, float, float]]  # (band_top, rate, tax_in_band)
    notes: list[str]


def _banded(price: float, bands: list[tuple[float, float]], surcharge: float = 0.0) -> tuple[float, list[tuple[float, float, float]]]:
    tax = 0.0
    lower = 0.0
    out = []
    for top, rate in bands:
        if price <= lower:
            break
        portion = min(price, top) - lower
        t = portion * (rate + surcharge)
        out.append((top, rate + surcharge, t))
        tax += t
        lower = top
    return tax, out


def sdlt(price: float, *, residential: bool, additional_dwelling: bool = False, non_resident: bool = False, first_time_buyer: bool = False) -> SdltResult:
    price = max(0.0, float(price))
    notes: list[str] = []
    if not residential:
        tax, br = _banded(price, NONRES_BANDS)
        notes.append("Non-residential rates assumed (no dwelling on the land).")
        return SdltResult(round(tax), "non_residential", br, notes)
    if first_time_buyer and not additional_dwelling and price <= 500_000:
        tax, br = _banded(price, FTB_BANDS, 0.02 if non_resident else 0.0)
        notes.append("First-time buyer relief applied (price ≤ £500k).")
        return SdltResult(round(tax), "residential_ftb", br, notes)
    surcharge = (0.05 if additional_dwelling else 0.0) + (0.02 if non_resident else 0.0)
    if additional_dwelling and price < 40_000:
        surcharge -= 0.05  # higher rates do not apply below £40k
    tax, br = _banded(price, RES_BANDS, surcharge)
    if additional_dwelling:
        notes.append("Higher rates for additional dwellings (+5%) applied.")
    if non_resident:
        notes.append("Non-UK-resident surcharge (+2%) applied.")
    return SdltResult(round(tax), "residential", br, notes)

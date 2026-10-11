"""HM Land Registry Price Paid Data — sold-price comparables via the public SPARQL endpoint.

Endpoint: https://landregistry.data.gov.uk/landregistry/query (SPARQL 1.1, JSON results).
Licence: OGL v3.0 (Crown copyright). Address data in PPD contains Royal Mail/OS rights;
it is used here only to show comparables to the user, not republished in bulk.

PPD has no floor areas, so £/m² requires EPC floor areas (separate, keyed source). Without
them PLOT reports the price distribution and refuses to infer a finished value from fewer
than three relevant sales.
"""

from __future__ import annotations

import datetime as dt
import statistics
from typing import Any

from plot.sources.base import EnrichmentSource, EvidenceItem, RunContext, SiteContext, SourceError, SourceMeta
from plot.sources.http import PublicHttp
from plot.sources.postcodes import nearest_postcodes

SPARQL = "https://landregistry.data.gov.uk/landregistry/query"
ATTR = "Contains HM Land Registry data © Crown copyright and database right. Licensed under the Open Government Licence v3.0."


def build_query(postcodes: list[str], since: dt.date, limit: int = 200) -> str:
    values = " ".join(f'"{p.upper()}"' for p in postcodes if p and p.replace(" ", "").isalnum())
    return f"""
PREFIX lrppi: <http://landregistry.data.gov.uk/def/ppi/>
PREFIX lrcommon: <http://landregistry.data.gov.uk/def/common/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT ?postcode ?paon ?saon ?street ?town ?amount ?date ?ptype ?newbuild ?estate ?category
WHERE {{
  VALUES ?postcode {{ {values} }}
  ?addr lrcommon:postcode ?postcode .
  ?tx lrppi:propertyAddress ?addr ;
      lrppi:pricePaid ?amount ;
      lrppi:transactionDate ?date .
  OPTIONAL {{ ?tx lrppi:propertyType/skos:prefLabel ?ptype }}
  OPTIONAL {{ ?tx lrppi:newBuild ?newbuild }}
  OPTIONAL {{ ?tx lrppi:estateType/skos:prefLabel ?estate }}
  OPTIONAL {{ ?tx lrppi:transactionCategory/skos:prefLabel ?category }}
  OPTIONAL {{ ?addr lrcommon:paon ?paon }}
  OPTIONAL {{ ?addr lrcommon:saon ?saon }}
  OPTIONAL {{ ?addr lrcommon:street ?street }}
  OPTIONAL {{ ?addr lrcommon:town ?town }}
  FILTER (?date >= "{since.isoformat()}"^^xsd:date)
}}
ORDER BY DESC(?date)
LIMIT {limit}
"""


def parse_bindings(data: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        rows = data["results"]["bindings"]
    except (KeyError, TypeError) as exc:
        raise SourceError("parse_error", "SPARQL response missing results.bindings") from exc
    out = []
    for b in rows:
        def v(k: str, b: dict = b) -> str | None:
            return b.get(k, {}).get("value")

        amount = v("amount")
        if amount is None:
            continue
        out.append(
            {
                "postcode": v("postcode"),
                "address": ", ".join(x for x in [v("saon"), v("paon"), v("street"), v("town")] if x),
                "price": int(float(amount)),
                "date": (v("date") or "")[:10],
                "property_type": v("ptype"),
                "new_build": (v("newbuild") or "").lower() == "true",
                "tenure": v("estate"),
                "category": v("category"),
            }
        )
    return out


def summarise(sales: list[dict[str, Any]]) -> dict[str, Any]:
    houses = [s for s in sales if (s.get("property_type") or "").lower() in ("detached", "semi-detached", "terraced") and "standard" in (s.get("category") or "standard").lower()]
    detached = [s["price"] for s in houses if (s.get("property_type") or "").lower() == "detached"]
    all_prices = [s["price"] for s in houses]
    res: dict[str, Any] = {"n_sales": len(sales), "n_houses": len(houses), "n_detached": len(detached)}
    if len(all_prices) >= 3:
        res["house_median"] = int(statistics.median(all_prices))
        res["house_p25"] = int(statistics.quantiles(all_prices, n=4)[0]) if len(all_prices) >= 4 else min(all_prices)
        res["house_p75"] = int(statistics.quantiles(all_prices, n=4)[2]) if len(all_prices) >= 4 else max(all_prices)
    if len(detached) >= 3:
        res["detached_median"] = int(statistics.median(detached))
    return res


class LandRegistryPricePaid(EnrichmentSource):
    meta = SourceMeta(
        id="hmlr.price_paid",
        name="HM Land Registry Price Paid (sold comparables)",
        category="market",
        kind="live_api",
        licence="Open Government Licence v3.0",
        attribution=ATTR,
        terms_url="https://www.gov.uk/government/statistical-data-sets/price-paid-data-downloads",
        docs_url="https://landregistry.data.gov.uk/app/qonsole",
        notes="Sold prices within ~1 km postcodes, last 3 years. No floor areas (needs EPC data for £/m²).",
    )
    topics = ("market",)
    refresh_days = 60

    def enrich(self, ctx: RunContext, site: SiteContext) -> list[EvidenceItem]:
        years = ctx.config.get("years", 3)
        since = (ctx.now - dt.timedelta(days=365 * years)).date()
        with PublicHttp(ctx.db) as http:
            pcs = [r["postcode"] for r in nearest_postcodes(http, site.lon, site.lat, radius=1500, limit=40)]
            if not pcs:
                return [self._insufficient("No postcodes within 1.5 km to search for comparables")]
            data = http.post_form_json(SPARQL, {"query": build_query(pcs, since)}, headers={"Accept": "application/sparql-results+json"})
        sales = parse_bindings(data)
        summary = summarise(sales)
        if summary.get("n_houses", 0) < 3:
            return [self._insufficient(f"Only {summary.get('n_houses', 0)} house sale(s) found since {since}; not enough evidence for a value estimate", sales)]
        return [
            EvidenceItem(
                topic="market",
                kind="comparables",
                title=f"{summary['n_houses']} house sales nearby since {since:%b %Y} (median £{summary['house_median']:,})",
                summary="Sold prices (HM Land Registry). Not size-adjusted: floor areas unknown without EPC data.",
                data={"summary": summary, "sales": sales[:60], "since": since.isoformat(), "postcodes_searched": pcs},
                source_url="https://landregistry.data.gov.uk/app/ppd",
                data_date=dt.date.fromisoformat(sales[0]["date"]) if sales and sales[0]["date"] else None,
                status="confirmed",
                confidence=0.6,
                licence=self.meta.licence,
                attribution=ATTR,
            )
        ]

    def _insufficient(self, msg: str, sales: list[dict[str, Any]] | None = None) -> EvidenceItem:
        return EvidenceItem(
            topic="market",
            kind="comparables-insufficient",
            title="Insufficient comparable sales evidence",
            summary=msg,
            data={"sales": (sales or [])[:30]},
            status="unknown",
            confidence=0.2,
            source_url="https://landregistry.data.gov.uk/app/ppd",
            licence=self.meta.licence,
            attribution=ATTR,
        )

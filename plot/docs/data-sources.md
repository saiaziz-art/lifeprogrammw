# Data sources, licences and integration status

Checked 11–12 October 2026. "Verified" says *how* each contract was confirmed. Live calls
could not be made from the build environment because its egress policy returns
**HTTP 403** for these hosts. That is recorded honestly by the app as `network_blocked`
and must be fixed by allowing the hosts in the deployment's network policy.

## Hosts to allow (server side)

| Host | Used by | Needed for |
|---|---|---|
| `www.planning.data.gov.uk` | brownfield discovery, constraints, INSPIRE title extents, planning applications, map constraint layer | **Core** |
| `environment.data.gov.uk` | EA flood zones / surface water / recorded outlines (OGC API Features) | **Core** |
| `api.postcodes.io` | reverse geocoding, nearest postcodes, place geocoding for alert emails | **Core** |
| `landregistry.data.gov.uk` | Price Paid sold comparables (SPARQL) | Market evidence |
| `naptan.api.dft.gov.uk` | rail station locations | Location score |
| `map.bgs.ac.uk` | BGS mapped geology (WMS GetFeatureInfo) | Ground screening |
| `raw.githubusercontent.com` | archived brownfield-register snapshots (fallback) | Fallback only |
| `epc.opendatacommunities.org` | EPC floor areas (optional, needs free key) | £/m² comparables |
| `api-proxy.ofcom.org.uk` | broadband coverage (optional, needs free key) | Utilities |
| `api.anthropic.com` | optional AI extraction from alert emails | Optional |
| your IMAP / SMTP hosts | alert-email ingestion, digest delivery | Optional |

Browser-side (map tiles, loaded by your browser not the server): `tiles.openfreemap.org`,
`server.arcgisonline.com`, `environment.data.gov.uk`, `map.bgs.ac.uk`.

## Sources

| Source (id) | Endpoint | Licence / terms | How verified | Status in build env |
|---|---|---|---|---|
| Planning Data — brownfield (`planning_data.brownfield`) | `GET /entity.json?dataset=brownfield-land&geometry=<WKT>&geometry_relation=intersects&limit=500&offset=` | OGL v3.0; dataset attribution | Query parameters read from platform source `digital-land/digital-land.info` (`application/search/filters.py`): `geometry_relation` defaults to **within**, so PLOT always sends `intersects`; `limit` ≤ 500; response `{entities, links, count}`. Field names from `digital-land/specification` `dataset-field.csv` | network_blocked |
| Planning Data — constraints (`planning_data.constraints`) | same endpoint, 35 datasets on site + 10 radius checks | OGL v3.0; Historic England / Natural England / EA / OS attributions carried per item | as above; dataset list (incl. `nutrient-neutrality-catchment`, `title-boundary`, `built-up-area`, `agricultural-land-classification`) from `specification/dataset.csv` | network_blocked |
| INSPIRE title extents (`planning_data.title_boundary`) | `dataset=title-boundary` | OGL + OS attribution "© Crown copyright and database rights Ordnance Survey 100026316" | HMLR licence terms (OGL since July 2020 with OS conditions on geometry) | network_blocked |
| Planning applications (`planning_data.applications`) | `dataset=planning-application` | OGL | alpha dataset: partial LPA coverage, so absence ≠ no history | network_blocked |
| EA flood risk (`ea.flood`) | `environment.data.gov.uk/spatialdata/flood-map-for-planning-flood-zones/ogc/features/v1` (+ RoFSW, recorded outlines) | OGL; "© Environment Agency copyright and/or database right" | Flood-zones endpoint from data.gov.uk catalogue record (Jan 2026). Collections are discovered at runtime. RoFSW / recorded-outline URLs **unverified** until first run | network_blocked |
| postcodes.io (`postcodes_io`) | `/postcodes?lon&lat&radius&limit`, `/postcodes/{pc}`, `/places?q=` | Data OGL (ONS, OS Code-Point Open, Royal Mail notices); service MIT | Long-standing public API | network_blocked |
| HMLR Price Paid (`hmlr.price_paid`) | SPARQL `https://landregistry.data.gov.uk/landregistry/query` | OGL v3.0; Royal Mail / OS address notice | HMLR linked-data vocabulary (`lrppi:`, `lrcommon:`) | network_blocked |
| NaPTAN rail (`dft.naptan_rail`) | `https://naptan.api.dft.gov.uk/v1/access-nodes?dataFormat=csv&atcoAreaCodes=910` | OGL v3.0 | DfT NaPTAN API (area 910 = National Rail; stop type RLY) | network_blocked |
| BGS geology (`bgs.geology`) | WMS `map.bgs.ac.uk/arcgis/services/BGS_Detailed_Geology/MapServer/WMSServer` | OGL with acknowledgement (BGS WMS page). **GeoSure is licensed and not used** | Endpoint/layer names **unverified**; layers discovered from GetCapabilities | network_blocked |
| Archived register snapshots (`github.digital_land_snapshots`) | `raw.githubusercontent.com/digital-land/brownfield-land-collection/main/fixed/…csv` | OGL v3.0 (publishing LPA) | Downloaded and parsed in the build env: New Forest DC register, 56 rows, 41 active, records last updated 2020 | **ok — real data, flagged historic** |
| LPA register CSVs (`lpa.brownfield_csv`) | URLs you add | OGL | — | not configured |
| Alert emails (`email.alerts`, `email.upload`) | IMAP / `.eml` upload | your own emails; listing content stays the advertiser's | parser unit-tested on a synthetic fixture | not configured |
| EPC (`mhclg.epc`) | `epc.opendatacommunities.org/api/v1/domestic/search` (Basic auth) | OGL; address terms | — | not configured (key) |
| Ofcom (`ofcom.coverage`) | `api-proxy.ofcom.org.uk/broadband/coverage/{pc}` | Ofcom terms | — | not configured (key) |
| ArcGIS layers (`arcgis.layers`) | any FeatureServer you add (Coal Authority, NE, SSEN…) | per layer | standard ArcGIS REST `query` | not configured |
| Rightmove / Zoopla / OnTheMarket / Plotfinder / auctions / agent sites | — | no consumer API or reuse licence; automated extraction not permitted | — | **blocked by terms** (alert emails / manual import instead) |

## What is deliberately *not* inferred

- **Owner identity**: needs an HM Land Registry official copy (about £7 per title). INSPIRE polygons carry no names.
- **Availability of off-market land**: only recorded from an owner's own words.
- **Settlement boundaries, Local Plan allocations, CIL rates**: LPA-specific. ONS built-up areas are used only as a labelled proxy.
- **Groundwater levels, radon, sewer and water-main positions**: these need licensed data, quotes or a site investigation.
- **Journey times to London**: a default list of stations with direct Waterloo services is used as a bonus, labelled "verify"; no times are guessed.
- **Construction costs**: screening defaults (Q3 2026, southern England) labelled as such and editable. They are not BCIS data and not quotes.

## Policy facts encoded (verify against current text)

- Class Q since 21 May 2024: up to 10 homes and 1,000 m² total, at most 150 m² each. Excluded on Article 2(3) land (National Parks, National Landscapes, conservation areas, WHS) and for listed buildings.
- Biodiversity net gain from 6 Aug 2026: area-based exemption for sites of 0.2 ha or less, and the self-build exemption removed. The rule is date-aware in the engine.
- SDLT from 1 Apr 2025:
  - Residential: 0/2/5/10/12%, plus 5% for additional dwellings and 2% for non-residents.
  - First-time buyers: relief to £300k, up to a £500k purchase price.
  - Non-residential: 0/2/5%.
- Habitats mitigation zones:
  - Dorset Heathlands: no net new dwellings within 400 m; contributions from 400 m to 5 km.
  - Thames Basin Heaths: 400 m exclusion zone; SANG and SAMM contributions from 400 m to 5 km.
  - New Forest: 13.8 km recreation zone.
  - Bird Aware Solent: 5.6 km zone.
  - Poole Harbour: 5 km zone.
- Planning and Infrastructure Act 2025: Nature Restoration Fund levies may replace bespoke nutrient mitigation. The app flags this rather than assuming either outcome.

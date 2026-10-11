# PLOT — UK land & small-home sourcing platform

A private web application that finds, screens and costs land opportunities in Dorset,
Hampshire, the New Forest fringe and adjoining areas, aiming for a well-designed,
legally viable and financially sensible small home. It does not simply look for cheap land.

**Status (12 Oct 2026):** Phase 1 is complete and tested. Large parts of Phases 2–4 are
implemented (see the table below). Live government APIs are implemented but were
**blocked by the build environment's network policy (HTTP 403)**, so the only real records
loaded so far are New Forest DC's archived brownfield register (OGL, last updated 2020),
clearly flagged as historic. No fabricated property data is used anywhere in the
opportunity feed.

## What it does

- **Daily autonomous run at 07:00 Europe/London** (separate scheduler and worker processes, durable Postgres queue):
  1. Retrieve new records.
  2. Detect price and status changes.
  3. Refresh public data.
  4. Deduplicate.
  5. Screen against your preferences.
  6. Enrich with planning and environmental data.
  7. Recalculate feasibility.
  8. Rank.
  9. Build the daily digest.
  10. Deliver it by email or webhook.

  Every run, step and source is recorded with its real outcome (succeeded / partial / failed).
- **Planning screening (England)** classifies each site into one of five classes:
  - permission verified;
  - approval exists, review needed;
  - plausible route;
  - speculative;
  - significant obstacle.

  It covers designations, Green Belt, heritage, flood, habitats mitigation zones, nutrient neutrality, Class Q, replacement dwellings, BNG (with the Aug 2026 rules), CIL and S106. Each finding carries its evidence, dates and uncertainty.
- **Site risk:** flood (rivers & sea, surface water, historic), ground (mapped geology → foundation case), drainage (mains vs package plant), utilities, access, site preparation. Each item is Confirmed / Estimated / Unknown / Requires investigation.
- **Design Studio:** compact cabin (40 m²), small home (65 m²) and larger home (90 m²) concepts in timber frame, SIPs, modular, masonry or conversion. It tests geometric fit, draws concept plans and massing, and shows the cost effect live.
- **QS-style cost plan:** 16 categories, low/base/high, date-stamped editable rates, deterministic SDLT and VAT, finance, inflation and contingency.
  - **Residual valuation** gives the maximum land offer.
  - **Scenarios:** buy now, subject to planning, option agreement.
  - **Sensitivity** to ground, services, inflation, delay and build rates.
- **Scoring /100** (30/25/20/15/10, editable). Fatal flags override the score. Data confidence is reported separately. Explanations, manual overrides and feedback are supported.
- **Off-market pipeline** with nine evidence-gated stages, and owner outreach drafts with UK GDPR/PECR checks that require your approval. PLOT never sends letters.
- **Six main views:** Dashboard, Land Map, Deal Explorer, Site Dossier, Design Studio, Acquisition Pipeline. Plus Admin (integrations, runs, jobs, credentials, cost rates, missing information, expenses, audit), Preferences, and Add & Inbox.

Read [docs/architecture.md](docs/architecture.md) and [docs/data-sources.md](docs/data-sources.md)
(licences, endpoints, what was verified and how, and hosts to allow).

## Run it

### Docker (recommended)

```bash
cd plot
cp .env.example .env
docker compose run --rm api plot keygen      # paste into PLOT_SECRET_KEY in .env
docker compose up -d --build
# open http://localhost:3000 → create the owner account
```

### Local development

```bash
# PostgreSQL 16 + PostGIS running locally, database "plot"
cd plot/backend
python3 -m venv .venv && .venv/bin/pip install -e ".[ai,dev]"
printf "PLOT_ENV=development\nPLOT_SECRET_KEY=%s\n" "$(.venv/bin/plot keygen)" > .env
.venv/bin/plot migrate && .venv/bin/plot seed
.venv/bin/plot api            # http://127.0.0.1:8000/api/docs
.venv/bin/plot scheduler      # separate terminal
.venv/bin/plot worker         # separate terminal
.venv/bin/plot run-now        # optional: run the sourcing workflow immediately

cd ../frontend && npm ci && npm run dev    # http://localhost:3000
```

Tests: `cd backend && .venv/bin/pytest` (needs a `plot_test` PostGIS database;
override with `PLOT_TEST_DATABASE_URL`).

## Phase status

| Phase | Item | State |
|---|---|---|
| 1 | Searchable database, map, preferences, migrations, auth | Done |
| 1 | Live public-data integrations (Planning Data, EA, postcodes.io, HMLR, NaPTAN, BGS) | Implemented and unit-tested; **live calls blocked in build env (403)** |
| 1 | Manual listing import, preliminary planning screening | Done |
| 1 | Working daily scheduled run | Done; verified with real scheduler and worker processes |
| 2 | Alert-email ingestion (IMAP + .eml), geocoding, unlocated-listing inbox | Done (IMAP not yet tried against a real mailbox) |
| 2 | Off-market leads from registers and manual pins; INSPIRE parcel matching; price histories; scoring | Done |
| 2 | Automatic detection of derelict/vacant buildings from aerial imagery | **Not implemented**: needs licensed high-resolution imagery and a reviewed model |
| 2 | Council land-asset / public disposal registers | Partial: generic CSV route only for brownfield-format files |
| 3 | Engineering and utility screening, site design, cost plans, residual valuation | Done (screening level) |
| 3 | Terrain / gradient, radon, network asset maps | Not integrated (shown as Unknown / Requires investigation) |
| 4 | Owner-research workflow, compliant outreach, due-diligence checklists, scenarios | Done |
| 4 | Exported PDF dossier / report | Not yet |

## Important limits

PLOT is a screening and research tool. Planning outcomes are never guaranteed. Design
concepts are not architectural drawings. Engineering conclusions need site investigation.
Cost rates are assumptions until replaced with quotes. Get professional advice before
committing money.

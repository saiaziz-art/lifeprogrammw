# PLOT — UK land & small-home sourcing platform

A private web application that finds, screens and costs land opportunities in Dorset,
Hampshire, the New Forest fringe and adjoining areas, aiming for a well-designed,
legally viable and financially sensible small home. It does not simply look for cheap land.

**Status (12 Oct 2026):**
- Phase 1 is complete and tested, and large parts of Phases 2–4 are implemented (see the
  table below).
- The **Off-Market Acquisition & Owner Outreach Agent** is implemented. Its whole workflow
  has been tested end to end against the real daily run and API, using clearly labelled mock
  owners and mocked public/mail APIs (see [docs/offmarket-agent.md](docs/offmarket-agent.md)).
- Live government, OpenStreetMap, Companies House and Stannp APIs are implemented, but the
  build environment's network policy **blocks them (HTTP 403)**. So the only real records
  loaded so far are New Forest DC's archived brownfield register (OGL, last updated 2020),
  clearly flagged as historic.
- No fabricated property data or simulated engagement figures are used anywhere.

## What it does

- **Daily autonomous run at 07:00 Europe/London** (separate scheduler and worker processes, durable Postgres queue):
  1. Retrieve new records.
  2. Detect price and status changes.
  3. Refresh public data.
  4. Deduplicate.
  5. Screen against your preferences.
  6. Enrich with planning and environmental data.
  7. Recalculate feasibility.
     - 7b. Off-market: score prospects, check for owner replies, and prepare up to five drafts for your approval. It never sends.
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
- **Off-market acquisition agent** (details in [docs/offmarket-agent.md](docs/offmarket-agent.md)).
  - **Discovery** (daily, never contacts anyone): garden plots (side, corner, backland with a driveway test), settlement-edge paddocks, outbuildings, replacement dwellings, derelict buildings and sites with past residential applications.
  - **Off-Market Acquisition Score** (30/25/20/15/10), with a separate evidence confidence that is capped when access or the planning route is uncertain.
  - **Ownership** comes only from HM Land Registry official copies that you upload and confirm.
  - **Personalised letters and emails** in your chosen persona: Claude when configured, otherwise a deterministic composer. Every draft is checked for length, banned or unconfirmed claims, prices, assumptions about the owner, and a privacy/opt-out notice.
  - **UK GDPR/PECR eligibility checks.** Approval is hash-bound, one message and one recipient at a time, with no bulk approval.
  - **Delivery:** printable PDF letters (manual post or Stannp), or Gmail/Outlook via OAuth with idempotent sending and the provider's message ID recorded.
  - **Replies:** categorised, with opt-outs honoured immediately. Suggested replies need your approval, and a private negotiation brief is prepared when an owner asks about price.
  - **A 12-stage CRM** with real-count KPIs, plus an acquisition briefing in the daily digest.
- **Six main views:** Dashboard, Land Map, Deal Explorer, Site Dossier, Design Studio, Acquisition Pipeline. Plus the Off-Market CRM (prospects, approval queue), Admin (integrations, mail connections, runs, jobs, credentials, cost rates, missing information, expenses, audit), Preferences (incl. buyer profile, outreach rules, prospecting areas), and Add & Inbox.

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
override with `PLOT_TEST_DATABASE_URL`). 129 tests, including the off-market end-to-end workflow.

### Sandbox (separate test environment)

Try the whole outreach workflow on synthetic data with clearly labelled mock owners. The
sandbox has its own database; mailbox connections and postal submission are disabled.

```bash
createdb plot_sandbox && psql -d plot_sandbox -c "CREATE EXTENSION postgis"
export PLOT_ENV=sandbox PLOT_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/plot_sandbox
.venv/bin/plot migrate && .venv/bin/plot seed && .venv/bin/plot sandbox-seed && .venv/bin/plot api
```

### Before the off-market agent can work on real land

1. Allow the hosts listed in [docs/data-sources.md](docs/data-sources.md) (at least
   `www.planning.data.gov.uk` and `overpass-api.de`). Alternatively, import OS OpenMap – Local:
   `plot import-os-openmap <file.gpkg> --bbox w,s,e,n`.
2. Set your prospecting areas, buyer profile and outreach rules in Preferences → Off-market,
   and tick the confirmation.
3. For each prospect you like, buy the official copy of the register (about £7), upload it,
   and verify the owner.
4. Optional: connect Gmail or Outlook (Admin → Mail; you supply the OAuth client), add a
   Stannp key, a Companies House key and an Anthropic key.

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
| 4 | Off-market discovery engine (gardens, paddocks, outbuildings, replacement, derelict, planning history) | Done; tested on synthetic geometry; **live layers blocked in build env** |
| 4 | Official-copy upload, parsing and owner verification; Companies House | Done (Companies House needs a key) |
| 4 | Outreach writer, compliance, hash-bound approval, printable letters | Done; end-to-end tested |
| 4 | Gmail / Outlook OAuth send and reply sync; Stannp | Implemented and tested against mocks; **not yet run against a real mailbox or Stannp** |
| 4 | Reply intelligence, negotiation brief, CRM, daily acquisition briefing | Done |
| 4 | HMLR CCOD/OCOD company-ownership data | Not integrated (can't be linked to INSPIRE IDs reliably) |
| 4 | Exported PDF dossier / report | Not yet |

## Important limits

PLOT is a screening and research tool. Planning outcomes are never guaranteed. Outreach
safeguards reflect UK GDPR, PECR and ICO guidance as implemented here; they are not legal
advice, so review your legitimate interests assessment and privacy wording yourself. Design
concepts are not architectural drawings. Engineering conclusions need site investigation.
Cost rates are assumptions until replaced with quotes. Get professional advice before
committing money.

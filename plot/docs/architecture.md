# Architecture

```
 browser ──► Next.js (React/TS, MapLibre)  ──/api/*──►  FastAPI (Python)  ──►  PostgreSQL 16 + PostGIS
                                                            ▲                     ▲      ▲
                         scheduler process ── enqueue ──────┘ (jobs table)        │      │
                         worker process(es) ── claim (FOR UPDATE SKIP LOCKED) ────┘      │
                                   └── source adapters ──► public APIs / IMAP / SMTP ────┘
```

## Backend (`backend/plot`)

| Module | Responsibility |
|---|---|
| `models.py`, `alembic/` | Schema and migrations. Sites, listings and price events, evidence (every finding with source, dates, status, licence), versioned assessments, leads, outreach, pipeline, jobs, runs, source runs, digests, cost rates, encrypted secrets, audit log, heartbeats |
| `sources/` | One adapter per source. `DiscoverySource` yields `CandidateRecord`s; `EnrichmentSource` returns `EvidenceItem`s. `http.py` provides polite retries and classifies errors (`network_blocked`, `rate_limited`, `not_found`…). Blocked sources raise `SourceBlocked` with the lawful alternative |
| `ingest.py` | Upsert with change detection: price change, status change, relisted, and withdrawn (only after 3 consecutive *successful* fetches without the listing). Register and off-market records become evidence plus a Lead, never a Listing |
| `engine/planning/` | Jurisdiction registry; England rules return findings, routes, obligations, a 5-way classification and confidence. Wales, Scotland and NI plug in by registering their own `Jurisdiction` |
| `engine/site_risk.py` | Flood, ground (mapped lithology → screening hazards), drainage, utilities, access, site preparation. Every item is Confirmed / Estimated / Unknown / Requires investigation. Outputs the cost drivers |
| `engine/design/` | 40 / 65 / 90 m² concepts, construction methods, parametric room layouts, and a geometric fit test (rotated footprint inside the setback zone, garden check) |
| `engine/costs/` | Date-stamped rate library, SDLT, 16-category cost plan (low / base / high plus root-sum-square range), residual solver, three acquisition scenarios, sensitivity |
| `engine/scoring.py`, `engine/assess.py` | Weighted score /100, fatal flags that override ranking, data confidence (kept separate), explanations, fingerprinted assessments (recomputed only when inputs change) |
| `jobs/` | Postgres queue (retries with backoff, stale-lock reaper, dedupe keys), cron scheduler (time-zone and DST aware, misfire grace, missed runs logged), worker, the 10-step pipeline, notifications |
| `api/` | REST API with cookie sessions (argon2id, hashed tokens), CSRF header, login rate limit, validation, audit |
| `ai/extract.py` | Optional Claude structured extraction; every value is verified against the source text by deterministic code |

## Honesty rules enforced in code

- A run is **succeeded** only if every enabled, configured live source delivered. Otherwise it is **partial** (completed with failures) or **failed** (crashed).
- Blocked-by-terms and not-configured sources are shown as such, not as failures, and never as data.
- Unknown evidence carries its *expected* penalty in physical scoring, so missing data never makes a site look better.
- Off-market leads advance only on recorded evidence. Outreach can't be approved until the compliance checks pass, and can't be "sent" until it is approved. PLOT never sends letters itself.
- INSPIRE extents are adopted as boundaries only for exact locations (not postcode centroids), and only when their area is consistent with the reported area.
- Historic records (for example the 2020 register snapshot) are flagged `stale`, lower data confidence and are sorted last in the digest.

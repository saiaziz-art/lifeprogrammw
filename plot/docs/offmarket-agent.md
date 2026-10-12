# Off-Market Acquisition & Owner Outreach Agent

A selective, research-led way to find a few suitable landowners and start a respectful
conversation with them. It is **not** a bulk email tool. PLOT can research, qualify and
draft on its own. It **never contacts anyone by itself**: every outbound message, including
follow-ups, needs your individual approval, and goes only to the recipient you approved.

## The workflow

```
Discover ─► Feasibility screened ─► Owner verified ─► Draft prepared ─► Awaiting my approval
   │              (site engines)       (official copy      (writer +          (one message,
   │                                    + your check)       compliance)        one recipient)
   ▼
Contacted ─► Awaiting response ─► Interested ─► Feasibility discussion ─► Negotiation ─► Under offer ─► Completed / Closed
 (letter posted / email accepted)   (reply logged or synced)
```

Stages up to *Contacted* follow from evidence: a verified register, a draft that passes the
checks, your approval, a recorded posting or a provider-confirmed send. They can't be set by
hand. Later stages are moved by you, and only after the owner has expressed interest.

## 1. Discovery (`plot/offmarket/analyse.py`, `geodata.py`, `agent.py`)

Each morning the `offmarket.discovery` source analyses every enabled **prospecting area**
(Preferences → Off-market: a centre and a radius of 100–2,000 m).

**Inputs.** All are lawful open data:
- HM Land Registry INSPIRE title extents (`title-boundary` on planning.data.gov.uk);
- building footprints and roads, either from OS OpenMap – Local imported with
  `plot import-os-openmap <file.gpkg> --bbox w,s,e,n` (preferred), or from OpenStreetMap via
  Overpass;
- constraints, built-up areas, heritage at risk and planning applications from
  planning.data.gov.uk.

**Candidate types.** Each title extent is tested in this order:

| Type | Test (BNG metres) |
|---|---|
| Derelict or disused building | OSM `ruins` / `disused` / `abandoned` tags, or a heritage-at-risk polygon |
| Past residential applications | Refused, withdrawn, or approved more than 3 years ago (possibly lapsed) |
| Garden plot | One house, title 600–6,000 m², in or within 50 m of a built-up area. A notional 10×24, 12×25 or 14×28 m plot must fit outside an 8 m amenity zone around the house. It is tried first on the road frontage (side or corner garden), otherwise in the rear garden with a 3.6 m drive that stays 1 m clear of the house. It is rejected if the owner would keep less than max(120 m², 35%) of their garden, or if separation is under 10 m. Flags: backland, classified road, plot under 55% of the neighbouring median plot size (character) |
| Replacement dwelling | House of 100 m² or less on a title of 1,000 m² or more |
| Settlement-edge paddock | No buildings, 600–20,000 m², outside but within 60 m of a built-up area, less than 10% built cover, with road frontage |
| Outbuilding or yard | Non-residential building of 80 m² or more, within 500 m of a built-up area |

Frontage counts only the boundary that faces the road, not side boundaries shared with
neighbours. A candidate with no identifiable access is dropped. The best 8 per area
(configurable) are kept. Each is stored as a site whose boundary is the **indicative new
plot**. The lead keeps the title extent, house, access strip, metrics, constraints, sources
and caveats. The full site engines (planning, risk, design fit, cost plan, residual) then run
as for any other site.

Everything inferred is labelled as such. Ownership in particular is **never** assumed
because land adjoins a house.

## 2. Ownership (`plot/offmarket/title.py`, `ownership.py`)

The lead says which INSPIRE ID to look up. You buy the **official copy of the register**
(about £7) from HM Land Registry, and upload the PDF. PLOT parses it deterministically:

- title number, tenure, class of title and edition;
- the property description and address;
- proprietors: individuals or companies (with Co. Regn. No.) and the "of" address;
- price paid;
- restrictions, registered charges (and the lender), covenants, rights and easements, notices
  and options;
- personal representatives or trustees.

Each item is shown with the register's own wording. You then confirm two things: that the
title plan covers the land, and that the owner details match the document. Only then is a
verified **Contact** created (source `title_register`, linked to the document). The greeting
uses first names only, never an assumed Mr/Mrs/Ms. For company proprietors, Companies House
can be queried for the registered office and status (needs a free API key).

CCOD/OCOD (HMLR's company-ownership datasets) are **not integrated**. They are keyed by title
number, and the INSPIRE ID is not a title number, so they can't link a parcel to an owner
reliably.

## 3. Off-Market Acquisition Score (`plot/offmarket/score.py`)

| Component | Max | Driven by |
|---|---|---|
| Residential planning potential | 30 | Screening class, infill vs backland, countryside location, conservation area / TPO / Green Belt / National Park / National Landscape, past refusals |
| Physical subdivision / redevelopment | 25 | Access type and frontage, plot size, garden kept, separation, character vs neighbours, whether the house concept fits, flood |
| Total development economics | 20 | Residual land value against your all-in budget |
| Ownership & acquisition practicality | 15 | Verification status, owner keeps their home, charges, covenants, easements, personal representatives |
| Suitability for your future home | 10 | Location score, design fit |

**Evidence confidence** is scored separately. It rises with checked designations, building
data, verified ownership and market evidence. It is **capped at 0.45** (medium at best) when
access is a backland strip, a private lane or unknown, or when no credible planning route has
been established. An attractive-looking parcel with doubtful access is therefore never shown
as a high-confidence opportunity. Fatal flags from the site engines carry through.

Owner benefits are possibilities, for example: "This could potentially allow the owner to
keep their home while releasing value from part of the garden." Nothing is assumed about the
owner's age, health, finances or motives.

## 4. The writer (`plot/outreach/writer.py`)

Facts are built only from verified sources:
- the register: names, property, title number;
- the analysis: observations, phrased tentatively;
- your **confirmed** buyer profile: persona (private buyer / "my wife and I" / professionally
  represented), returning to the UK, any local connection or funding statement you entered.

Two generators produce the draft:
- **Claude Opus 5.5** (`claude-opus-5-5`, structured output), when an Anthropic API key is
  set. If its draft fails validation, it gets one repair attempt.
- **Composer**: a deterministic writer used otherwise, and as the fallback. It builds the
  letter from the situation (opportunity type, recipient type, channel, persona, constraints)
  with varied phrasing, so neighbours never get the same template with the address swapped.

**Validation.** A draft that fails any of these can't be approved:
- **Length:** 80–140 words for a first approach. Follow-ups and replies have their own ranges.
- **Banned wording:** the phrases you listed and other sales clichés; urgency; invented
  familiarity ("as discussed", "walked past"); references to the owner's personal
  circumstances.
- **Unconfirmed claims:** cash buyer, funded, quick or chain-free, local, planning certainty.
  These are allowed only if your confirmed statements say so.
- **Prices:** any figure is blocked unless you explicitly approve it.
- **Questions:** at most one.
- **Signature and persona:** the message is signed with your name and its wording matches
  your persona.
- **Privacy notice:** present and complete. It says who you are, where the details came from
  (the HM Land Registry register), that you are writing once on legitimate interests, the
  retention date, rights, a one-line opt-out quoting a reference, and the ICO.

You can edit any draft, regenerate it with instructions, or reject it.

## 5. Compliance and approval (`plot/outreach/compliance.py`)

These checks run on every draft, and again at approval and at sending:

| Check | Rule |
|---|---|
| Buyer profile | Your identity facts are confirmed, with a reply address or email |
| Recipient verified | Verified contact from a verified official copy (or the owner themselves) |
| Recipient type | Unknown → blocked. Personal representatives, trustees, charities, partnerships → **manual compliance review** (you record why contact is appropriate) |
| Channel | **Post** to a verified proprietor, with your legitimate interests assessment recorded. **Email** to an individual only if the owner emailed first or consent is recorded (a published address is never permission). Email to a company or public body as a corporate subscriber, still with identity and opt-out |
| Do-not-contact | Name+address, address and email hashes (permanent) |
| Owner state | Never after an opt-out. Never after a decline, except a reply |
| Frequency | Minimum 21 days between contacts, at most 2 per year, one follow-up only, no follow-up after any reply, no repeat first approach |
| Duplicates | No other pending message to the same recipient |
| Retention | Within the retention period (default 6 months; `plot retention-sweep` and the daily run delete expired personal data unless a conversation is live) |
| Sensitive | A title held by personal representatives → manual review |
| Content | Writer validation; no price in a first approach |

**Approval** binds content to recipient: `approved_hash = H(content_hash, recipient_hash)`.
- You approve one message at a time and confirm the recipient shown. The API also checks the
  recipient hash you confirmed. There is **no bulk-approval endpoint**.
- Editing the message, or changing the recipient's details, clears the approval.
- The send gate recomputes both hashes and re-runs every check before anything leaves.

## 6. Delivery (`plot/outreach/letters.py`, `delivery.py`, `mail.py`)

**Letters.** Letters are PDFs: A4, address block positioned for a DL window envelope, privacy
notice at the foot, reference number.
- Until approval the PDF carries a **DRAFT** watermark, and the printable version doesn't
  exist.
- After you post the approved letter, you record the date and postage. This is shown as
  *self-declared*, because Royal Mail delivery isn't tracked.
- Optionally the letter can go to **Stannp** (print and post). You confirm the cost first,
  and the adapter starts in test mode, where Stannp produces a proof only. The Stannp request
  format is **unverified against the live API** until first use.

**Email.** Email goes through Gmail or Microsoft 365, connected by OAuth 2.0 with PKCE and
minimum scopes; tokens are encrypted at rest.
- PLOT can copy a draft into your mailbox's Drafts folder.
- Sending transmits exactly the approved MIME message. Before calling the provider, PLOT sets
  an idempotency key and commits, so a crash can never cause a silent resend.
- The provider's own message ID and thread ID are recorded. "Sent" means **accepted by the
  provider**, not delivered or read.
- A timeout or 5xx response becomes **delivery unconfirmed**. PLOT never retries on its own.
  "Check mailbox" searches for the message's own Message-ID. If you confirm it was not sent,
  the message must be approved again before a new attempt.

## 7. Replies (`plot/outreach/replies.py`)

Replies arrive in two ways: synced from the threads of messages PLOT sent (read-only), or
logged by you for letters, calls and meetings.

**Categories.** Interested, possibly interested, wants information, requests a price, not
interested, requests no further contact, or unknown. Rules decide the category, with an
optional Claude second opinion whose quote must appear verbatim in the reply. **An opt-out
always wins.**

**What happens next:**
- **Opt-out:** permanent suppression, all pending drafts withdrawn, lead closed, no reply
  drafted.
- **Decline:** suppression and the lead is closed. You can re-categorise a misread decline,
  which lifts the automatic suppression.
- **Interest, a question or a price request:** PLOT drafts a suggested reply for your
  approval. It asks about boundaries, access, expectations, planning conditionality and
  timing. It never contains a figure.
- **Price request:** a private **negotiation brief** is prepared:
  - walk-away price (maximum viable land price) and a prudent figure;
  - opening range and comparables;
  - the biggest value risks and the unknowns to resolve;
  - conditional, option and overage structures, and questions for the owner.

  It is never sent.

**Decline reasons.** These describe the deal (price, privacy, planning, timing, not selling).
They feed into qualification tips; owners are never profiled personally.

## 8. CRM and daily briefing

`/offmarket` shows:
- **Briefing:** a one-sentence summary, for example "Three promising off-market sites found.
  Two owner letters ready for review. One owner has replied with interest or questions. One
  opportunity has been downgraded."
- **Real-count KPIs:** viable prospects, new this week, drafts awaiting approval, letters
  posted and emails sent, response and positive-interest rates, follow-ups due, active
  negotiations.
- **Shortlist cards:** parcel plan, indicative plot, access, planning, constraints, ownership
  status, maximum viable price, owner benefit, contact method, draft status and next action.
- **Other tabs:** a 13-stage board, estimated land values, decline reasons, and the
  do-not-contact list.

Each prospect has a detail page with the ownership upload and verification, the recipient,
the message editor and approval, correspondence, reply logging, the brief and the history.
`/offmarket/approvals` is the one-at-a-time approval queue.

The 07:00 run adds step **7b**: sync leads, run the retention sweep, sync replies, rescore,
and prepare up to five drafts (follow-ups first) for eligible verified owners. It **never
sends**. The daily digest carries the acquisition briefing.

## 9. Sandbox and tests

**Sandbox.** The sandbox is a separate environment: `PLOT_ENV=sandbox` with its own database.
It contains a synthetic street analysed by the real engine, and mock registers naming
obviously fictitious "Sandbox-Test" owners at example addresses. Mailbox connections and
postal submission are disabled, and a banner is shown on every page.

```bash
createdb plot_sandbox && psql -d plot_sandbox -c "CREATE EXTENSION postgis"
export PLOT_ENV=sandbox PLOT_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/plot_sandbox
plot migrate && plot seed && plot sandbox-seed && plot api
```

**Tests.**
- `tests/test_offmarket.py`: analyser and scorer.
- `tests/test_outreach_units.py`: register parser, writer validation and variety, reply
  classification.
- `tests/test_outreach_e2e.py` drives the **whole workflow through the real daily run and the
  API**:
  - discover → verify a mock register PDF → assess → draft → approve (wrong recipient hash
    refused; edit clears approval) → final PDF → mark posted → log a reply → suggested reply
    → price request → negotiation brief;
  - the email path through the real OAuth code against a mocked Gmail: send, no resend, and
    an opt-out synced from the thread that suppresses and closes the lead;
  - an ambiguous send reconciled by Message-ID;
  - suppression and recipient changes invalidating approval.

## What has not been verified live

From the build environment, the egress proxy refuses planning.data.gov.uk, Overpass,
Companies House and Stannp (HTTP 403). So discovery on real parcels, Companies House and
Stannp have been exercised only against documented contracts and mocks. Gmail and Graph
calls follow the published REST APIs, but have not been run against a real mailbox, because
no OAuth client is configured. Verifying a real owner needs an official copy that you buy
yourself.

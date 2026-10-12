"""Separate test environment (PLOT_ENV=sandbox, its own database) with clearly labelled
synthetic data: a made-up street analysed by the real discovery engine, and mock HM Land
Registry registers naming obviously fictitious "Sandbox-Test" owners at example addresses.

Nothing here is real, and mailbox connections and postal submission are disabled in the
sandbox, so mock contacts can never be written to.
"""

from __future__ import annotations

import io

from shapely.geometry import LineString, Polygon, box
from sqlalchemy import select
from sqlalchemy.orm import Session

from plot.offmarket.analyse import AreaData, Feature

OX, OY = 420000.0, 110000.0  # BNG origin for the synthetic street (geometry is invented)
AREA = {"name": "TEST sandbox street", "lon": None, "lat": None, "radius_m": 400}
MOCK_LABEL = "TEST DATA — mock contact for the sandbox; not a real person"


def b(x1: float, y1: float, x2: float, y2: float) -> Polygon:
    return box(OX + x1, OY + y1, OX + x2, OY + y2)


def street_area(with_tpo_on_wide_plot: bool = False) -> AreaData:
    """Synthetic street (BNG metres). See tests/test_offmarket.py for what each plot exercises."""
    titles, buildings = [], []
    for i in range(10):
        x = -150 + i * 15
        titles.append(Feature(f"T{i}", b(x, 5, x + 15, 45)))
        buildings.append(Feature(f"H{i}", b(x + 3, 9, x + 12, 19), {"building": "house"}))
    titles.append(Feature("DEEP", b(100, 5, 118, 115)))
    buildings.append(Feature("HDEEP", b(104, 9, 114, 19), {"building": "house"}))
    titles.append(Feature("BACK", b(20, 5, 46, 105)))
    buildings.append(Feature("HBACK", b(21, 9, 31, 19), {"building": "house"}))
    titles.append(Feature("WIDE", b(150, 5, 200, 45)))
    buildings.append(Feature("HWIDE", b(153, 9, 163, 19), {"building": "detached"}))
    titles.append(Feature("PADDOCK", b(205, 5, 245, 60)))
    roads = [Feature("R1", LineString([(OX - 150, OY), (OX + 250, OY)]), {"highway": "residential", "name": "Test Road"})]
    bua = [Feature("BUA", b(-160, -50, 202, 120), {"name": "Testville"})]
    constraints = {}
    if with_tpo_on_wide_plot:
        constraints["tree-preservation-zone"] = [Feature("TPO1", b(170, 5, 200, 45), {"name": "TPO test"})]
    return AreaData(titles=titles, buildings=buildings, roads=roads, constraints=constraints, built_up=bua, applications=[], sources={"titles": "TEST synthetic titles", "buildings": "TEST synthetic buildings", "roads": "TEST synthetic roads"})


REGISTER_GARDEN = """TEST DATA - SYNTHETIC REGISTER FOR THE SANDBOX - NOT A REAL TITLE
Official copy of register of title
Title number TT123456 Edition date 14.06.2019
- This official copy shows the entries on the register of title on 05 Oct 2026 at 10:15:22.
- This date must be quoted as the "search from date" in any official search application based on this copy.
A: Property Register
This register describes the land and estate comprised in the title.
HAMPSHIRE : NEW FOREST
1 (12 March 1985) The Freehold land shown edged with red on the plan of the above Title filed at
the Registry and being 12 Test Road, Testville (TE1 1ST).
2 (12 March 1985) The land has the benefit of a right of way on foot only over the passageway
tinted brown on the title plan.
B: Proprietorship Register
This register specifies the class of title and identifies the owner. It contains any entries that
affect the right of disposal.
Title absolute
1 (14 June 2019) PROPRIETOR: ALEX SANDBOX-TEST and SAM SANDBOX-TEST of 12 Test Road,
Testville TE1 1ST.
2 (14 June 2019) The price stated to have been paid on 30 May 2019 was £465,000.
3 (14 June 2019) RESTRICTION: No disposition by a sole proprietor of the registered estate
(except a trust corporation) under which capital money arises is to be registered unless
authorised by an order of the court.
C: Charges Register
This register contains any charges and other matters that affect the land.
1 (12 March 1985) A Conveyance of the land in this title dated 1 June 1960 made between (1)
Test Estates Limited and (2) Pat Example contains covenants details of which are set out in the
schedule of restrictive covenants annexed hereto.
2 (14 June 2019) REGISTERED CHARGE dated 30 May 2019.
3 (14 June 2019) Proprietor: EXAMPLE TEST BANK PLC (Co. Regn. No. 01234567) of 1 Test Street,
London EC1A 9ZZ.
End of register
"""

REGISTER_PADDOCK = """TEST DATA - SYNTHETIC REGISTER FOR THE SANDBOX - NOT A REAL TITLE
Official copy of register of title
Title number TT765432
Edition date 02.02.2022
- This official copy shows the entries on the register of title on 01 Oct 2026 at 09:00:01.
A: Property Register
This register describes the land and estate comprised in the title.
HAMPSHIRE : NEW FOREST
1 (02 February 2022) The Freehold land shown edged with red on the plan of the above Title filed
at the Registry and being land on the north side of Test Road, Testville.
B: Proprietorship Register
This register specifies the class of title and identifies the owner. It contains any entries that
affect the right of disposal.
Title absolute
1 (02 February 2022) PROPRIETOR: SANDBOX TEST LAND LIMITED (Co. Regn. No. 09876543) of
Unit 4, Test Business Park, Testville TE1 9ZZ.
2 (02 February 2022) The price stated to have been paid on 10 January 2022 was £60,000.
C: Charges Register
This register contains any charges and other matters that affect the land.
End of register
"""


def register_pdf(text: str) -> bytes:
    """Render a mock register as a PDF (exercises the real PDF upload/extraction path)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen.canvas import Canvas

    buf = io.BytesIO()
    c = Canvas(buf, pagesize=A4, invariant=1)
    c.setFont("Helvetica", 9)
    y = A4[1] - 20 * mm
    for line in text.splitlines():
        if y < 20 * mm:
            c.showPage()
            c.setFont("Helvetica", 9)
            y = A4[1] - 20 * mm
        c.drawString(18 * mm, y, line)
        y -= 4.6 * mm
    c.showPage()
    c.save()
    return buf.getvalue()


def configure_test_buyer(db: Session) -> None:
    from plot.preferences import load_preferences, save_preferences

    prefs, _ = load_preferences(db)
    prefs.buyer.first_name = prefs.buyer.first_name or "Sai"
    prefs.buyer.last_name = prefs.buyer.last_name or "Sandbox"
    prefs.buyer.reply_email = prefs.buyer.reply_email or "sai.sandbox@example.invalid"
    prefs.buyer.reply_postal_address = prefs.buyer.reply_postal_address or "TEST ADDRESS, 1 Example Street, Testville TE1 0AA"
    prefs.buyer.facts_confirmed = True
    save_preferences(db, prefs, "sandbox-seed")


def seed(db: Session, actor: str = "sandbox-seed") -> dict:
    """Discover the synthetic street with the real engine, assess, score and attach mock registers."""
    from plot import geo
    from plot.engine.assess import assess_site, rerank
    from plot.engine.costs.rates import load_rates, seed_rates
    from plot.ingest import upsert
    from plot.models import Lead, Site
    from plot.offmarket import agent, ownership
    from plot.offmarket.analyse import Analyser
    from plot.preferences import ProspectingArea, load_preferences
    from plot.sources.registry import sync_sources

    sync_sources(db)
    seed_rates(db)
    configure_test_buyer(db)
    lon, lat = geo.bng_to_lonlat(OX + 50, OY + 30)
    area = ProspectingArea(name=AREA["name"], lon=lon, lat=lat, radius_m=AREA["radius_m"])
    data = street_area()
    created = []
    for c in agent.select_candidates(Analyser(data).run(), 8):
        rec = agent.candidate_record(c, area, data.sources)
        rec.title = "TEST — " + rec.title
        site, _ = upsert(db, rec, None)
        created.append(site.id)
    db.flush()
    prefs, v = load_preferences(db)
    rc = load_rates(db)
    for sid in created:
        assess_site(db, db.get(Site, sid), prefs, v, rates_cache=rc, force=True)
    agent.sync_leads(db)
    rerank(db)
    agent.rescore_all(db)
    attached = {}
    for lead in db.scalars(select(Lead).where(Lead.site_id.in_(created))).all():
        tid = (lead.analysis or {}).get("title_id")
        text = {"WIDE": REGISTER_GARDEN, "PADDOCK": REGISTER_PADDOCK}.get(tid)
        if text and not lead.ownership_status == "verified":
            doc = ownership.upload_title(db, lead, f"TEST-register-{tid}.pdf", register_pdf(text), actor, record_fee=False)
            attached[tid] = {"lead_id": lead.id, "title_document_id": doc.id}
    db.flush()
    return {"sites": created, "registers": attached, "note": MOCK_LABEL}

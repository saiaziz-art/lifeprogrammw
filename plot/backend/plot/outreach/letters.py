"""Printable A4 letters (PDF) for owner outreach.

Layout follows common UK practice for a DL window envelope (address block ~25 mm from the
left edge and ~48 mm from the top, A4 folded in three). Until a letter is approved the PDF
carries a DRAFT watermark and cannot be marked as posted.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
from xml.sax.saxutils import escape

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Frame, Paragraph

from plot.models import Outreach

INK = HexColor("#1f2421")
MUTED = HexColor("#5b625d")
BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=10.8, leading=15.2, textColor=INK, spaceAfter=8.5)
SMALL = ParagraphStyle("small", fontName="Helvetica", fontSize=7.4, leading=9.6, textColor=MUTED)
SENDER = ParagraphStyle("sender", fontName="Helvetica", fontSize=9.2, leading=12.4, textColor=MUTED, alignment=2)
ADDR = ParagraphStyle("addr", fontName="Helvetica", fontSize=10.5, leading=13.6, textColor=INK)
SUBJ = ParagraphStyle("subj", fontName="Helvetica-Bold", fontSize=10.8, leading=15, textColor=INK, spaceAfter=10)


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def render_letter(o: Outreach, *, sender_name: str, sender_address: str | None, sender_email: str | None, sender_phone: str | None, letter_date: dt.date, draft: bool) -> bytes:
    buf = io.BytesIO()
    W, H = A4
    c = Canvas(buf, pagesize=A4, pageCompression=1, invariant=1)
    c.setTitle(f"Letter {o.generator_meta.get('reference', o.id) if o.generator_meta else o.id}")
    c.setAuthor(sender_name)
    c.setCreator("PLOT")

    # sender block (top right)
    sender_lines = [sender_name] + [x.strip() for x in (sender_address or "").replace(",", "\n").splitlines() if x.strip()]
    contact = [x for x in (sender_email, sender_phone) if x]
    sp = _p("\n".join(sender_lines + contact), SENDER)
    w, h = sp.wrap(70 * mm, 60 * mm)
    sp.drawOn(c, W - 20 * mm - w, H - 18 * mm - h)

    # recipient block in the envelope window
    rp = _p(o.recipient_name + "\n" + (o.recipient_address or ""), ADDR)
    w, h = rp.wrap(85 * mm, 40 * mm)
    rp.drawOn(c, 25 * mm, H - 48 * mm - h)

    # date
    c.setFont("Helvetica", 10.5)
    c.setFillColor(INK)
    c.drawString(25 * mm, H - 98 * mm, f"{letter_date.day} {letter_date:%B %Y}")

    # privacy / opt-out notice at the foot
    ref = (o.generator_meta or {}).get("reference", f"#{o.id}")
    foot = _p((o.footer or "") + f"\nReference: {ref}", SMALL)
    fw, fh = foot.wrap(W - 50 * mm, 60 * mm)
    c.setStrokeColor(HexColor("#d7dbd6"))
    c.line(25 * mm, 14 * mm + fh + 4, W - 25 * mm, 14 * mm + fh + 4)
    foot.drawOn(c, 25 * mm, 14 * mm)

    # body
    story = []
    if o.subject:
        story.append(_p(o.subject, SUBJ))
    for para in [p for p in (o.body or "").split("\n\n") if p.strip()]:
        story.append(_p(para.strip(), BODY))
    top = H - 106 * mm
    bottom = 14 * mm + fh + 10
    frame = Frame(25 * mm, bottom, W - 50 * mm, top - bottom, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, showBoundary=0)
    rest = frame.addFromList(story, c)
    if draft:
        _watermark(c, W, H)
    while story:  # overflow onto continuation pages
        c.showPage()
        frame = Frame(25 * mm, 20 * mm, W - 50 * mm, H - 40 * mm, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        frame.addFromList(story, c)
        if draft:
            _watermark(c, W, H)
    del rest
    c.showPage()
    c.save()
    return buf.getvalue()


def _watermark(c: Canvas, W: float, H: float) -> None:
    c.saveState()
    c.setFillColor(Color(0.75, 0.1, 0.1, alpha=0.13))
    c.setFont("Helvetica-Bold", 92)
    c.translate(W / 2, H / 2)
    c.rotate(35)
    c.drawCentredString(0, 0, "DRAFT")
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(0, -40, "Not approved — do not post")
    c.restoreState()


def pdf_sha(pdf: bytes) -> str:
    return hashlib.sha256(pdf).hexdigest()

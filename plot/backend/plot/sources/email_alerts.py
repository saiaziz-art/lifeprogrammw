"""Alert-email ingestion — the lawful route for portal, agent and auction listings.

You subscribe to saved-search alerts on Rightmove/Zoopla/OnTheMarket, plot directories,
agents and auction houses, and forward them to a mailbox PLOT can read (IMAP), or upload
.eml files. PLOT records each listing's canonical URL, the headline facts quoted in the
email, and links back to the original — it does not visit or copy the portal page.

Location: an alert email rarely contains coordinates. PLOT geocodes a verbatim postcode
(exact) or a place name (approximate, flagged). Listings it cannot place go to the
ingest inbox for you to drop a pin.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import html
import imaplib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from email.message import EmailMessage
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from sqlalchemy import select

from plot.models import IngestInbox, ProcessedMessage
from plot.sources.base import CandidateRecord, DiscoverySource, RunContext, SourceError, SourceMeta
from plot.sources.http import PublicHttp

PORTAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("rightmove", re.compile(r"https?://(?:www\.)?rightmove\.co\.uk/properties/(\d+)", re.I)),
    ("zoopla", re.compile(r"https?://(?:www\.)?zoopla\.co\.uk/(?:for-sale|new-homes)/details/(\d+)", re.I)),
    ("onthemarket", re.compile(r"https?://(?:www\.)?onthemarket\.com/details/(\d+)", re.I)),
    ("primelocation", re.compile(r"https?://(?:www\.)?primelocation\.com/(?:for-sale|new-homes)/details/(\d+)", re.I)),
    ("plotfinder", re.compile(r"https?://(?:www\.)?plotfinder\.net/[^\s\"'<>]*?(\d{4,})", re.I)),
    ("plotbrowser", re.compile(r"https?://(?:www\.)?plotbrowser\.com/[^\s\"'<>]*?(\d{3,})", re.I)),
]
# generic: any link whose path looks like a property/lot detail page
GENERIC_LISTING = re.compile(r"https?://[^\s\"'<>]+/(?:property|properties|lot|lots|details|listing|plot)s?/[^\s\"'<>]+", re.I)

PRICE_RE = re.compile(r"(?:(guide price|offers over|offers in excess of|offers in the region of|oieo|oiro|asking price|fixed price|price)\s*:?\s*)?(?:£|GBP\s?)\s?(\d{1,3}(?:,\d{3})+|\d{4,})(?!\d)", re.I)
POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b", re.I)
AREA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(acres?|hectares?|ha\b|sq\.?\s?ft|sq\.?\s?m|m²)", re.I)
LAND_WORDS = re.compile(r"\b(plot|land|building plot|barn|paddock|acre|woodland|site|for conversion|planning permission|derelict|renovation|bungalow|cottage)\b", re.I)

_QUALIFIERS = {
    "guide price": "guide",
    "offers over": "offers_over",
    "offers in excess of": "offers_over",
    "offers in the region of": "offers_in_region",
    "oieo": "offers_over",
    "oiro": "offers_in_region",
    "asking price": "asking",
    "fixed price": "fixed",
    "price": "asking",
}


class _Text(HTMLParser):
    """Collects text and anchors in document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[tuple[str, str]] = []  # ("text"|"link", value)
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("style", "script", "head"):
            self._skip += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.chunks.append(("link", href))
        if tag in ("br", "p", "div", "tr", "td", "li", "h1", "h2", "h3", "table"):
            self.chunks.append(("text", "\n"))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "script", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.chunks.append(("text", data))


def unwrap_tracking(url: str) -> str:
    """Alert emails wrap links in click-trackers; recover an embedded destination if present."""
    url = html.unescape(url)
    for _ in range(3):
        q = parse_qs(urlparse(url).query)
        inner = next((v[0] for k, v in q.items() if k.lower() in ("url", "u", "redirect", "dest", "destination", "target", "r") and v and v[0].startswith("http")), None)
        if not inner:
            break
        url = unquote(inner)
    return url


@dataclass
class ParsedListing:
    portal: str
    ref: str
    url: str
    text: str
    asking_price: int | None = None
    price_qualifier: str | None = None
    postcode: str | None = None
    area_m2: float | None = None
    area_as_written: str | None = None
    land_like: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def _message_parts(msg: EmailMessage) -> tuple[str | None, str | None]:
    html_body = text_body = None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if part.get_content_disposition() == "attachment":
            continue
        try:
            payload = part.get_content()
        except (LookupError, KeyError):
            payload = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
        if ctype == "text/html" and html_body is None:
            html_body = payload
        elif ctype == "text/plain" and text_body is None:
            text_body = payload
    return html_body, text_body


def _area(text: str) -> tuple[float | None, str | None]:
    m = AREA_RE.search(text)
    if not m:
        return None, None
    v, unit = float(m.group(1)), m.group(2).lower()
    if unit.startswith("acre"):
        return v * 4046.8564224, m.group(0)
    if unit.startswith("hect") or unit == "ha":
        return v * 10_000, m.group(0)
    if "ft" in unit:
        return v * 0.09290304, m.group(0)
    return v, m.group(0)


def parse_alert(msg: EmailMessage) -> list[ParsedListing]:
    html_body, text_body = _message_parts(msg)
    chunks: list[tuple[str, str]]
    if html_body:
        p = _Text()
        p.feed(html_body)
        chunks = p.chunks
    else:
        chunks = []
        for token in re.split(r"(https?://\S+)", text_body or ""):
            chunks.append(("link" if token.startswith("http") else "text", token))

    # locate listing links, then take the text between consecutive listing links as the card
    positions: list[tuple[int, str, str, str]] = []
    for i, (kind, val) in enumerate(chunks):
        if kind != "link":
            continue
        url = unwrap_tracking(val)
        for portal, pat in PORTAL_PATTERNS:
            m = pat.search(url)
            if m:
                positions.append((i, portal, m.group(1), m.group(0)))
                break
        else:
            g = GENERIC_LISTING.search(url)
            if g and not any(s in url.lower() for s in ("unsubscribe", "preferences", "privacy", "login", "account")):
                positions.append((i, urlparse(url).netloc.lower(), hashlib.sha1(g.group(0).encode()).hexdigest()[:16], g.group(0)))

    # collapse repeated links to the same listing (image + title links)
    collapsed: list[tuple[int, str, str, str]] = []
    for pos in positions:
        if collapsed and (collapsed[-1][1], collapsed[-1][2]) == (pos[1], pos[2]):
            continue
        collapsed.append(pos)

    def text_of(a: int, b: int) -> str:
        t = " ".join(v for k, v in chunks[a:b] if k == "text")
        return re.sub(r"[ \t\r\f\v]+", " ", t).strip()

    out: dict[tuple[str, str], ParsedListing] = {}
    for n, (idx, portal, ref, url) in enumerate(collapsed):
        nxt = collapsed[n + 1][0] if n + 1 < len(collapsed) else min(len(chunks), idx + 25)
        prv = collapsed[n - 1][0] + 1 if n > 0 else max(0, idx - 15)
        after = text_of(idx + 1, nxt)
        # alert layouts usually put the facts after the link; fall back to text before it
        card = after if PRICE_RE.search(after) else (text_of(prv, idx) + " " + after).strip()
        key = (portal, ref)
        if key in out:
            continue
        pm = PRICE_RE.search(card)
        pc = POSTCODE_RE.search(card)
        area, area_txt = _area(card)
        out[key] = ParsedListing(
            portal=portal,
            ref=ref,
            url=url,
            text=card[:2000],
            asking_price=int(pm.group(2).replace(",", "")) if pm else None,
            price_qualifier=_QUALIFIERS.get((pm.group(1) or "price").lower()) if pm else None,
            postcode=f"{pc.group(1).upper()} {pc.group(2).upper()}" if pc else None,
            area_m2=area,
            area_as_written=area_txt,
            land_like=bool(LAND_WORDS.search(card)),
        )
    return list(out.values())


def message_hash(msg: EmailMessage, raw: bytes) -> str:
    mid = msg.get("Message-ID")
    return hashlib.sha256((mid or "").encode() if mid else raw).hexdigest()


def _place_query(text: str) -> str | None:
    # A comma-separated address fragment ending in a town is common in alert cards: "Land at X, Sway, Lymington"
    parts = [p.strip() for p in re.split(r",|\n", text) if p.strip()]
    for p in reversed(parts[:6]):
        if re.fullmatch(r"[A-Z][A-Za-z' \-]{2,40}", p) and not LAND_WORDS.search(p):
            return p
    return None


class EmailAlertIngest(DiscoverySource):
    meta = SourceMeta(
        id="email.alerts",
        name="Listing alert emails (IMAP / .eml upload)",
        category="on_market",
        kind="email",
        licence="Your own received emails; listing content remains the advertiser's",
        attribution="Listing details quoted from alert emails; follow the original link for the advert.",
        notes="Subscribe to portal/agent/auction alerts and forward them to the configured mailbox. "
        "Stores canonical links and quoted headline facts only.",
        required_secrets=("imap_host", "imap_user", "imap_password"),
        default_config={"folder": "INBOX", "mark_seen": True, "max_messages": 200},
    )

    def discover(self, ctx: RunContext) -> Iterable[CandidateRecord]:
        host, user, pw = ctx.secrets["imap_host"], ctx.secrets["imap_user"], ctx.secrets["imap_password"]
        try:
            conn = imaplib.IMAP4_SSL(host, timeout=60)
            conn.login(user, pw)
        except (OSError, imaplib.IMAP4.error) as exc:
            raise SourceError("imap_login_failed", f"could not log in to {host}: {exc}") from exc
        try:
            conn.select(ctx.config.get("folder", "INBOX"))
            typ, data = conn.search(None, "UNSEEN")
            if typ != "OK":
                raise SourceError("imap_error", f"IMAP search failed: {typ}")
            ids = (data[0] or b"").split()[: ctx.config.get("max_messages", 200)]
            raws = []
            for mid in ids:
                typ, msg_data = conn.fetch(mid, "(RFC822)")
                if typ == "OK" and msg_data and isinstance(msg_data[0], tuple):
                    raws.append(msg_data[0][1])
                    if ctx.config.get("mark_seen", True):
                        conn.store(mid, "+FLAGS", "\\Seen")
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass
        yield from ingest_raw_messages(ctx, raws, self.meta.id)


def ingest_raw_messages(ctx: RunContext, raws: list[bytes], source_id: str) -> Iterable[CandidateRecord]:
    """Shared by IMAP polling and .eml upload. Yields located listings; parks unlocated ones."""
    from plot.ai.extract import Extractor

    extractor = Extractor(ctx.secrets.get("anthropic_api_key"))
    http: PublicHttp | None = None
    try:
        for raw in raws:
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            h = message_hash(msg, raw)
            if ctx.db.scalar(select(ProcessedMessage).where(ProcessedMessage.message_hash == h)):
                continue
            listings = parse_alert(msg)
            ctx.db.add(ProcessedMessage(message_hash=h, source_id=source_id, listings_found=len(listings)))
            for pl in listings:
                fields: dict[str, Any] = {}
                rejected: list[str] = []
                ai = extractor.extract_listing(pl.text)
                if ai:
                    fields, rejected = ai
                price = pl.asking_price if pl.asking_price is not None else fields.get("asking_price")
                postcode = pl.postcode or fields.get("postcode")
                area = pl.area_m2 or fields.get("area_m2")
                title = fields.get("title") or (pl.text[:120] if pl.text else f"{pl.portal} listing {pl.ref}")
                lon = lat = None
                loc_quality = None
                reason = None
                try:
                    if http is None:
                        http = PublicHttp(ctx.db)
                    from plot.sources.postcodes import lookup_postcode, search_place

                    if postcode:
                        r = lookup_postcode(http, postcode)
                        if r:
                            lon, lat, loc_quality = r["longitude"], r["latitude"], "postcode_centroid"
                    if lon is None:
                        q = fields.get("address_text") or _place_query(pl.text)
                        if q:
                            r = search_place(http, q)
                            if r:
                                lon, lat, loc_quality = r["longitude"], r["latitude"], "place_centroid_approximate"
                    if lon is None:
                        reason = "no postcode or recognisable place name in the alert text"
                except SourceError as e:
                    reason = f"geocoding failed: {e}"
                ref = f"{pl.portal}:{pl.ref}"
                if lon is None:
                    if not ctx.db.scalar(select(IngestInbox).where(IngestInbox.source_id == source_id, IngestInbox.external_ref == ref)):
                        ctx.db.add(IngestInbox(source_id=source_id, external_ref=ref, url=pl.url, title=title[:500], asking_price=price, text=pl.text, reason=reason or "unlocated"))
                    continue
                yield CandidateRecord(
                    source_id=source_id,
                    external_ref=ref,
                    record_type="listing",
                    lon=lon,
                    lat=lat,
                    title=title[:300],
                    category="plot" if pl.land_like else "other",
                    postcode=postcode,
                    address=fields.get("address_text"),
                    area_m2=area,
                    description=pl.text,
                    url=pl.url,
                    asking_price=price,
                    price_qualifier=pl.price_qualifier or fields.get("price_qualifier"),
                    tenure=fields.get("tenure"),
                    publisher=pl.portal,
                    licence="Advertiser's content (quoted from your alert email)",
                    attribution=f"Listing advertised on {pl.portal}; see original link.",
                    confidence="medium" if loc_quality == "postcode_centroid" else "low",
                    signals=["on_market", f"location:{loc_quality}"],
                    raw={"portal": pl.portal, "location_quality": loc_quality, "area_as_written": pl.area_as_written, "ai_rejected": rejected, "ai_used": bool(ai)},
                )
    finally:
        if http is not None:
            http.close()

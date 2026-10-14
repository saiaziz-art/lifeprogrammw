"""Sources that cannot lawfully be automated without permission.

Each is registered so the admin screen shows it, why it is blocked, and the lawful route
PLOT supports instead (alert-email ingestion, manual import, or a licensed feed).
PLOT never scrapes portals or bypasses technical measures.
"""

from __future__ import annotations

from collections.abc import Iterable

from plot.sources.base import CandidateRecord, DiscoverySource, RunContext, SourceBlocked, SourceMeta


def _blocked(id_: str, name: str, terms_url: str, reason: str, alternative: str) -> type[DiscoverySource]:
    meta = SourceMeta(
        id=id_,
        name=name,
        category="on_market",
        kind="blocked",
        licence="Proprietary — no public reuse licence",
        attribution="",
        terms_url=terms_url,
        notes=f"{reason} Lawful route: {alternative}",
        default_enabled=True,
    )

    class _Blocked(DiscoverySource):
        def discover(self, ctx: RunContext) -> Iterable[CandidateRecord]:
            raise SourceBlocked(f"{reason} Use instead: {alternative}")

    _Blocked.meta = meta
    _Blocked.__name__ = "Blocked_" + id_.replace(".", "_")
    return _Blocked


Rightmove = _blocked(
    "portal.rightmove",
    "Rightmove",
    "https://www.rightmove.co.uk/this-site/terms-of-use.html",
    "No public consumer API; automated extraction is not permitted by the site terms.",
    "Rightmove saved-search alert emails forwarded to the PLOT inbox, or paste listing URLs into Manual import.",
)
Zoopla = _blocked(
    "portal.zoopla",
    "Zoopla",
    "https://www.zoopla.co.uk/terms/",
    "Public listings API was withdrawn; automated extraction is not permitted by the site terms.",
    "Zoopla alert emails forwarded to the PLOT inbox, or Manual import.",
)
OnTheMarket = _blocked(
    "portal.onthemarket",
    "OnTheMarket",
    "https://www.onthemarket.com/terms/",
    "No public consumer API.",
    "OnTheMarket alert emails, or Manual import.",
)
Plotfinder = _blocked(
    "portal.plotfinder",
    "Plotfinder / self-build plot directories",
    "https://www.plotfinder.net/",
    "Subscription directories without a reuse licence or API.",
    "Directory alert emails (if your subscription sends them) or Manual import.",
)
Auctions = _blocked(
    "auctions.catalogues",
    "Auction catalogues (regional & national auction houses)",
    "https://www.eigroup.co.uk/",
    "Catalogue terms vary by auctioneer; aggregated auction data (e.g. EIG) is a paid licence.",
    "Auction-house alert emails, Manual import of lots, or configure a licensed feed adapter once permission is obtained.",
)
AgentSites = _blocked(
    "agents.websites",
    "Estate & rural land agent websites",
    "",
    "Individual agent sites have their own terms; crawling them is not enabled without written permission.",
    "Register for agents' email alerts (forward to PLOT inbox) or Manual import; add an agent feed adapter where an agent grants permission.",
)

BLOCKED_SOURCES = [Rightmove, Zoopla, OnTheMarket, Plotfinder, Auctions, AgentSites]

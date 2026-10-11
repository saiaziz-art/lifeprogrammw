"""Jurisdiction-neutral planning result types and the jurisdiction registry.

England is implemented; Wales (PPW/TAN), Scotland (NPF4) and Northern Ireland (SPPS) plug
in by registering their own Jurisdiction with the same output contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

CLASSES = {
    "permission_verified": "Existing lawful residential use / permission verified",
    "approval_exists_review": "Residential approval exists, conditions require review",
    "plausible_route": "Plausible planning route requiring professional assessment",
    "speculative_high_risk": "Speculative or high planning risk",
    "significant_obstacle": "Significant planning obstacle / unsuitable",
}

EFFECT_ORDER = {"fatal": 0, "major": 1, "moderate": 2, "minor": 3, "info": 4, "positive": 5}


@dataclass
class Finding:
    code: str
    title: str
    effect: str  # fatal | major | moderate | minor | info | positive
    status: str  # confirmed | estimated | unknown | requires_investigation
    detail: str
    policy: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)  # [{title,url,date,source_id}]
    action: str | None = None


@dataclass
class Route:
    code: str
    name: str
    plausibility: str  # strong | possible | weak | excluded
    detail: str


@dataclass
class Obligation:
    code: str
    name: str
    applies: str  # yes | likely | possible | no | unknown
    detail: str
    cost_key: str | None = None


@dataclass
class PlanningResult:
    jurisdiction: str
    classification: str
    classification_label: str
    score: float  # 0..max (planning weight)
    max_score: float
    findings: list[Finding]
    routes: list[Route]
    obligations: list[Obligation]
    lpa: str | None
    confidence: float
    checks: dict[str, str]
    actions: list[str]
    disclaimer: str = (
        "Screening only. Planning permission is never guaranteed; outcomes depend on the LPA's "
        "assessment of a full application against the development plan and material considerations."
    )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["findings"] = sorted(d["findings"], key=lambda f: EFFECT_ORDER.get(f["effect"], 9))
        return d


class Jurisdiction(Protocol):
    code: str

    def assess(self, facts, prefs, weight: float) -> PlanningResult: ...


_REGISTRY: dict[str, Jurisdiction] = {}


def register(j: Jurisdiction) -> None:
    _REGISTRY[j.code] = j


def get(code: str) -> Jurisdiction:
    if code not in _REGISTRY:
        raise KeyError(f"no planning rules implemented for jurisdiction '{code}'")
    return _REGISTRY[code]

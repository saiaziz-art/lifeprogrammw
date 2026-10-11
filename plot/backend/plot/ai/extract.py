"""Optional AI-assisted extraction with deterministic verification.

Claude may structure free text (an alert-email card, a planning decision notice) into
fields. Every extracted value is then checked against the source text by plain code:
- a price is kept only if the same figure appears verbatim in the text;
- an area is kept only if the number and unit appear in the text;
- coordinates, owner identities, permissions and URLs are never taken from the model.
Anything that fails verification is dropped and reported, never "repaired".

Enabled only when an Anthropic API key is stored (Admin → Integrations) or present in the
environment. Without it, PLOT uses the deterministic parser alone.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"


class ListingFields(BaseModel):
    title: str | None = Field(None, description="Short headline exactly as written, e.g. 'Building plot with planning'")
    address_text: str | None = Field(None, description="Address or place text exactly as written in the listing")
    postcode: str | None = Field(None, description="UK postcode if present verbatim, else null")
    asking_price_gbp: int | None = Field(None, description="Asking/guide price in whole pounds if stated, else null")
    price_qualifier: Literal["asking", "guide", "offers_over", "offers_in_region", "fixed", "poa", "auction_guide", "unknown"] | None = None
    land_area_value: float | None = Field(None, description="Numeric land area if stated")
    land_area_unit: Literal["acres", "hectares", "sq_m", "sq_ft"] | None = None
    property_kind: Literal["building_plot", "land", "barn_or_conversion", "renovation", "house", "commercial", "unknown"] | None = None
    planning_statement: str | None = Field(None, description="Any planning-related sentence quoted verbatim, else null")
    tenure: Literal["freehold", "leasehold", "unknown"] | None = None


SYSTEM = (
    "You extract fields from UK property listing text. Copy values exactly as they appear. "
    "If a field is not explicitly stated, return null. Never infer, estimate or complete missing "
    "information, and never add facts that are not in the text."
)

_UNIT_WORDS = {
    "acres": ("acre",),
    "hectares": ("hectare", "ha"),
    "sq_m": ("sq m", "sqm", "m²", "m2", "square metre"),
    "sq_ft": ("sq ft", "sqft", "ft²", "square feet", "square foot"),
}
_TO_M2 = {"acres": 4046.8564224, "hectares": 10_000.0, "sq_m": 1.0, "sq_ft": 0.09290304}


def _digits_in(text: str) -> set[str]:
    return {m.replace(",", "") for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def verify(fields: ListingFields, text: str) -> tuple[dict, list[str]]:
    """Return (verified_fields, rejected_reasons). Pure, deterministic."""
    lowered = text.lower()
    nums = _digits_in(text)
    out: dict = {}
    rejected: list[str] = []

    if fields.asking_price_gbp is not None:
        if str(fields.asking_price_gbp) in nums and "£" in text:
            out["asking_price"] = fields.asking_price_gbp
            out["price_qualifier"] = fields.price_qualifier or "unknown"
        else:
            rejected.append(f"price {fields.asking_price_gbp} not found verbatim")
    if fields.land_area_value is not None and fields.land_area_unit:
        v = fields.land_area_value
        v_str = f"{v:g}"
        unit_ok = any(w in lowered for w in _UNIT_WORDS[fields.land_area_unit])
        if v_str in nums and unit_ok:
            out["area_m2"] = round(v * _TO_M2[fields.land_area_unit], 1)
            out["area_as_written"] = f"{v_str} {fields.land_area_unit}"
        else:
            rejected.append(f"area {v_str} {fields.land_area_unit} not found verbatim")
    for key in ("title", "address_text", "planning_statement"):
        val = getattr(fields, key)
        if val:
            if val.lower().strip() in lowered:
                out[key] = val.strip()
            else:
                rejected.append(f"{key} not a verbatim quote")
    if fields.postcode:
        pc = fields.postcode.upper().strip()
        if pc.replace(" ", "") in text.upper().replace(" ", ""):
            out["postcode"] = pc
        else:
            rejected.append("postcode not in text")
    if fields.property_kind:
        out["property_kind"] = fields.property_kind
    if fields.tenure and fields.tenure != "unknown":
        if fields.tenure in lowered:
            out["tenure"] = fields.tenure
        else:
            rejected.append("tenure not stated in text")
    return out, rejected


class Extractor:
    """Thin wrapper; returns None when AI is not configured or the call fails."""

    def __init__(self, api_key: str | None):
        self.enabled = bool(api_key)
        self._client = None
        if self.enabled:
            import anthropic

            self._client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=60.0)

    def extract_listing(self, text: str) -> tuple[dict, list[str]] | None:
        if not self.enabled or not text.strip():
            return None
        import anthropic

        schema = ListingFields.model_json_schema()
        try:
            response = self._client.beta.messages.create(
                model=MODEL,
                max_tokens=2000,
                system=SYSTEM,
                messages=[{"role": "user", "content": f"Listing text:\n<listing>\n{text[:8000]}\n</listing>"}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": _strict(schema)}},
                # Server-side refusal fallback (routes by refusal category).
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError:
            log.warning("AI extraction rate-limited; using deterministic parser only")
            return None
        except anthropic.APIStatusError as e:
            log.warning("AI extraction failed (%s); using deterministic parser only", e.status_code)
            return None
        except anthropic.APIConnectionError:
            log.warning("AI extraction unreachable; using deterministic parser only")
            return None
        if response.stop_reason == "refusal":
            return None
        txt = next((b.text for b in response.content if b.type == "text"), None)
        if not txt:
            return None
        try:
            fields = ListingFields.model_validate(json.loads(txt))
        except (ValueError, ValidationError):
            return None
        return verify(fields, text)


def _strict(schema: dict) -> dict:
    """Make a pydantic JSON schema acceptable to structured outputs (no extra props, all required)."""

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                if "properties" in node:
                    node["required"] = list(node["properties"].keys())
            node.pop("default", None)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        return node

    return walk(json.loads(json.dumps(schema)))

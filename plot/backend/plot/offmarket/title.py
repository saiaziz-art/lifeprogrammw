"""Deterministic parsing of an HM Land Registry official copy of the register (PDF or text).

Only an official copy — bought by you from HM Land Registry — identifies the registered
proprietor. Map polygons (INSPIRE) and a house standing on a parcel never do.

Everything extracted here is shown to you with the verbatim register wording beside it,
and nothing becomes a "verified owner" until you confirm it against the document.
"""

from __future__ import annotations

import io
import re
from typing import Any

SECTION_RE = re.compile(r"\b([ABC])\s*:\s*(Property|Proprietorship|Charges)\s+[Rr]egister\b")
ENTRY_RE = re.compile(r"(?:(?<=\s)|^)(\d{1,3})\s+(?=\(\d{1,2}\s+[A-Z][a-z]+\s+\d{4}\)|[A-Z(])")
DATE_RE = r"\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]+\s+\d{4}"
COMPANY_RE = re.compile(r"\b(LIMITED|LTD|PLC|LLP|L\.L\.P\.|COMPANY|CORPORATION|COUNCIL|TRUST CORPORATION|HOUSING ASSOCIATION|CIC)\b", re.I)
POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b")


def pdf_text(content: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("–", "-").replace("’", "'")).strip()


def _title_case(name: str) -> str:
    def fix(w: str) -> str:
        if not w.isupper():
            return w
        parts = re.split(r"([-'])", w.lower())
        out = "".join(p.capitalize() if p not in "-'" else p for p in parts)
        return re.sub(r"^Mc(\w)", lambda m: "Mc" + m.group(1).upper(), out)

    return " ".join(fix(w) for w in name.split())


def _sections(text: str) -> dict[str, str]:
    t = _norm(text)
    marks = [(m.start(), m.group(1)) for m in SECTION_RE.finditer(t)]
    out: dict[str, str] = {}
    for i, (pos, key) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(t)
        body = t[pos:end]
        if key not in out or len(body) > len(out[key]):
            out[key] = body
    out["_all"] = t
    return out


def _entries(section: str) -> list[dict[str, Any]]:
    """Split a register section into numbered entries: '1 (12 March 2019) PROPRIETOR: …'."""
    # drop the standard explanatory sentence after the heading
    body = re.sub(r"^[ABC]\s*:\s*\w+\s+[Rr]egister\s*", "", section)
    body = re.sub(r"This register (describes|specifies|contains)[^.]*\.\s*", "", body)
    body = re.sub(r"\bEnd of register\b.*$", "", body, flags=re.I | re.S)
    cuts = [m for m in ENTRY_RE.finditer(body)]
    out = []
    expected = 1
    starts = []
    for m in cuts:
        if int(m.group(1)) == expected:
            starts.append(m)
            expected += 1
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(body)
        txt = body[m.end() : end].strip()
        dm = re.match(r"\((" + DATE_RE + r")\)\s*", txt)
        out.append({"n": int(m.group(1)), "date": dm.group(1) if dm else None, "text": txt[dm.end() :] if dm else txt})
    return out


def _split_names(names: str) -> list[str]:
    parts = re.split(r"\s+and\s+|\s*,\s*(?=[A-Z][A-Z]+\s)", names)
    return [p.strip(" ,") for p in parts if p.strip(" ,")]


def parse_proprietor(entry_text: str) -> dict[str, Any]:
    t = entry_text
    m = re.search(r"PROPRIETORS?\s*:\s*(.+)", t)
    rest = m.group(1) if m else t
    addr = None
    am = re.search(r"\s(?:of|whose address for service is)\s+(.+?)\.?\s*$", rest)
    names_part = rest[: am.start()] if am else rest
    if am:
        addr = am.group(1)
        addr = re.split(r"\s+and\s+(?:of|care of)\s+", addr)[0].strip(" .")
    reg = re.search(r"\(Co\.?\s*Regn\.?\s*No\.?\s*([A-Z]{0,2}\d{5,8})\)", names_part)
    names_part = re.sub(r"\(Co\.?\s*Regn\.?\s*No\.?[^)]*\)", "", names_part).strip(" ,")
    pr = bool(re.search(r"personal representative|executor|administrator|deceased", t, re.I))
    trustees = bool(re.search(r"\btrustees?\b", t, re.I))
    names_part = re.split(r"\s+(?:as|the)\s+(?:personal representatives?|executors?|trustees?)\b", names_part, flags=re.I)[0]
    if reg or COMPANY_RE.search(names_part):
        people = [{"name": _title_case(names_part.strip()), "kind": "company", "company_number": reg.group(1) if reg else None}]
    else:
        people = [{"name": _title_case(n), "kind": "individual", "company_number": None} for n in _split_names(names_part)]
    pc = POSTCODE_RE.search(addr or "")
    return {
        "proprietors": people,
        "address": addr,
        "postcode": f"{pc.group(1)} {pc.group(2)}" if pc else None,
        "personal_representatives": pr,
        "trustees": trustees,
        "raw": t,
    }


def parse_register(text: str) -> dict[str, Any]:
    secs = _sections(text)
    allt = secs["_all"]
    warnings: list[str] = []
    tn = re.search(r"Title number\s*:?\s*([A-Z]{1,3}\d{1,7})\b", allt)
    ed = re.search(r"Edition date\s*:?\s*(\d{2}\.\d{2}\.\d{4})", allt)
    oc = re.search(r"entries (?:on|in) the register of title on\s+(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})", allt)
    if "Official copy" not in allt and "official copy" not in allt:
        warnings.append("This does not look like an HM Land Registry official copy — check the document")
    a = _entries(secs.get("A", ""))
    b = _entries(secs.get("B", ""))
    c = _entries(secs.get("C", ""))
    if not (secs.get("A") and secs.get("B")):
        warnings.append("Property and/or Proprietorship register sections not found")

    prop = next((e for e in a if re.search(r"(Freehold|Leasehold) land", e["text"])), a[0] if a else None)
    tenure = None
    if prop:
        tm = re.search(r"\b(Freehold|Leasehold)\b", prop["text"])
        tenure = tm.group(1).lower() if tm else None
    address = None
    if prop:
        pm = re.search(r"being\s+(.+?)(?:\.\s*$|\.\s+NOTE)", prop["text"])
        address = re.sub(r"\s*\(([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\)", r" \1", pm.group(1)).strip() if pm else None
    admin = re.search(r"\b([A-Z][A-Z ,&'-]+?)\s*:\s*([A-Z][A-Z ,&'-]+?)\s+1\s+\(", secs.get("A", ""))
    cls = re.search(r"\b(Title absolute|Possessory title|Qualified title|Good leasehold title|Title good leasehold)\b", secs.get("B", ""), re.I)

    owners = [e for e in b if re.search(r"PROPRIETORS?\s*:", e["text"])]
    prop_info = parse_proprietor(owners[-1]["text"]) if owners else None
    if not owners:
        warnings.append("No PROPRIETOR entry found — enter the owner manually from the document")
    if len(owners) > 1:
        warnings.append("More than one proprietor entry — the latest one is used; check the document")

    price = None
    for e in b:
        pm = re.search(r"(price stated to have been paid|value stated as at)\s*(?:on\s+)?(" + DATE_RE + r")?[^£]*£\s?([\d,]+)", e["text"])
        if pm:
            price = {"amount": int(pm.group(3).replace(",", "")), "date": pm.group(2), "kind": "price_paid" if "price" in pm.group(1) else "value_stated", "raw": e["text"]}

    def find(entries, pat):
        return [{"n": e["n"], "date": e["date"], "text": e["text"]} for e in entries if re.search(pat, e["text"], re.I)]

    restrictions = find(b, r"RESTRICTION")
    charges = []
    for e in c:
        if re.search(r"REGISTERED CHARGE", e["text"]):
            nxt = next((x for x in c if x["n"] == e["n"] + 1 and re.search(r"Proprietor\s*:", x["text"])), None)
            lender = None
            if nxt:
                lm = re.search(r"Proprietor\s*:\s*(.+?)(?:\s+\(Co\.|\s+of\s)", nxt["text"])
                lender = _title_case(lm.group(1)) if lm else None
            charges.append({"n": e["n"], "date": e["date"], "text": e["text"], "lender": lender})
    covenants = find(a + c, r"covenant")
    easements = find(a + c, r"right of way|rights? (?:granted|reserved)|easement|the benefit of|subject to the rights|right to (?:pass|drain|use)")
    notices = find(b + c, r"\bNOTICE\b|option|agreement for sale|estate contract")
    out = {
        "title_number": tn.group(1) if tn else None,
        "edition_date": ed.group(1) if ed else None,
        "official_copy_date": oc.group(1) if oc else None,
        "tenure": tenure,
        "class_of_title": cls.group(1) if cls else None,
        "admin_area": f"{admin.group(1).strip()} : {admin.group(2).strip()}" if admin else None,
        "property_description": prop["text"] if prop else None,
        "property_address": address,
        "proprietors": (prop_info or {}).get("proprietors", []),
        "proprietor_address": (prop_info or {}).get("address"),
        "proprietor_postcode": (prop_info or {}).get("postcode"),
        "proprietor_entry": (prop_info or {}).get("raw"),
        "personal_representatives": bool(prop_info and prop_info["personal_representatives"]),
        "trustees": bool(prop_info and prop_info["trustees"]),
        "price_paid": price,
        "restrictions": restrictions,
        "charges": charges,
        "covenants": covenants,
        "easements": easements,
        "notices": notices,
        "warnings": warnings,
    }
    if out["personal_representatives"]:
        warnings.append("Held by personal representatives/executors — a sensitive situation; contact needs manual review")
    if notices:
        warnings.append("Notices/option-type entries on the register — someone else may have an interest in the land")
    if tenure == "leasehold":
        warnings.append("Leasehold title — the freeholder's consent and the lease terms matter")
    return out


def contact_kind(extracted: dict[str, Any]) -> str:
    props = extracted.get("proprietors") or []
    if extracted.get("personal_representatives"):
        return "personal_representatives"
    if extracted.get("trustees"):
        return "trustees"
    if props and all(p["kind"] == "company" for p in props):
        name = props[0]["name"].lower()
        if "council" in name:
            return "public_body"
        return "company"
    return "individual" if props else "unknown"


def default_salutation(extracted: dict[str, Any], kind: str) -> str:
    """A neutral greeting: first names only, never an assumed title (Mr/Mrs/Ms)."""
    props = extracted.get("proprietors") or []
    if kind == "individual" and props:
        firsts = [p["name"].split()[0] for p in props if p["name"].split()]
        if len(firsts) == 1:
            return firsts[0]
        return ", ".join(firsts[:-1]) + " and " + firsts[-1]
    return ""


def address_lines(addr: str | None) -> list[str]:
    if not addr:
        return []
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    if parts:
        pc = POSTCODE_RE.search(parts[-1])
        if pc and parts[-1].strip() != pc.group(0):
            town = POSTCODE_RE.sub("", parts[-1]).strip()
            parts = parts[:-1] + ([town] if town else []) + [f"{pc.group(1)} {pc.group(2)}"]
    return parts

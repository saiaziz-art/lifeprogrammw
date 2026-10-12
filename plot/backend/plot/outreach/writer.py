"""The outreach writer: situation facts → a short, honest, individually tailored letter or email.

Principles applied (from your brief): relevance, a low-friction enquiry, benefit framing as
possibilities, credibility as a genuine private buyer, respect for an owner who has not
advertised, flexibility, one easy question, and trust (no pressure, urgency, flattery or
claims about planning or finance that are not confirmed).

Two generators share the same facts and the same validator:
- `ai`: Claude Opus 5.5 with structured output, when an Anthropic API key is configured;
- `composer`: a deterministic writer that builds each draft from the situation (opportunity
  type, recipient, channel, persona, confirmed facts, constraints) with varied phrasing.
A draft that fails validation can never be approved.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import asdict, dataclass, field
from typing import Any

WORD_LIMITS = {"initial": (80, 140), "follow_up": (45, 120), "reply": (35, 180)}

BANNED_PHRASES = [
    "exciting investment opportunity", "unlock hidden value", "limited-time offer", "limited time offer", "i hope this email finds you well",
    "i hope this letter finds you well", "hidden gem", "act now", "act fast", "don't miss", "once in a lifetime", "golden opportunity",
    "win-win", "no-brainer", "game changer", "guaranteed", "guarantee", "windfall", "planning uplift", "maximise the value", "maximize the value",
    "unlock the potential", "unlock value", "investment opportunity", "urgent", "deadline", "before it's too late", "only a few days",
    "exclusive offer", "amazing", "incredible", "stunning", "perfect opportunity", "dear homeowner", "dear sir/madam", "to whom it may concern",
    "as we discussed", "as discussed", "following our conversation", "great to meet", "lovely to meet", "as you know", "you may remember",
    "per our", "circle back", "touch base", "synergy", "leverage",
]
ASSUMPTION_TERMS = [
    "retire", "retirement", "downsiz", "elderly", "pension", "your health", "mobility", "struggling", "need the money", "financial difficulty",
    "debt", "inheritance", "probate", "passed away", "bereave", "late husband", "late wife", "divorce", "separation", "care home", "too much for you",
]
UNCONFIRMED_CLAIMS = {
    "cash": [r"\bcash buyer\b", r"\bcash purchase\b", r"\bin cash\b"],
    "funding": [r"\bfully (?:funded|financed)\b", r"\bfinance (?:is )?in place\b", r"\bmortgage (?:agreed|approved|in principle)\b", r"\bfunds? (?:are )?(?:ready|available)\b"],
    "speed": [r"\bcomplete (?:quickly|immediately|fast|within)\b", r"\bquick completion\b", r"\bno chain\b", r"\bchain[- ]free\b", r"\bproceed immediately\b"],
    "local": [r"\blive locally\b", r"\blocal (?:family|couple|resident)\b", r"\bwe live (?:nearby|in|near)\b", r"\bI live (?:nearby|in|near)\b", r"\bneighbour\b", r"\bgrew up\b"],
    "visit": [r"\bwalk(?:ed|ing) past\b", r"\bdrove past\b", r"\bdriving past\b", r"\bsaw your (?:house|home|garden|land)\b", r"\bvisited\b"],
    "planning": [r"\bwill (?:get|obtain|be granted) planning\b", r"\bplanning (?:is|would be) (?:straightforward|easy|certain|likely)\b", r"\beasily get planning\b", r"\bdefinitely\b"],
}
PRICE_RE = re.compile(r"£\s?\d|\b\d{2,3}\s?k\b|\b\d[\d,]{3,}\s*(?:pounds|gbp)\b|\bGBP\b", re.I)


@dataclass
class Facts:
    channel: str  # post | email
    kind: str  # initial | follow_up | reply
    persona: str  # private | couple | represented
    recipient_kind: str
    recipient_name: str
    salutation: str
    opportunity_type: str
    place: str | None
    property_ref: str | None  # verified from the register, e.g. "12 Example Road" or "the land on the north side of Example Lane"
    property_is_home: bool  # proprietor's address is the property itself
    title_number: str | None
    first_name: str
    last_name: str | None
    partner_term: str | None
    returning_to_uk: bool
    local_connection: str | None
    funding_statement: str | None
    agent_name: str | None
    agent_company: str | None
    reply_email: str | None
    reply_phone: str | None
    reply_postal_address: str | None
    observations: list[str] = field(default_factory=list)  # hedged, map-derived: side_garden, corner, generous, backland, frontage_road
    road_name: str | None = None
    sensitive_setting: str | None = None  # conservation area / national landscape / national park
    planning_history: str | None = None  # withdrawn | lapsed | refused | None
    reference: str = ""
    retention_until: str | None = None
    previous_sent_on: str | None = None  # follow-ups
    reply_category: str | None = None  # replies
    reply_excerpt: str | None = None
    include_price: bool = False
    price_text: str | None = None

    @property
    def sender_display(self) -> str:
        if self.persona == "represented" and self.agent_name:
            return self.agent_name
        return " ".join(x for x in (self.first_name, self.last_name) if x)

    @property
    def principal_name(self) -> str:
        return " ".join(x for x in (self.first_name, self.last_name) if x)


@dataclass
class Draft:
    subject: str
    body: str  # salutation … sign-off (what the owner reads as the message)
    footer: str  # identity, source of data, privacy and opt-out (not counted in the word limit)
    generator: str
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return f"{self.body}\n\n{self.footer}".strip()


# --------------------------------------------------------------------------- helpers


def message_words(body: str) -> int:
    """Words in the message itself, excluding the greeting line and the sign-off block."""
    lines = [ln for ln in body.strip().splitlines()]
    if lines and re.match(r"^(Dear|Hi|Hello)\b", lines[0].strip()):
        lines = lines[1:]
    text = "\n".join(lines)
    text = re.split(r"\n\s*(Best wishes|Kind regards|All the best|With best wishes|Many thanks|Thank you again|Yours sincerely|Yours faithfully|Warm regards|Best regards),?\s*\n", text)[0]
    return len(re.findall(r"[A-Za-z0-9£'’-]+", text))


def short_property(ref: str | None) -> str | None:
    if not ref:
        return None
    first = ref.split(",")[0].strip()
    if re.match(r"^(land|the land|part of|plot)\b", first, re.I):
        return first[0].lower() + first[1:] if not first.lower().startswith("the ") else first
    return first


def town_from_address(addr: str | None) -> str | None:
    if not addr:
        return None
    parts = [p.strip() for p in re.sub(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", "", addr).split(",") if p.strip()]
    return parts[-1] if len(parts) >= 2 else None


def _contract(text: str, formal: bool) -> str:
    if not formal:
        return text
    pairs = [("I'm", "I am"), ("I'd", "I would"), ("I've", "I have"), ("I'll", "I will"), ("we're", "we are"), ("We're", "We are"), ("we'd", "we would"), ("We'd", "We would"),
             ("it's", "it is"), ("It's", "It is"), ("you'd", "you would"), ("You'd", "You would"), ("don't", "do not"), ("wouldn't", "would not"), ("couldn't", "could not"),
             ("isn't", "is not"), ("there's", "there is"), ("you're", "you are"), ("that's", "that is"), ("won't", "will not"), ("haven't", "have not")]
    for a, b in pairs:
        text = re.sub(rf"\b{re.escape(a)}\b", b, text)
    return text


# --------------------------------------------------------------------------- composer


class Composer:
    def __init__(self, facts: Facts, seed: int, instructions: str | None = None):
        self.f = facts
        self.rng = random.Random(seed)
        self.instr = (instructions or "").lower()
        self.formal = "formal" in self.instr or facts.recipient_kind in ("company", "public_body")
        self.short = "short" in self.instr or "brief" in self.instr
        self.applied: list[str] = []
        self.said_conditional = False
        self.said_portion = False

    def pick(self, options: list[str]) -> str:
        return self.rng.choice(options)

    # ---- who is writing

    def we(self) -> tuple[str, str, str]:
        """(subject pronoun, possessive, object) for the buyer voice."""
        if self.f.persona == "couple" and self.f.partner_term:
            return "we", "our", "us"
        return "I", "my", "me"

    def intro(self) -> str:
        f, place = self.f, self.f.place or "the area"
        if f.persona == "represented":
            who = f.principal_name or "a private client"
            co = f" at {f.agent_company}" if f.agent_company else ""
            return self.pick([
                f"I'm writing on behalf of {who}, a private buyer looking for a plot around {place} on which to build their own home. I work{co} and help with the search.",
                f"My name is {f.agent_name}{(', of ' + f.agent_company + ',') if f.agent_company else ''} and I'm helping {who}, a private individual, find a small site around {place} for their own future home.",
            ])
        pt = f.partner_term
        if f.persona == "couple" and pt:
            if f.returning_to_uk:
                return self.pick([
                    f"My {pt} and I are planning our move back to the UK and have been looking around {place} for somewhere to build a small, well-designed home.",
                    f"My {pt} and I are moving back to the UK and hope to build a modest, well-designed home of our own somewhere around {place}.",
                    f"My {pt} and I are planning to return to the UK, and we'd love to build a small, carefully designed house in or near {place}.",
                ])
            return self.pick([
                f"My {pt} and I are looking for somewhere around {place} to build a small, well-designed home of our own.",
                f"My {pt} and I hope to build a modest home of our own, and we've been looking around {place}.",
            ])
        if f.returning_to_uk:
            return self.pick([
                f"I'm planning a move back to the UK and have been looking around {place} for somewhere to build a small, well-designed home.",
                f"I'm moving back to the UK and hope to build a modest home of my own somewhere around {place}.",
            ])
        return self.pick([
            f"I'm looking for somewhere around {place} to build a small, well-designed home for myself.",
            f"I hope to build a modest home of my own and have been looking around {place}.",
        ])

    def opener(self) -> str:
        if self.f.recipient_kind in ("company", "public_body"):
            return self.pick(["I'm writing with a short enquiry about some land your organisation owns.", "I hope you don't mind a direct enquiry about one of your sites."])
        return self.pick([
            "I hope you don't mind me getting in touch out of the blue.",
            "Please forgive a letter out of the blue." if self.f.channel == "post" else "Please forgive an email out of the blue.",
            "I'm getting in touch with a slightly unusual enquiry.",
            "This is a message you weren't expecting, so I'll keep it short.",
        ])

    # ---- why this property

    def relevance_and_ask(self) -> str:
        f = self.f
        we, our, _ = self.we()
        prop = short_property(f.property_ref) or "your property"
        home = f.property_is_home and not prop.lower().startswith(("land", "the land"))
        your_prop = f"your home at {prop}" if home else (prop if prop.lower().startswith(("land", "the land")) else f"your property at {prop}")
        obs = set(f.observations)
        t = f.opportunity_type
        cond = self.pick([
            "if it turned out to be practical and planning permission could be obtained",
            "assuming it proved practical and planning permission were possible",
            "only if it was practical and planning permission could be secured",
        ])
        if t == "garden_subdivision":
            if "corner" in obs:
                where = f"the corner plot at {prop}"
            elif "side_garden" in obs:
                where = f"the garden beside {('your home at ' + prop) if home else prop}"
            elif "backland" in obs:
                where = f"the long rear garden at {prop}"
            else:
                where = f"the garden at {prop}"
            seen = self.pick(["While researching the area, I noticed that", "Looking at the local map, it seemed that", "From the map, it looks as though"])
            if "generous" in obs:
                return f"{seen} {where} is unusually generous, and I wondered whether you'd ever consider selling a small part of it, {cond}."
            return self.pick([
                f"I wondered whether you'd ever consider selling a small section of {where}, {cond}.",
                f"{seen} there may be room for a small home in part of {where}, and I wondered whether you'd ever consider selling that part, {cond}.",
            ])
        if t == "edge_paddock":
            land = prop if prop.lower().startswith(("land", "the land")) else f"the land at {prop}"
            land = land if land.lower().startswith("the ") else "the " + land
            return self.pick([
                f"I came across {land} and wondered whether you might ever be open to discussing a sale, either of the whole site or a smaller portion.",
                f"{land[0].upper() + land[1:]} caught {our} eye, and I wondered whether you'd ever consider selling all or part of it, {cond}.",
            ])
        if t in ("outbuilding", "derelict"):
            what = "the building" if t == "derelict" else "the outbuilding and the land around it"
            hedge = ", which looks as though it may not be in use," if t == "derelict" else ""
            return self.pick([
                f"I noticed {what} at {prop}{hedge} and wondered whether you might ever be open to discussing a sale of that part, {cond}.",
                f"I wondered whether you'd ever consider selling {what} at {prop}{hedge.replace(',', '', 1) if hedge else ''} — on {our} side, it would only go ahead {cond}.",
            ])
        if t == "replacement":
            return self.pick([
                f"I wondered, very tentatively, whether you might ever think about selling {your_prop} privately. In time, {we} would hope to build a new, energy-efficient home on the plot.",
                f"I'm writing to ask whether you'd ever consider a private sale of {your_prop}. To be open about it, {we}'d hope eventually to build a new, low-energy house there.",
            ])
        if t == "planning_history":
            hist = "a new home has been considered" if f.planning_history in ("withdrawn", "lapsed", None) else "a new home was proposed"
            return self.pick([
                f"I saw from the council's public planning records that {hist} at {prop} in the past, and wondered whether you might be open to discussing a sale of that land, {cond}.",
                f"The council's planning records suggest {hist} at {prop} before. I wondered whether you'd ever consider selling the land concerned, {cond}.",
            ])
        if t == "register_site":
            return f"I noticed that the land at {prop} appears on the council's brownfield land register, and wondered whether you might be open to discussing a sale, {cond}."
        return f"I wondered whether you might ever be open to discussing a sale of the land at {prop}, {cond}."

    def setting_note(self) -> str | None:
        if not self.f.sensitive_setting:
            return None
        we, _, _ = self.we()
        return self.pick([
            f"{we.capitalize()}'d want anything built to be modest and in keeping with the {self.f.sensitive_setting}.",
            f"Given the {self.f.sensitive_setting}, {we}'d be thinking of something low-key and sympathetic.",
        ])

    def benefit(self) -> str | None:
        f, t = self.f, self.f.opportunity_type
        we, our, _ = self.we()
        conditional = "Any sale could be conditional on planning permission, so nothing would complete unless consent was granted."
        flexible = f"{we.capitalize()}'re flexible about timing" + ("." if self.said_portion else " and about how much of the land would be involved.")
        if t == "garden_subdivision":
            keep = "You would keep your home and the larger part of the garden." if f.property_is_home else "The rest of the property would stay as it is."
            return keep if self.said_conditional else self.pick([keep, conditional.replace("Any sale", "Any arrangement")])
        if t in ("edge_paddock", "outbuilding", "derelict", "planning_history", "register_site"):
            return flexible if self.said_conditional else (conditional if self.said_portion else self.pick([conditional, flexible]))
        if t == "replacement":
            return "A private sale would avoid marketing and viewings, and timing could be arranged to suit you."
        return None

    def respect(self) -> str:
        we, our, _ = self.we()
        if self.f.recipient_kind in ("company", "public_body"):
            return self.pick([f"{we.capitalize()}'d be happy to investigate feasibility at {our} own cost before anything is agreed.", f"{we.capitalize()}'re happy to look into feasibility first, at {our} own cost, with no commitment on your side."])
        return self.pick([
            "I appreciate it's probably not something you've considered, but I thought it couldn't hurt to ask.",
            "I realise you haven't advertised anything and may have no interest at all, which is completely fine.",
            "I appreciate this may not be something you'd ever want to do, and I quite understand if so.",
            f"{we.capitalize()}'re still exploring possibilities, so there's no expectation or commitment at this stage.",
        ])

    def close(self) -> str:
        if self.f.recipient_kind in ("company", "public_body"):
            return self.pick(["Would a short conversation about it be worthwhile?", "Would you be open to a brief discussion about whether a disposal might suit the company?" if self.f.recipient_kind == "company" else "Would you be open to a brief discussion about it?"])
        return self.pick([
            "Absolutely no pressure either way. If it's something you'd be open to discussing, I'd be happy to have a quick chat.",
            "Would you be open to a short, no-obligation chat about it? If not, there's no need to reply.",
            "If it's something you might consider, I'd be very happy to hear from you. If not, please don't feel you need to reply.",
            "Is it something you might be open to talking about? Either way, thank you for reading this.",
        ])

    def contact_line(self) -> str | None:
        f = self.f
        bits = []
        if f.reply_email:
            bits.append(f.reply_email)
        if f.reply_phone:
            bits.append(f.reply_phone)
        if not bits:
            return None if f.channel == "email" else "You can write to me at the address above."
        if f.channel == "email":
            return f"You can simply reply to this email{(' or call ' + f.reply_phone) if f.reply_phone else ''}." if f.reply_phone else None
        return f"You can reach me at {' or '.join(bits)}, or write to the address above."

    def signoff(self) -> str:
        f = self.f
        word = "Kind regards" if self.formal else self.pick(["Best wishes", "All the best", "Kind regards", "With best wishes"])
        name = f.sender_display if (self.formal or f.persona == "represented") else f.first_name
        return f"{word},\n{name}"

    def greeting(self) -> str:
        f = self.f
        if f.recipient_kind in ("company", "public_body") or not f.salutation:
            return "Dear Sir or Madam," if f.recipient_kind in ("company", "public_body") else f"Dear {f.recipient_name},"
        word = "Dear" if (f.channel == "post" or self.formal) else "Hi"
        return f"{word} {f.salutation},"

    def subject(self) -> str:
        f = self.f
        place = f.place or "your area"
        prop = short_property(f.property_ref)
        if f.kind == "follow_up":
            return self.pick([f"Following up on my letter about {prop or place}", f"{prop or place} — a brief follow-up"])
        if f.kind == "reply":
            return f"Re: {prop or place}"
        if f.recipient_kind in ("company", "public_body"):
            return f"{(prop[0].upper() + prop[1:]) if prop else 'Land near ' + place} — possible disposal"
        if f.opportunity_type in ("edge_paddock", "outbuilding", "derelict", "planning_history", "register_site"):
            return self.pick([f"Enquiry about some land near {place}", f"A question about the land at {prop}" if prop else f"A question about some land in {place}"])
        return self.pick([f"A quick question about your property in {place}", f"A small enquiry about {prop}" if prop else f"A small enquiry about a property in {place}"])

    # ---- assemble

    def initial(self) -> list[str]:
        f = self.f
        paras: list[list[str]] = []
        if f.recipient_kind in ("company", "public_body"):
            prop = short_property(f.property_ref) or "your land"
            ref = f" (title {f.title_number})" if f.title_number else ""
            paras.append([self.pick([f"I'm writing about {prop}{ref}.", f"I'm getting in touch about {prop}{ref}, which I understand the {'company' if f.recipient_kind == 'company' else 'authority'} owns."])])
            who = self.intro().replace("have been looking", "are looking")
            paras.append([who, self.pick(["We're not developers.", "This is for our own home, not a development scheme."]) if self.we()[0] == "we" else self.pick(["I'm not a developer.", "This is for my own home, not a development scheme."])])
            paras.append([self.pick([
                "I'd like to explore whether you would consider selling all or part of the site, potentially conditional on planning permission or through an option arrangement.",
                "I'd be interested in discussing a possible disposal of all or part of the site — for example, a sale conditional on planning, or an option.",
            ]).replace("I'd", "We'd" if self.we()[0] == "we" else "I'd")])
            paras.append([self.respect()])
            if f.funding_statement:
                paras[-1].append(f.funding_statement.rstrip(".") + ".")
            paras.append([self.close()])
        else:
            opener = self.opener()
            intro = self.intro()
            if f.local_connection:
                intro += " " + f.local_connection.rstrip(".") + "."
            paras.append([opener])
            paras.append([intro])
            ask_txt = self.relevance_and_ask()
            self.said_conditional = "planning permission" in ask_txt
            self.said_portion = "portion" in ask_txt or "all or part" in ask_txt
            ask = [ask_txt]
            sn = self.setting_note()
            if sn and not self.short:
                ask.append(sn)
            paras.append(ask)
            middle = [self.respect()]
            b = self.benefit()
            if b and not self.short:
                middle.insert(0, b) if self.rng.random() < 0.5 else middle.append(b)
            if f.funding_statement and not self.short:
                middle.append(f.funding_statement.rstrip(".") + ".")
            paras.append(middle)
            paras.append([self.close()])
        cl = self.contact_line()
        if cl:
            paras[-1].append(cl)
        return [" ".join(p) for p in paras if p]

    def follow_up(self) -> list[str]:
        f = self.f
        prop = short_property(f.property_ref) or "your property"
        when = f" on {f.previous_sent_on}" if f.previous_sent_on else " a few weeks ago"
        we, our, _ = self.we()
        return [
            self.pick([f"I wrote to you{when} about {prop}, and wanted to follow up just once in case my letter went astray.", f"I sent you a short letter{when} about {prop}. I'm writing once more only in case it didn't reach you."]).replace("my letter", "my letter" if f.channel == "post" else "my message").replace("short letter", "short letter" if f.channel == "post" else "short email"),
            self.pick([f"{we.capitalize()}'re still hoping to find somewhere around {f.place or 'the area'} for a small home, and the question still stands: would you ever consider a sale, {('subject to planning permission' if f.opportunity_type != 'replacement' else 'privately')}?", f"In short, {we}'d still love to talk if a sale might ever suit you."]),
            self.pick(["I don't want to be a nuisance, so I won't write again unless I hear from you.", "This will be my last note — if it's not of interest, there's no need to reply."]),
        ] + ([c] if (c := self.contact_line()) else [])

    def reply(self) -> list[str]:
        f = self.f
        we, our, us = self.we()
        cat = f.reply_category
        thanks = self.pick(["Thank you very much for getting back to me.", "Thank you for your reply — I really appreciate you taking the time.", "Many thanks for replying."])
        if cat == "not_interested":
            return [thanks, "I completely understand, and I won't contact you again about this. Thank you for letting me know, and all the best."]
        if cat == "requests_price":
            return [
                thanks,
                f"Before suggesting a figure, {we}'d want to check a few practical things so that anything {we} put forward is realistic: how the plot would be accessed, what services are nearby, and whether the council would be supportive in principle.",
                "One option would be an agreement conditional on planning permission, so nothing completes unless consent is granted. If you already have a figure in mind, I'd be glad to hear it.",
                "Would you be happy for me to come and have a look at a time that suits you?",
            ]
        lines = [thanks, self.pick(["It would be really helpful to understand a little more about what you'd be comfortable with.", "I'd love to understand a bit more about how you see it."])]
        qs = []
        if f.opportunity_type == "garden_subdivision":
            qs.append("roughly which part of the garden you'd consider")
            qs.append("how you'd feel about a separate driveway")
        else:
            qs.append("roughly which part of the land you'd consider, or whether you'd prefer to sell it all")
            qs.append("how access to the road currently works")
        qs += ["whether you'd be comfortable with a sale conditional on planning permission", "any timing that would suit you"]
        lines.append(f"In particular: {', '.join(qs[:-1])}, and {qs[-1]}.")
        lines.append(self.pick(["Would a short phone call or a visit be the easiest next step?", "Would you be happy to have a short chat, by phone or in person, whenever is convenient?"]))
        return lines

    def compose(self) -> Draft:
        f = self.f
        if f.kind == "follow_up":
            paras = self.follow_up()
        elif f.kind == "reply":
            paras = self.reply()
        else:
            paras = self.initial()
        if f.include_price and f.price_text:
            paras.insert(-1, f.price_text)
        body = self.greeting() + "\n\n" + "\n\n".join(paras) + "\n\n" + self.signoff()
        body = _contract(body, self.formal)
        for k in ("formal", "short", "brief", "warmer", "friendlier"):
            if k in self.instr:
                self.applied.append(k)
        return Draft(subject=_contract(self.subject(), self.formal), body=body, footer=footer_text(f), generator="composer", meta={"applied_instructions": self.applied})


# --------------------------------------------------------------------------- footer & validation


def footer_text(f: Facts) -> str:
    who = f.principal_name or f.first_name
    contact = f.reply_email or (f.reply_postal_address and "the address above") or "the contact details above"
    prop = short_property(f.property_ref) or "this property"
    if f.recipient_kind in ("company", "public_body"):
        src = f"I found the registered owner of {prop} in the public HM Land Registry register" + (f" (title {f.title_number})" if f.title_number else "") + "."
        return (
            f"About this {('letter' if f.channel == 'post' else 'email')}: sent by {who}"
            + (f" (via {f.agent_name}{', ' + f.agent_company if f.agent_company else ''})" if f.persona == "represented" and f.agent_name else "")
            + f", a private buyer. {src} If you would prefer not to receive further messages, reply to {contact} quoting {f.reference} and we will not contact you again."
        )
    keep = f"until {f.retention_until}" if f.retention_until else "only for a short period"
    if f.kind == "reply":
        return (
            f"About this {('letter' if f.channel == 'post' else 'email')}: from {who}"
            + (f", sent on their behalf by {f.agent_name}" if f.persona == "represented" and f.agent_name else "")
            + f", replying to you about {prop}. I keep our correspondence only while we are discussing this and will delete it if you ask. "
            f"If you'd rather not hear from me again, just say so quoting {f.reference} and I won't write again. "
            "You can also complain to the Information Commissioner's Office (ico.org.uk)."
        )
    once = "This is my only follow-up" if f.kind == "follow_up" else "I'm writing once"
    return (
        f"About this {('letter' if f.channel == 'post' else 'email')}: it is from {who}"
        + (f", sent on their behalf by {f.agent_name}{(' of ' + f.agent_company) if f.agent_company else ''}" if f.persona == "represented" and f.agent_name else "")
        + f", a private individual hoping to buy land for a home. I found your name and address in the public HM Land Registry register for {prop}"
        + (f" (title {f.title_number})" if f.title_number else "")
        + f". {once}, relying on legitimate interests, and I will keep your details {keep} to deal with any reply, then delete them. "
        f"You can ask what I hold, ask me to delete it, or tell me not to contact you again — reply to {contact} quoting {f.reference} and I won't write again. "
        "You can also complain to the Information Commissioner's Office (ico.org.uk)."
    )


def validate(d: Draft, f: Facts, *, buyer_confirmed: dict[str, bool] | None = None) -> list[dict[str, str]]:
    """Issues with severity 'block' (cannot be approved) or 'warn'."""
    issues: list[dict[str, str]] = []
    text = f"{d.subject}\n{d.body}"
    low = text.lower()
    lo, hi = WORD_LIMITS.get(f.kind, (80, 140))
    n = message_words(d.body)
    if not lo <= n <= hi:
        issues.append({"code": "length", "severity": "block", "message": f"{n} words — keep the message between {lo} and {hi}"})
    for ph in BANNED_PHRASES:
        if ph in low:
            issues.append({"code": "banned_phrase", "severity": "block", "message": f"Remove “{ph}”"})
    for t in ASSUMPTION_TERMS:
        if t in low:
            issues.append({"code": "personal_assumption", "severity": "block", "message": f"Do not refer to or imply the owner's personal circumstances (“{t}”)"})
    conf = buyer_confirmed or {}
    for key, pats in UNCONFIRMED_CLAIMS.items():
        allowed = (key == "funding" and conf.get("funding")) or (key == "local" and conf.get("local")) or (key == "speed" and conf.get("speed")) or (key == "cash" and conf.get("cash"))
        for p in pats:
            if re.search(p, text, re.I) and not allowed:
                issues.append({"code": f"unconfirmed_{key}", "severity": "block", "message": f"Claim not confirmed in your buyer profile: “{re.search(p, text, re.I).group(0)}”"})
    if PRICE_RE.search(text) and not f.include_price:
        issues.append({"code": "price", "severity": "block", "message": "A price or figure appears — prices are never sent without your explicit approval"})
    q = text.count("?")
    if q > 1:
        issues.append({"code": "questions", "severity": "block", "message": f"{q} questions — ask one simple question"})
    if f.kind == "initial" and q == 0 and not re.search(r"wondered whether|would you|if it's something|if it is something|might consider|be open to", low):
        issues.append({"code": "no_ask", "severity": "warn", "message": "No clear, easy question for the owner to answer"})
    if f.first_name.lower() not in d.body.lower() and (f.agent_name or "").lower() not in d.body.lower():
        issues.append({"code": "identity", "severity": "block", "message": "The message must be signed with your name"})
    if f.persona == "represented" and f.principal_name and f.principal_name.split()[0].lower() not in d.body.lower():
        issues.append({"code": "identity", "severity": "block", "message": "A represented approach must say on whose behalf it is written"})
    if f.persona == "couple" and f.partner_term and f.kind == "initial" and f.partner_term not in low:
        issues.append({"code": "persona", "severity": "warn", "message": f"Persona is 'you and your {f.partner_term}' but the draft doesn't say so"})
    if f.persona != "couple" and re.search(r"\bmy (wife|husband|partner)\b", low):
        issues.append({"code": "persona", "severity": "block", "message": "Draft mentions a partner but your persona is not set to a couple"})
    if not d.footer or ("land registry" not in d.footer.lower() and f.recipient_kind not in ("company", "public_body") and f.kind != "reply") or f.reference not in d.footer or "ico.org.uk" not in d.footer and f.recipient_kind not in ("company", "public_body"):
        issues.append({"code": "privacy_notice", "severity": "block", "message": "The privacy and opt-out notice is missing or incomplete"})
    if re.search(r"\bdear (mr|mrs|ms|miss)\b", low):
        issues.append({"code": "assumed_title", "severity": "warn", "message": "Avoid assuming a title (Mr/Mrs/Ms) unless the owner uses one"})
    paras = [p for p in d.body.split("\n\n") if p.strip()]
    if any(len(re.findall(r"\w+", p)) > 70 for p in paras):
        issues.append({"code": "paragraphs", "severity": "warn", "message": "Keep paragraphs short"})
    return issues


def blocking(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    return [i for i in issues if i["severity"] == "block"]


def seed_for(*parts: Any) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def compose(facts: Facts, *, seed: int, instructions: str | None = None, buyer_confirmed: dict[str, bool] | None = None, tries: int = 24) -> Draft:
    """Deterministic composer: tries phrase combinations until one passes validation."""
    best: tuple[int, Draft, list] | None = None
    for i in range(tries):
        d = Composer(facts, seed + i * 7919, instructions).compose()
        issues = validate(d, facts, buyer_confirmed=buyer_confirmed)
        nb = len(blocking(issues))
        if best is None or nb < best[0]:
            best = (nb, d, issues)
        if nb == 0:
            break
    d = best[1]
    d.meta |= {"seed": seed, "attempt": i, "issues": best[2]}
    return d


# --------------------------------------------------------------------------- Claude


AI_SYSTEM = """You write short letters and emails from a private buyer in the UK to a landowner who has NOT advertised their land.
Write like a normal, thoughtful British person: friendly, polite, conversational, understated, plain English, short paragraphs.
Apply: relevance (why this specific land), a low-friction enquiry (would they ever consider discussing a sale), benefits framed only as possibilities,
credibility (a genuine private buyer seeking land for their own home — never a developer or marketing firm), respect (they may have no interest at all),
flexibility (timing, plot size, conditional arrangements), exactly one simple question or easy invitation to reply, and trust.
Hard rules:
- Use ONLY the facts provided. Never invent familiarity, a visit, a local connection, finance, cash, speed of completion or anything else not given.
- Never mention or guess the owner's age, health, finances, family or motives. Never promise or imply planning permission or a windfall.
- Never include a price or any figure in pounds.
- No sales clichés, urgency, flattery or corporate jargon. Do not write "I hope this email finds you well".
- Greeting and sign-off exactly as instructed. Do not add a privacy notice; it is appended separately.
- Make this draft genuinely specific to the situation; do not produce a generic template."""

AI_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["subject", "body"],
    "additionalProperties": False,
}


def ai_compose(facts: Facts, api_key: str, *, instructions: str | None = None, prior_issues: list[dict[str, str]] | None = None, client=None) -> Draft:
    from plot.ai.extract import MODEL

    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
    lo, hi = WORD_LIMITS.get(facts.kind, (80, 140))
    c = Composer(facts, 0, instructions)
    brief = {k: v for k, v in asdict(facts).items() if v not in (None, "", [], False) and k not in ("retention_until", "reference")}
    user = (
        f"Situation facts (verified unless marked as an observation):\n{json.dumps(brief, indent=1, default=str)}\n\n"
        f"Write a {'letter' if facts.channel == 'post' else 'email'} ({facts.kind.replace('_', ' ')}). Message length {lo}-{hi} words excluding greeting and sign-off.\n"
        f"Start with the greeting line exactly: {c.greeting()}\nEnd with the sign-off exactly:\n{c.signoff()}\n"
        + ("Observations about the land come from maps and must be phrased tentatively.\n" if facts.observations else "")
        + (f"Your instructions for this version: {instructions}\n" if instructions else "")
        + (f"A previous attempt failed these checks — fix them: {json.dumps(prior_issues)}\n" if prior_issues else "")
    )
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=1200,
        system=AI_SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": AI_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    txt = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    data = json.loads(txt)
    return Draft(subject=data["subject"].strip(), body=data["body"].strip(), footer=footer_text(facts), generator="ai", meta={"model": getattr(resp, "model", MODEL), "instructions": instructions})


def generate(facts: Facts, *, seed: int, api_key: str | None = None, instructions: str | None = None, buyer_confirmed: dict[str, bool] | None = None, client=None) -> Draft:
    """AI first when configured (validated, one repair attempt), otherwise or on failure the composer."""
    note = None
    if api_key or client is not None:
        issues: list[dict[str, str]] | None = None
        for _ in range(2):
            try:
                d = ai_compose(facts, api_key or "", instructions=instructions, prior_issues=issues, client=client)
            except Exception as exc:  # noqa: BLE001 — fall back to the composer, and say so
                note = f"AI writer unavailable ({type(exc).__name__}); used the composer"
                break
            issues = validate(d, facts, buyer_confirmed=buyer_confirmed)
            if not blocking(issues):
                d.meta["issues"] = issues
                return d
            note = "AI draft failed validation twice; used the composer"
    d = compose(facts, seed=seed, instructions=instructions, buyer_confirmed=buyer_confirmed)
    if note:
        d.meta["note"] = note
    return d

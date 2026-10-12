"""Unit tests: register parsing, writer validation and variety, reply classification (TEST DATA)."""

from __future__ import annotations

import pytest

from plot.offmarket import title as T
from plot.outreach import replies, writer
from plot.sandbox import REGISTER_GARDEN, REGISTER_PADDOCK, register_pdf
from tests.conftest import fixture_text

# ------------------------------------------------------------------ title register


def test_register_individuals_parsed_with_verbatim_entries():
    x = T.parse_register(fixture_text("title_register_individuals.txt"))
    assert x["title_number"] == "TT123456" and x["tenure"] == "freehold" and x["class_of_title"] == "Title absolute"
    assert x["property_address"] == "12 Example Road, Testbridge SP6 9ZZ"
    assert [p["name"] for p in x["proprietors"]] == ["Alex Jordan Tester", "Sam Riley Tester"]
    assert x["proprietor_postcode"] == "SP6 9ZZ"
    assert x["price_paid"] == {"amount": 465000, "date": "30 May 2019", "kind": "price_paid", "raw": "The price stated to have been paid on 30 May 2019 was £465,000."}
    assert x["charges"][0]["lender"] == "Example Test Bank Plc"
    assert "covenants" in x["covenants"][0]["text"] and "right of way" in x["easements"][0]["text"]
    assert x["restrictions"] and not x["notices"] and not x["personal_representatives"]
    assert T.contact_kind(x) == "individual" and T.default_salutation(x, "individual") == "Alex and Sam"


def test_register_company_and_option_notice_flagged():
    x = T.parse_register(fixture_text("title_register_company.txt"))
    assert x["proprietors"] == [{"name": "Example Land Holdings Limited", "kind": "company", "company_number": "09876543"}]
    assert T.contact_kind(x) == "company" and T.default_salutation(x, "company") == ""
    assert x["notices"] and any("option" in w.lower() for w in x["warnings"])
    assert T.address_lines(x["proprietor_address"]) == ["Unit 4", "Test Business Park", "Testbury", "BH99 9ZZ"]


def test_personal_representatives_detected():
    text = REGISTER_GARDEN.replace("PROPRIETOR: ALEX SANDBOX-TEST and SAM SANDBOX-TEST of", "PROPRIETOR: ALEX SANDBOX-TEST and SAM SANDBOX-TEST as personal representatives of PAT SANDBOX-TEST deceased of")
    x = T.parse_register(text)
    assert x["personal_representatives"] and T.contact_kind(x) == "personal_representatives"
    assert any("manual review" in w for w in x["warnings"])


def test_register_pdf_roundtrip_through_pypdf():
    text = T.pdf_text(register_pdf(REGISTER_PADDOCK))
    x = T.parse_register(text)
    assert x["title_number"] == "TT765432" and x["proprietors"][0]["kind"] == "company"


def test_not_an_official_copy_warns():
    assert any("official copy" in w for w in T.parse_register("Some random letter about a house")["warnings"])


# ------------------------------------------------------------------ writer


def facts(**kw) -> writer.Facts:
    base = dict(channel="post", kind="initial", persona="couple", recipient_kind="individual", recipient_name="Alex Sandbox-Test", salutation="Alex", opportunity_type="garden_subdivision", place="Testville", property_ref="12 Test Road, Testville TE1 1ST", property_is_home=True, title_number="TT123456", first_name="Sai", last_name="Testbuyer", partner_term="wife", returning_to_uk=True, local_connection=None, funding_statement=None, agent_name=None, agent_company=None, reply_email="sai.test@example.invalid", reply_phone=None, reply_postal_address="TEST ADDRESS", observations=["side_garden"], reference="PLOT-0001-1", retention_until="12 April 2027")
    return writer.Facts(**(base | kw))


@pytest.mark.parametrize("typ", ["garden_subdivision", "edge_paddock", "outbuilding", "derelict", "replacement", "planning_history", "register_site"])
@pytest.mark.parametrize("persona", ["private", "couple", "represented"])
def test_composer_passes_validation_for_every_situation(typ, persona):
    f = facts(opportunity_type=typ, persona=persona, agent_name="Jo Agent" if persona == "represented" else None, partner_term="wife" if persona == "couple" else None)
    d = writer.compose(f, seed=writer.seed_for(typ, persona))
    assert not writer.blocking(writer.validate(d, f)), (d.body, writer.validate(d, f))


def test_drafts_for_different_situations_are_genuinely_different():
    a = writer.compose(facts(), seed=1).body
    b = writer.compose(facts(opportunity_type="edge_paddock", property_ref="land on the north side of Test Road, Testville", property_is_home=False), seed=1).body
    c = writer.compose(facts(recipient_kind="company", recipient_name="Sandbox Test Land Limited", salutation=""), seed=1).body
    assert len({a, b, c}) == 3
    assert "garden" in a and "garden" not in b and c.startswith("Dear Sir or Madam")
    # same situation, different owners: different wording, not a template with the address swapped
    bodies = {writer.compose(facts(), seed=writer.seed_for(i)).body for i in range(6)}
    assert len(bodies) >= 4


@pytest.mark.parametrize(
    "text,code",
    [
        ("I hope this email finds you well.", "banned_phrase"),
        ("This is an exciting investment opportunity for you.", "banned_phrase"),
        ("As a cash buyer we can move fast.", "unconfirmed_cash"),
        ("We can complete quickly with no chain.", "unconfirmed_speed"),
        ("We live locally and love the area.", "unconfirmed_local"),
        ("I walked past your house last week.", "unconfirmed_visit"),
        ("Planning would be straightforward here.", "unconfirmed_planning"),
        ("We could offer £120,000 for the plot.", "price"),
        ("Perhaps now you are retired the garden is too much?", "personal_assumption"),
        ("Would you sell? Would you meet? Would you call?", "questions"),
    ],
)
def test_validator_blocks_dishonest_or_pushy_wording(text, code):
    f = facts()
    d = writer.compose(f, seed=3)
    paras = d.body.split("\n\n")
    paras.insert(2, text)
    d.body = "\n\n".join(paras)
    assert code in {i["code"] for i in writer.blocking(writer.validate(d, f))}


def test_confirmed_funding_statement_is_allowed():
    f = facts(funding_statement="We have a self-build mortgage agreed in principle")
    d = writer.compose(f, seed=5, buyer_confirmed={"funding": True})
    assert "mortgage agreed in principle" in d.body
    assert not writer.blocking(writer.validate(d, f, buyer_confirmed={"funding": True}))
    assert {"unconfirmed_funding"} <= {i["code"] for i in writer.validate(d, f, buyer_confirmed={})}


def test_footer_and_length_rules():
    f = facts()
    d = writer.compose(f, seed=9)
    assert "HM Land Registry" in d.footer and "PLOT-0001-1" in d.footer and "12 April 2027" in d.footer
    d.footer = ""
    assert "privacy_notice" in {i["code"] for i in writer.validate(d, f)}
    d2 = writer.compose(f, seed=9)
    d2.body = "Dear Alex,\n\nWould you sell part of your garden?\n\nBest wishes,\nSai"
    assert "length" in {i["code"] for i in writer.validate(d2, f)}


def test_ai_writer_output_is_validated_and_falls_back(monkeypatch):
    class Block:
        type = "text"

        def __init__(self, t):
            self.text = t

    class Resp:
        model = "claude-opus-5-5"

        def __init__(self, t):
            self.content = [Block(t)]

    class Client:
        def __init__(self, texts):
            self.texts = list(texts)
            self.calls = []
            self.beta = self
            self.messages = self

        def create(self, **kw):
            self.calls.append(kw)
            return Resp(self.texts.pop(0))

    f = facts()
    good = writer.compose(f, seed=11)
    import json

    ok = Client([json.dumps({"subject": good.subject, "body": good.body})])
    d = writer.generate(f, seed=1, client=ok)
    assert d.generator == "ai" and ok.calls[0]["model"] == "claude-opus-5-5" and ok.calls[0]["output_config"]["format"]["type"] == "json_schema"
    bad = json.dumps({"subject": "Hi", "body": "Dear Alex,\n\nAs a cash buyer I hope this email finds you well.\n\nSai"})
    c2 = Client([bad, bad])
    d2 = writer.generate(f, seed=1, client=c2)
    assert d2.generator == "composer" and "failed validation" in d2.meta["note"] and len(c2.calls) == 2


# ------------------------------------------------------------------ replies


@pytest.mark.parametrize(
    "text,cat",
    [
        ("Please remove us from your list.", "requests_no_contact"),
        ("Not interested. Please don't contact me again.", "requests_no_contact"),
        ("STOP", "requests_no_contact"),
        ("No thank you, it's not for sale.", "not_interested"),
        ("How much would you pay?", "requests_price"),
        ("Which part of the garden did you have in mind? Tell me more.", "wants_information"),
        ("We'd be happy to discuss, give me a call.", "interested"),
        ("Maybe in the future, not sure yet.", "possibly_interested"),
        ("Received your letter.", "unknown"),
    ],
)
def test_reply_classification_with_opt_out_priority(text, cat):
    assert replies.classify(text)["category"] == cat


def test_decline_reasons_are_about_the_deal():
    assert replies.classify("No thanks — we'd lose our privacy and the view.")["decline_reason"] == "privacy"
    assert replies.classify("Not interested, the council would never give planning.")["decline_reason"] == "planning_concerns"
    assert set(replies.DECLINE_REASONS) >= {"not_selling", "price_expectations", "privacy", "planning_concerns"}

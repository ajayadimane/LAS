from decimal import Decimal

from las.common.codes import normalise_mobile, parse_amount
from las.parse.candidates import extract_candidates
from las.parse.context import MailContext, split_quoted
from las.parse.ground import Grounder
from las.parse.schema import CustomerMention, ExtractedAction, Extraction


def test_mobile_normalisation() -> None:
    for raw in ("+91 98765-43210", "098765 43210", "919876543210", "९८७६५४३२१०", "98765.43210"):
        assert normalise_mobile(raw) == "9876543210", raw
    for bad in ("12345", "5876543210", "98765432101"):
        assert normalise_mobile(bad) is None


def test_candidates() -> None:
    text = (
        "Customer Ramesh, mob +91 98765 43210, ramesh@gmail.com. Paid Rs. 1,475/- UTR 412345678901 "
        "for INV-2026-000012 and case las-2609-000003. Also 5,000 rupees. txn id T2609ABC99"
    )
    c = extract_candidates(text, own_addresses={"las@integramicro.co.in"})
    assert c.mobiles == ["9876543210"]
    assert c.emails == ["ramesh@gmail.com"]
    assert "412345678901" in c.txn_refs and "T2609ABC99" in c.txn_refs
    assert Decimal("1475.00") in c.amounts and Decimal("5000.00") in c.amounts
    assert c.codes["invoice"] == ["INV-2026-000012"] and c.codes["case"] == ["LAS-2609-000003"]
    assert parse_amount("₹ 2,950.00") == Decimal("2950.00")


def _ctx(body: str) -> MailContext:
    return MailContext(subject="s", new_text=body, quoted_text="", forwarded=False)


def test_grounding_rejects_invented_values() -> None:
    body = "Please onboard रमेश पाटिल, 9876543210, needs project report. Customer has given consent."
    ctx = _ctx(body)
    g = Grounder(
        ctx,
        extract_candidates(body),
        active_services={"DPR_GEN", "BUREAU_CHECK"},
        synonyms={"DPR_GEN": ["project report"]},
        display_names={},
    )
    a = ExtractedAction(
        intent="NEW_CUSTOMER",
        confidence=0.9,
        customers=[
            CustomerMention(
                name="Ramesh Patil",  # transliterated: not a span of the source
                mobile="9876543211",  # hallucinated
                services=["DPR_GEN", "BUREAU_CHECK"],
                services_stated_explicitly=True,
                authorization_statement="Customer has given consent.",
            )
        ],
    )
    ga = g.ground(a, Extraction(actions=[a]))
    c = ga.entities["customers"][0]
    assert c["name"] is None and c["mobile"] is None
    assert c["authorization"] == "Customer has given consent."
    assert c["services"] == ["DPR_GEN", "BUREAU_CHECK"]
    assert c["services_explicit"] is False  # CIBIL/bureau not named in the mail
    assert {p.field for p in ga.problems if p.kind == "ungrounded"} == {
        "customers.0.name",
        "customers.0.mobile",
    }


def test_grounding_passes_through_onboard_explicit() -> None:
    body = "FYI, Ramesh Patil 9876543210 is interested in a loan."
    ctx = _ctx(body)
    g = Grounder(
        ctx,
        extract_candidates(body),
        active_services={"DPR_GEN"},
        synonyms={"DPR_GEN": ["project report"]},
        display_names={},
    )

    def customer(explicit: bool) -> CustomerMention:
        return CustomerMention(
            name="Ramesh Patil", mobile="9876543210", onboarding_requested_explicitly=explicit
        )

    for explicit in (True, False):
        a = ExtractedAction(intent="NEW_CUSTOMER", confidence=0.9, customers=[customer(explicit)])
        ga = g.ground(a, Extraction(actions=[a]))
        assert ga.entities["customers"][0]["onboard_explicit"] is explicit


def test_quote_split_keeps_forward() -> None:
    body = "fyi\n\n---------- Forwarded message ---------\nFrom: Ravi\nOnboard Suresh 9123456780"
    assert "Suresh" in split_quoted(body).new_text


def test_quote_split_handles_from_to_date_subject_block() -> None:
    """A quoted reply header with a To: line between From: and Date: (seen from some webmail
    clients) must still be recognised as quoted history, not left in the reply's new text."""
    body = (
        "List the details of my customer\n\n\n"
        "From: Integra LAS <las@integramicro.co.in>\n"
        "To: <masirulislam@botaiml.com>\n"
        "Date: Wed, 30 Sep 2026 15:23:51 +0530\n"
        "Subject: Status update\n\n"
        "Dear SK Masirul Islam,\n\nAjay (89xxxx0310, LC100003)\n"
    )
    split = split_quoted(body)
    assert split.new_text == "List the details of my customer"
    assert "Ajay" not in split.new_text and "LC100003" not in split.new_text
    assert "Ajay" in split.quoted_text

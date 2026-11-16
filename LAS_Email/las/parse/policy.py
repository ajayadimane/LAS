"""Risk tier → execution policy. Deterministic; the LLM's output is only an input.

AUTO    execute and state what was understood.
CONFIRM send a readback; execute only on the BD's clear yes (the readback as sent).
REVIEW  ops queue + holding reply.
Missing required fields → a clarification request (status AWAITING_CLARIFICATION).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from las.parse.ground import GroundedAction

Tier = Literal["AUTO", "CONFIRM", "REVIEW"]

_INJECTION = re.compile(
    r"(?i)(ignore (all |any )?(the )?(previous|prior|above) (instructions|prompts?|rules)"
    r"|disregard (the |all )?(previous|above|system)"
    r"|you are now\b|you are (an? )?(ai|assistant|chatgpt|gemini|claude|llm)\b"
    r"|system prompt|\bsystem\s*:|\bassistant\s*:|developer mode|jailbreak"
    r"|mark (the |this )?(payment|invoice)s? (as )?(verified|paid)"
    r"|(reveal|print|show) (your|the) (prompt|instructions)"
    r"|act as (an? )?(admin|ops|system)"
    r"|पिछले (सभी )?निर्देश(ों)? (को )?(अनदेखा|भूल))"
)


def injection_heuristic(text: str) -> bool:
    return bool(_INJECTION.search(text))


@dataclass
class Decision:
    tier: Tier
    missing: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    # REVIEW only: NEEDS_REVIEW (ops queue + holding reply) or REJECTED (auto-declined, no ops action).
    terminal: Literal["NEEDS_REVIEW", "REJECTED"] = "NEEDS_REVIEW"

    @property
    def needs_clarification(self) -> bool:
        return bool(self.missing) and self.tier != "REVIEW"


def normalise_actions(actions: list[GroundedAction]) -> list[GroundedAction]:
    """Several NEW_CUSTOMER actions in one mail become one MULTI_CUSTOMER (always previewed)."""
    news = [a for a in actions if a.intent == "NEW_CUSTOMER"]
    if len(news) <= 1:
        multi = [a for a in actions if a.intent == "MULTI_CUSTOMER"]
        for m in multi:
            if len(m.entities["customers"]) == 1:  # a one-row "list" is a single customer
                m.intent = "NEW_CUSTOMER"
        return actions
    merged = GroundedAction(
        intent="MULTI_CUSTOMER",
        confidence=min(a.confidence for a in news),
        entities=dict(
            news[0].entities, customers=[c for a in news for c in a.entities["customers"]]
        ),
        evidence={k: v for a in news for k, v in a.evidence.items()},
        problems=[p for a in news for p in a.problems],
    )
    out: list[GroundedAction] = []
    inserted = False
    for a in actions:
        if a.intent == "NEW_CUSTOMER":
            if not inserted:
                out.append(merged)
                inserted = True
            continue
        out.append(a)
    return out


def contradictions(actions: list[GroundedAction]) -> list[str]:
    by_name: dict[str, set[str]] = {}
    by_mobile: dict[str, set[str]] = {}
    intents = {a.intent for a in actions}
    for a in actions:
        if a.intent not in ("NEW_CUSTOMER", "MULTI_CUSTOMER"):
            continue
        for c in a.entities["customers"]:
            if c["name"] and c["mobile"]:
                by_name.setdefault(c["name"].casefold(), set()).add(c["mobile"])
                by_mobile.setdefault(c["mobile"], set()).add(c["name"].casefold())
    out = [f"name '{n}' has {len(m)} different mobiles" for n, m in by_name.items() if len(m) > 1]
    out += [
        f"mobile {m[:2]}xxxx{m[-4:]} has {len(n)} different names"
        for m, n in by_mobile.items()
        if len(n) > 1
    ]
    if "CANCEL_REQUEST" in intents and intents & {
        "NEW_CUSTOMER",
        "SELECT_SERVICES",
        "MULTI_CUSTOMER",
    }:
        out.append("cancel and create in the same mail")
    return out


def _required(a: GroundedAction) -> list[str]:
    e = a.entities
    missing: list[str] = []
    if a.intent == "NEW_CUSTOMER":
        c = e["customers"][0] if e["customers"] else {}
        if not c.get("name"):
            missing.append("customer_name")
        if not c.get("mobile"):
            missing.append("mobile")
        # authorization isn't required: a BD only mails us after the customer has already
        # authorised them (las/workflow/actions/onboarding.py records an implied declaration).
    elif a.intent == "SELECT_SERVICES":
        if not (
            e["services"] or e["menu_choices"] or any(c.get("services") for c in e["customers"])
        ):
            missing.append("services")
    elif a.intent == "CORRECTION":
        corr = e.get("correction") or {}
        if not corr.get("new"):
            missing.append(f"corrected_{corr.get('field') or 'value'}")
    return missing


def decide(
    a: GroundedAction,
    *,
    threshold: float,
    injection: bool,
    contradiction: list[str],
    escalated: bool,
) -> Decision:
    if injection:
        return Decision("REVIEW", reasons=["suspected prompt injection"])
    if a.intent == "OUT_OF_SCOPE":
        return Decision("REVIEW", reasons=["out of scope"], terminal="REJECTED")
    if contradiction and a.intent in (
        "NEW_CUSTOMER",
        "MULTI_CUSTOMER",
        "CANCEL_REQUEST",
        "SELECT_SERVICES",
    ):
        return Decision("REVIEW", reasons=contradiction)
    if a.confidence < threshold and a.intent not in (
        "GENERAL_QUESTION",
        "STATUS_QUERY",
        "CLARIFICATION_REPLY",
    ):
        reason = "low confidence after escalation" if escalated else "low confidence"
        if not _required(a):  # confident enough to ask? only when something concrete is missing
            return Decision("REVIEW", reasons=[reason])
    missing = _required(a)
    if a.intent == "NEW_CUSTOMER":
        c = a.entities["customers"][0] if a.entities["customers"] else {}
        inferred = bool(c.get("services")) and not c.get("services_explicit")
        not_requested = not c.get("onboard_explicit", True)
        reasons = (["services inferred"] if inferred else []) + (
            ["onboarding not requested"] if not_requested else []
        )
        return Decision("CONFIRM" if inferred or not_requested else "AUTO", missing, reasons)
    if a.intent == "MULTI_CUSTOMER":
        return Decision("CONFIRM", [], ["multiple customers"])
    if a.intent == "SELECT_SERVICES":
        explicit = (
            a.entities["services_explicit"]
            or bool(a.entities["menu_choices"])
            or all(c.get("services_explicit") for c in a.entities["customers"] if c.get("services"))
        )
        return Decision(
            "AUTO" if explicit else "CONFIRM", missing, [] if explicit else ["services inferred"]
        )
    if a.intent == "CANCEL_REQUEST":
        return Decision("CONFIRM", [], ["cancellation"])
    if a.intent in (
        "STATUS_QUERY",
        "GENERAL_QUESTION",
        "PAYMENT_PROOF",
        "SUBMIT_DOCUMENT",
        "CONFIRM",
        "CLARIFICATION_REPLY",
        "CORRECTION",
    ):
        return Decision("AUTO", missing)
    return Decision("REVIEW", reasons=[f"unhandled intent {a.intent}"])


QUESTION_TEXT = {
    "customer_name": "The customer's full name",
    "mobile": "The customer's 10-digit mobile number",
    "services": "Which services the customer needs",
    "corrected_mobile": "The correct mobile number",
    "corrected_name": "The correct name",
    "corrected_email": "The correct email address",
    "corrected_services": "The correct services",
    "customer": "Which customer this is about (name or mobile number)",
    "txn_ref": "The UTR / transaction ID of the payment",
    "attachment_mapping": "Which customer and service each attached file is for",
    "address": "The customer's postal address (street, city, state and 6-digit PIN code)",
}

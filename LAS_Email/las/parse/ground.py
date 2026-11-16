"""Deterministic grounding of extracted values against the source text.

A value the model produced that is not present in the mail/attachments (after normalisation) is
removed and reported as ``ungrounded``; a required value that is simply absent is ``missing``.
Ungrounded or missing values are never executed — they become clarification questions.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

from las.common.codes import (
    ascii_digits,
    normalise_code,
    normalise_email,
    normalise_mobile,
    parse_amount,
)
from las.parse.candidates import Candidates
from las.parse.context import MailContext
from las.parse.schema import CustomerMention, ExtractedAction, Extraction


@dataclass(frozen=True)
class FieldProblem:
    field: str
    kind: Literal["ungrounded", "missing", "invalid"]
    detail: str = ""


@dataclass
class GroundedAction:
    intent: str
    confidence: float
    entities: dict[str, Any]
    evidence: dict[str, str]
    problems: list[FieldProblem] = field(default_factory=list)

    @property
    def grounded(self) -> bool:
        return not any(p.kind == "ungrounded" for p in self.problems)

    def has_ungrounded(self) -> bool:
        return any(p.kind == "ungrounded" for p in self.problems)


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", ascii_digits(text)).casefold()
    t = re.sub(r"[\"'“”‘’`.,;:!?()\[\]{}]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


class Source:
    def __init__(self, text: str) -> None:
        self.raw = text
        self.norm = _norm(text)
        self.digits = re.sub(r"\D", "", ascii_digits(text))

    def has_span(self, span: str | None) -> bool:
        if not span or not span.strip():
            return False
        n = _norm(span)
        return bool(n) and n in self.norm


_NAME_OK = re.compile(r"^[^\d@<>{}\[\]|\\/=+*#$%^&~]{2,80}$")
# Same shape as the leads intake form's pincode check (las/leads/webform.py:PINCODE_RE) and the
# DB CHECK on leads.pincode (docs/schema.sql) -- kept local here rather than imported since
# las/parse never otherwise depends on las/leads.
_PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")


def _plausible_name(name: str) -> bool:
    return bool(_NAME_OK.match(name.strip())) and len(name.split()) <= 6


class Grounder:
    def __init__(
        self,
        ctx: MailContext,
        cands: Candidates,
        *,
        active_services: set[str],
        synonyms: dict[str, list[str]],
        display_names: dict[str, str],
    ) -> None:
        self.ctx = ctx
        self.cands = cands
        self.src = Source(ctx.source_text())
        self.name_src = Source(ctx.name_source_text())
        self.active = active_services
        self.synonyms = synonyms
        self.display = display_names
        self.attachment_names = {a.filename.casefold(): a.filename for a in ctx.attachments}

    # -- helpers --------------------------------------------------------------------------
    def service_named_in_text(self, code: str) -> bool:
        phrases = [
            code,
            code.replace("_", " "),
            self.display.get(code, ""),
            *self.synonyms.get(code, []),
        ]
        return any(p and _norm(p) in self.src.norm for p in phrases)

    def _mobile(self, value: str | None, path: str, probs: list[FieldProblem]) -> str | None:
        if not value:
            return None
        m = normalise_mobile(value)
        if m is None:
            probs.append(FieldProblem(path, "invalid", "not a valid Indian mobile"))
            return None
        if m not in self.cands.mobiles:
            probs.append(FieldProblem(path, "ungrounded"))
            return None
        return m

    def _email(self, value: str | None, path: str, probs: list[FieldProblem]) -> str | None:
        if not value:
            return None
        e = normalise_email(value)
        if e is None or e not in self.cands.emails:
            probs.append(FieldProblem(path, "ungrounded" if e else "invalid"))
            return None
        return e

    def _name(self, value: str | None, path: str, probs: list[FieldProblem]) -> str | None:
        if not value or not value.strip():
            return None
        if not _plausible_name(value):
            probs.append(FieldProblem(path, "invalid", "not a plausible name"))
            return None
        if not self.name_src.has_span(value):
            probs.append(FieldProblem(path, "ungrounded"))
            return None
        return re.sub(r"\s+", " ", value.strip())

    def _span(self, value: str | None, path: str, probs: list[FieldProblem]) -> str | None:
        if not value or not value.strip():
            return None
        if not self.src.has_span(value):
            probs.append(FieldProblem(path, "ungrounded"))
            return None
        return value.strip()

    def _pincode(self, value: str | None, path: str, probs: list[FieldProblem]) -> str | None:
        if not value or not value.strip():
            return None
        digits = "".join(ascii_digits(value).split())
        if not _PINCODE_RE.match(digits):
            probs.append(FieldProblem(path, "invalid", "not a valid 6-digit PIN code"))
            return None
        if not self.src.has_span(value):
            probs.append(FieldProblem(path, "ungrounded"))
            return None
        return digits

    def _services(
        self, codes: list[str], claimed_explicit: bool, path: str, probs: list[FieldProblem]
    ) -> tuple[list[str], bool]:
        out: list[str] = []
        for c in codes:
            code = c.strip().upper()
            if code not in self.active:
                probs.append(FieldProblem(path, "invalid", f"unknown service {c}"))
            elif code not in out:
                out.append(code)
        explicit = (
            bool(out) and claimed_explicit and all(self.service_named_in_text(c) for c in out)
        )
        return out, explicit

    def _customer(self, c: CustomerMention, i: int, probs: list[FieldProblem]) -> dict[str, Any]:
        p = f"customers.{i}"
        services, explicit = self._services(
            c.services, c.services_stated_explicitly, f"{p}.services", probs
        )
        code = normalise_code(c.customer_code) if c.customer_code else None
        if code and code not in self.cands.all_codes():
            probs.append(FieldProblem(f"{p}.customer_code", "ungrounded"))
            code = None
        return {
            "name": self._name(c.name, f"{p}.name", probs),
            "mobile": self._mobile(c.mobile, f"{p}.mobile", probs),
            "email": self._email(c.email, f"{p}.email", probs),
            "customer_code": code,
            "services": services,
            "services_explicit": explicit,
            "onboard_explicit": bool(c.onboarding_requested_explicitly),
            "authorization": self._span(c.authorization_statement, f"{p}.authorization", probs),
            "row_ref": c.row_ref,
            "street_address": self._span(c.street_address, f"{p}.street_address", probs),
            "city": self._span(c.city, f"{p}.city", probs),
            "state_province": self._span(c.state_province, f"{p}.state_province", probs),
            "postal_code": self._pincode(c.postal_code, f"{p}.postal_code", probs),
            "bank_name": self._span(c.bank_name, f"{p}.bank_name", probs),
            "project_name": self._span(c.project_name, f"{p}.project_name", probs),
        }

    # -- main -----------------------------------------------------------------------------
    def ground(self, a: ExtractedAction, extraction: Extraction) -> GroundedAction:
        probs: list[FieldProblem] = []
        customers = [self._customer(c, i, probs) for i, c in enumerate(a.customers)]
        services, services_explicit = self._services(
            a.services, a.services_stated_explicitly, "services", probs
        )
        codes: list[str] = []
        for c in a.codes:
            code = normalise_code(c)
            if code in self.cands.all_codes():
                codes.append(code)
            else:
                probs.append(FieldProblem("codes", "ungrounded", c))
        txn_ref = None
        if a.txn_ref:
            t = a.txn_ref.strip().upper()
            if t in {x.upper() for x in self.cands.txn_refs}:
                txn_ref = t
            else:
                probs.append(FieldProblem("txn_ref", "ungrounded"))
        amount = None
        if a.amount:
            amt = parse_amount(a.amount)
            if amt is not None and amt in self.cands.amounts:
                amount = str(amt)
            else:
                probs.append(FieldProblem("amount", "ungrounded"))
        filenames = []
        for f in a.attachment_filenames:
            real = self.attachment_names.get(f.casefold())
            if real:
                filenames.append(real)
            else:
                probs.append(FieldProblem("attachment_filenames", "ungrounded", f))
        menu = [
            n for n in a.menu_choices if 1 <= n <= 50 and str(n) in ascii_digits(self.ctx.new_text)
        ]
        if len(menu) != len(a.menu_choices):
            probs.append(FieldProblem("menu_choices", "ungrounded"))
        correction = None
        if a.intent == "CORRECTION" and a.correction_field:
            new_val: str | None
            f = a.correction_field
            if f == "mobile":
                new_val = self._mobile(a.correction_new_value, "correction.new", probs)
            elif f == "email":
                new_val = self._email(a.correction_new_value, "correction.new", probs)
            elif f == "name":
                new_val = self._name(a.correction_new_value, "correction.new", probs)
            else:
                new_val = ",".join(services) if services else None
            old = a.correction_old_value
            if f == "mobile" and old:
                old = normalise_mobile(old) or old
            correction = {"field": f, "old": old, "new": new_val}
        all_auth = self._span(
            extraction.all_customers_authorized_statement, "all_authorized", probs
        )
        evidence = {e.field: e.span for e in a.evidence if self.src.has_span(e.span)}
        entities: dict[str, Any] = {
            "customers": customers,
            "services": services,
            "services_explicit": services_explicit,
            "menu_choices": menu,
            "codes": codes,
            "txn_ref": txn_ref,
            "amount": amount,
            "attachment_filenames": filenames,
            "correction": correction,
            "confirm_answer": a.confirm_answer,
            "answers_clarification_id": a.answers_clarification_id,
            "question_summary": (a.question_summary or "")[:300] or None,
            "all_authorized": all_auth,
        }
        return GroundedAction(
            intent=a.intent,
            confidence=float(a.confidence),
            entities=entities,
            evidence=evidence,
            problems=probs,
        )

"""Classifier extracted from LAS_Email; this module has no DB/mail dependencies."""

import re
from dataclasses import dataclass
from typing import Literal

from .sniff import CSV, XLSX

DocClass = Literal[
    "DPR",
    "PAN",
    "AADHAAR",
    "UDYAM",
    "LAND_RECORD",
    "QUOTATION",
    "BANK_STATEMENT",
    "PAYMENT_PROOF",
    "CONSENT_FORM",
    "BATCH_SHEET",
    "OTHER",
]

RULE_THRESHOLD = 0.8

_RULES: list[tuple[str, list[str], list[str]]] = [
    # (class, strong text patterns, filename patterns)
    (
        "PAN",
        [
            r"permanent account number",
            r"income tax department",
            r"\b[a-z]{5}\d{4}[a-z]\b",
        ],
        [r"\bpan\b"],
    ),
    (
        "AADHAAR",
        [r"aadhaar", r"आधार", r"ಆಧಾರ್", r"unique identification authority"],
        [r"aadha?r", r"adhar"],
    ),
    ("UDYAM", [r"udyam-[a-z]{2}-\d{2}-\d{7}", r"udyam registration"], [r"udyam"]),
    (
        "DPR",
        [
            r"detailed project report",
            r"project report",
            r"(sources|means) of finance",
            r"financing means",
            r"cost of (the )?project",
            r"projected (profit|balance)",
        ],
        [r"\bdpr\b", r"project[_ ]?report"],
    ),
    (
        "QUOTATION",
        [r"\bquotation\b", r"proforma invoice", r"\bquote no"],
        [r"quot", r"proforma"],
    ),
    (
        "LAND_RECORD",
        [
            r"\brtc\b",
            r"pahani",
            r"ಪಹಣಿ",
            r"7/12",
            r"khata",
            r"survey no",
            r"खसरा",
            r"jamabandi",
            r"record of rights",
        ],
        [r"rtc", r"pahani", r"7[-_]12", r"land", r"khata"],
    ),
    (
        "BANK_STATEMENT",
        [r"statement of account", r"opening balance", r"closing balance"],
        [r"statement"],
    ),
    (
        "PAYMENT_PROOF",
        [
            r"\butr\b",
            r"transaction (id|successful)",
            r"paid (successfully|to)",
            r"upi ref",
            r"payment successful",
        ],
        [r"payment", r"receipt", r"screenshot", r"utr"],
    ),
    (
        "CONSENT_FORM",
        [r"consent", r"authori[sz]ation letter", r"i hereby authori[sz]e"],
        [r"consent", r"authori"],
    ),
]


def type_markers(doc_class: str) -> list[str]:
    """Text patterns that justify classifying a document as doc_class --
    reused to re-verify a document's content before accepting it as an SR's
    input (see las/workflow/actions/doc_relevance.py) instead of just
    trusting a stored classification that may have come from a
    lower-confidence LLM-vision guess."""
    for cls, text_pats, _ in _RULES:
        if cls == doc_class:
            return text_pats
    return []  # e.g. "OTHER" has no defining pattern — nothing to re-verify


@dataclass(frozen=True)
class Classification:
    doc_class: str
    confidence: float
    method: Literal["RULES", "LLM"]


def classify_by_rules(filename: str, mime: str, text: str) -> Classification:
    if mime in (XLSX, CSV):
        header = text.split("\n", 1)[0].lower()
        if any(
            k in header for k in ("mobile", "phone", "name", "contact", "मोबाइल", "नाम")
        ):
            return Classification("BATCH_SHEET", 0.95, "RULES")
        return Classification("OTHER", 0.5, "RULES")
    fn = filename.lower()
    t = text.lower()
    best: tuple[str, float] = ("OTHER", 0.0)
    for cls, text_pats, name_pats in _RULES:
        text_hits = sum(1 for p in text_pats if re.search(p, t))
        name_hit = any(re.search(p, fn) for p in name_pats)
        score = 0.0
        if text_hits:
            score = min(
                0.8 + 0.07 * (text_hits - 1) + (0.05 if name_hit else 0.0), 0.97
            )
        elif name_hit:
            score = 0.7
        if score > best[1]:
            best = (cls, score)
    return Classification(best[0], best[1] if best[1] else 0.3, "RULES")

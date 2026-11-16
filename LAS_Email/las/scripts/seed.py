"""Idempotent seed of reference data: service catalog, synonyms, fees, AI pricing, test BDs.

Run: ``uv run las-seed`` (uses DATABASE_URL) or ``uv run las-seed --no-test-bds``.
Existing rows are never overwritten, so prices you fill in by hand survive re-seeding.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from las.common.db import dispose_engine, get_session_factory, session_scope
from las.common.models import (
    AiModelPricing,
    BdPartner,
    ServiceCatalog,
    ServiceSynonym,
)
from las.common.settings import get_settings

EFFECTIVE_FROM = dt.date(2026, 4, 1)

# (code, group, display, order, re_referral, input_file, sla_hours, token_budget (unused since
# 0020: no per-request budget), active)
SERVICES: list[tuple[str, int, str, int, bool, bool, int, int | None, bool]] = [
    ("DPR_GEN", 1, "DPR generation (Detailed Project Report)", 1, False, True, 48, 400_000, True),
    ("DPR_ANALYSIS", 1, "DPR analysis", 2, False, True, 24, 300_000, True),
    ("ID_VERIFY", 2, "Identity / KYC verification (via IFPL)", 3, True, False, 24, None, True),
    ("BUREAU_CHECK", 2, "Credit bureau check (via IFPL)", 4, True, False, 24, None, True),
    ("UDYAM_VERIFY", 2, "Udyam verification (via IFPL)", 5, True, False, 24, None, True),
    ("LAND_VERIFY", 2, "Land record verification (via IFPL)", 6, True, False, 48, None, True),
    ("SMALL_BORROWING", 2, "Small borrowing (via IFPL)", 7, True, False, 72, None, True),
    # Deterministic DPR-driven loan-eligibility engine (las/services/appraisal/appraisal.py) --
    # unrelated to the "Value Addition" IFPL bundle below; the two used to share a display name
    # ("VALUE ADDITION") purely by historical accident.
    ("APPRAISAL", 3, "Credit Appraisal", 8, False, True, 24, 200_000, True),
    ("QUOTATION_CHECK", 3, "DOC VERIFICATION", 9, False, True, 24, 100_000, True),
    # Active as a placeholder ahead of the PostGIS-based geo-spatial check itself (not yet
    # designed/built) -- selectable and billable, fulfilled manually by ops for now.
    ("GEO_CHECK", 3, "Geo-spatial land check", 10, False, True, 48, None, True),
    # The actual IFPL-routed bundle (identity/KYC + bureau + Udyam + land + small-borrowing,
    # las/workflow/actions/services.py:FOLDED_INTO_VALUE_ADDITION) sold to the BD/customer as one
    # menu item: one SR, one combined referral link, no document or fee collected by us -- IFPL
    # collects both on their own portal. The ₹500 fee row below is informational only (shown in
    # the selection checklist) -- a requires_re_referral service is always excluded from what LAS
    # actually bills (las/workflow/actions/services.py:menu_fee).
    ("VALUE_ADDITION", 2, "Value Addition", 11, True, False, 24, None, True),
]

SYNONYMS: dict[str, list[tuple[str, str]]] = {
    "DPR_GEN": [
        ("dpr", "en"),
        ("project report", "en"),
        ("detailed project report", "en"),
        ("prepare dpr", "en"),
        ("make dpr", "en"),
        ("dpr banana hai", "hi-Latn"),
        ("project report bana do", "hi-Latn"),
        ("प्रोजेक्ट रिपोर्ट", "hi"),
        ("डीपीआर", "hi"),
        ("ಯೋಜನಾ ವರದಿ", "kn"),
        ("ಡಿಪಿಆರ್", "kn"),
    ],
    "DPR_ANALYSIS": [
        ("dpr analysis", "en"),
        ("check the dpr", "en"),
        ("review dpr", "en"),
        ("analyse project report", "en"),
        ("analyze project report", "en"),
        ("dpr check karo", "hi-Latn"),
        ("डीपीआर जांच", "hi"),
        ("ಡಿಪಿಆರ್ ವಿಶ್ಲೇಷಣೆ", "kn"),
    ],
    "ID_VERIFY": [
        ("kyc", "en"),
        ("kyc check", "en"),
        ("identity verification", "en"),
        ("aadhaar verification", "en"),
        ("pan verification", "en"),
        ("ekyc", "en"),
        ("केवाईसी", "hi"),
        ("ಕೆವೈಸಿ", "kn"),
    ],
    "BUREAU_CHECK": [
        ("cibil", "en"),
        ("cibil check", "en"),
        ("credit score", "en"),
        ("bureau check", "en"),
        ("credit report", "en"),
        ("सिबिल", "hi"),
        ("ಸಿಬಿಲ್", "kn"),
    ],
    "UDYAM_VERIFY": [
        ("udyam", "en"),
        ("udyam verification", "en"),
        ("msme registration check", "en"),
        ("उद्यम", "hi"),
    ],
    "LAND_VERIFY": [
        ("land verification", "en"),
        ("land record check", "en"),
        ("rtc check", "en"),
        ("pahani", "en"),
        ("7/12", "en"),
        ("khata", "en"),
        ("जमीन जांच", "hi"),
        ("ಪಹಣಿ", "kn"),
        ("ಭೂ ದಾಖಲೆ ಪರಿಶೀಲನೆ", "kn"),
    ],
    "SMALL_BORROWING": [
        ("small borrowing", "en"),
        ("small loan", "en"),
        ("chhota loan", "hi-Latn"),
        ("छोटा लोन", "hi"),
    ],
    "APPRAISAL": [
        ("appraisal", "en"),
        ("eligibility", "en"),
        ("eligibility check", "en"),
        ("loan eligibility", "en"),
        ("policy check", "en"),
        ("assessment", "en"),
        ("eligibility dekh lo", "hi-Latn"),
        ("पात्रता", "hi"),
        ("ಅರ್ಹತೆ", "kn"),
    ],
    "QUOTATION_CHECK": [
        ("quotation check", "en"),
        ("vendor check", "en"),
        ("quotation verification", "en"),
        ("verify quotation", "en"),
        ("कोटेशन", "hi"),
        ("ಕೊಟೇಶನ್", "kn"),
    ],
    "GEO_CHECK": [("geo check", "en"), ("geo tagging", "en"), ("gps check", "en")],
    "VALUE_ADDITION": [
        ("value addition", "en"),
        ("value add", "en"),
        ("kyc and cibil", "en"),
        ("kyc bureau udyam land", "en"),
    ],
}

def _pricing_rows() -> list[dict[str, Any]]:
    s = get_settings()
    models = sorted(
        {s.llm_extraction_model, s.llm_escalation_model, s.llm_vision_model, s.llm_dpr_model}
    )
    rows: list[dict[str, Any]] = [
        # TODO(ops): fill in USD prices per 1M tokens from https://ai.google.dev/pricing.
        # Cost stays NULL in ai_usage until these are set (tokens are still recorded).
        dict(
            provider=s.llm_provider_name,
            model=m,
            usage_unit="TOKEN",
            input_per_mtok=None,
            output_per_mtok=None,
            cache_write_per_mtok=None,
            cache_read_per_mtok=None,
            valid_from=EFFECTIVE_FROM,
        )
        for m in models
    ]
    # Local LiteParse parsing/OCR costs nothing per page; rows exist so ai_usage cost is 0, not NULL.
    from las.attachments.parser import LiteParseParser

    try:
        parser_model = LiteParseParser().model
    except ImportError:
        parser_model = "liteparse-unknown"
    rows.append(
        dict(
            provider="liteparse",
            model=parser_model,
            usage_unit="PAGE",
            per_unit_price=Decimal("0"),
            valid_from=EFFECTIVE_FROM,
        )
    )
    return rows


TEST_BDS = [
    dict(
        ibkart_bd_code="BD-TEST-001",
        full_name="Test BD One",
        email="bd.one@example.com",
        mobile="9876500001",
        region="Karnataka",
        daily_request_cap=50,
    ),
    dict(
        ibkart_bd_code="BD-TEST-002",
        full_name="Test BD Two",
        email="bd.two@example.com",
        mobile="9876500002",
        region="Maharashtra",
        daily_request_cap=50,
    ),
]


async def seed_reference_data(session: AsyncSession, *, test_bds: bool = True) -> None:
    for code, group, name, order, re_ref, needs_file, sla, _budget, active in SERVICES:
        await session.execute(
            insert(ServiceCatalog)
            .values(
                service_code=code,
                service_group=group,
                display_name=name,
                menu_order=order,
                requires_re_referral=re_ref,
                re_partner_code="IFPL" if re_ref else None,
                requires_input_file=needs_file,
                sla_business_hours=sla,
                is_active=active,
            )
            .on_conflict_do_nothing()
        )
    for code, phrases in SYNONYMS.items():
        for phrase, lang in phrases:
            await session.execute(
                insert(ServiceSynonym)
                .values(service_code=code, phrase=phrase, language=lang)
                .on_conflict_do_nothing()
            )
    for row in _pricing_rows():
        await session.execute(insert(AiModelPricing).values(**row).on_conflict_do_nothing())
    if test_bds:
        for bd in TEST_BDS:
            await session.execute(insert(BdPartner).values(**bd).on_conflict_do_nothing())


async def _main(test_bds: bool) -> None:
    async with session_scope(get_session_factory()) as session:
        await seed_reference_data(session, test_bds=test_bds)
    await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-test-bds", action="store_true")
    args = parser.parse_args()
    asyncio.run(_main(not args.no_test_bds))
    print("seed complete")


if __name__ == "__main__":
    main()

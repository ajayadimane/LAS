"""Builds the Tikamgarh AS-930 harvester CAM snapshot fixture for milestone 4.

No real Tikamgarh DPR source file exists anywhere in this repo (see
PROGRESS.md milestone 2) -- the brief's "[PATH TO TIKAMGARH DPR]" was never
filled in. This script therefore cannot run the real pipeline against a
real document; it runs the real orchestrator.analyze() against a FakeLLM
whose .extract()/.generate() return the same golden figures used in
tests/test_dpr_cam_financial_engine.py and tests/test_dpr_cam_engine_bridge.py
(independently derived from cam-design-reference/LAS Customer CAM Report
Template.pdf -- see PROGRESS.md for the derivation), with
cam_engine_enabled=True, so the resulting snapshot is what the real
pipeline would produce if fed a document stating these exact figures.

This is a clearly labeled simulation, not a real end-to-end document run.
Usage: python scripts/build_tikamgarh_fixture.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.cam_snapshot import BorrowerProfile, ProjectProfile
from app.services.dpr_agentic.cam_verification import verify_snapshot
from app.services.dpr_agentic.financial_schema import (
    CrewRole,
    ExtractedValue,
    HarvesterAssumptions,
    ProjectFinancials,
    YearFinancials,
)
from app.services.dpr_agentic.orchestrator import PROMPTS, analyze
from app.services.dpr_agentic.schemas import AgentOutput, AnalysisInput, Finding

FIXTURE_PATH = (
    Path(__file__).parent.parent / "tests" / "fixtures" / "tikamgarh_cam_snapshot.json"
)

TIKAMGARH_FINANCIALS = ProjectFinancials(
    term_loan_amount=ExtractedValue(value=22.50, page=3, status="extracted"),
    loan_tenor_years=ExtractedValue(value=7, page=3, status="extracted"),
    loan_moratorium_years=ExtractedValue(value=0, page=3, status="extracted"),
    standard_interest_rate_pct=9.00,
    cost_of_project_components=[],
    cost_of_project_stated_total=30.00,
    means_of_finance_stated_total=30.00,
    fund_flow_sources_total=ExtractedValue(value=102.65, page=12, status="extracted"),
    fund_flow_applications_total=ExtractedValue(
        value=62.36, page=12, status="extracted"
    ),
    years=[
        YearFinancials(
            year=1,
            net_profit=3.77,
            profit_before_tax=4.27,
            tax_paid=0.50,
            depreciation=4.50,
            interest_on_term_loan=2.03,
            principal_repayment=3.21,
            interest_repayment=2.03,
            stated_dscr=1.58,
        )
    ],
    harvester_assumptions=HarvesterAssumptions(
        acres_per_year=ExtractedValue(value=1200, page=4, status="extracted"),
        acres_per_day=ExtractedValue(value=10, page=19, status="extracted"),
        operating_days_per_season=ExtractedValue(value=30, page=19, status="extracted"),
        seasons_per_year=ExtractedValue(value=2, page=4, status="extracted"),
        price_per_acre=ExtractedValue(value=1800, page=19, status="extracted"),
        running_cost_per_acre_stated=ExtractedValue(
            value=750, page=8, status="extracted"
        ),
        running_cost_per_acre_corrected=ExtractedValue(
            value=900, page=19, status="extracted"
        ),
        fuel_litres_per_acre=ExtractedValue(value=8.0, page=19, status="extracted"),
        fuel_price_per_litre=ExtractedValue(value=95, page=19, status="extracted"),
        fuel_cost_stated=ExtractedValue(value=7.20, page=8, status="extracted"),
        crew_structure=[
            CrewRole(
                role="Operator",
                count=1,
                annual_wage_per_person=2.20,
                page=20,
                status="extracted",
            ),
            CrewRole(
                role="Helper",
                count=1,
                annual_wage_per_person=1.10,
                page=20,
                status="extracted",
            ),
        ],
        crew_wage_total_p_and_l_stated=ExtractedValue(
            value=2.40, page=8, status="extracted"
        ),
    ),
)

# Narrative findings a real Risk/Verifier/Correction/Report agent chain
# would plausibly restate about the same underlying defects -- exercises
# finding_consolidation.consolidate() on realistic cross-agent duplication,
# not just the calculated ones.
CAPACITY_FINDING = Finding(
    category="assumption",
    severity="high",
    title="Claimed annual acreage exceeds plausible machine throughput",
    explanation=(
        "At 10 acres/day over 30 operating days across 2 seasons, one "
        "harvester can plausibly cover 300-600 acres/year; the DPR plans "
        "1,200."
    ),
    evidence_page=4,
    suggested_action=(
        "Revise the acreage plan or provide evidence of additional capacity."
    ),
    provenance="ai_interpreted",
)
MISSING_QUOTATION_FINDING = Finding(
    category="missing_document",
    severity="high",
    title="Dealer quotation missing for the harvester",
    explanation=(
        "No vendor quotation or pro-forma invoice is attached for the "
        "stated 30.00 lakh cost."
    ),
    evidence_page=3,
    suggested_action=(
        "Attach an authorised dealer's quotation matching the stated "
        "project cost."
    ),
    provenance="extracted",
)


class TikamgarhFakeLLM:
    """Returns the golden figures for extraction, and a mix of agent
    outputs including deliberately duplicated narrative findings (as the
    real risk/verifier/correction/report chain would produce) to exercise
    consolidation on a realistic case, not just the calculated findings."""

    async def extract(self, prompt: str, context: dict) -> ProjectFinancials:
        return TIKAMGARH_FINANCIALS

    async def generate(self, prompt: str, context: dict) -> AgentOutput:
        name = next(k for k, v in PROMPTS.items() if v == prompt)
        if name == "document":
            return AgentOutput(
                summary="Document normalized.", findings=[MISSING_QUOTATION_FINDING]
            )
        if name in ("sector", "risk", "verifier", "correction", "report"):
            # Each of these agents sees the capacity issue and restates it
            # in its own words -- same dedup key (category + title + page).
            return AgentOutput(summary=f"{name} reviewed.", findings=[CAPACITY_FINDING])
        return AgentOutput(summary=f"{name} reviewed.")


async def _run() -> None:
    payload = AnalysisInput(
        service_request_id="SR-TIKAMGARH-GOLDEN",
        wallet_document_id="W-TIKAMGARH-GOLDEN",
        document_sha256=hashlib.sha256(b"tikamgarh-golden-fixture").hexdigest(),
        pages=[
            {
                "page": 1,
                "text": (
                    "Simulated Tikamgarh AS-930 harvester DPR (see script "
                    "docstring)."
                ),
            }
        ],
    )
    result = await analyze(payload, TikamgarhFakeLLM(), cam_engine_enabled=True)

    snapshot = build_snapshot_from_analysis(
        result,
        report_id="RPT-TIKAMGARH-GOLDEN",
        report_version=1,
        analysis_run_id="RUN-TIKAMGARH-GOLDEN",
        borrower_profile=BorrowerProfile(),  # real name unknown -- never invented
        project_profile=ProjectProfile(
            project_name="Custom Hiring Centre -- AS-930 combine harvester",
            business_sector="Agri-MSME",
            business_subsector="Harvester Custom Hiring Centre",
            project_location="Nachanwara, Tikamgarh",
            project_purpose=(
                "Combine harvester custom hiring service for soybean and wheat"
            ),
            machinery_equipment=["AS-930 combine harvester"],
        ),
    )

    verification = verify_snapshot(snapshot)

    print(f"Findings: {len(snapshot.findings)} (consolidated)")
    for f in snapshot.findings:
        print(f"  - [{f.priority.value}] {f.title} (x{f.merged_duplicate_count})")
    print(f"Year-1 DSCR (pat_dep_interest): {snapshot.financial_ratios.average_dscr}")
    print(f"Breakeven (accounting): {snapshot.financial_ratios.breakeven_accounting}")
    print(f"Scenarios: {[s.scenario_id for s in snapshot.scenario_analysis]}")
    print()
    print(f"Verification: is_publishable={verification.is_publishable}")
    for issue in verification.issues:
        print(f"  [{issue.severity}] {issue.code}: {issue.message}")

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(
        json.dumps(snapshot.model_dump(mode="json"), indent=2, default=str)
    )
    print(f"\nFixture written to {FIXTURE_PATH}")


if __name__ == "__main__":
    asyncio.run(_run())

"""The seven Section 12 CAM annexures (A-G), each built from the same
CAMSnapshot + the stored AnalysisResult + the job registry row -- never a
separate render or a new calculation engine (see docs/CAM_REPORT_SPEC.md,
saved verbatim from the user's spec).

Every section below lists exactly what it fills from what already exists
today, and an explicit `missing` list for whatever the current pipeline
does not yet produce. Per the spec: "Do not invent content merely to make
annexures appear complete. An annexure may explicitly state that data is
unavailable or analysis is incomplete." Nothing here fabricates a figure
that was not already computed and traced elsewhere in this pipeline.
"""

from __future__ import annotations

from typing import Any

from .cam_snapshot import CalculationTrace, CAMSnapshot
from .schemas import AnalysisResult


def _trace(t: CalculationTrace | None) -> dict | None:
    return t.model_dump(mode="json") if t is not None else None


def _money(m) -> dict | None:
    return m.model_dump(mode="json") if m is not None else None


def build_annexure_a(snapshot: CAMSnapshot) -> dict:
    """Financial Statements: projected P&L, balance sheet, cash flow,
    working capital, ratio analysis.

    Fed by: `financial_projections` (P&L-shaped, but this pipeline never
    populates it -- no agent or calculation engine produces a
    FinancialProjection today) and `financial_ratios` (DSCR family only).
    Not fed at all: balance sheet, cash flow statement, working capital --
    none of these has a model in CAMSnapshot yet, let alone a computation
    behind it.
    """
    ratios = snapshot.financial_ratios
    return {
        "profit_and_loss": [
            fp.model_dump(mode="json") for fp in snapshot.financial_projections
        ],
        "balance_sheet": None,
        "cash_flow": None,
        "working_capital": None,
        "ratio_analysis": {
            "dscr_definition_used": ratios.dscr_definition_used.value,
            "average_dscr": _trace(ratios.average_dscr),
            "dscr_pat_dep_only": _trace(ratios.dscr_pat_dep_only),
            "breakeven_accounting": _trace(ratios.breakeven_accounting),
            "breakeven_operating_cashflow": _trace(ratios.breakeven_operating_cashflow),
            "breakeven_debt_service_threshold": _trace(
                ratios.breakeven_debt_service_threshold
            ),
        },
        "missing": [
            "profit_and_loss: modeled (FinancialProjection) but never "
            "populated by this pipeline -- no agent or engine produces one",
            "balance_sheet: not modeled anywhere in the snapshot",
            "cash_flow: not modeled anywhere in the snapshot",
            "working_capital: not modeled anywhere in the snapshot",
            "ratio_analysis beyond DSCR/break-even (e.g. current ratio, "
            "debt-equity ratio): not modeled",
        ],
    }


def build_annexure_b(snapshot: CAMSnapshot) -> dict:
    """Repayment: amortization, principal/interest schedule, moratorium
    treatment, year-wise DSCR, repayment assumptions.

    Fed by: `repayment_schedule` (principal/interest/DSCR per year -- but
    the engine only ever computes year 1, never a full tenure) and
    `scenario_analysis[*].dscr_by_year` for the scenario-level DSCR path.
    Not fed: moratorium treatment and repayment assumptions have no field
    anywhere in the snapshot.
    """
    return {
        "repayment_schedule": [
            rp.model_dump(mode="json") for rp in snapshot.repayment_schedule
        ],
        "year_wise_dscr_by_scenario": {
            s.scenario_id: {str(y): _trace(t) for y, t in s.dscr_by_year.items()}
            for s in snapshot.scenario_analysis
        },
        "moratorium_treatment": None,
        "repayment_assumptions": None,
        "missing": [
            "full multi-year amortization/principal schedule: the engine "
            "only ever computes year 1, never the full loan tenure",
            "moratorium_treatment: not modeled anywhere in the snapshot",
            "repayment_assumptions (as explicit narrative text): not "
            "modeled anywhere in the snapshot",
        ],
    }


def build_annexure_c(snapshot: CAMSnapshot) -> dict:
    """Sensitivity Analysis: scenario assumptions, revenue/cost/cash-flow/
    debt-service impact.

    Fed by: `scenario_analysis` (label, description, is_hypothetical,
    debt-service impact via DSCR-by-year and break-even figures). Not fed:
    ScenarioAnalysis has no revenue_impact or cost_impact field at all --
    only the debt-service side of a scenario is modeled today.
    """
    return {
        "scenarios": [s.model_dump(mode="json") for s in snapshot.scenario_analysis],
        "missing": [
            "revenue_impact per scenario: not modeled in ScenarioAnalysis",
            "cost_impact per scenario: not modeled in ScenarioAnalysis",
            "cash_flow_impact per scenario: not modeled beyond the "
            "break-even operating-cashflow figure already included above",
        ],
    }


def build_annexure_d(snapshot: CAMSnapshot) -> dict:
    """Project Feasibility: sector assumptions, installed capacity,
    utilization, raw material requirements, production assumptions, market
    evidence.

    Fed by: `operational_assumptions` (sector, a free-form assumptions
    dict, classification) -- but this pipeline never populates it; every
    stored report has it at its empty default. The dict itself is
    structurally ready to hold any of the needed sub-items once an agent
    starts filling it in, but none does today.
    """
    oa = snapshot.operational_assumptions
    return {
        "sector": oa.sector,
        "assumptions": dict(oa.assumptions),
        "classification": oa.classification,
        "missing": [
            "installed_capacity: not populated (operational_assumptions "
            "is always empty in this pipeline today)",
            "utilization: not populated",
            "raw_material_requirements: not populated",
            "production_assumptions: not populated",
            "market_evidence: not populated",
        ],
    }


def build_annexure_e(snapshot: CAMSnapshot) -> dict:
    """Findings Register: finding ID, dedup key + merged duplicate count,
    severity + priority rule, defect/business-risk class, verification
    status, financial impact, source evidence, proposed correction,
    resolution status.

    Fed by: `findings` (VerifiedFinding) for everything except a
    structured financial-impact figure (only ever embedded as text inside
    `priority_rule`, e.g. "... discrepancy is 7.2% ...") and full
    `evidence` objects (quote/page/class) -- `_map_finding()` in
    cam_adapter.py always leaves `evidence=[]`, populating only
    `source_pages`. `proposed_corrections` (CorrectionProposal) is never
    generated by any code path, and `resolution_status` has no write path
    either -- every finding stays "open" forever.
    """
    return {
        "findings": [f.model_dump(mode="json") for f in snapshot.findings],
        "proposed_corrections": [
            c.model_dump(mode="json") for c in snapshot.proposed_corrections
        ],
        "missing": [
            "evidence: VerifiedFinding.evidence is always empty -- only "
            "source_pages (page numbers, no quotes) is populated",
            "financial_impact as a structured value: only ever embedded "
            "as text inside priority_rule (e.g. a discrepancy percentage), "
            "never a dedicated field",
            "proposed_corrections: never generated by any code path",
            "resolution_status transitions: no review workflow writes "
            "this field -- every finding stays 'open'",
        ],
    }


def build_annexure_f(snapshot: CAMSnapshot) -> dict:
    """SBI and Scheme References: lending product, relevant public policy,
    document requirements, scheme eligibility (subsidy/AIF status), source
    URLs, effective dates, retrieval dates, unverified policy areas.

    Fed by: `policy_assessment` and `readiness_assessment.subsidy_benefits`
    -- both exist as schema-ready models, but neither is ever populated:
    the policy agent explicitly refuses to invent policy statements
    without a real, attached policy corpus (see PROGRESS.md), and no
    subsidy/AIF-eligibility agent exists at all yet. "Lending product"
    itself has no field anywhere in the snapshot.
    """
    pa = snapshot.policy_assessment
    return {
        "lending_product": None,
        "policy_statements": [s.model_dump(mode="json") for s in pa.statements],
        "unverified_policy_areas": list(pa.unverified_policy_areas),
        "subsidy_benefits": [
            b.model_dump(mode="json")
            for b in snapshot.readiness_assessment.subsidy_benefits
        ],
        "missing": [
            "lending_product: not modeled anywhere in the snapshot",
            "policy_statements: the policy agent has no policy corpus "
            "attached, so this is always empty -- not a bug, a documented "
            "refusal to invent unsourced policy claims",
            "subsidy_benefits: no subsidy/AIF-eligibility agent exists "
            "yet, so this is always empty",
        ],
    }


def build_annexure_g(
    snapshot: CAMSnapshot, run: dict | None, result: AnalysisResult
) -> dict:
    """Audit Trail: analysis ID, report version, source document checksum,
    agent execution details, Gemini model + pinned version, calculation
    version, configuration values used (DSCR definition, priority
    thresholds, readiness mode), reviewer actions, report generation
    timestamp.

    Fed by: the snapshot's own identity fields, the job-registry `run` row
    (status/attempts/timestamps), and `result.gemini_model` (captured at
    analysis time as of this round -- see schemas.py; None for any report
    stored before this field existed). Not fed: per-role execution timing
    (only which roles ran is known, from `agent_outputs`' keys, not how
    long each took), the actual priority-threshold values finding_rules.py
    used (hardcoded constants in code, never recorded per-run), and
    reviewer actions (no review workflow exists).
    """
    return {
        "analysis_id": snapshot.analysis_run_id,
        "report_id": snapshot.report_id,
        "report_version": snapshot.report_version,
        "source_document_checksum": result.document_sha256,
        "agent_roles_executed": sorted(result.agent_outputs.keys()),
        "gemini_model": result.gemini_model,
        "schema_version": snapshot.schema_version,
        "dscr_definition_used": snapshot.financial_ratios.dscr_definition_used.value,
        "readiness_score_mode": snapshot.readiness_assessment.score_mode,
        "readiness_status": snapshot.readiness_assessment.status.value,
        "run_status": run.get("status") if run else None,
        "run_attempts": run.get("attempts") if run else None,
        "report_generated_at": snapshot.generated_at.isoformat(),
        "reviewer_actions": [],
        "missing": [
            "per-role execution timing/duration: only which roles ran is "
            "known (agent_roles_executed above), not how long each took",
            "priority thresholds used: hardcoded constants in "
            "finding_rules.py, never recorded as a value per run",
            "reviewer_actions: no review workflow exists yet -- always empty",
            *(
                [
                    "gemini_model: not captured for reports stored before "
                    "this field existed"
                ]
                if result.gemini_model is None
                else []
            ),
        ],
    }


def build_annexures(
    snapshot: CAMSnapshot, result: AnalysisResult, run: dict | None = None
) -> dict[str, Any]:
    """All seven Section 12 annexures for one report, keyed by letter."""
    return {
        "A_financial_statements": build_annexure_a(snapshot),
        "B_repayment": build_annexure_b(snapshot),
        "C_sensitivity_analysis": build_annexure_c(snapshot),
        "D_project_feasibility": build_annexure_d(snapshot),
        "E_findings_register": build_annexure_e(snapshot),
        "F_sbi_and_scheme_references": build_annexure_f(snapshot),
        "G_audit_trail": build_annexure_g(snapshot, run, result),
    }

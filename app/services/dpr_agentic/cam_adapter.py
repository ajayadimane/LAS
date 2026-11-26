"""AnalysisResult (schemas.py) -> CAMSnapshot (cam_snapshot.py) adapter.

`result.findings` is already deduplicated and rule-classified by the time it
reaches here (finding_consolidation.consolidate(), run inside
orchestrator.analyze()/parallel_graph.analyze_parallel() -- see Finding's
dedup_key/merged_duplicate_count/finding_class/priority/priority_rule
fields). This module only reshapes an already-consolidated Finding into a
VerifiedFinding; it does not dedupe or classify anything itself. A fallback
to finding_rules' own classifiers is kept for a Finding persisted before
those fields existed (report_store.py keeps old rows as-is -- see
PROGRESS.md Section 16 gap on report versioning).

Still NOT done here (milestone 2's remaining scope, Section 9):
  - financial_projections (a full multi-year P&L/balance-sheet/cash-flow
    model -- disclosed gap, needs extraction fields not yet gathered)

project_cost / means_of_finance / repayment_schedule / financial_ratios /
scenario_analysis are populated from cam_engine_bridge.CamEngineOutputs
when Settings.cam_engine_enabled was True for this run (see
_engine_derived_fields below); they stay empty/default otherwise, exactly
as before that wiring existed.

Borrower/project identity is not in AnalysisResult at all (the agentic
pipeline only ever sees document text), so it must be supplied by the caller
from Core LAS' own records (enrolment/person data), not invented here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from .cam_snapshot import (
    BorrowerProfile,
    CAMSnapshot,
    DocumentReference,
    DscrDefinition,
    FinanceSourceClass,
    FinancialProjection,
    FinancialRatios,
    FindingClass,
    FindingPriority,
    FindingSeverity,
    MeansOfFinance,
    MeansOfFinanceLine,
    Money,
    OperationalAssumptions,
    PolicyAssessment,
    ProjectCost,
    ProjectCostLine,
    ProjectProfile,
    Provenance,
    ReadinessAssessment,
    ReadinessStatus,
    RepaymentPeriod,
    ScenarioAnalysis,
    ValueClassification,
    VerifiedFinding,
)
from .finding_rules import (
    classify_finding_class,
    compute_dedup_key,
    priority_for_narrative,
)
from .schemas import AnalysisResult, Finding


def _classify_funding_line(label: str, source_class: str):
    """Don't misrepresent conditional/back-ended support as cash at inception."""
    lowered = label.casefold()
    if "reimburse" in lowered:
        return FinanceSourceClass.REIMBURSEMENT_SCHEME, False
    if "back end" in lowered or "back-end" in lowered or "backended" in lowered:
        return FinanceSourceClass.BACK_ENDED_SUBSIDY, False
    if "subsid" in lowered or "grant" in lowered:
        return FinanceSourceClass.CONDITIONAL_SUBSIDY, False
    if source_class == "loan":
        return FinanceSourceClass.UPFRONT_LOAN, True
    if source_class == "promoter":
        return FinanceSourceClass.UPFRONT_OWN_FUNDED, True
    # Unknown 'other' sources remain unclassified instead of adding
    # them to the upfront funding figure without an authenticated source.
    return FinanceSourceClass.FUTURE_CASH_INFLOW, False


def _means_of_finance_from_engine(engine) -> MeansOfFinance:
    components = []
    for c in engine.means_of_finance_components:
        cls, upfront = _classify_funding_line(c.label, c.source_class)
        components.append(
            MeansOfFinanceLine(
                label=c.label,
                amount=Money(amount=Decimal(str(c.amount)), unit="lakh"),
                source_class=cls,
                counted_in_upfront_funding=upfront,
            )
        )
    total = (
        Money(amount=Decimal(engine.means_of_finance_stated_total), unit="lakh")
        if engine.means_of_finance_stated_total is not None
        else None
    )
    upfront_lines = [
        c.amount.amount for c in components if c.counted_in_upfront_funding and c.amount
    ]
    upfront_total = (
        Money(amount=sum(upfront_lines, Decimal(0)), unit="lakh")
        if upfront_lines
        else None
    )
    conditional = any(not c.counted_in_upfront_funding for c in components)
    return MeansOfFinance(
        components=components,
        dpr_reported_total=total,
        upfront_funding_total=upfront_total,
        funding_gap=None if conditional else engine.funding_balances_cost,
        requested_loan=(
            Money(amount=Decimal(engine.requested_loan), unit="lakh")
            if engine.requested_loan is not None
            else None
        ),
        promoter_contribution=(
            Money(amount=Decimal(engine.promoter_contribution), unit="lakh")
            if engine.promoter_contribution is not None
            else None
        ),
        proposed_tenure_months=engine.proposed_tenure_months,
    )


def _project_cost_from_engine(engine) -> ProjectCost:
    components = [
        ProjectCostLine(
            label=c.label,
            dpr_reported=Money(amount=Decimal(c.amount), unit="lakh"),
            provenance=Provenance.EXTRACTED,
        )
        for c in engine.project_cost_components
    ]
    return ProjectCost(
        components=components,
        dpr_reported_total=(
            Money(amount=Decimal(engine.project_cost_stated_total), unit="lakh")
            if engine.project_cost_stated_total is not None
            else None
        ),
        recomputed_total=None,
        reconciliation=engine.cost_reconciliation,
    )


def _engine_derived_fields(
    result: AnalysisResult,
) -> tuple[
    FinancialRatios,
    FinancialProjection,
    list[RepaymentPeriod],
    list[ScenarioAnalysis],
    ProjectCost,
    MeansOfFinance,
]:
    """Folds cam_engine_bridge.CamEngineOutputs (set only when
    Settings.cam_engine_enabled was True for this run -- milestone 2b/4)
    into the snapshot's financial_ratios/repayment_schedule/
    scenario_analysis/project_cost/means_of_finance. Returns empty/default
    values when the engine didn't run for this result, exactly as before
    this wiring existed."""
    default_ratios = FinancialRatios(
        dscr_definition_used=DscrDefinition.PAT_DEP_INTEREST
    )
    if not result.cam_engine_outputs:
        return default_ratios, [], [], ProjectCost(), MeansOfFinance()

    from .cam_engine_bridge import CamEngineOutputs

    engine = CamEngineOutputs.model_validate(result.cam_engine_outputs)
    ratios = FinancialRatios(
        dscr_definition_used=DscrDefinition.PAT_DEP_INTEREST,
        # The engine's DSCR is the first repaying year's, not an average.
        first_year_dscr=engine.dscr_pat_dep_interest,
        dscr_pat_dep_only=engine.dscr_pat_dep,
        breakeven_accounting=engine.breakeven_accounting,
        breakeven_operating_cashflow=engine.breakeven_operating_cashflow,
        breakeven_debt_service_threshold=engine.breakeven_debt_service_threshold,
    )

    repayment_schedule: list[RepaymentPeriod] = []
    if engine.year1_principal_due is not None and engine.year1_interest_due is not None:
        principal = Decimal(engine.year1_principal_due)
        interest = Decimal(engine.year1_interest_due)
        repayment_schedule.append(
            RepaymentPeriod(
                year=engine.debt_service_year or 1,
                principal_due=Money(amount=principal, unit="lakh"),
                interest_due=Money(amount=interest, unit="lakh"),
                total_debt_service=Money(amount=principal + interest, unit="lakh"),
                dscr=engine.dscr_pat_dep_interest,
            )
        )

    scenario_analysis = [
        ScenarioAnalysis(
            scenario_id=s.scenario_id,
            label=s.label,
            is_hypothetical=s.is_hypothetical,
            description=f"Source class: {s.source_class}.",
            dscr_by_year={1: s.dscr},
            **(
                {
                    "breakeven_accounting": be.breakeven_accounting,
                    "breakeven_operating_cashflow": be.breakeven_operating_cashflow,
                    "breakeven_debt_service_threshold": (
                        be.breakeven_debt_service_threshold
                    ),
                }
                if (be := engine.breakeven_by_scenario.get(s.scenario_id))
                else {}
            ),
        )
        for s in engine.scenarios
    ]
    project_cost = _project_cost_from_engine(engine)
    means_of_finance = _means_of_finance_from_engine(engine)
    return ratios, repayment_schedule, scenario_analysis, project_cost, means_of_finance


def _value_classification(f: Finding) -> ValueClassification:
    return {
        "calculated": ValueClassification.RECOMPUTED,
        "extracted": ValueClassification.VERIFIED_SOURCE_FACT,
        "assumption": ValueClassification.UNVERIFIED_ASSUMPTION,
        "ai_interpreted": ValueClassification.UNVERIFIED_ASSUMPTION,
        "unverified": ValueClassification.UNVERIFIED_ASSUMPTION,
    }.get(f.provenance, ValueClassification.UNVERIFIED_ASSUMPTION)


def _severity(f: Finding) -> FindingSeverity:
    return FindingSeverity(f.severity)


def _map_finding(f: Finding, index: int) -> VerifiedFinding:
    finding_class = f.finding_class or classify_finding_class(f)
    if f.priority:
        priority, priority_rule = f.priority, f.priority_rule or ""
    else:
        priority, priority_rule = priority_for_narrative(f)
    return VerifiedFinding(
        finding_id=f"F{index:04d}",
        dedup_key=f.dedup_key or compute_dedup_key(f),
        merged_duplicate_count=f.merged_duplicate_count,
        title=f.title,
        category=f.category,
        finding_class=FindingClass(finding_class),
        severity=_severity(f),
        priority=FindingPriority(priority),
        priority_rule=priority_rule,
        verification=f.verification,
        explanation=f.explanation,
        dpr_reported_value=f.dpr_stated_value,
        recomputed_value=f.recomputed_display_value,
        why_it_matters=f.why_it_matters,
        should_be=f.should_be,
        value_classification=_value_classification(f),
        source_pages=[f.evidence_page] if f.evidence_page is not None else [],
        evidence=[],
        suggested_action=f.suggested_action,
        responsible_person=None,
        resolution_status="open",
        requires_borrower_confirmation=f.verification == "needs_clarification",
    )


def _source_profiles(
    result: AnalysisResult,
    borrower: BorrowerProfile | None,
    project: ProjectProfile | None,
):
    """Populate DPR-labeled facts only after literal page/quote verification.

    Wallet-provided borrower identity (if supplied) always takes precedence.
    """
    b = borrower.model_copy(deep=True) if borrower else BorrowerProfile()
    p = project.model_copy(deep=True) if project else ProjectProfile()
    document = result.agent_outputs.get("document")
    verified = {}
    if document:
        grouped = {}
        for fact in document.document_facts:
            if fact.verified:
                grouped.setdefault(fact.field, set()).add(fact.value.strip())
        # Contradictory names or project descriptions remain unverified;
        # a "last writer wins" dictionary would silently choose one.
        verified = {
            field: next(iter(values))
            for field, values in grouped.items()
            if len({v.casefold() for v in values}) == 1
        }
    if not b.borrower_name:
        b.borrower_name = verified.get("borrower_name")
    if not p.lending_bank:
        # The DPR's own bank first, then the dashboard's BankName, then the
        # configured default -- the same rule the policy search used.
        from .grounding import resolve_lending_bank

        p.lending_bank, p.lending_bank_source = resolve_lending_bank(
            document.document_facts if document else [], result.service_details
        )
    for key in (
        "project_name",
        "business_sector",
        "business_subsector",
        "project_location",
        "project_purpose",
        "scheme_claimed",
    ):
        if not getattr(p, key):
            setattr(p, key, verified.get(key))
    return b, p


def _basic_financials(result: AnalysisResult):
    """Reuses the one structured extraction call for all project sectors."""
    from .financial_schema import ProjectFinancials

    raw = result.extracted_financials
    if not raw:
        return [], None, None
    extracted = ProjectFinancials.model_validate(raw)
    projections = [
        FinancialProjection(
            year=year.year,
            revenue=Money(amount=Decimal(str(year.revenue)), unit="lakh")
            if year.revenue is not None
            else None,
            operating_expenses=Money(
                amount=Decimal(str(year.operating_expenses)), unit="lakh"
            )
            if year.operating_expenses is not None
            else None,
            net_profit=Money(amount=Decimal(str(year.net_profit)), unit="lakh")
            if year.net_profit is not None
            else None,
            depreciation=Money(amount=Decimal(str(year.depreciation)), unit="lakh")
            if year.depreciation is not None
            else None,
            interest_on_term_loan=Money(
                amount=Decimal(str(year.interest_on_term_loan)), unit="lakh"
            )
            if year.interest_on_term_loan is not None
            else None,
            provenance=Provenance.EXTRACTED,
        )
        for year in extracted.years
    ]
    project_cost = (
        Money(amount=Decimal(str(extracted.cost_of_project_stated_total)), unit="lakh")
        if extracted.cost_of_project_stated_total is not None
        else None
    )
    financing = extracted.means_of_finance_components
    return projections, project_cost, financing


def _policy_assessment(result: AnalysisResult) -> PolicyAssessment:
    """Annexure F from grounded policy statements (grounding.py). Only
    statements with an official source become policy statements -- still
    "confirm with the bank", since a web page is not the bank's sanction
    terms; the rest are listed as unverified areas."""
    from .cam_snapshot import PolicyStatement, PolicyStatementStatus

    policy = [s for s in result.external_evidence if s.kind == "policy"]
    statements = [
        PolicyStatement(
            text=s.text,
            status=PolicyStatementStatus.BANK_CONFIRMATION_PENDING,
            source_url=(
                official := next(src for src in s.sources if src.tier == "official")
            ).url,
            source_domain=official.domain or None,
            retrieval_date=s.retrieved_at,
        )
        for s in policy
        if s.official
    ]
    unverified = [
        "From a non-official source ("
        + ", ".join(sorted({x.domain for x in s.sources}))
        + f"): {s.text}"
        for s in policy
        if not s.official
    ][:6]
    if not statements:
        unverified.insert(
            0,
            "The bank's product eligibility and lending terms are "
            "not verified against a dated official source.",
        )
    for check in result.scheme_checks:
        # The scheme table's arithmetic (schemes.py): what was checked and
        # the most the scheme can give -- still for the bank to confirm.
        most = (
            f"; the most it can give for this project is "
            f"{check['max_subsidy_lakh']:g} lakh ({check['how']})"
            if check.get("max_subsidy_lakh") is not None
            else ""
        )
        unverified.append(
            f"Checked against the {check['scheme']} rules in the LAS scheme "
            f"table{most}. Basis: {check['basis']} ({check['status']}, "
            "provisional)."
        )
    unverified.append(
        "Any subsidy or credit guarantee claimed in the DPR requires "
        "confirmation from the scheme authority or the bank."
    )
    return PolicyAssessment(statements=statements, unverified_policy_areas=unverified)


def _operational_assumptions(result: AnalysisResult) -> OperationalAssumptions:
    """Annexure D from the extracted unit economics (unit_economics.py):
    printed figures with their pages, plus what code derived from them."""
    if not result.extracted_financials:
        return OperationalAssumptions()
    from .financial_schema import ProjectFinancials
    from .unit_economics import operational_assumptions

    fin = ProjectFinancials.model_validate(result.extracted_financials)
    assumptions = operational_assumptions(fin)
    if bm := fin.benchmark_used:
        # What the DPR was checked against (benchmarks.py), as built there.
        assumptions.update(bm.get("annexure") or {})
    # Published benchmarks (grounding.py research), with their sources.
    benchmarks = [s for s in result.external_evidence if s.kind == "research"][:8]
    for i, s in enumerate(benchmarks, start=1):
        domains = ", ".join(sorted({src.domain for src in s.sources}))
        assumptions[f"Market evidence {i}"] = f"{s.text} ({domains})"
    if not assumptions:
        return OperationalAssumptions()
    return OperationalAssumptions(
        sector=next(
            (
                name
                for name, model in (
                    ("harvester", fin.harvester_assumptions),
                    ("polyhouse", fin.polyhouse_assumptions),
                    ("dairy", fin.dairy_assumptions),
                    ("cold_storage", fin.cold_storage_assumptions),
                )
                if model is not None
            ),
            None,
        ),
        assumptions=assumptions,
        classification="recomputed"
        if any(k.endswith("(computed)") for k in assumptions)
        else "verified_source_fact",
    )


def document_verification_notice():
    """The notice for this report, from config: provider, service and fee."""
    from app.core.config import get_settings

    from .cam_snapshot import DocumentVerificationNotice

    settings = get_settings()
    by_code = {s.code: s for s in settings.services}
    service = by_code.get(settings.document_verification_service_code)
    quotation = by_code.get(settings.quotation_verification_service_code)
    return DocumentVerificationNotice(
        provider=settings.document_verification_provider,
        service_name=service.name if service else "Value Addition",
        service_fee_inr=Decimal(service.fee) if service else None,
        quotation_service_name=quotation.name if quotation else None,
        quotation_service_fee_inr=Decimal(quotation.fee) if quotation else None,
    )


def build_snapshot_from_analysis(
    result: AnalysisResult,
    *,
    report_id: str,
    report_version: int,
    analysis_run_id: str,
    borrower_profile: BorrowerProfile | None = None,
    project_profile: ProjectProfile | None = None,
    report_reference: str | None = None,
) -> CAMSnapshot:
    """Best-effort milestone-1 mapping. `borrower_profile`/`project_profile`
    must come from the caller's own Core LAS/Wallet-sourced data (e.g.
    report_service.py's enrolment lookup) -- this adapter never invents them.
    """
    findings = [_map_finding(f, i) for i, f in enumerate(result.findings, start=1)]
    critical_count = sum(1 for f in findings if f.severity == FindingSeverity.CRITICAL)
    (
        financial_ratios,
        repayment_schedule,
        scenario_analysis,
        project_cost,
        means_of_finance,
    ) = _engine_derived_fields(result)

    borrower_profile, project_profile = _source_profiles(
        result, borrower_profile, project_profile
    )
    projections, basic_project_cost, basic_funding = _basic_financials(result)
    if project_cost.dpr_reported_total is None and basic_project_cost is not None:
        project_cost.dpr_reported_total = basic_project_cost
    if not project_cost.components and result.extracted_financials:
        from .financial_schema import ProjectFinancials

        raw = ProjectFinancials.model_validate(result.extracted_financials)
        project_cost.components = [
            ProjectCostLine(
                label=c.label,
                dpr_reported=Money(amount=Decimal(str(c.amount)), unit="lakh"),
                provenance=Provenance.EXTRACTED,
            )
            for c in raw.cost_of_project_components
        ]
    if basic_funding and not means_of_finance.components:
        from .cam_engine_bridge import _LOAN_LABEL_KEYWORDS, _PROMOTER_LABEL_KEYWORDS

        for line in basic_funding:
            label = line.label.lower()
            is_loan = any(t in label for t in _LOAN_LABEL_KEYWORDS)
            is_promoter = any(t in label for t in _PROMOTER_LABEL_KEYWORDS)
            source_class, upfront = _classify_funding_line(
                line.label,
                "loan" if is_loan else "promoter" if is_promoter else "other",
            )
            means_of_finance.components.append(
                MeansOfFinanceLine(
                    label=line.label,
                    amount=Money(amount=Decimal(str(line.amount)), unit="lakh"),
                    source_class=source_class,
                    counted_in_upfront_funding=upfront,
                )
            )
            if is_loan and means_of_finance.requested_loan is None:
                means_of_finance.requested_loan = Money(
                    amount=Decimal(str(line.amount)), unit="lakh"
                )
            if is_promoter and means_of_finance.promoter_contribution is None:
                means_of_finance.promoter_contribution = Money(
                    amount=Decimal(str(line.amount)), unit="lakh"
                )
    if result.extracted_financials and means_of_finance.requested_loan is None:
        from .financial_schema import ProjectFinancials

        term = ProjectFinancials.model_validate(
            result.extracted_financials
        ).term_loan_amount
        if term.status == "extracted" and term.value is not None:
            means_of_finance.requested_loan = Money(
                amount=Decimal(str(term.value)), unit="lakh"
            )
    if result.extracted_financials and means_of_finance.dpr_reported_total is None:
        from .financial_schema import ProjectFinancials

        total = ProjectFinancials.model_validate(
            result.extracted_financials
        ).means_of_finance_stated_total
        if total is not None:
            means_of_finance.dpr_reported_total = Money(
                amount=Decimal(str(total)), unit="lakh"
            )
    if means_of_finance.components and means_of_finance.upfront_funding_total is None:
        eligible = [
            c.amount.amount
            for c in means_of_finance.components
            if c.counted_in_upfront_funding and c.amount
        ]
        if eligible:
            means_of_finance.upfront_funding_total = Money(
                amount=sum(eligible, Decimal(0)), unit="lakh"
            )
    if result.extracted_financials and means_of_finance.proposed_tenure_months is None:
        from .financial_schema import ProjectFinancials

        term = ProjectFinancials.model_validate(
            result.extracted_financials
        ).loan_tenor_years
        if term.status == "extracted" and term.value is not None:
            means_of_finance.proposed_tenure_months = round(term.value * 12)
    # A generic (non-harvester) DPR can still carry its own full loan
    # schedule; CAM repayment must not depend on a harvester-only engine.
    if result.extracted_financials and not repayment_schedule:
        from .financial_schema import ProjectFinancials

        raw_fin = ProjectFinancials.model_validate(result.extracted_financials)
        schedule = {r.year: r for r in raw_fin.repayment_table}
        from .financial_schema import YearFinancials

        years_by_number = {y.year: y for y in raw_fin.years}
        for number in sorted(set(years_by_number) | set(schedule)):
            year = years_by_number.get(number, YearFinancials(year=number))
            scheduled = schedule.get(year.year)
            principal_value = (
                scheduled.principal_due.value
                if scheduled and scheduled.principal_due.status == "extracted"
                else year.principal_repayment
            )
            interest_value = (
                scheduled.interest_due.value
                if scheduled and scheduled.interest_due.status == "extracted"
                else year.interest_repayment
            )
            principal = (
                Decimal(str(principal_value)) if principal_value is not None else None
            )
            interest = (
                Decimal(str(interest_value)) if interest_value is not None else None
            )
            if principal is None and interest is None:
                continue
            trace = None
            cads = None
            if all(
                x is not None
                for x in (
                    year.net_profit,
                    year.depreciation,
                    year.interest_on_term_loan,
                    principal,
                    interest,
                )
            ):
                from .cam_financial_engine import (
                    CadsDefinition,
                )
                from .cam_financial_engine import (
                    dscr as calculated_dscr,
                )

                trace = calculated_dscr(
                    net_profit=Decimal(str(year.net_profit)),
                    depreciation=Decimal(str(year.depreciation)),
                    interest_on_term_loan=Decimal(str(year.interest_on_term_loan)),
                    principal_due=principal,
                    interest_due=interest,
                    definition=CadsDefinition.PAT_DEP_INTEREST,
                )
                cads = (
                    Decimal(str(year.net_profit))
                    + Decimal(str(year.depreciation))
                    + Decimal(str(year.interest_on_term_loan))
                )
            repayment_schedule.append(
                RepaymentPeriod(
                    year=year.year,
                    principal_due=Money(amount=principal, unit="lakh")
                    if principal is not None
                    else None,
                    interest_due=Money(amount=interest, unit="lakh")
                    if interest is not None
                    else None,
                    total_debt_service=Money(amount=principal + interest, unit="lakh")
                    if principal is not None and interest is not None
                    else None,
                    cash_available_for_debt_service=Money(amount=cads, unit="lakh")
                    if cads is not None
                    else None,
                    dscr=trace,
                    dscr_stated_in_dpr=Decimal(str(year.stated_dscr))
                    if year.stated_dscr is not None
                    else None,
                )
            )
        # Do not write Year-1 DSCR into the average DSCR field.
        # The yearly schedule already provides the properly scoped ratio.
    incomplete_financials = any(
        str(q).startswith(
            (
                "FINANCIAL_EXTRACTION_INCOMPLETE:",
                "FINANCIAL_SECTION_INCOMPLETE:",
                "FINANCIAL_EXTRACTION_FAILED:",
            )
        )
        for q in result.unresolved_questions
    )
    status = (
        ReadinessStatus.INSUFFICIENT_INFORMATION
        if result.status != "completed" or incomplete_financials
        else ReadinessStatus.EXPERT_REVIEW_REQUIRED
        if critical_count
        else ReadinessStatus.CORRECTIONS_REQUIRED
        if findings
        else ReadinessStatus.READY_FOR_SUBMISSION_REVIEW
    )

    from app.core.config import get_settings

    from . import cam_review

    score, passed, checks = cam_review.readiness_score(result)
    generated_at = datetime.now(timezone.utc)
    return CAMSnapshot(
        report_id=report_id,
        report_version=report_version,
        analysis_run_id=analysis_run_id,
        service_request_id=result.service_request_id,
        borrower_profile=borrower_profile,
        project_profile=project_profile,
        document_inventory=[
            DocumentReference(
                wallet_document_id=result.wallet_document_id,
                document_sha256=result.document_sha256,
            )
        ],
        project_cost=project_cost,
        means_of_finance=means_of_finance,
        operational_assumptions=_operational_assumptions(result),
        financial_projections=projections,
        repayment_schedule=repayment_schedule,
        financial_ratios=financial_ratios,
        scenario_analysis=scenario_analysis,
        findings=findings,
        proposed_corrections=[],
        policy_assessment=_policy_assessment(result),
        readiness_assessment=ReadinessAssessment(
            status=status,
            critical_findings_count=critical_count,
            outstanding_clarifications_count=len(result.unresolved_questions),
            score_mode="checks" if score is not None else "status",
            total_score=score,
            checks_passed=passed if score is not None else None,
            checks_total=checks if score is not None else None,
        ),
        report_reference=report_reference,
        financial_review=cam_review.financial_review(result),
        assumption_review=cam_review.assumption_review(result),
        document_checklist=cam_review.document_checklist(result),
        scheme_review=cam_review.scheme_review(result),
        stress_test=cam_review.stress_test(result),
        re_review_by=cam_review.re_review_by(
            generated_at, get_settings().cam_re_review_days
        ),
        document_verification=(notice := document_verification_notice()),
        report_limitations=[
            "Values not present in the DPR or not reproducibly calculated remain "
            "unverified. Bank-product eligibility and external subsidy "
            "eligibility are not certified.",
            "The borrower's documents (" + "; ".join(notice.documents) + ") are "
            f"not verified by this analysis; {notice.provider} verifies them "
            f"under the {notice.service_name} service."
            + (
                f" The vendor quotation is validated under the "
                f"{notice.quotation_service_name} service."
                if notice.quotation_service_name
                else ""
            ),
            "Findings above are deduplicated by category, affected metric "
            "and source page; a finding's merged_duplicate_count shows how "
            "many agent outputs raised it. Priority for findings without a "
            "code-verified calculation behind them is a severity-based "
            "fallback, not a measured financial-impact percentage (see "
            "PROGRESS.md milestone 2).",
        ],
        evidence_registry=[],
        generated_at=generated_at,
    )

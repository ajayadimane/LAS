"""Independent verification layer (brief Section 13) between the CAM
snapshot adapter and final HTML/PDF rendering.

This module never reruns an agent and never recomputes a figure itself --
it only checks that a CAMSnapshot is internally consistent and complete
enough to publish. A `blocking` issue means the brief's "critical
inconsistencies must block final report publication": the caller must not
render a final report from a snapshot that fails verification (Section 13)
-- render a clearly labeled incomplete draft instead, per existing report
policy (report_access.py gates release the same way for payment/consent).
A `warning` issue does not block, but should surface to a reviewer.

Each of the 13 checks in Section 13 is implemented as its own function so
a verification run's issue `code` traces directly back to the brief
section that required it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .cam_snapshot import CalculationTrace, CAMSnapshot, ReadinessStatus

_JINJA_PLACEHOLDER = re.compile(r"\{\{.*?\}\}")
# Literal bracket placeholders the sample CAM template itself ships with
# (cam-design-reference/LAS Customer CAM Report Template.pdf) -- a real,
# concrete example of exactly what Section 6.2/19.3 forbids reaching a
# rendered report.
_KNOWN_LITERAL_PLACEHOLDERS = (
    "[Customer full name]",
    "[BD name]",
    "[Branch name]",
    "[DD Mon YYYY]",
    "[YYYY]",
    "[000000]",
)

CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)


@dataclass(frozen=True)
class VerificationIssue:
    code: str
    severity: str  # "blocking" | "warning"
    message: str
    path: str = ""


@dataclass(frozen=True)
class VerificationResult:
    is_publishable: bool
    issues: list[VerificationIssue] = field(default_factory=list)

    @property
    def blocking_issues(self) -> list[VerificationIssue]:
        return [i for i in self.issues if i.severity == "blocking"]

    @property
    def warnings(self) -> list[VerificationIssue]:
        return [i for i in self.issues if i.severity == "warning"]


def _blocking(code: str, message: str, path: str = "") -> VerificationIssue:
    return VerificationIssue(code=code, severity="blocking", message=message, path=path)


def _warning(code: str, message: str, path: str = "") -> VerificationIssue:
    return VerificationIssue(code=code, severity="warning", message=message, path=path)


# 1. Required schema fields -------------------------------------------------


def check_required_schema_fields(snapshot: CAMSnapshot) -> list[VerificationIssue]:
    issues = []
    for field_name in ("report_id", "analysis_run_id", "service_request_id"):
        if not getattr(snapshot, field_name).strip():
            issues.append(
                _blocking(
                    "REQUIRED_SCHEMA_FIELD_MISSING",
                    f"{field_name} is empty.",
                    field_name,
                )
            )
    return issues


# 2. Financial units ----------------------------------------------------
# (Money.currency/unit are Literal-constrained by pydantic at parse time;
# nothing further to check once a CAMSnapshot has validated.)


# 3. Cross-page financial consistency ---------------------------------------


def check_cross_page_financial_consistency(
    snapshot: CAMSnapshot,
) -> list[VerificationIssue]:
    cost = snapshot.project_cost.dpr_reported_total
    loan = snapshot.means_of_finance.requested_loan
    promoter = snapshot.means_of_finance.promoter_contribution
    if cost is None or loan is None or promoter is None:
        return []
    total_funding = loan.amount + promoter.amount
    if abs(total_funding - cost.amount) > 1:
        return [
            _blocking(
                "PROJECT_COST_FUNDING_MISMATCH",
                f"Page 1/3 project cost ({cost.amount}) does not equal "
                f"requested loan + promoter contribution ({total_funding}) "
                "without this being an explicitly labeled revised scenario.",
                "project_cost/means_of_finance",
            )
        ]
    return []


# 4. Source-reference existence ---------------------------------------------


def check_source_reference_existence(snapshot: CAMSnapshot) -> list[VerificationIssue]:
    known_documents = {d.wallet_document_id for d in snapshot.document_inventory}
    issues = []
    all_evidence = list(snapshot.evidence_registry) + [
        e for f in snapshot.findings for e in f.evidence
    ]
    for evidence in all_evidence:
        if evidence.source_document_id not in known_documents:
            issues.append(
                _blocking(
                    "SOURCE_REFERENCE_DOES_NOT_EXIST",
                    f"Evidence {evidence.evidence_id} cites document "
                    f"{evidence.source_document_id}, which is not in "
                    "document_inventory.",
                    f"evidence/{evidence.evidence_id}",
                )
            )
    return issues


# 5. Page-level citations -----------------------------------------------


def check_page_level_citations(snapshot: CAMSnapshot) -> list[VerificationIssue]:
    issues = []
    known_page_counts = [
        d.page_count for d in snapshot.document_inventory if d.page_count is not None
    ]
    max_known_pages = max(known_page_counts) if known_page_counts else None
    for finding in snapshot.findings:
        for page in finding.source_pages:
            if page < 1:
                issues.append(
                    _blocking(
                        "INVALID_PAGE_CITATION",
                        f"Finding {finding.finding_id} cites page {page}, "
                        "which is not a valid page number.",
                        f"findings/{finding.finding_id}",
                    )
                )
            elif max_known_pages is not None and page > max_known_pages:
                issues.append(
                    _warning(
                        "PAGE_CITATION_EXCEEDS_DOCUMENT",
                        f"Finding {finding.finding_id} cites page {page}, "
                        f"beyond the longest known source document "
                        f"({max_known_pages} pages).",
                        f"findings/{finding.finding_id}",
                    )
                )
    return issues


# 6. Calculation reproducibility ---------------------------------------------


def _iter_calculation_traces(
    snapshot: CAMSnapshot,
) -> list[tuple[str, CalculationTrace]]:
    traces: list[tuple[str, CalculationTrace]] = []
    if snapshot.project_cost.reconciliation:
        traces.append(
            ("project_cost.reconciliation", snapshot.project_cost.reconciliation)
        )
    if snapshot.means_of_finance.funding_gap:
        traces.append(
            ("means_of_finance.funding_gap", snapshot.means_of_finance.funding_gap)
        )
    ratios = snapshot.financial_ratios
    for name in (
        "average_dscr",
        "breakeven_accounting",
        "breakeven_operating_cashflow",
        "breakeven_debt_service_threshold",
    ):
        trace = getattr(ratios, name)
        if trace:
            traces.append((f"financial_ratios.{name}", trace))
    for period in snapshot.repayment_schedule:
        if period.dscr:
            traces.append((f"repayment_schedule[{period.year}].dscr", period.dscr))
    for scenario in snapshot.scenario_analysis:
        for year, trace in scenario.dscr_by_year.items():
            traces.append(
                (f"scenario[{scenario.scenario_id}].dscr_by_year[{year}]", trace)
            )
    return traces


def check_calculation_reproducibility(snapshot: CAMSnapshot) -> list[VerificationIssue]:
    issues = []
    for path, trace in _iter_calculation_traces(snapshot):
        if trace.status == "COMPUTED" and trace.computed_value is None:
            issues.append(
                _blocking(
                    "CALCULATION_NOT_REPRODUCIBLE",
                    f"{path} is marked COMPUTED but carries no computed_value.",
                    path,
                )
            )
        if (
            trace.status == "NOT_COMPUTABLE"
            and not (trace.reason_not_computable or "").strip()
        ):
            issues.append(
                _blocking(
                    "CALCULATION_NOT_REPRODUCIBLE",
                    f"{path} is marked NOT_COMPUTABLE but gives no reason.",
                    path,
                )
            )
        if not trace.formula.strip():
            issues.append(
                _blocking(
                    "CALCULATION_NOT_REPRODUCIBLE",
                    f"{path} carries no formula -- not reproducible.",
                    path,
                )
            )
    return issues


# 7. Scenario labeling --------------------------------------------------


def check_scenario_labeling(snapshot: CAMSnapshot) -> list[VerificationIssue]:
    issues = []
    non_hypothetical_count = 0
    for scenario in snapshot.scenario_analysis:
        if not scenario.label.strip():
            issues.append(
                _blocking(
                    "SCENARIO_NOT_LABELED",
                    f"Scenario {scenario.scenario_id} has no label.",
                    f"scenario[{scenario.scenario_id}]",
                )
            )
        if not scenario.description.strip():
            issues.append(
                _warning(
                    "SCENARIO_NOT_DESCRIBED",
                    f"Scenario {scenario.scenario_id} has no description "
                    "explaining its assumptions.",
                    f"scenario[{scenario.scenario_id}]",
                )
            )
        if not scenario.is_hypothetical:
            non_hypothetical_count += 1
    if snapshot.scenario_analysis and non_hypothetical_count != 1:
        issues.append(
            _warning(
                "SCENARIO_FACT_AMBIGUITY",
                f"{non_hypothetical_count} scenarios are marked as the "
                "non-hypothetical (DPR-stated) case; expected exactly one, "
                "so a reader cannot tell which scenario is a reported fact.",
                "scenario_analysis",
            )
        )
    return issues


# 8. Policy-source validity --------------------------------------------------


def check_policy_source_validity(snapshot: CAMSnapshot) -> list[VerificationIssue]:
    issues = []
    for i, statement in enumerate(snapshot.policy_assessment.statements):
        if statement.status.value == "verified_policy_requirement" and not (
            statement.source_url and statement.effective_date
        ):
            issues.append(
                _blocking(
                    "UNVERIFIED_POLICY_CLAIMED_AS_REQUIREMENT",
                    f"Policy statement {i} is marked a verified policy "
                    "requirement without a dated source.",
                    f"policy_assessment.statements[{i}]",
                )
            )
    return issues


# 9. Readiness-status consistency --------------------------------------------


def check_readiness_status_consistency(
    snapshot: CAMSnapshot,
) -> list[VerificationIssue]:
    issues = []
    readiness = snapshot.readiness_assessment
    actual_critical = sum(
        1 for f in snapshot.findings if f.severity.value == "critical"
    )
    if actual_critical != readiness.critical_findings_count:
        issues.append(
            _blocking(
                "READINESS_CRITICAL_COUNT_MISMATCH",
                f"readiness_assessment.critical_findings_count "
                f"({readiness.critical_findings_count}) does not match the "
                f"actual number of critical findings ({actual_critical}).",
                "readiness_assessment",
            )
        )
    if (
        actual_critical > 0
        and readiness.status != ReadinessStatus.EXPERT_REVIEW_REQUIRED
    ):
        issues.append(
            _blocking(
                "READINESS_STATUS_INCONSISTENT_WITH_FINDINGS",
                f"{actual_critical} critical finding(s) present but "
                f"readiness status is {readiness.status.value}, not "
                "EXPERT_REVIEW_REQUIRED.",
                "readiness_assessment",
            )
        )
    must_fix_count = sum(1 for f in snapshot.findings if f.priority.value == "must_fix")
    if (
        readiness.status == ReadinessStatus.READY_FOR_SUBMISSION_REVIEW
        and must_fix_count > 0
    ):
        issues.append(
            _blocking(
                "READINESS_STATUS_INCONSISTENT_WITH_FINDINGS",
                f"readiness status is READY_FOR_SUBMISSION_REVIEW but "
                f"{must_fix_count} must-fix finding(s) remain open.",
                "readiness_assessment",
            )
        )
    return issues


# 10. Unsupported claims (subsidy eligibility) -------------------------------


def check_unsupported_claims(snapshot: CAMSnapshot) -> list[VerificationIssue]:
    issues = []
    for i, benefit in enumerate(snapshot.readiness_assessment.subsidy_benefits):
        if benefit.eligibility_status == "verified" and not (
            benefit.source and benefit.source_effective_date
        ):
            issues.append(
                _blocking(
                    "UNSUPPORTED_SUBSIDY_CLAIM",
                    f"Subsidy benefit {i} ({benefit.scheme_name}) is marked "
                    "verified eligibility without a dated source.",
                    f"readiness_assessment.subsidy_benefits[{i}]",
                )
            )
    return issues


# 11. Placeholder elimination -------------------------------------------


def _iter_strings(value: object) -> list[str]:
    strings: list[str] = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            strings.extend(_iter_strings(v))
    elif isinstance(value, (list, tuple)):
        for v in value:
            strings.extend(_iter_strings(v))
    return strings


def check_placeholder_elimination(snapshot: CAMSnapshot) -> list[VerificationIssue]:
    issues = []
    for text in _iter_strings(snapshot.model_dump(mode="json")):
        if _JINJA_PLACEHOLDER.search(text):
            issues.append(
                _blocking(
                    "UNRESOLVED_TEMPLATE_PLACEHOLDER",
                    f"Unresolved template binding found: {text!r}",
                )
            )
        for literal in _KNOWN_LITERAL_PLACEHOLDERS:
            if literal in text:
                issues.append(
                    _blocking(
                        "UNRESOLVED_TEMPLATE_PLACEHOLDER",
                        f"Unresolved literal placeholder {literal!r} found "
                        f"in: {text!r}",
                    )
                )
    return issues


# 12. Report generation completeness -----------------------------------------


def check_report_generation_completeness(
    snapshot: CAMSnapshot,
) -> list[VerificationIssue]:
    issues = []
    if (
        snapshot.readiness_assessment.status != ReadinessStatus.INSUFFICIENT_INFORMATION
        and not snapshot.document_inventory
    ):
        issues.append(
            _blocking(
                "REPORT_INCOMPLETE",
                "No source document is recorded, but the report does not "
                "declare itself INSUFFICIENT_INFORMATION.",
                "document_inventory",
            )
        )
    if snapshot.generated_at > datetime.now(timezone.utc) + CLOCK_SKEW_TOLERANCE:
        issues.append(
            _blocking(
                "REPORT_GENERATED_AT_IN_FUTURE",
                f"generated_at ({snapshot.generated_at.isoformat()}) is in the future.",
                "generated_at",
            )
        )
    return issues


# 13. Finding uniqueness ------------------------------------------------


def check_finding_uniqueness(snapshot: CAMSnapshot) -> list[VerificationIssue]:
    seen: dict[str, str] = {}
    issues = []
    for f in snapshot.findings:
        if f.dedup_key in seen:
            issues.append(
                _blocking(
                    "DUPLICATE_FINDING_DEDUP_KEY",
                    f"Findings {seen[f.dedup_key]} and {f.finding_id} share "
                    f"dedup key {f.dedup_key!r} -- consolidation was not "
                    "applied before this snapshot was built.",
                    f"findings/{f.finding_id}",
                )
            )
        else:
            seen[f.dedup_key] = f.finding_id
    return issues


_ALL_CHECKS = (
    check_required_schema_fields,
    check_cross_page_financial_consistency,
    check_source_reference_existence,
    check_page_level_citations,
    check_calculation_reproducibility,
    check_scenario_labeling,
    check_policy_source_validity,
    check_readiness_status_consistency,
    check_unsupported_claims,
    check_placeholder_elimination,
    check_report_generation_completeness,
    check_finding_uniqueness,
)


def verify_snapshot(snapshot: CAMSnapshot) -> VerificationResult:
    """Runs every Section 13 check. A snapshot with any blocking issue must
    not be published as a final report (caller's responsibility -- this
    function only reports, it never raises or mutates)."""
    issues: list[VerificationIssue] = []
    for check in _ALL_CHECKS:
        issues.extend(check(snapshot))
    return VerificationResult(
        is_publishable=not any(i.severity == "blocking" for i in issues),
        issues=issues,
    )

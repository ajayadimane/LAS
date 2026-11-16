"""Scheme rules (schemes.py): the DPR's subsidy, cost and contribution
against the table's rules for the scheme it claims."""

from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.finding_judge import apply_verdicts, assign_ids
from app.services.dpr_agentic.financial_schema import (
    ColdStorageAssumptions,
    ExtractedValue,
    MeansOfFinanceComponent,
    ProjectFinancials,
)
from app.services.dpr_agentic.schemas import AnalysisResult, DocumentFact, Verdict
from app.services.dpr_agentic.schemes import (
    max_subsidy,
    own_contribution,
    run_scheme_checks,
    schemes_claimed,
    subsidy_claimed,
)


def claimed(text):
    return [DocumentFact(field="scheme_claimed", value=text, page=1, quote=text)]


def fin(cost, funding, **kw):
    return ProjectFinancials(
        cost_of_project_stated_total=cost,
        means_of_finance_components=[
            MeansOfFinanceComponent(label=label, amount=amount)
            for label, amount in funding.items()
        ],
        **kw,
    )


def ids(findings):
    return {f.check_id for f in findings}


def test_margin_money_alone_is_the_borrowers_share_not_subsidy():
    """Seen live: "APPLICANT MARGIN MONEY" (25% own share) on an AIF-only
    DPR was read as subsidy and flagged as subsidy AIF does not give."""
    f = fin(31.95, {"SBI TERM LOAN AMOUNT": 23.96, "APPLICANT MARGIN MONEY": 7.99})
    assert subsidy_claimed(f) is None and own_contribution(f) == 7.99
    assert run_scheme_checks(claimed("AIF"), f)[0] == []


def test_schemes_and_funding_lines_are_recognised():
    assert [r.key for r in schemes_claimed("AIF with SMAM subsidy")] == [
        "smam",
        "aif",
    ]
    f = fin(
        30,
        {"Promoter's contribution": 3, "Capital subsidy (SMAM)": 12, "Term loan": 15},
    )
    assert subsidy_claimed(f) == 12 and own_contribution(f) == 3


def test_pmegp_subsidy_cap_and_own_share():
    within = fin(
        40, {"Own contribution": 4, "PMEGP margin money subsidy": 14, "Loan": 22}
    )
    assert run_scheme_checks(claimed("PMEGP"), within)[0] == []  # 35% of 40 = 14
    over = fin(60, {"Own contribution": 2, "Margin money subsidy": 21, "Loan": 37})
    findings, [record] = run_scheme_checks(claimed("PMEGP"), over)
    # 35% of the 50-lakh cap = 17.5; cost above the cap; own share 3.3%.
    assert ids(findings) == {
        "SCHEME_SUBSIDY_ABOVE_MAX",
        "SCHEME_COST_CAP_PMEGP",
        "SCHEME_OWN_SHARE_PMEGP",
    }
    assert record["max_subsidy_lakh"] == 17.5
    assert {f.priority for f in findings} == {"should_fix"}


def test_pmfme_subsidy_is_capped_at_ten_lakh():
    f = fin(40, {"Promoter": 4, "PMFME subsidy": 12, "Loan": 24})
    [finding], [record] = run_scheme_checks(claimed("PMFME"), f)
    assert finding.check_id == "SCHEME_SUBSIDY_ABOVE_MAX"
    assert "at most 10 lakh" in finding.explanation
    assert record["how"] == "35%, capped at 10 lakh"


def test_subsidy_under_a_scheme_that_gives_none():
    f = fin(30, {"Promoter": 7.5, "Subsidy": 10, "Term loan": 12.5})
    [finding], _ = run_scheme_checks(claimed("AIF, CGTMSE cover"), f)
    assert finding.check_id == "SCHEME_NO_SUBSIDY"
    # With SMAM also claimed, the subsidy has a source: 40% of 30 = 12.
    assert run_scheme_checks(claimed("AIF and SMAM"), f)[0] == []


def test_aif_subvention_rate_and_loan_cap():
    f = fin(
        300,
        {"Promoter": 60, "Term loan": 240},
        subvention_rate_pct=4,
        term_loan_amount=ExtractedValue(value=240, page=3, status="extracted"),
    )
    assert ids(run_scheme_checks(claimed("AIF"), f)[0]) == {
        "SCHEME_SUBVENTION_RATE",
        "SCHEME_SUBVENTION_LOAN_CAP",
    }


def test_midh_cold_storage_subsidy_from_the_cost_norm():
    f = fin(
        468,
        {"Promoter": 70, "MIDH back-ended subsidy": 140, "Bank loan": 258},
        cold_storage_assumptions=ColdStorageAssumptions(
            storage_type="Type II",
            capacity_mt=ExtractedValue(value=2000, page=3, status="extracted"),
        ),
    )
    assert max_subsidy(schemes_claimed("MIDH")[0], f) == (
        120.0,
        "50% of 12,000 rupees a tonne x 2000 tonnes",
    )
    [finding], _ = run_scheme_checks(claimed("MIDH / NHB"), f)
    assert finding.check_id == "SCHEME_SUBSIDY_ABOVE_MAX"


def test_no_claimed_scheme_means_no_checks_and_findings_are_anchored():
    f = fin(30, {"Subsidy": 20})
    assert run_scheme_checks([], f) == ([], [])
    [finding] = assign_ids(run_scheme_checks(claimed("CGTMSE"), f)[0])
    kept, rejected = apply_verdicts(
        [finding],
        [Verdict(finding_id=finding.finding_id, decision="reject", reason="r")],
    )
    assert rejected == [] and kept[0].priority == "should_fix"


def test_annexure_f_lists_the_scheme_rules_applied():
    result = AnalysisResult(
        service_request_id="t",
        wallet_document_id="d",
        document_sha256="a" * 64,
        status="completed",
        agent_outputs={},
        findings=[],
        unresolved_questions=[],
        scheme_checks=[
            {
                "scheme": "PMEGP",
                "max_subsidy_lakh": 17.5,
                "how": "35% of 50 lakh",
                "basis": "KVIC guidelines.",
                "status": "approved 2026-11-13",
            }
        ],
    )
    snap = build_snapshot_from_analysis(
        result, report_id="r", report_version=1, analysis_run_id="r"
    )
    areas = snap.policy_assessment.unverified_policy_areas
    assert any("PMEGP rules" in a and "17.5 lakh (35% of 50 lakh)" in a for a in areas)


# --- Scheme comparison (2.x layout, page 1) ------------------------------------

from app.services.dpr_agentic import cam_review  # noqa: E402
from app.services.dpr_agentic.financial_schema import (  # noqa: E402
    DairyAssumptions,
    HarvesterAssumptions,
    PolyhouseAssumptions,
    YearFinancials,
)
from app.services.dpr_agentic.schemes import (  # noqa: E402
    SCHEME_RULES,
    project_sector,
    scheme_options,
    subvention_value,
)

RULE = {r.key: r for r in SCHEME_RULES}


def ev(value):
    return ExtractedValue(value=value, page=3, status="extracted")


def harvester_fin(**kw):
    """A Rs 30 lakh harvester, Rs 22.5 lakh loan repaid 3.21 a year."""
    return fin(
        30,
        {"Term loan": 22.5, "Promoter": 7.5},
        term_loan_amount=ev(22.5),
        harvester_assumptions=HarvesterAssumptions(price_per_acre=ev(1800)),
        years=[
            YearFinancials(year=n, principal_repayment=3.21, interest_repayment=1)
            for n in range(1, 8)
        ],
        **kw,
    )


def test_project_sector_is_read_from_the_sector_groups():
    assert project_sector(harvester_fin()) == "harvester"
    dairy = fin(10, {}, dairy_assumptions=DairyAssumptions(animals=ev(10)))
    assert project_sector(dairy) == "dairy_farm"
    both = harvester_fin(polyhouse_assumptions=PolyhouseAssumptions())
    assert project_sector(both) is None  # two sectors: not judged
    assert project_sector(fin(10, {})) is None


def test_recommendations_promise_the_cautious_subsidy():
    f = harvester_fin()
    # Checking a claim: the lenient end (40% of 30).
    assert max_subsidy(RULE["smam"], f)[0] == 12
    # Recommending: one combine for an individual, at most 6.4 lakh.
    assert max_subsidy(RULE["smam"], f, cautious=True) == (
        6.4,
        "40%, capped at 6.4 lakh",
    )
    assert max_subsidy(RULE["midh"], f, cautious=True) is None  # no norm here


def test_subvention_is_three_percent_on_the_outstanding_loan():
    # 22.5, 19.29, 16.08 ... outstanding at the start of each of 7 years.
    value, how = subvention_value(RULE["aif"], harvester_fin())
    expected = sum(0.03 * (22.5 - 3.21 * n) for n in range(7))
    assert abs(value - round(expected, 2)) < 0.01
    assert "7 years" in how


def test_options_rank_combinations_by_benefit():
    options = scheme_options(harvester_fin(), "harvester")
    assert [o.name for o in options] == [
        "SMAM + Agriculture Infrastructure Fund (AIF)",
        "SMAM",
        "Agriculture Infrastructure Fund (AIF)",
    ]
    assert options[0].total == round(options[1].total + options[2].total, 2)
    assert scheme_options(harvester_fin(), None) == []
    assert scheme_options(harvester_fin(), "dairy_farm") == []


def _result(facts, f):
    findings, records = run_scheme_checks(facts, f)
    return AnalysisResult(
        service_request_id="SR",
        wallet_document_id="W",
        document_sha256="a" * 64,
        status="completed",
        agent_outputs={"document": {"document_facts": facts}},
        findings=findings,
        unresolved_questions=[],
        extracted_financials=f.model_dump(mode="json"),
        scheme_checks=records,
    )


def test_aif_alone_for_a_harvester_suggests_adding_smam():
    review = cam_review.scheme_review(_result(claimed("AIF"), harvester_fin()))
    assert review.verdict == "better_available"
    assert review.chosen.name == "Agriculture Infrastructure Fund (AIF)"
    assert review.recommended.name == "SMAM + Agriculture Infrastructure Fund (AIF)"
    assert review.alternative.name == "SMAM"
    assert review.recommended.capital_subsidy == "40% · ₹ 6.4 L"
    assert review.recommendation.startswith(
        "Keep Agriculture Infrastructure Fund (AIF) and add SMAM"
    )
    assert review.gaps[0].criterion == "Scheme named in the DPR"
    assert "Add SMAM" in review.gaps[0].action


def test_the_best_combination_already_claimed_is_correct():
    review = cam_review.scheme_review(
        _result(claimed("AIF and SMAM subsidy"), harvester_fin())
    )
    assert review.verdict == "correct"
    assert review.recommendation.startswith("Stay with SMAM + Agriculture")
    assert review.alternative.name == "SMAM"


def test_a_scheme_that_does_not_cover_the_project():
    dairy = fin(
        10,
        {"Term loan": 9, "Promoter": 1},
        term_loan_amount=ev(9),
        dairy_assumptions=DairyAssumptions(animals=ev(10)),
    )
    facts = claimed("AIF")
    findings, _ = run_scheme_checks(facts, dairy)
    assert "SCHEME_ACTIVITY_AIF" in ids(findings)
    review = cam_review.scheme_review(_result(facts, dairy))
    assert review.verdict == "not_eligible"
    assert "DEDS" in review.recommendation
    assert review.gaps[0].criterion == "Eligible activity"


def test_no_comparison_for_sectors_the_table_does_not_cover():
    review = cam_review.scheme_review(
        _result(claimed("PMEGP"), fin(20, {"Promoter": 2, "Loan": 18}))
    )
    assert review.verdict == "not_assessed" and review.recommended is None

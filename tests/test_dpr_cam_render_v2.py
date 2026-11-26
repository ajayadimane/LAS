"""The 2.x "DPR Pre-submission Review" layout (cam_render_v2.py) and the
snapshot fields it prints (cam_review.py): readiness, action plan,
recomputed figures, scheme fit, verification, documents, and the
PSR reference stored with the report."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.services.dpr_agentic import cam_review
from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.cam_render import (
    render_cam_report_html,
    render_cam_report_pdf,
    uses_v2_layout,
)
from app.services.dpr_agentic.cam_render_v2 import (
    build_action_plan,
    build_cover,
    build_verification,
    readiness,
)
from app.services.dpr_agentic.cam_snapshot import KycVerification
from app.services.dpr_agentic.schemas import (
    AgentOutput,
    AnalysisResult,
    ChecklistAnswer,
    DocumentFact,
    Finding,
)


def _finding(priority, title="Issue", category="dscr"):
    f = Finding(
        category=category,
        severity="high",
        title=title,
        explanation="Because.",
        suggested_action="Fix it.",
        evidence_page=3,
    )
    f.priority = priority
    f.priority_rule = f"{priority.upper()}: test"
    return f


def _result(findings=(), checks=(), calculations=(), facts=(), **extra):
    outputs = {}
    if facts:
        outputs["document"] = AgentOutput(document_facts=list(facts))
    if checks:
        outputs["sector"] = AgentOutput(checks=list(checks))
    return AnalysisResult(
        service_request_id="SR",
        wallet_document_id="W",
        document_sha256="a" * 64,
        status="completed",
        agent_outputs=outputs,
        findings=list(findings),
        unresolved_questions=[],
        calculations=list(calculations),
        **extra,
    )


def _snap(result=None, **kw):
    return build_snapshot_from_analysis(
        result or _result(),
        report_id="r",
        report_version=1,
        analysis_run_id="run",
        **kw,
    )


def test_new_snapshots_use_the_new_layout_and_issued_ones_keep_theirs():
    snap = _snap()
    assert snap.schema_version.startswith("2.") and uses_v2_layout(snap)
    old = snap.model_copy(update={"schema_version": "1.0.0"})
    assert not uses_v2_layout(old)
    assert "<h1>DPR Analysis</h1>" in render_cam_report_html(snap)
    assert "<h1>DPR Analysis</h1>" not in render_cam_report_html(old)


def test_readiness_score_counts_check_families_and_checklist_answers():
    result = _result(
        calculations=[
            {"check": "dscr_year_1", "match": True},
            {"check": "dscr_year_2", "match": False},  # the DSCR family fails
            {"check": "cost_of_project_total", "match": True},
        ],
        checks=[
            ChecklistAnswer(check_id="SEC_PRICE_REALISTIC", status="ok"),
            ChecklistAnswer(check_id="SEC_SEASONALITY", status="issue"),
            ChecklistAnswer(check_id="SEC_INPUTS_AVAILABLE", status="cannot_tell"),
            ChecklistAnswer(check_id="NOT_A_REAL_ITEM", status="ok"),
        ],
    )
    assert cam_review.readiness_score(result) == (50, 2, 4)
    ra = _snap(result).readiness_assessment
    assert (ra.score_mode, ra.total_score) == ("checks", 50)
    assert cam_review.readiness_score(_result()) == (None, 0, 0)


@pytest.mark.parametrize(
    ("priorities", "selected"),
    [
        (["must_fix", "should_fix"], "Needs major revision"),
        (["should_fix", "good_to_fix"], "Ready after minor fixes"),
        (["good_to_fix"], "Ready to submit"),
        ([], "Ready to submit"),
    ],
)
def test_readiness_option_follows_the_finding_counts(priorities, selected):
    snap = _snap(_result(findings=[_finding(p) for p in priorities]))
    chosen = [o["label"] for o in readiness(snap)["options"] if o["selected"]]
    assert chosen == [selected]


def test_cover_prints_the_bank_reference_and_counts():
    result = _result(
        findings=[_finding("must_fix")],
        service_details={"bank_name": "State Bank of India"},
    )
    snap = _snap(result, report_reference="PSR/2026/000042")
    cover = build_cover(snap)
    assert cover["bank"] == "State Bank of India"
    assert cover["counts"] == {"must_fix": 1, "should_fix": 0, "good_to_fix": 0}
    html = render_cam_report_html(snap)
    assert "PSR/2026/000042" in html
    assert "against State Bank of India baselines" in html


def test_action_plan_orders_by_priority_and_explains_why():
    snap = _snap(
        _result(
            findings=[
                _finding("good_to_fix", "Minor", "market"),
                _finding("must_fix", "Major", "dscr"),
            ]
        )
    )
    rows = build_action_plan(snap)["rows"]
    assert [(r["n"], r["title"], r["priority"]) for r in rows] == [
        (1, "Major", "Must fix"),
        (2, "Minor", "Can improve"),
    ]
    assert rows[0]["why"] == "Decides whether the bank sees the loan as repayable"
    assert rows[1]["why"] == "Revenue may be overstated"


def test_action_plan_rows_say_what_we_found_and_what_to_do():
    f = _finding("should_fix", "Your experience in this work is not shown", "risk")
    f.explanation = "The DPR lists only your schooling. " * 2
    f.suggested_action = "Describe your experience or training in this kind of work."
    [row] = build_action_plan(_snap(_result(findings=[f])))["rows"]
    assert row["pages"] == "3"
    assert row["found"].startswith("The DPR lists only your schooling.")
    assert row["todo"] == f.suggested_action
    assert "dpr" not in row and "should_be" not in row


def test_a_calculated_row_shows_both_figures_and_a_customer_action():
    f = _finding("must_fix", "Means Of Finance Total does not reconcile")
    f.provenance = "calculated"
    f.dpr_stated_value = "70.00"
    f.recomputed_display_value = "105.00"
    f.suggested_action = "Verify this figure with the applicant."
    [row] = build_action_plan(_snap(_result(findings=[f])))["rows"]
    assert row["title"] == "Means of finance total does not add up"
    assert row["found"] == "Your DPR: 70.00. Should be: 105.00."
    assert "applicant" not in row["todo"]


def test_a_long_explanation_is_cut_at_a_sentence_boundary():
    from app.services.dpr_agentic.cam_render_v2 import _plain_excerpt

    text = "First point is short. " + "Second sentence is very long " * 20 + "."
    assert _plain_excerpt(text) == "First point is short."
    one_long = "word " * 100
    cut = _plain_excerpt(one_long)
    assert cut.endswith("…") and len(cut) <= 241 and not cut.endswith(" …")


def test_the_html_has_the_plain_column_headings():
    html = render_cam_report_html(_snap(_result(findings=[_finding("must_fix")])))
    for heading in ("What's wrong", "What we found in your DPR", "What to do"):
        assert heading in html
    assert "Should be" not in html


def test_recomputed_figures_come_from_the_code_checks():
    result = _result(
        calculations=[
            {"check": "dscr_year_1", "computed_value": 1.05, "match": True},
            {"check": "dscr_year_2", "computed_value": 1.6, "match": True},
            {"check": "dscr_average", "computed_value": 1.2, "match": False},
        ]
    )
    rows = {m.measure: m for m in _snap(result).financial_review}
    assert (rows["Average DSCR"].recomputed, rows["Average DSCR"].status) == (
        "1.20",
        "below",
    )
    assert (rows["Minimum DSCR"].recomputed, rows["Minimum DSCR"].status) == (
        "1.05",
        "below",
    )
    assert rows["Project IRR (%)"].status == "pending"


def test_total_project_cost_is_recomputed_from_the_cost_lines():
    fin = {
        "cost_of_project_components": [
            {"label": "Machinery", "amount": 30.0},
            {"label": "Building", "amount": 12.5},
        ],
        "cost_of_project_stated_total": 45.0,
    }
    # The stored check, as financial_calc names it.
    stored = _result(
        extracted_financials=fin,
        calculations=[
            {
                "check": "cost_of_project_total",
                "computed_value": 42.5,
                "stated_value": 45.0,
                "match": False,
            }
        ],
    )
    row = {m.measure: m for m in _snap(stored).financial_review}[
        "Total project cost (₹ lakh)"
    ]
    assert (row.dpr_value, row.recomputed, row.status) == ("45.00", "42.50", "below")
    # An older result without the check is recomputed from the DPR's lines.
    older = _result(extracted_financials=fin)
    row = {m.measure: m for m in _snap(older).financial_review}[
        "Total project cost (₹ lakh)"
    ]
    assert (row.recomputed, row.status) == ("42.50", "below")


def test_documents_follow_the_checklist_answers():
    result = _result(
        checks=[
            ChecklistAnswer(check_id="SEC_DEMAND_EVIDENCE", status="issue"),
        ]
    )
    result.agent_outputs["document"] = AgentOutput(
        checks=[ChecklistAnswer(check_id="DOC_QUOTATION", status="ok")]
    )
    docs = {d.document: d for d in _snap(result).document_checklist}
    assert docs["Machinery quotations (≥ 2), civil estimate"].status == "available"
    assert docs["Buyer / buy-back tie-up letters"].status == "missing"
    assert docs["Last 3 years' ITR and financials"].status == "to_confirm"


def test_scheme_fit_shows_the_chosen_schemes_rules_and_gaps():
    claimed = DocumentFact(
        field="scheme_claimed", value="PMEGP", page=1, quote="PMEGP", verified=True
    )
    result = _result(
        facts=[claimed],
        scheme_checks=[
            {
                "scheme": "PMEGP",
                "max_subsidy_lakh": 17.5,
                "how": "35% of 50 lakh",
                "basis": "b",
                "status": "approved",
                "gaps": [
                    {
                        "criterion": "Promoter contribution",
                        "requires": "At least 5%",
                        "dpr_shows": "3.3%",
                        "action": "Raise it.",
                    }
                ],
            }
        ],
    )
    review = _snap(result).scheme_review
    assert review.chosen.name == "PMEGP" and review.chosen.eligible == "partly"
    assert review.chosen.capital_subsidy == "35% · ₹ 17.5 L"
    assert review.verdict == "not_assessed" and review.recommended is None
    assert [g.criterion for g in review.gaps] == ["Promoter contribution"]
    html = render_cam_report_html(_snap(result))
    assert "Promoter contribution" in html and "Partly" in html
    # No "eligible" verdict is ever printed for a scheme.
    assert ">Eligible<" not in html


def test_kyc_is_pending_until_ifpl_results_arrive():
    snap = _snap()
    ver = build_verification(snap)
    assert ver["cards"] is None
    assert ver["pending"] == (
        "Pending: Get your Credit and KYC details verified by IFPL "
        "through our Value Addition Service."
    )
    assert "Doc Verification" in ver["quotation"]
    filled = snap.model_copy(
        update={
            "kyc_verification": KycVerification(
                consent_date=datetime(2026, 11, 10, tzinfo=timezone.utc),
                credit_score=742,
                credit_band="good",
                pan="verified",
                aadhaar="verified",
                address="mismatch",
                name_dpr_kyc="match",
            )
        }
    )
    ver = build_verification(filled)
    assert ver["cards"]["score"] == "742"
    assert ("Address", "Mismatch") in ver["cards"]["kyc"]
    assert "on 10-11-2026" in ver["subtitle"]


def test_re_review_is_seven_days_after_the_review():
    snap = _snap()
    assert (snap.re_review_by - snap.generated_at).days == 7


def test_pdf_flows_long_action_plans_and_counts_real_pages():
    import pymupdf

    findings = [_finding("should_fix", f"Issue {i}") for i in range(40)]
    snap = _snap(_result(findings=findings), report_reference="PSR/2026/000007")
    pdf = render_cam_report_pdf(snap)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        pages = doc.page_count
        assert pages > 6  # the 40-row plan continues on more pages
        text_all = "".join(p.get_text() for p in doc)
        assert f"Page 1 of {pages}" in doc[0].get_text()
        assert f"Page {pages} of {pages}" in doc[pages - 1].get_text()
        assert "Issue 39" in text_all and "Annexure" not in text_all
        assert "Review ref PSR/2026/000007" in doc[2].get_text()
        fonts = {f[3] for p in doc for f in p.get_fonts()}
        assert any("Inter" in f for f in fonts)


def test_stored_reports_print_their_row_number(dpr_pg):
    from app.services.dpr_agentic.jobs import claim, submit
    from app.services.dpr_agentic.report_store import store_report

    run_id, _ = submit(
        dpr_pg,
        service_request_id="SR-1",
        document_sha256="b" * 64,
        source_reference="svc:psr",
    )
    claim(dpr_pg, run_id)
    store_report(dpr_pg, run_id, _result())
    with dpr_pg.connect() as conn:
        row_id, reference = conn.execute(
            text(
                "SELECT id, cam_snapshot->>'report_reference' "
                "FROM dpr_report_artifacts WHERE source_reference='svc:psr'"
            )
        ).one()
    year = datetime.now(timezone.utc).year
    assert reference == f"PSR/{year}/{row_id:06d}"


def test_banking_terms_are_explained_once_per_row():
    f = _finding("should_fix", "Repayment fails if the price falls 10%")
    f.provenance = "calculated"
    f.explanation = "Year-2 DSCR falls to 0.9."
    f.suggested_action = "Lengthen the tenor so the DSCR stays above 1."
    [row] = build_action_plan(_snap(_result(findings=[f])))["rows"]
    assert row["found"].count("how many times your yearly earnings") == 1
    assert "how many times" not in row["todo"]  # already explained
    assert "tenor (loan period)" in row["todo"]


def test_the_stated_dscr_finding_reads_in_customer_words():
    f = _finding("should_fix", "Stated DSCR does not follow the standard formula")
    f.provenance = "calculated"
    f.explanation = "DSCR recomputed as (net profit + depreciation) / ..."
    [row] = build_action_plan(_snap(_result(findings=[f])))["rows"]
    assert row["title"].startswith("Your DSCR is worked out differently")
    assert "recomputed as" not in row["found"]


def test_a_lower_case_title_starts_with_a_capital():
    f = _finding("should_fix", "transport cost from Bhopal is not budgeted")
    [row] = build_action_plan(_snap(_result(findings=[f])))["rows"]
    assert row["title"] == "Transport cost from Bhopal is not budgeted"


# --- section 3 in plain words (2026-11-25) ----------------------------------


def test_measures_print_plain_labels_and_what_they_mean():
    from types import SimpleNamespace

    from app.services.dpr_agentic.cam_render_v2 import build_financials

    def row(measure, baseline, status="meets"):
        return SimpleNamespace(
            measure=measure,
            dpr_value="2.0",
            recomputed="2.1",
            baseline=baseline,
            status=status,
        )

    snap = SimpleNamespace(
        financial_review=[
            row("Average DSCR", "≥ 1.25"),
            row("Debt : equity", "≤ 3:1", "below"),
            row("Payback period (years)", "< loan tenure (7 yrs)", "pending"),
            row("Something new", "≥ 2"),
        ],
        assumption_review=[],
        stress_test=None,
    )
    fin = build_financials(snap)
    by_label = {m["measure"]: m for m in fin["measures"]}
    dscr = by_label["Ability to repay, average year (DSCR)"]
    assert dscr["baseline"] == "At least 1.25" and "instalments" in dscr["meaning"]
    de = by_label["Loan compared with your own money"]
    assert de["baseline"] == "Not more than 3 to 1"
    assert de["status"] == "Below what the bank wants"
    payback = by_label["Time to earn back the cost (years)"]
    assert payback["baseline"] == "Shorter than the loan period (7 years)"
    assert payback["status"] == "Not enough information"
    assert by_label["Something new"]["baseline"] == "At least 2"  # passes through
    assert fin["stress"].startswith("We could not test this")


def test_the_stress_box_says_whether_you_could_still_repay():
    from decimal import Decimal
    from types import SimpleNamespace

    from app.services.dpr_agentic.cam_render_v2 import _stress_text

    def stress(lowest):
        return SimpleNamespace(
            price_fall_pct=10,
            average_dscr=Decimal("2.26"),
            minimum_dscr=Decimal(lowest),
            minimum_year="year 1",
            most_sensitive="Your biggest risk is your selling price.",
        )

    ok = _stress_text(stress("1.88"))
    assert "drops to 2.26 in an average year and 1.88 in your weakest year" in ok
    assert "so you would still be able to repay." in ok
    assert ok.endswith("Your biggest risk is your selling price.")
    assert "would struggle to pay your instalments" in _stress_text(stress("0.95"))

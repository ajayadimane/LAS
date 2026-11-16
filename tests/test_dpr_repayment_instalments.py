"""Per-instalment repayment schedules are summed in code, and calendar-year
numbering is normalised to 1..N -- both found on real DPRs by the
evaluation harness (2026-11-12)."""

from app.services.dpr_agentic.financial_calc import dscr_per_year
from app.services.dpr_agentic.financial_schema import (
    ExtractedValue,
    ProjectFinancials,
    RepaymentInstalment,
    RepaymentTableRow,
    YearFinancials,
    aggregate_repayment_instalments,
    normalize_year_numbers,
)


def _monthly_year(year: int, page: int) -> list[RepaymentInstalment]:
    """Shape of the real schedule: 10 capitalised-interest months (negative
    principal) and two instalment months of Rs 2,06,560."""
    rows = [
        RepaymentInstalment(
            loan_year=year,
            period=f"m{m}",
            interest=14_000.0,
            principal=-14_000.0,
            page=page,
        )
        for m in range(10)
    ]
    rows += [
        RepaymentInstalment(
            loan_year=year,
            period="Jul",
            instalment=206_560.0,
            interest=14_593.17,
            principal=191_966.83,
            page=page,
        ),
        RepaymentInstalment(
            loan_year=year,
            period="Jan",
            instalment=206_560.0,
            interest=13_654.12,
            principal=192_905.88,
            page=page,
        ),
    ]
    return rows


def test_monthly_rupee_schedule_becomes_yearly_lakh_rows():
    fin = ProjectFinancials(
        repayment_instalments=_monthly_year(3, 12), repayment_amount_unit="rupee"
    )
    [row] = aggregate_repayment_instalments(fin).repayment_table
    assert row.year == 3
    # Principal repaid excludes capitalised interest: 191,966.83 + 192,905.88.
    assert row.principal_due.value == 3.85
    # Interest paid = instalments - principal = 413,120 - 384,872.71.
    assert row.interest_due.value == 0.28
    assert row.principal_due.page == 12 and row.principal_due.status == "extracted"


def test_a_moratorium_year_with_no_repayment_gets_no_row():
    rows = [
        RepaymentInstalment(loan_year=1, interest=14_000.0, principal=-14_000.0)
        for _ in range(12)
    ]
    fin = ProjectFinancials(repayment_instalments=rows, repayment_amount_unit="rupee")
    assert aggregate_repayment_instalments(fin).repayment_table == []


def test_no_unit_means_no_aggregation_rather_than_a_guess():
    fin = ProjectFinancials(repayment_instalments=_monthly_year(3, 12))
    assert aggregate_repayment_instalments(fin).repayment_table == []


def test_a_stated_yearly_row_is_never_overwritten():
    stated = RepaymentTableRow(
        year=3,
        principal_due=ExtractedValue(value=9.99, page=5, status="extracted"),
        interest_due=ExtractedValue(value=1.11, page=5, status="extracted"),
    )
    fin = ProjectFinancials(
        repayment_table=[stated],
        repayment_instalments=_monthly_year(3, 12),
        repayment_amount_unit="rupee",
    )
    [row] = aggregate_repayment_instalments(fin).repayment_table
    assert row.principal_due.value == 9.99


def test_aggregated_rows_feed_dscr():
    fin = ProjectFinancials(
        years=[
            YearFinancials(
                year=3, net_profit=9.12, depreciation=3.54, interest_on_term_loan=1.63
            )
        ],
        repayment_instalments=_monthly_year(3, 12),
        repayment_amount_unit="rupee",
    )
    [result] = dscr_per_year(aggregate_repayment_instalments(fin))
    assert result.computed_value == round((9.12 + 3.54 + 1.63) / (3.85 + 0.28), 2)


def test_calendar_years_are_renumbered_in_order():
    fin = ProjectFinancials(
        years=[
            YearFinancials(year=y, net_profit=float(y - 2025))
            for y in (2027, 2026, 2028)
        ]
    )
    fixed = normalize_year_numbers(fin)
    assert {y.year: y.net_profit for y in fixed.years} == {1: 1.0, 2: 2.0, 3: 3.0}


def test_projection_years_are_left_alone():
    fin = ProjectFinancials(years=[YearFinancials(year=1), YearFinancials(year=2)])
    assert normalize_year_numbers(fin) is fin

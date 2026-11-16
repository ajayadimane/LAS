"""DSCR-statement rows read in code (yearly_rows.py). The layout is
LiteParse's output of a real DPR's DSCR page (figures only)."""

from types import SimpleNamespace

from app.services.dpr_agentic.financial_schema import ProjectFinancials, YearFinancials
from app.services.dpr_agentic.yearly_rows import apply_yearly_rows, parse_yearly_rows

PAGE = """PROJECTED PROFITABILITY AND DSCR
    Net Profit before Interest & Dep (D)   2.13  13.54   14.28   15.06
    Interest on Term Loan                  0.32   1.84    1.63    1.39
    Depreciaton (WDV)                      0.71   4.17    3.54    3.01
    Profit after Tax                       1.11   7.53    9.12   10.66
    (ii) Interest on Term Loan             0.32   1.84    1.63    1.39
    Cash available for Repayment           2.13  13.54  14.28    15.06
    TL Installment                         0.00   4.13    4.13    4.13
    D.S.C.R                                0.00   3.28    3.46    3.65
    Average D.S.C.R                        3.46   3.46    3.46    3.46
"""


def _pages(*texts):
    return [SimpleNamespace(page=i, text=t) for i, t in enumerate(texts, start=10)]


def test_reads_instalment_interest_and_stated_dscr():
    rows = parse_yearly_rows(_pages(PAGE))
    assert set(rows) == {"total_instalment", "interest_on_term_loan", "stated_dscr"}
    assert rows["total_instalment"].values == (0.0, 4.13, 4.13, 4.13)
    assert rows["stated_dscr"].values == (0.0, 3.28, 3.46, 3.65)
    assert rows["stated_dscr"].page == 10


def test_a_row_printed_twice_differently_is_dropped():
    other = PAGE.replace(
        "(ii) Interest on Term Loan             0.32   1.84",
        "(ii) Interest on Term Loan             0.32   1.94",
    )
    assert "interest_on_term_loan" not in parse_yearly_rows(_pages(other))


def test_a_trailing_total_column_is_ignored():
    page = "DSCR statement\nPrincipal repayment  1.00  2.00  3.00  6.00\n"
    assert parse_yearly_rows(_pages(page))["principal_repayment"].values == (1, 2, 3)


def test_pages_without_a_dscr_or_repayment_heading_are_skipped():
    pnl = "Profit and Loss\nInterest on Term Loan  0.32  1.84  1.63  1.39\n"
    assert parse_yearly_rows(_pages(pnl)) == {}


def test_code_rows_replace_the_models_and_clear_an_unprinted_principal():
    model = ProjectFinancials(
        years=[
            YearFinancials(
                year=2,
                net_profit=7.53,
                principal_repayment=4.13,
                interest_repayment=1.84,
                stated_dscr=3.2,
            )
        ]
    )
    fixed = apply_yearly_rows(model, parse_yearly_rows(_pages(PAGE)))
    y2 = next(y for y in fixed.years if y.year == 2)
    assert y2.principal_repayment is None and y2.total_instalment == 4.13
    assert y2.stated_dscr == 3.28 and y2.net_profit == 7.53
    assert [y.year for y in fixed.years] == [1, 2, 3, 4]


def test_rows_printed_in_rupees_are_converted_to_lakh():
    """Seen live: "Total instalment 4,06,680" read as 406680 lakh -> DSCR 0.0."""
    page = "DSCR statement\nTotal Instalment  4,06,680  8,13,360  8,13,360\n"
    fixed = apply_yearly_rows(ProjectFinancials(), parse_yearly_rows(_pages(page)))
    assert [y.total_instalment for y in fixed.years] == [4.07, 8.13, 8.13]


def test_an_instalment_row_equal_to_principal_is_principal_only():
    page = (
        "DSCR statement\nTotal Instalment  4.07  8.13  8.13\n"
        "Principal repayment  4.07  8.13  8.13\n"
    )
    fixed = apply_yearly_rows(ProjectFinancials(), parse_yearly_rows(_pages(page)))
    assert [y.principal_repayment for y in fixed.years] == [4.07, 8.13, 8.13]
    assert all(y.total_instalment is None for y in fixed.years)


def test_balance_sheet_totals_are_read_and_converted_from_rupees():
    page = SimpleNamespace(
        page=26,
        text=(
            "Annexure 5- Projected balance sheet\n"
            "   Total assets            57,27,219  53,57,190  51,06,809\n"
            "   Total liabilities       86,92,219  83,22,190  80,71,809\n"
            "   Current Liabilities     18,46,500  18,47,430  18,48,379\n"
            "   Total current assets     4,06,469   5,45,477   7,54,382\n"
        ),
    )
    rows = parse_yearly_rows([page])
    assert set(rows) == {"total_assets", "total_liabilities"}
    fin = apply_yearly_rows(ProjectFinancials(), rows)
    assert [(y.total_assets, y.total_liabilities) for y in fin.years] == [
        (57.27, 86.92),
        (53.57, 83.22),
        (51.07, 80.72),
    ]

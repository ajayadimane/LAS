"""Code-read monthly repayment schedules (schedule_parser.py). The layout
below is the real LiteParse output of a rejected harvester DPR's schedule,
with the borrower's details removed."""

from types import SimpleNamespace

import pytest

from app.services.dpr_agentic.financial_extraction import (
    SCHEDULE_ALREADY_READ,
    extract_financial_sections,
)
from app.services.dpr_agentic.financial_schema import ProjectFinancials
from app.services.dpr_agentic.schedule_parser import parse_instalment_schedule

YEAR_I = """                                        Repayment Schedule of Term Loan

Year - I

Month        Opening     Instalment      Interest      Principal     Closing Balance
         Balance

Apr-25             -                 -             -             -     -
May-25             -                 -             -             -     -
Jun-25             -                 -             -             -     -
Jul-25             -                 -             -             -     -
Aug-25             -                 -             -             -     -
Sep-25             -                 -             -             -     -
Oct-25             -                 -             -             -     -
Nov-25             -                 -             -             -     -
Dec-25             -                 -             -             -     -
Jan-26             -                 -             -             -     -
Feb-26   2,100,000.00                -    15,750.00     (15,750.00)   2,115,750.00
Mar-26   2,115,750.00                -    15,868.13     (15,868.13)   2,131,618.13

    Total in Rs.                     -    31,618.13     (31,618.13)
"""

YEAR_III = """Year - III

Month      Opening        Instalment  Interest    Principal     Closing Balance
           Balance

Apr-27    1,902,624.73     -           14,269.69   (14,269.69)   1,916,894.42
May-27    1,916,894.42     -           14,376.71   (14,376.71)   1,931,271.12
Jun-27    1,931,271.12     -           14,484.53   (14,484.53)   1,945,755.66
Jul-27    1,945,755.66    206,560.00   14,593.17   191,966.83    1,753,788.83
Aug-27    1,753,788.83     -           13,153.42   (13,153.42)   1,766,942.24
Sep-27    1,766,942.24     -           13,252.07   (13,252.07)   1,780,194.31
Oct-27    1,780,194.31     -           13,351.46   (13,351.46)   1,793,545.77
Nov-27    1,793,545.77     -           13,451.59   (13,451.59)   1,806,997.36
Dec-27    1,806,997.36     -           13,552.48   (13,552.48)   1,820,549.84
Jan-28    1,820,549.84    206,560.00   13,654.12   192,905.88    1,627,643.96
Feb-28    1,627,643.96     -           12,207.33   (12,207.33)   1,639,851.29
Mar-28    1,639,851.29     -           12,298.88   (12,298.88)   1,652,150.18

    Total in Rs.          413,120.00  162,645.45   250,474.55
"""


def _pages(*texts):
    return [SimpleNamespace(page=i, text=t) for i, t in enumerate(texts, start=11)]


def test_reads_a_monthly_rupee_schedule_with_year_headers():
    schedule = parse_instalment_schedule(_pages(YEAR_I, YEAR_III))
    assert schedule is not None
    assert schedule.unit == "rupee"
    assert schedule.pages == [11, 12]
    # Ten pre-disbursement dash rows are dropped; 2 + 12 remain.
    assert len(schedule.instalments) == 14
    assert {r.loan_year for r in schedule.instalments} == {1, 3}
    jul = next(r for r in schedule.instalments if r.period == "Jul-27")
    assert (jul.instalment, jul.interest, jul.principal) == (
        206560.0,
        14593.17,
        191966.83,
    )
    feb = next(r for r in schedule.instalments if r.period == "Feb-26")
    assert feb.principal == -15750.0 and feb.instalment is None


def test_a_continuation_page_keeps_the_previous_year():
    first, second = YEAR_III.split("Sep-27")
    schedule = parse_instalment_schedule(_pages(YEAR_I, first, "Sep-27" + second))
    assert {r.loan_year for r in schedule.instalments if r.period.endswith("27")} == {3}


def test_rows_that_do_not_roll_forward_reject_the_whole_table():
    broken = YEAR_III.replace("1,753,788.83", "1,953,788.83", 1)  # Jul closing
    broken = broken.replace("1,766,942.24", "1,966,942.24", 1)  # Aug closing
    assert parse_instalment_schedule(_pages(broken)) is None


def test_too_few_rows_is_not_a_schedule():
    assert parse_instalment_schedule(_pages(YEAR_III.split("Sep-27")[0])) is None


def test_pages_without_a_schedule_give_none():
    pnl = "Profit and Loss\nYear 1 2 3\nNet profit 1.11 7.53 9.12\n"
    assert parse_instalment_schedule(_pages(pnl)) is None


class _RecordingLLM:
    def __init__(self):
        self.prompts: dict[str, str] = {}

    async def extract_group(self, prompt, ctx, group):
        self.prompts[group] = prompt
        return ProjectFinancials()


@pytest.mark.asyncio
async def test_extraction_uses_the_code_read_schedule_and_tells_gemini_to_skip_it():
    pages = _pages(
        "Cost of Project 28.5 lakh. Means of Finance: term loan 21.0",
        YEAR_I,
        YEAR_III,
    )
    llm = _RecordingLLM()
    output, _ = await extract_financial_sections(llm, pages, {})
    assert SCHEDULE_ALREADY_READ in llm.prompts["repayment"]
    assert SCHEDULE_ALREADY_READ not in llm.prompts["cost_funding"]
    assert output.repayment_amount_unit == "rupee"
    [year3] = output.repayment_table  # year 1 is a moratorium year: no row
    assert year3.year == 3
    assert year3.principal_due.value == 3.85
    assert year3.interest_due.value == 0.28


@pytest.mark.asyncio
async def test_a_code_read_schedule_makes_the_repayment_retry_unnecessary():
    pages = _pages("Cost of Project 28.5 lakh. Means of Finance: term loan 21.0",
                   YEAR_I, YEAR_III)

    class Counting(_RecordingLLM):
        def __init__(self):
            super().__init__()
            self.groups = []

        async def extract_group(self, prompt, ctx, group):
            self.groups.append(group)
            return await super().extract_group(prompt, ctx, group)

    llm = Counting()
    await extract_financial_sections(llm, pages, {})
    assert llm.groups.count("repayment") == 1

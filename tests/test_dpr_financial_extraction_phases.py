"""financial_extraction.extract_financial_sections: concurrent phases,
deterministic retry selection, and an audit that reflects the merged result."""

import asyncio
from types import SimpleNamespace

import pytest

from app.services.dpr_agentic.financial_extraction import (
    RECOVERY_BUDGET,
    RETRY_SUFFIX,
    extract_financial_sections,
)
from app.services.dpr_agentic.financial_schema import (
    ExtractedValue,
    ProjectFinancials,
    YearFinancials,
)
from app.services.dpr_agentic.gemini import GeminiAgentError

# One page per domain, so every group has a recognised heading.
PAGES = [
    SimpleNamespace(
        page=1,
        text="Cost of Project: equipment 30 lakh. Means of Finance: term loan 22.5",
    ),
    SimpleNamespace(
        page=2, text="Repayment Schedule year 1 principal 3.21 interest 2.03"
    ),
    SimpleNamespace(page=3, text="Profit and Loss: net profit year 1 4.27"),
]

COMPLETE = {
    "cost_funding": ProjectFinancials(
        cost_of_project_stated_total=30.0,
        term_loan_amount=ExtractedValue(value=22.5, page=1, status="extracted"),
    ),
    "repayment": ProjectFinancials(
        years=[
            YearFinancials(year=1, principal_repayment=3.21, interest_repayment=2.03)
        ]
    ),
    "operating_financials": ProjectFinancials(
        years=[YearFinancials(year=1, net_profit=4.27, depreciation=4.5)]
    ),
}


class ScriptedLLM:
    """Answers each (group, is_retry) from a script; tracks concurrency."""

    def __init__(self, script):
        self.script = script
        self.calls: list[tuple[str, bool]] = []
        self.active = 0
        self.peak = 0

    async def extract_group(self, prompt, ctx, group):
        retry = prompt.endswith(RETRY_SUFFIX)
        self.calls.append((group, retry))
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        answer = self.script[(group, retry)]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.mark.asyncio
async def test_first_attempts_run_concurrently():
    llm = ScriptedLLM({(g, False): fin for g, fin in COMPLETE.items()})
    output, audit = await extract_financial_sections(llm, PAGES, {})
    assert llm.peak == 3
    assert audit.attempted == ("cost_funding", "repayment", "operating_financials")
    assert audit.empty == () and audit.failed == () and audit.recovered == ()
    assert output.cost_of_project_stated_total == 30.0
    [year] = output.years
    assert (year.principal_repayment, year.net_profit) == (3.21, 4.27)


@pytest.mark.asyncio
async def test_retries_go_to_the_first_incomplete_groups_in_fixed_order():
    script = {(g, False): ProjectFinancials() for g in COMPLETE}
    script.update({(g, True): ProjectFinancials() for g in COMPLETE})
    llm = ScriptedLLM(script)
    _, audit = await extract_financial_sections(llm, PAGES, {})
    retried = [g for g, retry in llm.calls if retry]
    assert len(retried) == RECOVERY_BUDGET == 2
    assert sorted(retried) == ["cost_funding", "repayment"]
    assert audit.empty == ("cost_funding", "repayment", "operating_financials")


@pytest.mark.asyncio
async def test_a_group_filled_by_its_retry_is_not_reported_empty():
    script = {(g, False): fin for g, fin in COMPLETE.items()}
    script[("repayment", False)] = ProjectFinancials()
    script[("repayment", True)] = COMPLETE["repayment"]
    _, audit = await extract_financial_sections(ScriptedLLM(script), PAGES, {})
    assert audit.recovered == ("repayment",)
    assert audit.empty == ()


@pytest.mark.asyncio
async def test_a_failed_call_is_recorded_and_others_still_merge():
    script = {(g, False): fin for g, fin in COMPLETE.items()}
    script[("repayment", False)] = GeminiAgentError("boom")
    output, audit = await extract_financial_sections(ScriptedLLM(script), PAGES, {})
    assert audit.failed == ("repayment",)
    assert output.cost_of_project_stated_total == 30.0
    assert output.years[0].net_profit == 4.27


@pytest.mark.asyncio
async def test_a_revenue_only_pnl_is_incomplete_and_retried():
    """Seen live: a run returned only revenue for the P&L, passed as
    complete, and the report silently had no DSCR."""
    from app.services.dpr_agentic.financial_schema import (
        financial_extraction_completeness,
    )

    script = {(g, False): fin for g, fin in COMPLETE.items()}
    script[("operating_financials", False)] = ProjectFinancials(
        years=[YearFinancials(year=1, revenue=21.6)]
    )
    script[("operating_financials", True)] = ProjectFinancials(
        years=[YearFinancials(year=1, revenue=21.6)]
    )
    llm = ScriptedLLM(script)
    output, audit = await extract_financial_sections(llm, PAGES, {})
    assert ("operating_financials", True) in llm.calls
    assert audit.empty == ("operating_financials",)
    assert (
        "profit_and_loss"
        in financial_extraction_completeness(output)["missing_sections"]
    )


@pytest.mark.asyncio
async def test_a_dscr_block_without_its_principal_row_is_retried_with_a_hint():
    """Seen live: stated DSCR and interest read, principal row missed; the
    group passed as complete and the report had no DSCR."""
    from app.services.dpr_agentic.financial_extraction import REPAYMENT_RETRY_HINT

    partial = ProjectFinancials(years=[YearFinancials(
        year=2, interest_repayment=1.84, stated_dscr=3.28)])
    full = ProjectFinancials(years=[YearFinancials(
        year=2, principal_repayment=4.13)])
    llm = ScriptedLLM({
        ("cost_funding", False): COMPLETE["cost_funding"],
        ("repayment", False): partial,
        ("repayment", True): full,
        ("operating_financials", False): COMPLETE["operating_financials"],
    })
    prompts = []
    original = llm.extract_group

    async def spy(prompt, ctx, group):
        prompts.append((group, prompt))
        return await original(prompt, ctx, group)

    llm.extract_group = spy
    output, audit = await extract_financial_sections(llm, PAGES, {})
    assert ("repayment", True) in llm.calls
    assert audit.recovered == ("repayment",) and audit.empty == ()
    retry_prompt = [p for g, p in prompts if g == "repayment"][-1]
    assert REPAYMENT_RETRY_HINT in retry_prompt
    year2 = next(y for y in output.years if y.year == 2)
    assert (year2.principal_repayment, year2.stated_dscr) == (4.13, 3.28)

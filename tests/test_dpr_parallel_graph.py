import asyncio
import hashlib

from app.services.dpr_agentic.schemas import AgentOutput, AnalysisInput
from app.services.dpr_agentic.upload_fanout import (
    can_finalize,
    execute_parallel_branches,
)


def test_parallel_upload_branches_start_independently():
    started = []

    async def wallet():
        started.append("wallet")
        await asyncio.sleep(0.05)
        return "wallet-ref"

    async def analysis():
        started.append("analysis")
        await asyncio.sleep(0.05)
        return "analysis-ref"

    w, a = asyncio.run(execute_parallel_branches(wallet, analysis))
    assert len(started) == 2
    assert can_finalize(w, a, document_identity_verified=True, access_authorized=True)
    assert not can_finalize(
        w, a, document_identity_verified=False, access_authorized=True
    )


def test_failed_wallet_branch_does_not_block_analysis():
    ran = []

    async def wallet():
        raise ConnectionError("secret")

    async def analysis():
        ran.append(True)
        return "analysis-ref"

    w, a = asyncio.run(execute_parallel_branches(wallet, analysis))
    assert not w.succeeded and a.succeeded and ran
    assert w.error == "ConnectionError"
    assert not can_finalize(
        w, a, document_identity_verified=True, access_authorized=True
    )


def test_agent_parallel_fanout_and_dependency_join():
    from app.services.dpr_agentic.parallel_graph import analyze_parallel

    active = set()
    maximum = [0]

    class FakeGemini:
        async def generate(self, prompt, context):
            from app.services.dpr_agentic.orchestrator import PROMPTS

            name = next(k for k, v in PROMPTS.items() if v == prompt)
            if name in ("finance", "sector", "research", "policy"):
                active.add(name)
                maximum[0] = max(maximum[0], len(active))
                await asyncio.sleep(0.02)
                active.remove(name)
            return AgentOutput(summary=name)

        async def extract(self, prompt, context):
            from app.services.dpr_agentic.financial_schema import ProjectFinancials

            return ProjectFinancials()

    payload = AnalysisInput(
        service_request_id="SR1",
        wallet_document_id="W1",
        document_sha256=hashlib.sha256(b"a").hexdigest(),
        pages=[{"page": 1, "text": "Project report sample data"}],
    )
    result = asyncio.run(analyze_parallel(payload, FakeGemini()))
    assert len(result.agent_outputs) == 10
    assert maximum[0] >= 2
    assert result.cam_engine_outputs is None


def test_analyze_parallel_cam_engine_flag_on_populates_outputs():
    from app.services.dpr_agentic.financial_schema import (
        ExtractedValue,
        ProjectFinancials,
        YearFinancials,
    )
    from app.services.dpr_agentic.parallel_graph import analyze_parallel

    class FakeGemini:
        async def generate(self, prompt, context):
            from app.services.dpr_agentic.orchestrator import PROMPTS

            name = next(k for k, v in PROMPTS.items() if v == prompt)
            return AgentOutput(summary=name)

        async def extract(self, prompt, context):
            return ProjectFinancials(
                term_loan_amount=ExtractedValue(value=10.0, page=1, status="extracted"),
                loan_tenor_years=ExtractedValue(value=5, page=1, status="extracted"),
                standard_interest_rate_pct=9.0,
                years=[YearFinancials(year=1, net_profit=2.0, depreciation=1.0)],
            )

    payload = AnalysisInput(
        service_request_id="SR2",
        wallet_document_id="W2",
        document_sha256=hashlib.sha256(b"b").hexdigest(),
        pages=[{"page": 1, "text": "Project report sample data"}],
    )
    result = asyncio.run(
        analyze_parallel(payload, FakeGemini(), cam_engine_enabled=True)
    )
    assert result.cam_engine_outputs is not None
    assert result.cam_engine_outputs["year1_principal_due"] == "2.00"

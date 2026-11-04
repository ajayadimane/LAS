import asyncio
import hashlib

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.dpr_agentic.financial_schema import ProjectFinancials
from app.services.dpr_agentic.orchestrator import PROMPTS, analyze, verify_evidence
from app.services.dpr_agentic.schemas import AgentOutput, AnalysisInput, Finding

PAYLOAD = {
    "service_request_id": "SR-1",
    "wallet_document_id": "W-1",
    "document_sha256": hashlib.sha256(b"sample").hexdigest(),
    "pages": [{"page": 1, "text": "Project cost is 80 lakh and loan is 60 lakh."}],
}


class FakeLLM:
    def __init__(self):
        self.calls = []

    async def generate(self, prompt, context):
        self.calls.append((prompt, context))
        return AgentOutput(
            summary="checked",
            findings=[
                Finding(
                    category="funding",
                    severity="medium",
                    title="Funding components need verification",
                    explanation="Additional funding details may be required",
                    evidence_page=1,
                    evidence_quote="Project cost is 80 lakh",
                    suggested_action="Provide complete means of finance",
                )
            ],
        )

    async def extract(self, prompt, context):
        return ProjectFinancials()


def test_orchestrator_runs_all_roles_and_verifies_quote():
    fake = FakeLLM()
    result = asyncio.run(analyze(AnalysisInput.model_validate(PAYLOAD), fake))
    assert len(fake.calls) == len(PROMPTS)
    assert result.findings[0].verification == "source_verified"
    assert result.wallet_document_id == "W-1"


def test_cam_engine_disabled_by_default_leaves_result_unchanged():
    """Settings.cam_engine_enabled defaults False; analyze() must behave
    exactly as before the engine existed unless the flag is explicitly
    passed True (milestone 2b)."""
    result = asyncio.run(analyze(AnalysisInput.model_validate(PAYLOAD), FakeLLM()))
    assert result.cam_engine_outputs is None


def test_cam_engine_enabled_populates_cam_engine_outputs():
    from app.services.dpr_agentic.financial_schema import (
        ExtractedValue,
        YearFinancials,
    )

    class LLMWithLoanTerms(FakeLLM):
        async def extract(self, prompt, context):
            return ProjectFinancials(
                term_loan_amount=ExtractedValue(value=10.0, page=1, status="extracted"),
                loan_tenor_years=ExtractedValue(value=5, page=1, status="extracted"),
                standard_interest_rate_pct=9.0,
                years=[YearFinancials(year=1, net_profit=2.0, depreciation=1.0)],
            )

    result = asyncio.run(
        analyze(
            AnalysisInput.model_validate(PAYLOAD),
            LLMWithLoanTerms(),
            cam_engine_enabled=True,
        )
    )
    assert result.cam_engine_outputs is not None
    assert result.cam_engine_outputs["year1_principal_due"] == "2.00"


def test_calculated_findings_from_extraction_are_prepended_and_code_verified():
    """A financial mismatch must surface as a Finding even if no narrative
    role happens to mention it -- it's built from extract(), not generate()."""

    class LLMWithMismatchedFinancials(FakeLLM):
        async def extract(self, prompt, context):
            return ProjectFinancials.model_validate(
                {
                    "cost_of_project_components": [{"label": "Land", "amount": 100}],
                    "cost_of_project_stated_total": 500,  # does not match the 100 above
                }
            )

    result = asyncio.run(
        analyze(AnalysisInput.model_validate(PAYLOAD), LLMWithMismatchedFinancials())
    )
    calculated = [f for f in result.findings if f.provenance == "calculated"]
    assert len(calculated) == 1
    assert calculated[0].verification == "source_verified"
    assert "does not reconcile" in calculated[0].title


def test_extraction_failure_degrades_gracefully():
    """A broken extraction call must not abort the rest of the analysis."""
    from app.services.dpr_agentic.gemini import GeminiAgentError

    class BrokenExtraction(FakeLLM):
        async def extract(self, prompt, context):
            raise GeminiAgentError("boom")

    result = asyncio.run(
        analyze(AnalysisInput.model_validate(PAYLOAD), BrokenExtraction())
    )
    assert not [f for f in result.findings if f.provenance == "calculated"]
    assert any("extraction failed" in q for q in result.unresolved_questions)
    # The rest of the pipeline still ran.
    assert result.status == "completed"


def test_false_source_quote_not_verified():
    item = AgentOutput(
        findings=[
            Finding(
                category="x",
                severity="high",
                title="t",
                explanation="e",
                evidence_page=1,
                evidence_quote="invented",
                suggested_action="review",
            )
        ]
    )
    assert (
        verify_evidence(item, {1: "other text"}).findings[0].verification
        == "unverified"
    )


def test_disabled_api_is_not_available():
    app = create_app(
        Settings(environment="test", context_path="", dpr_agentic_enabled=False)
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/internal/dpr-agentic/analyze", json=PAYLOAD)
        assert response.status_code == 404


def test_enabled_api_requires_secret():
    app = create_app(
        Settings(
            environment="test",
            context_path="",
            dpr_agentic_enabled=True,
            dpr_agentic_internal_token="test-only-token",
        )
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/internal/dpr-agentic/analyze", json=PAYLOAD)
        assert response.status_code == 403

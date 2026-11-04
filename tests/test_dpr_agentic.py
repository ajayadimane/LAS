import asyncio
import hashlib

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import dpr_analysis
from app.core.config import Settings
from app.main import create_app
from app.services.dpr_agentic.financial_schema import ProjectFinancials
from app.services.dpr_agentic.orchestrator import PROMPTS, analyze, verify_evidence
from app.services.dpr_agentic.schemas import AgentOutput, AnalysisInput, Finding


@pytest.fixture(autouse=True)
def _reset_internal_token_rejection_counter():
    """dpr_analysis._rejections_by_ip is module-level, shared process-wide
    state (see its own docstring on why: an in-process rate limiter) --
    without this, one test's rejections would bleed into the next test's
    count, since TestClient always reports the same client_ip."""
    dpr_analysis._rejections_by_ip.clear()
    yield
    dpr_analysis._rejections_by_ip.clear()


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
        response = client.post("/internal/dpr-agentic/analyze", json=PAYLOAD)
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
        response = client.post("/internal/dpr-agentic/analyze", json=PAYLOAD)
        assert response.status_code == 403


def test_internal_token_rotation_accepts_old_and_new_secret():
    """During a rotation, callers still using the old secret must not all
    be rejected at once -- dpr_agentic_internal_token_previous accepts it
    alongside the new dpr_agentic_internal_token."""
    app = create_app(
        Settings(
            environment="test",
            context_path="",
            dpr_agentic_enabled=True,
            dpr_agentic_internal_token="new-token",
            dpr_agentic_internal_token_previous="old-token",
            dpr_agentic_gemini_api_key=None,
        )
    )
    with TestClient(app) as client:
        for token in ("new-token", "old-token"):
            response = client.post(
                "/internal/dpr-agentic/analyze",
                json=PAYLOAD,
                headers={"x-internal-token": token},
            )
            # No Gemini key configured in this test -- 503, not 403/429,
            # is what proves the token check itself passed.
            assert response.status_code == 503, token


def test_internal_token_rejects_anything_else_even_with_a_previous_secret_set():
    app = create_app(
        Settings(
            environment="test",
            context_path="",
            dpr_agentic_enabled=True,
            dpr_agentic_internal_token="new-token",
            dpr_agentic_internal_token_previous="old-token",
            dpr_agentic_gemini_api_key=None,
        )
    )
    with TestClient(app) as client:
        response = client.post(
            "/internal/dpr-agentic/analyze",
            json=PAYLOAD,
            headers={"x-internal-token": "guessed-token"},
        )
        assert response.status_code == 403


def test_repeated_bad_internal_tokens_get_rate_limited():
    app = create_app(
        Settings(
            environment="test",
            context_path="",
            dpr_agentic_enabled=True,
            dpr_agentic_internal_token="test-only-token",
            dpr_agentic_internal_token_max_rejections=3,
            dpr_agentic_internal_token_rejection_window_seconds=60,
            dpr_agentic_gemini_api_key=None,
        )
    )
    with TestClient(app) as client:
        for _ in range(3):
            response = client.post(
                "/internal/dpr-agentic/analyze",
                json=PAYLOAD,
                headers={"x-internal-token": "wrong"},
            )
            assert response.status_code == 403

        # The 4th bad attempt in the window is rate-limited, not a plain
        # 403 -- and so would a *correct* token be, at this point.
        limited = client.post(
            "/internal/dpr-agentic/analyze",
            json=PAYLOAD,
            headers={"x-internal-token": "wrong"},
        )
        assert limited.status_code == 429

        still_limited_with_the_real_token = client.post(
            "/internal/dpr-agentic/analyze",
            json=PAYLOAD,
            headers={"x-internal-token": "test-only-token"},
        )
        assert still_limited_with_the_real_token.status_code == 429


def test_successful_calls_never_count_toward_the_rejection_limit():
    """A legitimate caller making many correct, frequent calls must never
    be rate-limited -- only recorded *rejections* count."""
    app = create_app(
        Settings(
            environment="test",
            context_path="",
            dpr_agentic_enabled=True,
            dpr_agentic_internal_token="test-only-token",
            dpr_agentic_internal_token_max_rejections=3,
            dpr_agentic_internal_token_rejection_window_seconds=60,
            dpr_agentic_gemini_api_key=None,
        )
    )
    with TestClient(app) as client:
        for _ in range(10):
            response = client.post(
                "/internal/dpr-agentic/analyze",
                json=PAYLOAD,
                headers={"x-internal-token": "test-only-token"},
            )
            # No Gemini key configured -- 503 proves the token check
            # passed every time, never 429.
            assert response.status_code == 503


def test_inspect_upload_shares_the_same_token_check_and_rate_limit():
    app = create_app(
        Settings(
            environment="test",
            context_path="",
            dpr_agentic_enabled=True,
            dpr_agentic_internal_token="test-only-token",
        )
    )
    with TestClient(app) as client:
        response = client.post(
            "/internal/dpr-agentic/inspect-upload"
            "?filename=a.pdf&wallet_document_id=W-1&service_request_id=SR-1",
            content=b"not a pdf",
            headers={"x-internal-token": "wrong"},
        )
        assert response.status_code == 403

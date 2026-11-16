"""An optional role's Gemini failure degrades to an explicit "unavailable"
output; a required role's failure still fails the run."""

import asyncio
import hashlib

import pytest

from app.services.dpr_agentic.financial_schema import ProjectFinancials
from app.services.dpr_agentic.gemini import GeminiAgentError
from app.services.dpr_agentic.orchestrator import OPTIONAL_ROLES, PROMPTS, analyze
from app.services.dpr_agentic.schemas import AgentOutput, AnalysisInput

PAYLOAD = AnalysisInput(
    service_request_id="SR-1",
    wallet_document_id="W-1",
    document_sha256=hashlib.sha256(b"x").hexdigest(),
    pages=[{"page": 1, "text": "Project cost is 80 lakh and loan is 60 lakh."}],
)


def _llm_failing(role_to_fail: str):
    class LLM:
        async def generate(self, prompt, context):
            role = next(k for k, v in PROMPTS.items() if prompt.startswith(v))
            if role == role_to_fail:
                raise GeminiAgentError("down")
            return AgentOutput(summary=role)

        async def extract(self, prompt, context):
            return ProjectFinancials(cost_of_project_stated_total=80.0)

    return LLM()


@pytest.mark.parametrize("role", ["planner", "sector", "correction"])
def test_optional_role_failure_still_produces_a_report(role):
    result = asyncio.run(analyze(PAYLOAD, _llm_failing(role)))
    assert result.status == "completed"
    assert "could not be completed" in result.agent_outputs[role].summary
    assert any(
        q.startswith(f"ROLE_UNAVAILABLE: {role}") for q in result.unresolved_questions
    )
    # Every other role still ran.
    assert set(result.agent_outputs) == set(PROMPTS)


@pytest.mark.parametrize("role", ["document", "finance", "risk", "verifier"])
def test_required_role_failure_fails_the_run(role):
    assert role not in OPTIONAL_ROLES
    with pytest.raises(GeminiAgentError):
        asyncio.run(analyze(PAYLOAD, _llm_failing(role)))

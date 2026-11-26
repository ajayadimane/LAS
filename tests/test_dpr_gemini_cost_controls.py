"""Offline Gemini REST contract tests; HTTP transport is mocked."""
import asyncio
import json

import httpx

from app.services.dpr_agentic.gemini import (
    CURRENT_ROLE, GeminiAgentClient, GeminiAgentError,
)


def _call(role, *, reason="STOP", level=None):
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            "candidates": [{"finishReason": reason, "content": {"parts": [{"text": "{}"}]}}],
            "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 10,
                              "totalTokenCount": 60, "thoughtsTokenCount": 10},
        })
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = GeminiAgentClient(http, "fake-key", "gemini-3.8-flash", thinking_level=level)
            tok = CURRENT_ROLE.set(role)
            try:
                return await client._call("Prompt", {"dpr": "dummy"}, {"type":"OBJECT","properties":{}})
            finally:
                CURRENT_ROLE.reset(tok)
    return captured, run


def test_document_uses_low_thinking():
    requests, run = _call("document")
    asyncio.run(run())
    assert requests[0]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "LOW"


def test_financial_extraction_uses_medium_thinking():
    requests, run = _call("financial_extraction")
    asyncio.run(run())
    assert requests[0]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "MEDIUM"


def test_override_is_respected():
    requests, run = _call("finance", level="high")
    asyncio.run(run())
    assert requests[0]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "HIGH"


def test_truncated_response_is_rejected():
    _, run = _call("finance", reason="MAX_TOKENS")
    try:
        asyncio.run(run())
    except GeminiAgentError:
        return
    raise AssertionError("Unexpectedly accepted truncated JSON")


def test_thinking_levels_follow_the_2026_11_17_ab():
    """Risk at LOW matched MEDIUM within run-to-run noise; sector at LOW
    turned capacity/running-cost issues into "ok", so it stays MEDIUM.
    Change these only with a new A/B on the eval set (scripts/dpr_eval.py)."""
    from app.services.dpr_agentic.gemini import _ROLE_THINKING_LEVEL

    assert _ROLE_THINKING_LEVEL["risk"] == "LOW"
    for role in ("finance", "sector", "verifier"):
        assert _ROLE_THINKING_LEVEL[role] == "MEDIUM"

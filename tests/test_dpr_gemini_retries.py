"""GeminiAgentClient retries: transient transport failures (429/5xx/timeouts)
and one retry for truncated or schema-invalid structured output. HTTP is
mocked; no network."""

import asyncio
import json

import httpx
import pytest

from app.services.dpr_agentic.gemini import GeminiAgentClient, GeminiAgentError

GOOD = {
    "candidates": [
        {
            "finishReason": "STOP",
            "content": {
                "parts": [
                    {
                        "text": json.dumps(
                            {
                                "summary": "ok",
                                "findings": [],
                                "unresolved_questions": [],
                            }
                        )
                    }
                ]
            },
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 1,
        "candidatesTokenCount": 1,
        "totalTokenCount": 2,
    },
}
TRUNCATED = {
    "candidates": [
        {"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": '{"summ'}]}}
    ]
}
INVALID = {
    "candidates": [
        {
            "finishReason": "STOP",
            "content": {"parts": [{"text": '{"findings": "not-a-list"}'}]},
        }
    ]
}


def _run(responses):
    """Serves `responses` in order (an httpx.Response, a JSON body for a 200,
    or an exception to raise); returns (result_or_exception, request_count)."""
    queue = list(responses)
    seen = []

    def handler(request):
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx.Response):
            return item
        return httpx.Response(200, json=item)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = GeminiAgentClient(http, "fake-key", "gemini-3.8-flash")
            client.transport_max_wait_seconds = 0
            try:
                return await client.generate("Prompt", {"dpr": "x"})
            except GeminiAgentError as exc:
                return exc

    return asyncio.run(go()), len(seen)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_status_is_retried_then_succeeds(status):
    result, calls = _run([httpx.Response(status), GOOD])
    assert calls == 2
    assert result.summary == "ok"


def test_timeout_is_retried_then_succeeds():
    result, calls = _run([httpx.ReadTimeout("slow"), GOOD])
    assert calls == 2
    assert result.summary == "ok"


def test_persistent_rate_limit_gives_up_after_three_attempts():
    result, calls = _run([httpx.Response(429)] * 3)
    assert calls == 3
    assert isinstance(result, GeminiAgentError)


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried(status):
    result, calls = _run([httpx.Response(status)])
    assert calls == 1
    assert isinstance(result, GeminiAgentError)


@pytest.mark.parametrize("bad", [TRUNCATED, INVALID])
def test_bad_structured_output_gets_one_retry(bad):
    result, calls = _run([bad, GOOD])
    assert calls == 2
    assert result.summary == "ok"


def test_bad_structured_output_twice_fails():
    result, calls = _run([TRUNCATED, TRUNCATED])
    assert calls == 2
    assert isinstance(result, GeminiAgentError)


def test_truncation_retry_doubles_the_output_cap_and_usage_still_counts():
    """Gemini 3 thinking tokens count against maxOutputTokens; resending the
    identical request truncated again on a real 8-year repayment schedule
    (3,930 of 4,096 tokens spent thinking). The retry must ask for more."""
    from app.services.dpr_agentic.gemini import CURRENT_ROLE, CURRENT_RUN_USAGE

    truncated = {
        **TRUNCATED,
        "usageMetadata": {"promptTokenCount": 100, "totalTokenCount": 4196},
    }
    caps = []
    responses = [truncated, GOOD]

    def handler(request):
        caps.append(json.loads(request.content)["generationConfig"]["maxOutputTokens"])
        return httpx.Response(200, json=responses.pop(0))

    async def go():
        usage: list[dict] = []
        usage_token = CURRENT_RUN_USAGE.set(usage)
        role_token = CURRENT_ROLE.set("financial_repayment")
        try:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as http:
                client = GeminiAgentClient(http, "fake-key", "gemini-3.8-flash")
                result = await client.generate("Prompt", {"dpr": "x"})
        finally:
            CURRENT_ROLE.reset(role_token)
            CURRENT_RUN_USAGE.reset(usage_token)
        return result, usage

    result, usage = asyncio.run(go())
    assert result.summary == "ok"
    # Role cap 4,096 + LOW thinking allowance 4,096, then doubled.
    # min(6144 client cap, 8192 repayment cap) + 4096 LOW thinking allowance.
    assert caps == [10240, 20480]
    # The truncated (but billed) call is in the usage log too.
    assert [u["finish_reason"] for u in usage] == ["MAX_TOKENS", "STOP"]
    assert usage[0]["total_tokens"] == 4196

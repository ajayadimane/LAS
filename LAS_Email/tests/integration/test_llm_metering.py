from decimal import Decimal

import pytest
from pydantic import BaseModel
from sqlalchemy import update

from las.common.db import SessionFactory
from las.common.deps import Deps
from las.common.interfaces import LLMAPIError, LLMResponse, LLMTimeoutError
from las.common.llm import Attribution, LLMFailed, Meter, PromptTemplate
from las.common.models import AiModelPricing, AiUsage
from tests.helpers import rows

PROMPT = PromptTemplate(name="t", version="v9", system="sys", user="hello {{x}}")


class Out(BaseModel):
    value: int


async def _set_price(db: SessionFactory, deps: Deps) -> None:
    async with db() as s:
        await s.execute(
            update(AiModelPricing)
            .where(AiModelPricing.model == deps.settings.llm_extraction_model)
            .values(
                input_per_mtok=Decimal("1"),
                output_per_mtok=Decimal("4"),
                cache_read_per_mtok=Decimal("0.25"),
            )
        )
        await s.commit()


async def test_every_attempt_is_metered_with_cost(deps: Deps, db: SessionFactory) -> None:
    await _set_price(db, deps)
    llm = deps.llm_client
    llm.provider = deps.settings.llm_provider_name  # type: ignore[misc]
    llm.queue += [
        LLMAPIError("503"),
        LLMResponse(text="not json", input_tokens=10, output_tokens=5),  # type: ignore[attr-defined]
        LLMResponse(
            text='{"value": 7}',
            input_tokens=1_000_000,
            output_tokens=500_000,
            cache_read_tokens=400_000,
            stop_reason="STOP",
            request_id="r-1",
        ),
    ]
    out = await deps.llm.extract(
        purpose="OTHER",
        prompt=PROMPT,
        variables={"x": "y"},
        output_model=Out,
        model=deps.settings.llm_extraction_model,
        attribution=Attribution(bd_id=1),
    )
    assert out.value.value == 7 and out.attempts == 3
    usage = sorted(await rows(db, AiUsage), key=lambda u: u.attempt_no)
    assert [u.status for u in usage] == ["API_ERROR", "VALIDATION_FAILED", "SUCCESS"]
    ok = usage[-1]
    assert (ok.input_tokens, ok.output_tokens, ok.cache_read_tokens) == (
        1_000_000,
        500_000,
        400_000,
    )
    assert ok.cost == Decimal("3.100000")  # 1*1 + 0.5*4 + 0.4*0.25
    assert ok.provider_request_id == "r-1" and ok.stop_reason == "STOP" and ok.bd_id == 1
    assert ok.prompt_template == "t" and ok.prompt_version == "v9" and len(ok.prompt_sha256) == 64
    assert ok.latency_ms is not None and ok.pricing_id is not None


async def test_unpriced_model_records_tokens_with_null_cost(deps: Deps, db: SessionFactory) -> None:
    deps.llm_client.push('{"value": 1}')  # type: ignore[attr-defined]
    await deps.llm.extract(
        purpose="OTHER",
        prompt=PROMPT,
        variables={},
        output_model=Out,
        model="unknown-model",
        attribution=Attribution(),
    )
    (u,) = await rows(db, AiUsage)
    assert u.cost is None and u.input_tokens == 100


async def test_timeout_then_give_up(deps: Deps, db: SessionFactory) -> None:
    deps.settings.llm_max_api_retries = 1
    deps.llm_client.queue += [LLMTimeoutError("t"), LLMTimeoutError("t")]  # type: ignore[attr-defined]
    with pytest.raises(LLMFailed):
        await deps.llm.extract(
            purpose="OTHER",
            prompt=PROMPT,
            variables={},
            output_model=Out,
            model="m",
            attribution=Attribution(),
        )
    assert [u.status for u in await rows(db, AiUsage)] == ["TIMEOUT", "TIMEOUT"]


async def test_metering_failure_never_raises(deps: Deps, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*a: object, **k: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(Meter, "_pricing", boom)
    deps.llm_client.push('{"value": 3}')  # type: ignore[attr-defined]
    out = await deps.llm.extract(
        purpose="OTHER",
        prompt=PROMPT,
        variables={},
        output_model=Out,
        model="m",
        attribution=Attribution(),
    )
    assert out.value.value == 3 and out.usage_ids == []

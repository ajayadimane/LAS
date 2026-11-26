"""The Gemini cost of a DPR analysis run (run_cost.py)."""

from decimal import Decimal
from types import SimpleNamespace

from app.services.dpr_agentic.run_cost import (
    GeminiPrices,
    price_rates,
    prices_from_settings,
    run_cost_usd,
    sum_usage,
    to_inr,
)

PRICES = GeminiPrices(
    input_per_mtok_usd=Decimal("0.75"),
    output_per_mtok_usd=Decimal("3.75"),
    cached_input_per_mtok_usd=Decimal("0.075"),
    search_per_1k_usd=Decimal("14"),
)


def _call(**kw):
    base = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "thinking_tokens": 0,
        "cached_tokens": 0,
        "search_queries": 0,
    }
    return base | kw


def test_usage_sums_every_call_and_tolerates_missing_keys():
    usage = sum_usage(
        [
            _call(prompt_tokens=100, completion_tokens=10, search_queries=2),
            {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55},
        ]
    )
    assert usage.gemini_calls == 2
    assert (usage.prompt_tokens, usage.completion_tokens) == (150, 15)
    assert (usage.thinking_tokens, usage.search_queries) == (0, 2)


def test_cost_bills_thinking_as_output_cache_at_its_rate_and_searches():
    usage = sum_usage(
        [
            _call(
                prompt_tokens=1_000_000,  # 200k of it from the cache
                cached_tokens=200_000,
                completion_tokens=100_000,
                thinking_tokens=100_000,
                search_queries=10,
            )
        ]
    )
    # 800k x 0.75 + 200k x 0.075 + 200k x 3.75 per 1M, + 10 x 14 / 1000
    # = 0.60 + 0.015 + 0.75 + 0.14
    assert run_cost_usd(usage, PRICES) == Decimal("1.505000")


def test_no_calls_cost_nothing():
    assert run_cost_usd(sum_usage([]), PRICES) == Decimal("0.000000")


def test_prices_need_all_four_rates():
    full = SimpleNamespace(
        dpr_gemini_price_input_per_mtok_usd=0.75,
        dpr_gemini_price_output_per_mtok_usd=3.75,
        dpr_gemini_price_cached_input_per_mtok_usd=0.075,
        dpr_gemini_price_search_per_1k_usd=14,
    )
    assert prices_from_settings(full) == PRICES
    assert price_rates(PRICES)["output_per_mtok_usd"] == "3.75"
    partial = SimpleNamespace(dpr_gemini_price_input_per_mtok_usd=0.75)
    assert prices_from_settings(partial) is None


def test_rupees_only_when_a_rate_is_set():
    assert to_inr(Decimal("1.5"), None) is None
    assert to_inr(Decimal("1.5"), 88.5) == Decimal("132.7500")


def test_usage_by_role_splits_tokens_cost_and_truncations_per_role():
    from app.services.dpr_agentic.run_cost import usage_by_role

    log = [
        {**_call(prompt_tokens=1000, completion_tokens=100, thinking_tokens=900),
         "role": "finance", "finish_reason": "MAX_TOKENS"},
        {**_call(prompt_tokens=1000, completion_tokens=500, thinking_tokens=500),
         "role": "finance", "finish_reason": "STOP"},
        {**_call(prompt_tokens=200, search_queries=4), "role": "policy",
         "finish_reason": "STOP"},
    ]
    out = usage_by_role(log, PRICES)
    assert list(out) == ["finance", "policy"]
    assert out["finance"]["calls"] == 2
    assert out["finance"]["truncated_calls"] == 1
    assert out["finance"]["thinking_tokens"] == 1400
    # 2,000 in x 0.75 + 2,000 out x 3.75, per million.
    assert out["finance"]["cost_usd"] == "0.009000"
    # 200 in x 0.75 / 1M + 4 searches x 14 / 1k.
    assert out["policy"]["cost_usd"] == "0.056150"
    assert "cost_usd" not in usage_by_role(log, None)["finance"]

"""What one DPR analysis run cost in Gemini charges, from the per-call usage
records gemini.py collects (CURRENT_RUN_USAGE). No I/O.

Gemini bills (per 1M tokens, rates from config -- they change over time,
so each run stores the cost at the rates it ran under):
- input: prompt tokens, less any served from the context cache, which are
  billed at the cache rate instead (promptTokenCount includes them);
- output: response tokens plus thinking tokens (thoughtsTokenCount is not
  part of candidatesTokenCount, but is billed as output);
- Google Search grounding: per search query, per 1,000 queries. Google's
  monthly free allowance is not netted off, so this is the cost at the
  paid rate (an upper bound while the allowance lasts).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

PER_MILLION = Decimal(1_000_000)
PER_THOUSAND = Decimal(1_000)


@dataclass(frozen=True)
class GeminiPrices:
    input_per_mtok_usd: Decimal
    output_per_mtok_usd: Decimal
    cached_input_per_mtok_usd: Decimal
    search_per_1k_usd: Decimal


@dataclass(frozen=True)
class RunUsage:
    gemini_calls: int
    prompt_tokens: int
    completion_tokens: int
    thinking_tokens: int
    cached_tokens: int
    total_tokens: int
    search_queries: int


def sum_usage(usage_log: list[dict]) -> RunUsage:
    def total(key: str) -> int:
        return sum(int(u.get(key) or 0) for u in usage_log)

    return RunUsage(
        gemini_calls=len(usage_log),
        prompt_tokens=total("prompt_tokens"),
        completion_tokens=total("completion_tokens"),
        thinking_tokens=total("thinking_tokens"),
        cached_tokens=total("cached_tokens"),
        total_tokens=total("total_tokens"),
        search_queries=total("search_queries"),
    )


def prices_from_settings(settings) -> GeminiPrices | None:
    """None unless every rate is configured: a partial price would store a
    cost that looks complete but is not."""
    rates = tuple(
        getattr(settings, name, None)
        for name in (
            "dpr_gemini_price_input_per_mtok_usd",
            "dpr_gemini_price_output_per_mtok_usd",
            "dpr_gemini_price_cached_input_per_mtok_usd",
            "dpr_gemini_price_search_per_1k_usd",
        )
    )
    if any(r is None for r in rates):
        return None
    return GeminiPrices(*(Decimal(str(r)) for r in rates))


def price_rates(prices: GeminiPrices) -> dict:
    """The rates a cost was computed at, for the run row (price_rates)."""
    return {k: str(v) for k, v in prices.__dict__.items()}


def run_cost_usd(usage: RunUsage, prices: GeminiPrices) -> Decimal:
    cached = min(usage.cached_tokens, usage.prompt_tokens)
    fresh_input = usage.prompt_tokens - cached
    output = usage.completion_tokens + usage.thinking_tokens
    cost = (
        Decimal(fresh_input) * prices.input_per_mtok_usd / PER_MILLION
        + Decimal(cached) * prices.cached_input_per_mtok_usd / PER_MILLION
        + Decimal(output) * prices.output_per_mtok_usd / PER_MILLION
        + Decimal(usage.search_queries) * prices.search_per_1k_usd / PER_THOUSAND
    )
    return cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def usage_by_role(
    usage_log: list[dict], prices: GeminiPrices | None
) -> dict[str, dict]:
    """{role: totals} for one run, cost included when rates are configured,
    for the run row (usage_by_role, migration 016). Token counts only."""
    by_role: dict[str, list[dict]] = {}
    for u in usage_log:
        by_role.setdefault(u.get("role") or "unknown", []).append(u)
    out: dict[str, dict] = {}
    for role, calls in sorted(by_role.items()):
        usage = sum_usage(calls)
        row = {
            "calls": usage.gemini_calls,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "thinking_tokens": usage.thinking_tokens,
            "cached_tokens": usage.cached_tokens,
            "search_queries": usage.search_queries,
            "truncated_calls": sum(
                1 for c in calls if c.get("finish_reason") not in (None, "STOP")
            ),
        }
        if prices is not None:
            row["cost_usd"] = str(run_cost_usd(usage, prices))
        out[role] = row
    return out


def to_inr(cost_usd: Decimal, usd_to_inr: float | None) -> Decimal | None:
    if usd_to_inr is None:
        return None
    return (cost_usd * Decimal(str(usd_to_inr))).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )

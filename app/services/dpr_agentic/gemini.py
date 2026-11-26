"""Gemini REST adapter; injected HTTP client makes agent tests offline and
deterministic."""

import base64
import json
import re
import time
from contextvars import ContextVar

import httpx
from pydantic import ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.core.logging import get_logger

from .financial_schema import ProjectFinancials
from .schemas import FINDING_CATEGORIES, AgentOutput

logger = get_logger(__name__)

# Set once per analysis run (scripts/dpr_worker.py's process_one) so every
# Gemini call made while processing it logs which run it belongs to -- no
# signature changes needed through orchestrator.py/parallel_graph.py/
# worker.py, since asyncio tasks copy the current context at creation time.
CURRENT_RUN_ID: ContextVar[str | None] = ContextVar("dpr_current_run_id", default=None)
# Same pattern, set around each role's call (parallel_graph.py's invoke(),
# orchestrator.py's extract_calculated_facts()) so the per-call token usage
# log line below can be attributed to a specific process/role, not just the
# run as a whole.
CURRENT_ROLE: ContextVar[str | None] = ContextVar("dpr_current_role", default=None)

# Gemini 3.8 Flash supports LOW/MEDIUM/HIGH; use LOW for mechanical
# transcription and planning, MEDIUM where reasoning/verification matters.
# Override with DPR_AGENTIC_THINKING_LEVEL when a deployment requires a
# uniform level. Unsupported models receive no new thinking-level parameter.
_ROLE_THINKING_LEVEL = {
    "vision_ocr": "LOW",
    "document": "LOW",
    "planner": "LOW",
    "financial_extraction": "MEDIUM",
    "financial_extraction_recovery": "LOW",
    "financial_cost_funding": "LOW",
    "financial_repayment": "LOW",
    "financial_operating_financials": "LOW",
    # Copy-as-printed extraction, like the groups above. Missing here, they
    # ran at MEDIUM with no cap: ~7k output tokens per call (seen live).
    "financial_unit_economics": "LOW",
    "financial_harvester": "LOW",
    "financial_polyhouse": "LOW",
    "financial_dairy": "LOW",
    "financial_cold_storage": "LOW",
    "financial_stated_ratios": "LOW",
    # Grounded search (grounding.py).
    "policy": "LOW",
    "research": "LOW",
    "finance": "MEDIUM",
    # A/B on the 15-DPR eval, 2026-11-17 (MEDIUM vs LOW, runs
    # 20261117T104112Z / 20261117T113700Z). Risk at LOW: checklist answers
    # agreed 90% with MEDIUM (unchanged roles agreed 88-93% run to run),
    # same findings, ~3.7k fewer output tokens per DPR -- kept. Sector at
    # LOW agreed only 80% and turned 7 capacity / running-cost "issue"
    # answers into "ok", with 24% fewer findings: more lenient exactly
    # where it matters, so sector stays MEDIUM.
    "sector": "MEDIUM",
    "risk": "LOW",
    "verifier": "MEDIUM",
    "correction": "LOW",
}
_ROLE_OUTPUT_CAP = {
    "vision_ocr": 4096,
    "document": 4096,
    "planner": 2048,
    "financial_cost_funding": 4096,
    # A long monthly schedule the code parser does not recognise is
    # transcribed row by row; at 4096 it ran out (seen live, 3 of 15 DPRs).
    "financial_repayment": 8192,
    "financial_operating_financials": 4096,
    "financial_unit_economics": 4096,
    "financial_harvester": 4096,
    "financial_polyhouse": 4096,
    "financial_dairy": 4096,
    "financial_cold_storage": 4096,
    "financial_stated_ratios": 2048,
    "policy": 4096,
    "research": 4096,
    # Sector and risk also answer a checklist; at 4096 a sector answer was
    # truncated and re-asked in full at double the cap (seen live).
    "sector": 6144,
    "risk": 6144,
    "correction": 4096,
}

# Set once per run (scripts/dpr_worker.py's process_one, alongside
# CURRENT_RUN_ID) to a fresh list. Every call below appends its usage record
# to it -- the same list object, since ContextVar.set() stores a reference
# and asyncio tasks copy the context (not the list) at creation time, so
# concurrent roles (parallel_graph.py) all append to one shared list. Left
# None outside of process_one() (e.g. ad hoc scripts, tests) so appending is
# opt-in, never required.
CURRENT_RUN_USAGE: ContextVar[list[dict] | None] = ContextVar(
    "dpr_current_run_usage", default=None
)


class GeminiAgentError(RuntimeError):
    pass


class GeminiTruncatedError(GeminiAgentError):
    """The response stopped before finishing (e.g. MAX_TOKENS). On Gemini 3,
    thinking tokens count against maxOutputTokens, so a run of long
    reasoning can leave almost nothing for the answer itself."""


class GeminiRunawayNumberError(GeminiAgentError):
    """The model got stuck writing one number -- seen live on a repayment
    schedule (eval 2026-11-25): `177589.00000000003000...` with zeros until
    the token cap, on both attempts (10k and 20k tokens). Retried at the
    same cap (a bigger one only buys more zeros) with RUNAWAY_RETRY_NOTE."""


# A structured call stops as soon as the model writes this many zeros in a
# row (Gemini's stopSequences), instead of running on to the token cap. No
# figure in a DPR has 30 consecutive zeros.
RUNAWAY_STOP_SEQUENCE = "0" * 30
# Any one digit repeated this often is a runaway number, whether the stop
# sequence cut it short or it ran into the cap.
_RUNAWAY_NUMBER = re.compile(r"(\d)\1{29,}")
RUNAWAY_RETRY_NOTE = (
    "\n\nYour previous answer got stuck repeating digits inside one number. "
    "Write every number in plain decimal form with at most 5 digits after "
    "the decimal point, and no trailing zeros."
)
# Set by _structured() for a retry after GeminiRunawayNumberError.
_SYSTEM_PROMPT_SUFFIX: ContextVar[str] = ContextVar(
    "dpr_system_prompt_suffix", default=""
)


def _is_runaway(text: str, finish_reason: str | None) -> bool:
    """True when a structured response was cut off inside a runaway number:
    the stop sequence fired (finish STOP, but the text ends mid-number and
    is not valid JSON), or the cap was hit with a long digit run."""
    if _RUNAWAY_NUMBER.search(text):
        return True
    if finish_reason not in (None, "STOP"):
        return False
    stripped = text.rstrip()
    if not stripped or not stripped[-1].isdigit():
        return False
    try:
        json.loads(stripped)
    except ValueError:
        return True
    return False


# Set by _structured() for a retry after GeminiTruncatedError, so the
# retried request asks for a larger output budget instead of repeating the
# identical request into the identical cap.
_OUTPUT_CAP_MULTIPLIER: ContextVar[int] = ContextVar(
    "dpr_output_cap_multiplier", default=1
)
# Upper bound for any single request's maxOutputTokens.
MAX_OUTPUT_TOKENS_CEILING = 32768
# Gemini 3 counts thinking tokens against maxOutputTokens, so the role caps
# (sized for the visible JSON answer) get this much extra per thinking
# level. Measured live 2026-11-12: at MEDIUM, 3.9k-7.8k thinking tokens on
# extraction calls, and 5 of 40 calls in one 4-DPR run truncated at 4,096.
# Only tokens actually generated are billed, so headroom costs nothing
# unless used.
_THINKING_ALLOWANCE = {"LOW": 4096, "MEDIUM": 8192, "HIGH": 16384}
# Roles whose thinking regularly overruns their level's allowance. Seen in
# the 15-DPR eval of 2026-11-13: finance truncated at 6,144 + 8,192 on 3 of
# 15 DPRs (22k-27k output billed for the pair), and the retry re-sent the
# whole prompt and re-did all the thinking. Starting higher avoids paying
# twice; unused headroom is not billed.
_ROLE_THINKING_ALLOWANCE = {"finance": 12288}


class GeminiTransportError(GeminiAgentError):
    """Every transport attempt failed. Not retried again at the content
    level: that would multiply attempts against a struggling API."""


class _TransientGeminiError(Exception):
    """A failure worth another attempt: rate limiting (429), a server-side
    error (5xx), or a timeout/connection failure. Anything else (400, 401,
    403, 404) will not get better by retrying and is raised at once."""


# Transport-level attempts per Gemini call (1 + 2 retries) and the cap on
# a single backoff wait. Exposed as class attributes on GeminiAgentClient so
# tests can drop the wait to zero.
TRANSPORT_ATTEMPTS = 3
TRANSPORT_MAX_WAIT_SECONDS = 20.0
# Structured-output attempts per role call (1 + 1 retry) for a truncated
# (non-STOP) or schema-invalid response.
CONTENT_ATTEMPTS = 2
_CONTENT_ERRORS = (KeyError, ValueError, IndexError, ValidationError, GeminiAgentError)


AGENT_OUTPUT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "document_facts": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "field": {
                        "type": "STRING",
                        "enum": [
                            "borrower_name",
                            "project_name",
                            "business_sector",
                            "business_subsector",
                            "project_location",
                            "project_purpose",
                            "scheme_claimed",
                            "bank_name",
                        ],
                    },
                    "value": {"type": "STRING"},
                    "page": {"type": "INTEGER"},
                    "quote": {"type": "STRING"},
                },
                "required": ["field", "value", "page", "quote"],
            },
        },
        "findings": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "category": {
                        "type": "STRING",
                        "enum": list(FINDING_CATEGORIES),
                    },
                    "severity": {
                        "type": "STRING",
                        "enum": ["critical", "high", "medium", "low", "info"],
                    },
                    "title": {"type": "STRING"},
                    "explanation": {"type": "STRING"},
                    "evidence_page": {"type": "INTEGER", "nullable": True},
                    "evidence_quote": {"type": "STRING", "nullable": True},
                    "suggested_action": {"type": "STRING"},
                    "why_it_matters": {"type": "STRING", "nullable": True},
                    "dpr_stated_value": {"type": "STRING", "nullable": True},
                    "should_be": {"type": "STRING", "nullable": True},
                },
                "required": [
                    "category",
                    "severity",
                    "title",
                    "explanation",
                    "suggested_action",
                ],
            },
        },
        "unresolved_questions": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["summary", "findings", "unresolved_questions"],
}

# Roles with a checklist (checklists.py) also answer it. Only they get the
# field: offered to every role, the planner, verifier and correction roles
# invented checklists of their own (seen live), costing output tokens.
CHECKLIST_OUTPUT_SCHEMA = {
    **AGENT_OUTPUT_SCHEMA,
    "properties": {
        **AGENT_OUTPUT_SCHEMA["properties"],
        "checks": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "check_id": {"type": "STRING"},
                    "status": {
                        "type": "STRING",
                        "enum": ["issue", "ok", "not_applicable", "cannot_tell"],
                    },
                    "explanation": {"type": "STRING"},
                    "evidence_page": {"type": "INTEGER", "nullable": True},
                    "evidence_quote": {"type": "STRING", "nullable": True},
                },
                "required": ["check_id", "status"],
            },
        },
    },
}

# The verifier's schema: the agent schema plus one verdict per candidate.
VERIFIER_OUTPUT_SCHEMA = {
    **AGENT_OUTPUT_SCHEMA,
    "properties": {
        **AGENT_OUTPUT_SCHEMA["properties"],
        "verdicts": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "finding_id": {"type": "STRING"},
                    "decision": {
                        "type": "STRING",
                        "enum": [
                            "keep",
                            "reject",
                            "merge",
                            "downgrade",
                            "needs_clarification",
                        ],
                    },
                    "reason": {"type": "STRING"},
                    "duplicate_of": {"type": "STRING", "nullable": True},
                    "new_severity": {
                        "type": "STRING",
                        "enum": ["critical", "high", "medium", "low", "info"],
                        "nullable": True,
                    },
                },
                "required": ["finding_id", "decision", "reason"],
            },
        },
    },
    "required": ["summary", "verdicts", "findings", "unresolved_questions"],
}

_AMOUNT = {"type": "NUMBER"}
_COMPONENT_SCHEMA = {
    "type": "OBJECT",
    "properties": {"label": {"type": "STRING"}, "amount": _AMOUNT},
    "required": ["label", "amount"],
}
_EV = {
    "type": "OBJECT",
    "properties": {
        "value": {"type": "NUMBER", "nullable": True},
        "page": {"type": "INTEGER", "nullable": True},
        "status": {"type": "STRING", "enum": ["extracted", "missing"]},
    },
    "required": [],
}
_CREW = {
    "type": "OBJECT",
    "properties": {
        "role": {"type": "STRING"},
        "count": {"type": "INTEGER"},
        "annual_wage_per_person": {"type": "NUMBER", "nullable": True},
        "page": {"type": "INTEGER", "nullable": True},
        "status": {"type": "STRING", "enum": ["extracted", "missing"]},
    },
    "required": ["role", "count"],
}
_HARVESTER_KEYS = (
    "acres_per_year",
    "acres_per_day",
    "operating_days_per_season",
    "seasons_per_year",
    "price_per_acre",
    "running_cost_per_acre_stated",
    "running_cost_per_acre_corrected",
    "fuel_litres_per_acre",
    "fuel_price_per_litre",
    "fuel_cost_stated",
    "crew_wage_total_p_and_l_stated",
)
FINANCIAL_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "cost_of_project_components": {"type": "ARRAY", "items": _COMPONENT_SCHEMA},
        "cost_of_project_stated_total": {"type": "NUMBER", "nullable": True},
        "means_of_finance_components": {"type": "ARRAY", "items": _COMPONENT_SCHEMA},
        "means_of_finance_stated_total": {"type": "NUMBER", "nullable": True},
        "standard_interest_rate_pct": {"type": "NUMBER", "nullable": True},
        "subvention_rate_pct": {"type": "NUMBER", "nullable": True},
        "effective_interest_rate_pct": {"type": "NUMBER", "nullable": True},
        "term_loan_amount": _EV,
        "loan_tenor_years": _EV,
        "loan_moratorium_years": _EV,
        "fund_flow_sources_total": _EV,
        "fund_flow_applications_total": _EV,
        "harvester_assumptions": {
            "type": "OBJECT",
            "nullable": True,
            "properties": {
                **{key: _EV for key in _HARVESTER_KEYS},
                "crew_structure": {"type": "ARRAY", "items": _CREW},
            },
        },
        "repayment_table": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "year": {"type": "INTEGER"},
                    "principal_due": _EV,
                    "interest_due": _EV,
                },
                "required": ["year"],
            },
        },
        "years": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "year": {"type": "INTEGER"},
                    **{
                        key: {"type": "NUMBER", "nullable": True}
                        for key in (
                            "revenue",
                            "operating_expenses",
                            "net_profit",
                            "depreciation",
                            "interest_on_term_loan",
                            "principal_repayment",
                            "interest_repayment",
                            "stated_dscr",
                            "profit_before_tax",
                            "tax_paid",
                        )
                    },
                },
                "required": ["year"],
            },
        },
    },
    "required": [],
}


# Small, domain-specific structured-output schemas. The former monolithic
# FINANCIAL_SCHEMA exposed dozens of optional keys; a valid `{}` cost ~15k
# tokens in one production run and left CAM fields empty.
FINANCIAL_GROUP_SCHEMAS = {
    "cost_funding": {
        "type": "OBJECT",
        "properties": {
            key: FINANCIAL_SCHEMA["properties"][key]
            for key in (
                "cost_of_project_components",
                "cost_of_project_stated_total",
                "means_of_finance_components",
                "means_of_finance_stated_total",
                "term_loan_amount",
                "loan_tenor_years",
                "loan_moratorium_years",
                # Feed cam_engine_bridge._fund_flow_finding; were only in the
                # retired monolithic schema, so never extracted in production.
                "fund_flow_sources_total",
                "fund_flow_applications_total",
            )
        },
        "required": [],
    },
    "repayment": {
        "type": "OBJECT",
        "properties": {
            key: FINANCIAL_SCHEMA["properties"][key]
            for key in (
                "repayment_table",
                "standard_interest_rate_pct",
                "subvention_rate_pct",
                "effective_interest_rate_pct",
            )
        }
        | {
            "years": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "year": {"type": "INTEGER"},
                        **{
                            key: {"type": "NUMBER", "nullable": True}
                            for key in (
                                "principal_repayment",
                                "interest_repayment",
                                "total_instalment",
                                "stated_dscr",
                            )
                        },
                    },
                    "required": ["year"],
                },
            },
            # A per-instalment schedule, transcribed as printed; summed into
            # yearly rows in code (financial_schema.aggregate_...()).
            "repayment_instalments": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "loan_year": {"type": "INTEGER"},
                        "period": {"type": "STRING", "nullable": True},
                        **{
                            key: {"type": "NUMBER", "nullable": True}
                            for key in (
                                "instalment",
                                "interest",
                                "principal",
                            )
                        },
                        "page": {"type": "INTEGER", "nullable": True},
                    },
                    "required": ["loan_year"],
                },
            },
            "repayment_amount_unit": {
                "type": "STRING",
                "enum": ["lakh", "rupee"],
                "nullable": True,
            },
        },
        "required": [],
    },
    "operating_financials": {
        "type": "OBJECT",
        "properties": {
            "years": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "year": {"type": "INTEGER"},
                        **{
                            key: {"type": "NUMBER", "nullable": True}
                            for key in (
                                "revenue",
                                "operating_expenses",
                                "net_profit",
                                "depreciation",
                                "interest_on_term_loan",
                                "profit_before_tax",
                                "tax_paid",
                            )
                        },
                    },
                    "required": ["year"],
                },
            },
        },
        "required": [],
    },
}

_NULLABLE_NUMBER = {"type": "NUMBER", "nullable": True}
FINANCIAL_GROUP_SCHEMAS["unit_economics"] = {
    "type": "OBJECT",
    "properties": {
        "unit_economics": {
            "type": "OBJECT",
            "nullable": True,
            "properties": {
                "unit_name": {"type": "STRING", "nullable": True},
                **{
                    key: _EV
                    for key in (
                        "capacity_per_day",
                        "operating_days_per_year",
                        "capacity_per_year",
                        "price_per_unit_rupees",
                        "variable_cost_per_unit_rupees",
                    )
                },
                "years": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "year": {"type": "INTEGER"},
                            "utilisation_pct": _NULLABLE_NUMBER,
                            "units_sold": _NULLABLE_NUMBER,
                            "page": {"type": "INTEGER", "nullable": True},
                        },
                        "required": ["year"],
                    },
                },
            },
        },
    },
    "required": [],
}
# Only planned for a combine-harvester DPR (financial_extraction.py); feeds
# the existing fuel / crew / acreage checks in cam_engine_bridge.py.
FINANCIAL_GROUP_SCHEMAS["harvester"] = {
    "type": "OBJECT",
    "properties": {
        "harvester_assumptions": FINANCIAL_SCHEMA["properties"][
            "harvester_assumptions"
        ],
    },
    "required": [],
}

# Only planned for a polyhouse / greenhouse DPR (financial_extraction.py);
# feeds benchmarks.py's structure-cost, yield and price checks.
FINANCIAL_GROUP_SCHEMAS["polyhouse"] = {
    "type": "OBJECT",
    "properties": {
        "polyhouse_assumptions": {
            "type": "OBJECT",
            "nullable": True,
            "properties": {
                "structure_type": {"type": "STRING", "nullable": True},
                "crop": {"type": "STRING", "nullable": True},
                "produce_unit": {"type": "STRING", "nullable": True},
                **{
                    key: _EV
                    for key in (
                        "area_sq_m",
                        "structure_cost_lakh",
                        "plants",
                        "yield_per_sq_m_per_year",
                        "yield_per_plant_per_year",
                        "units_per_year",
                        "price_per_unit_rupees",
                    )
                },
            },
        },
    },
    "required": [],
}


def _sector_schema(name: str, strings: tuple[str, ...], values: tuple[str, ...]):
    return {
        "type": "OBJECT",
        "properties": {
            name: {
                "type": "OBJECT",
                "nullable": True,
                "properties": {
                    **{k: {"type": "STRING", "nullable": True} for k in strings},
                    **{k: _EV for k in values},
                },
            },
        },
        "required": [],
    }


# Only planned for a dairy / cold-storage DPR (financial_extraction.py);
# feed benchmarks.py's checks.
FINANCIAL_GROUP_SCHEMAS["dairy"] = _sector_schema(
    "dairy_assumptions",
    ("animal_type",),
    (
        "animals",
        "milk_yield_litres_per_day",
        "lactation_days",
        "milk_per_year_litres",
        "milk_price_per_litre",
        "feed_cost_per_animal_per_day",
    ),
)
FINANCIAL_GROUP_SCHEMAS["cold_storage"] = _sector_schema(
    "cold_storage_assumptions",
    ("storage_type", "commodity", "rent_basis"),
    (
        "capacity_mt",
        "capital_cost_lakh",
        "chamber_volume_cubic_m",
        "rent_per_quintal_rupees",
    ),
)

# Planned when the DPR has a ratio, DSCR or assumptions page
# (financial_extraction.py); the CAM's recomputed-figures page prints these
# next to financial_ratios.py's own figures.
FINANCIAL_GROUP_SCHEMAS["stated_ratios"] = _sector_schema(
    "stated_ratios",
    ("break_even_basis", "depreciation_method"),
    (
        "irr_pct",
        "average_dscr",
        "break_even_pct",
        "debt_equity",
        "fixed_asset_coverage",
        "payback_years",
        "price_escalation_pct",
        "cost_escalation_pct",
        "depreciation_rate_pct",
    ),
)

OCR_SCHEMA = {
    "type": "OBJECT",
    "properties": {"text": {"type": "STRING"}},
    "required": ["text"],
}
OCR_SYSTEM_PROMPT = (
    "You are an OCR engine. Transcribe every word of visible text on this page, "
    "exactly as written, in reading order. Never summarize, translate, interpret, "
    "or add text that is not visibly printed on the page. If the page is blank or "
    "illegible, return an empty string."
)


class GeminiAgentClient:
    transport_attempts = TRANSPORT_ATTEMPTS
    transport_max_wait_seconds = TRANSPORT_MAX_WAIT_SECONDS
    content_attempts = CONTENT_ATTEMPTS

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        model: str,
        thinking_budget: int = 512,
        max_output_tokens: int = 6144,
        thinking_level: str | None = None,
    ):
        self.client, self.api_key, self.model = client, api_key, model
        self.thinking_budget = thinking_budget
        self.max_output_tokens = max_output_tokens
        self.thinking_level = thinking_level.upper() if thinking_level else None
        if self.thinking_level and self.thinking_level not in ("LOW", "MEDIUM", "HIGH"):
            raise ValueError("thinking_level must be low, medium or high")

    async def _call_with_parts(
        self,
        system_prompt: str,
        parts: list[dict],
        response_schema: dict | None,
        *,
        tools: list[dict] | None = None,
        candidate_out: dict | None = None,
        stop_runaway_numbers: bool = True,
    ) -> str:
        """`response_schema` None: plain text (a grounded search call --
        structured output is not combined with the search tool). `tools`:
        e.g. [{"google_search": {}}]. `candidate_out` receives the raw
        candidate (for its groundingMetadata). `stop_runaway_numbers`: cut a
        structured answer off at RUNAWAY_STOP_SEQUENCE (off for OCR, which
        must copy a page exactly)."""
        system_prompt += _SYSTEM_PROMPT_SUFFIX.get()
        request = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"maxOutputTokens": self._output_cap()},
        }
        if response_schema is not None:
            request["generationConfig"]["responseMimeType"] = "application/json"
            request["generationConfig"]["responseSchema"] = response_schema
            if stop_runaway_numbers:
                request["generationConfig"]["stopSequences"] = [RUNAWAY_STOP_SEQUENCE]
        if tools:
            request["tools"] = tools
        if self.model.startswith("gemini-3"):
            selected_level = self._thinking_level()
            request["generationConfig"]["thinkingConfig"] = {
                "thinkingLevel": selected_level
            }
        elif self.model.startswith("gemini-2.5"):
            request["generationConfig"]["thinkingConfig"] = {
                "thinkingBudget": self.thinking_budget
            }
        start = time.monotonic()
        context_bytes = sum(
            len(str(part.get("text", "")).encode("utf-8")) for part in parts
        )
        response = await self._post_with_retries(request)
        duration_ms = round((time.monotonic() - start) * 1000)
        result = response.json()
        candidate = (result.get("candidates") or [{}])[0]
        finish_reason = candidate.get("finishReason")
        # Usage is recorded before the finish check: a truncated response is
        # still billed, so it must count toward the run's tokens and cost.
        # Token counts only, never content: the basis for per-model cost
        # tracking without logging anything from the document or the model.
        usage = result.get("usageMetadata", {})
        # Grounded calls are also billed per search query.
        search_queries = len(
            (candidate.get("groundingMetadata") or {}).get("webSearchQueries") or []
        )
        logger.info(
            "dpr_gemini_call run_id=%s role=%s model=%s prompt_tokens=%s "
            "completion_tokens=%s total_tokens=%s thinking_tokens=%s cached_tokens=%s "
            "finish_reason=%s input_context_bytes=%s duration_ms=%s",
            CURRENT_RUN_ID.get(),
            CURRENT_ROLE.get() or "unknown",
            self.model,
            usage.get("promptTokenCount"),
            usage.get("candidatesTokenCount"),
            usage.get("totalTokenCount"),
            usage.get("thoughtsTokenCount"),
            usage.get("cachedContentTokenCount"),
            finish_reason,
            context_bytes,
            duration_ms,
        )
        usage_log = CURRENT_RUN_USAGE.get()
        if usage_log is not None:
            usage_log.append(
                {
                    "role": CURRENT_ROLE.get() or "unknown",
                    "prompt_tokens": usage.get("promptTokenCount") or 0,
                    "completion_tokens": usage.get("candidatesTokenCount") or 0,
                    "total_tokens": usage.get("totalTokenCount") or 0,
                    "thinking_tokens": usage.get("thoughtsTokenCount") or 0,
                    "cached_tokens": usage.get("cachedContentTokenCount") or 0,
                    "finish_reason": finish_reason,
                    "input_context_bytes": context_bytes,
                    "duration_ms": duration_ms,
                    "search_queries": search_queries,
                }
            )
        text = "".join(
            p.get("text", "")
            for p in (candidate.get("content") or {}).get("parts") or []
        )
        if (
            response_schema is not None
            and stop_runaway_numbers
            and _is_runaway(text, finish_reason)
        ):
            raise GeminiRunawayNumberError(
                f"Gemini got stuck in a runaway number: {finish_reason}"
            )
        # Stop incomplete/truncated structured JSON before it silently
        # propagates into the CAM financial snapshot.
        if finish_reason and finish_reason != "STOP":
            raise GeminiTruncatedError(
                f"Gemini did not finish structured output: {finish_reason}"
            )
        if candidate_out is not None:
            candidate_out.update(candidate)
        return "".join(
            p.get("text", "") for p in result["candidates"][0]["content"]["parts"]
        )

    def _thinking_level(self) -> str:
        return self.thinking_level or _ROLE_THINKING_LEVEL.get(
            CURRENT_ROLE.get() or "", "MEDIUM"
        )

    def _output_cap(self) -> int:
        """Visible-answer cap for the role, plus (on Gemini 3) room for
        thinking at the level in effect; doubled on a truncation retry."""
        visible = min(
            self.max_output_tokens,
            _ROLE_OUTPUT_CAP.get(CURRENT_ROLE.get() or "", self.max_output_tokens),
        )
        if self.model.startswith("gemini-3"):
            level_allowance = _THINKING_ALLOWANCE.get(self._thinking_level(), 8192)
            visible += max(
                level_allowance,
                _ROLE_THINKING_ALLOWANCE.get(CURRENT_ROLE.get() or "", 0),
            )
        return min(visible * _OUTPUT_CAP_MULTIPLIER.get(), MAX_OUTPUT_TOKENS_CEILING)

    async def _post_with_retries(self, request: dict) -> httpx.Response:
        """POSTs one generateContent request, retrying only transient
        failures (429, 5xx, timeouts, connection errors) with randomized
        exponential backoff. Gemini sends no Retry-After we could honour.
        Retrying is safe: generateContent has no side effects."""
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self.transport_attempts),
            wait=wait_random_exponential(
                multiplier=1, max=self.transport_max_wait_seconds
            ),
            retry=retry_if_exception_type(_TransientGeminiError),
            reraise=True,
        )
        try:
            async for attempt in retrying:
                with attempt:
                    try:
                        response = await self.client.post(
                            url,
                            headers={"x-goog-api-key": self.api_key},
                            json=request,
                            timeout=120.0,
                        )
                    except (httpx.TimeoutException, httpx.TransportError) as exc:
                        self._log_retryable(attempt, type(exc).__name__)
                        raise _TransientGeminiError(type(exc).__name__) from exc
                    if response.status_code == 429 or response.status_code >= 500:
                        self._log_retryable(attempt, f"http_{response.status_code}")
                        raise _TransientGeminiError(f"http_{response.status_code}")
                    response.raise_for_status()
                    return response
        except _TransientGeminiError as exc:
            raise GeminiTransportError(
                f"Gemini call failed after {self.transport_attempts} attempts: {exc}"
            ) from exc
        raise AssertionError("unreachable")  # pragma: no cover

    def _log_retryable(self, attempt, reason: str) -> None:
        logger.warning(
            "dpr_gemini_transient_error run_id=%s role=%s model=%s attempt=%s "
            "reason=%s",
            CURRENT_RUN_ID.get(),
            CURRENT_ROLE.get() or "unknown",
            self.model,
            attempt.retry_state.attempt_number,
            reason,
        )

    async def _structured(self, call, parse):
        """Runs `call()` then `parse(text)`, retrying once when the model's
        output is truncated (non-STOP) or does not validate against the
        schema -- both are usually transient for the same prompt. Transport
        failures were already retried inside _post_with_retries."""
        last_exc: Exception | None = None
        multiplier = 1
        suffix = ""
        for attempt in range(1, self.content_attempts + 1):
            token = _OUTPUT_CAP_MULTIPLIER.set(multiplier)
            suffix_token = _SYSTEM_PROMPT_SUFFIX.set(suffix)
            try:
                return parse(await call())
            except (httpx.HTTPError, GeminiTransportError):
                raise
            except _CONTENT_ERRORS as exc:
                last_exc = exc
                if isinstance(exc, GeminiRunawayNumberError):
                    # Same cap: more room only means more repeated digits.
                    suffix = RUNAWAY_RETRY_NOTE
                elif isinstance(exc, GeminiTruncatedError):
                    # Same request into the same cap would truncate again.
                    multiplier *= 2
                if attempt < self.content_attempts:
                    logger.warning(
                        "dpr_gemini_invalid_output_retry run_id=%s role=%s "
                        "attempt=%s error=%s next_output_cap_multiplier=%s",
                        CURRENT_RUN_ID.get(),
                        CURRENT_ROLE.get() or "unknown",
                        attempt,
                        # Class name only for parse errors: a ValidationError
                        # message can quote model output.
                        str(exc)
                        if isinstance(
                            exc, (GeminiTruncatedError, GeminiRunawayNumberError)
                        )
                        else type(exc).__name__,
                        multiplier,
                    )
            finally:
                _SYSTEM_PROMPT_SUFFIX.reset(suffix_token)
                _OUTPUT_CAP_MULTIPLIER.reset(token)
        assert last_exc is not None
        raise last_exc

    async def _call(
        self, system_prompt: str, context: dict, response_schema: dict
    ) -> str:
        return await self._call_with_parts(
            system_prompt,
            [{"text": json.dumps(context, ensure_ascii=False)}],
            response_schema,
        )

    async def generate(self, system_prompt: str, context: dict) -> AgentOutput:
        try:
            return await self._structured(
                lambda: self._call(
                    system_prompt,
                    context,
                    CHECKLIST_OUTPUT_SCHEMA
                    if "checklist" in context
                    else AGENT_OUTPUT_SCHEMA,
                ),
                AgentOutput.model_validate_json,
            )
        except (
            httpx.HTTPError,
            KeyError,
            ValueError,
            IndexError,
            ValidationError,
            GeminiAgentError,
        ) as exc:
            raise GeminiAgentError(
                "Gemini agent invocation failed or returned invalid structured output"
            ) from exc

    async def grounded_search(self, system_prompt: str, brief: dict) -> dict:
        """One Gemini call with the Google Search tool. Returns {"text",
        "grounding"} -- the answer and its groundingMetadata (which source
        chunks support which text segments). grounding.py decides what of
        it is kept."""

        async def call() -> dict:
            candidate: dict = {}
            text = await self._call_with_parts(
                system_prompt,
                [{"text": json.dumps(brief, ensure_ascii=False)}],
                None,
                tools=[{"google_search": {}}],
                candidate_out=candidate,
            )
            if not text.strip():
                raise GeminiAgentError("Grounded search returned no text")
            return {"text": text, "grounding": candidate.get("groundingMetadata") or {}}

        try:
            return await self._structured(call, lambda value: value)
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
            raise GeminiAgentError("Grounded search failed") from exc

    async def judge(self, system_prompt: str, context: dict) -> AgentOutput:
        """The verifier's call: an AgentOutput whose `verdicts` judge the
        candidate findings by id (applied by finding_judge.apply_verdicts)."""
        try:
            return await self._structured(
                lambda: self._call(system_prompt, context, VERIFIER_OUTPUT_SCHEMA),
                AgentOutput.model_validate_json,
            )
        except (
            httpx.HTTPError,
            KeyError,
            ValueError,
            IndexError,
            ValidationError,
            GeminiAgentError,
        ) as exc:
            raise GeminiAgentError(
                "Gemini verifier invocation failed or returned invalid structured output"
            ) from exc

    async def extract(self, system_prompt: str, context: dict) -> ProjectFinancials:
        """Structured financial-figure extraction -- a different shape and
        system prompt from generate(), never narrative findings."""
        try:
            return await self._structured(
                lambda: self._call(system_prompt, context, FINANCIAL_SCHEMA),
                ProjectFinancials.model_validate_json,
            )
        except (
            httpx.HTTPError,
            KeyError,
            ValueError,
            IndexError,
            ValidationError,
            GeminiAgentError,
        ) as exc:
            raise GeminiAgentError(
                "Gemini financial extraction failed or returned invalid "
                "structured output"
            ) from exc

    async def extract_group(
        self, system_prompt: str, context: dict, group: str
    ) -> ProjectFinancials:
        """Narrow financial extraction: validate only the selected source domain.

        Using the original monolithic response schema for a targeted retry
        repeated the empty-extraction problem. Return a partial typed model,
        then merge by year using financial_extraction.merge_section_financials.
        """
        if group not in FINANCIAL_GROUP_SCHEMAS:
            raise ValueError(f"Unsupported financial extraction domain: {group}")
        try:
            return await self._structured(
                lambda: self._call(
                    system_prompt, context, FINANCIAL_GROUP_SCHEMAS[group]
                ),
                ProjectFinancials.model_validate_json,
            )
        except (
            httpx.HTTPError,
            KeyError,
            ValueError,
            IndexError,
            ValidationError,
            GeminiAgentError,
        ) as exc:
            raise GeminiAgentError(
                f"Gemini {group} extraction returned invalid output"
            ) from exc

    async def ocr_page(self, image_bytes: bytes, mime_type: str = "image/png") -> str:
        """Literal transcription of one page image -- the vision fallback for
        a scanned document (document_pipeline/vision.py). Never analysis."""
        try:
            part = {
                "inline_data": {
                    "mime_type": mime_type,
                    "data": base64.b64encode(image_bytes).decode(),
                }
            }
            return await self._structured(
                lambda: self._call_with_parts(
                    OCR_SYSTEM_PROMPT, [part], OCR_SCHEMA, stop_runaway_numbers=False
                ),
                lambda text: json.loads(text).get("text", ""),
            )
        except (
            httpx.HTTPError,
            KeyError,
            ValueError,
            IndexError,
            GeminiAgentError,
        ) as exc:
            raise GeminiAgentError(
                "Gemini OCR failed or returned invalid structured output"
            ) from exc

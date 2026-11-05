"""Gemini REST adapter; injected HTTP client makes agent tests offline and
deterministic."""

import base64
import json
from contextvars import ContextVar

import httpx
from pydantic import ValidationError

from app.core.logging import get_logger

from .financial_schema import ProjectFinancials
from .schemas import AgentOutput

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


class GeminiAgentError(RuntimeError):
    pass


AGENT_OUTPUT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "findings": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "category": {"type": "STRING"},
                    "severity": {
                        "type": "STRING",
                        "enum": ["critical", "high", "medium", "low", "info"],
                    },
                    "title": {"type": "STRING"},
                    "explanation": {"type": "STRING"},
                    "evidence_page": {"type": "INTEGER", "nullable": True},
                    "evidence_quote": {"type": "STRING", "nullable": True},
                    "suggested_action": {"type": "STRING"},
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

_AMOUNT = {"type": "NUMBER"}
_COMPONENT_SCHEMA = {
    "type": "OBJECT",
    "properties": {"label": {"type": "STRING"}, "amount": _AMOUNT},
    "required": ["label", "amount"],
}
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
        "years": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "year": {"type": "INTEGER"},
                    "net_profit": {"type": "NUMBER", "nullable": True},
                    "depreciation": {"type": "NUMBER", "nullable": True},
                    "interest_on_term_loan": {"type": "NUMBER", "nullable": True},
                    "principal_repayment": {"type": "NUMBER", "nullable": True},
                    "interest_repayment": {"type": "NUMBER", "nullable": True},
                    "stated_dscr": {"type": "NUMBER", "nullable": True},
                },
                "required": ["year"],
            },
        },
    },
    "required": [],
}

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
    def __init__(self, client: httpx.AsyncClient, api_key: str, model: str):
        self.client, self.api_key, self.model = client, api_key, model

    async def _call_with_parts(
        self, system_prompt: str, parts: list[dict], response_schema: dict
    ) -> str:
        request = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": response_schema,
            },
        }
        response = await self.client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.api_key},
            json=request,
            timeout=120.0,
        )
        response.raise_for_status()
        result = response.json()
        # Token counts only, never content: the basis for per-model cost
        # tracking without logging anything from the document or the model.
        usage = result.get("usageMetadata", {})
        logger.info(
            "dpr_gemini_call run_id=%s role=%s model=%s prompt_tokens=%s "
            "completion_tokens=%s total_tokens=%s",
            CURRENT_RUN_ID.get(),
            CURRENT_ROLE.get() or "unknown",
            self.model,
            usage.get("promptTokenCount"),
            usage.get("candidatesTokenCount"),
            usage.get("totalTokenCount"),
        )
        return "".join(
            p.get("text", "") for p in result["candidates"][0]["content"]["parts"]
        )

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
            text = await self._call(system_prompt, context, AGENT_OUTPUT_SCHEMA)
            return AgentOutput.model_validate_json(text)
        except (
            httpx.HTTPError,
            KeyError,
            ValueError,
            IndexError,
            ValidationError,
        ) as exc:
            raise GeminiAgentError(
                "Gemini agent invocation failed or returned invalid structured output"
            ) from exc

    async def extract(self, system_prompt: str, context: dict) -> ProjectFinancials:
        """Structured financial-figure extraction -- a different shape and
        system prompt from generate(), never narrative findings."""
        try:
            text = await self._call(system_prompt, context, FINANCIAL_SCHEMA)
            return ProjectFinancials.model_validate_json(text)
        except (
            httpx.HTTPError,
            KeyError,
            ValueError,
            IndexError,
            ValidationError,
        ) as exc:
            raise GeminiAgentError(
                "Gemini financial extraction failed or returned invalid "
                "structured output"
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
            text = await self._call_with_parts(OCR_SYSTEM_PROMPT, [part], OCR_SCHEMA)
            return json.loads(text).get("text", "")
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
            raise GeminiAgentError(
                "Gemini OCR failed or returned invalid structured output"
            ) from exc

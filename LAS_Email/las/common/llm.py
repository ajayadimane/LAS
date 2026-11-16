"""The single metered entry point for model calls (LLM and local document parsing/OCR).

Every attempt writes one ``ai_usage`` row in its own short transaction, so usage survives even
when the caller's business transaction rolls back. Metering failures are logged, never raised.
"""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import hashlib
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from las.common.db import SessionFactory
from las.common.interfaces import (
    DocumentParser,
    LLMAPIError,
    LLMClient,
    LLMPart,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    ParsedDocument,
)
from las.common.logging import get_logger
from las.common.models import AiModelPricing, AiUsage
from las.common.settings import Settings

T = TypeVar("T", bound=BaseModel)

log = get_logger(__name__)

Purpose = Literal[
    "INTENT_EXTRACTION",
    "BATCH_ROW_EXTRACTION",
    "DOC_CLASSIFICATION",
    "OCR",
    "DPR_GENERATION",
    "DPR_ANALYSIS",
    "APPRAISAL_NARRATIVE",
    "QUOTATION_CHECK",
    "OTHER",
]
UsageStatus = Literal["SUCCESS", "VALIDATION_FAILED", "API_ERROR", "TIMEOUT", "BUDGET_BLOCKED"]

PROMPT_DIR = Path(__file__).resolve().parent.parent / "parse" / "prompts"


# --- prompts -----------------------------------------------------------------------------------
@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    system: str
    user: str

    def render_user(self, variables: dict[str, str]) -> str:
        out = self.user
        for k, v in variables.items():
            out = out.replace("{{" + k + "}}", v)
        return out


def load_prompt(name: str, version: str, directory: Path = PROMPT_DIR) -> PromptTemplate:
    """Prompt files: ``<name>.<version>.md`` with ``=== SYSTEM ===`` / ``=== USER ===`` sections."""
    raw = (directory / f"{name}.{version}.md").read_text(encoding="utf-8")
    _, _, rest = raw.partition("=== SYSTEM ===")
    system, _, user = rest.partition("=== USER ===")
    if not system.strip() or not user.strip():
        raise ValueError(f"prompt {name}.{version} must have SYSTEM and USER sections")
    return PromptTemplate(name=name, version=version, system=system.strip(), user=user.strip())


# --- schema ------------------------------------------------------------------------------------
def to_response_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema with $refs inlined (portable across structured-output backends)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                target = copy.deepcopy(defs[node["$ref"].split("/")[-1]])
                return resolve(target)
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k == "title":
                    continue  # pydantic's auto-generated schema metadata, not a field named "title"
                if k == "properties" and isinstance(v, dict):
                    # Keys here are field NAMES (e.g. a field actually called "title") and must
                    # never be filtered -- only each field's own schema is resolved recursively.
                    out[k] = {name: resolve(field_schema) for name, field_schema in v.items()}
                else:
                    out[k] = resolve(v)
            return out
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    result: dict[str, Any] = resolve(schema)
    return result


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


# --- attribution / errors ----------------------------------------------------------------------
@dataclass(frozen=True)
class Attribution:
    bd_id: int | None = None
    inbound_id: int | None = None
    attachment_id: int | None = None


class LLMFailed(Exception):
    """All attempts failed (API errors or invalid output)."""


@dataclass
class LLMOutcome(Generic[T]):
    value: T
    model: str
    attempts: int
    usage_ids: list[int] = field(default_factory=list)
    response: LLMResponse | None = None


# --- metering ----------------------------------------------------------------------------------
class Meter:
    def __init__(self, factory: SessionFactory) -> None:
        self._factory = factory

    async def _pricing(self, provider: str, model: str) -> AiModelPricing | None:
        today = dt.date.today()
        async with self._factory() as s:
            return (
                await s.execute(
                    select(AiModelPricing)
                    .where(
                        AiModelPricing.provider == provider,
                        AiModelPricing.model == model,
                        AiModelPricing.valid_from <= today,
                        (AiModelPricing.valid_to.is_(None)) | (AiModelPricing.valid_to >= today),
                    )
                    .order_by(AiModelPricing.valid_from.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

    @staticmethod
    def compute_cost(
        p: AiModelPricing | None,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read: int,
        cache_write: int,
        units: int,
    ) -> Decimal | None:
        if p is None:
            return None
        mtok = Decimal(1_000_000)
        if p.usage_unit == "TOKEN":
            parts: list[tuple[int, Decimal | None]] = [
                (input_tokens, p.input_per_mtok),
                (output_tokens, p.output_per_mtok),
                (
                    cache_read,
                    p.cache_read_per_mtok
                    if p.cache_read_per_mtok is not None
                    else p.input_per_mtok,
                ),
                (
                    cache_write,
                    p.cache_write_per_mtok
                    if p.cache_write_per_mtok is not None
                    else p.input_per_mtok,
                ),
            ]
            if any(n and price is None for n, price in parts):
                return None
            cost = sum((Decimal(n) * (price or 0) / mtok for n, price in parts), Decimal(0))
        else:
            if p.per_unit_price is None:
                return None
            cost = Decimal(units) * p.per_unit_price
        return cost.quantize(Decimal("0.000001"))

    async def record(
        self,
        *,
        provider: str,
        model: str,
        purpose: Purpose,
        prompt_template: str,
        prompt_version: str,
        prompt_sha256: str | None,
        attempt_no: int,
        status: UsageStatus,
        attribution: Attribution,
        latency_ms: int | None,
        response: LLMResponse | None = None,
        units: int = 0,
        error_detail: str | None = None,
    ) -> int | None:
        try:
            pricing = await self._pricing(provider, model)
            it = response.input_tokens if response else 0
            ot = response.output_tokens if response else 0
            cr = response.cache_read_tokens if response else 0
            cw = response.cache_creation_tokens if response else 0
            cost = self.compute_cost(
                pricing,
                input_tokens=it,
                output_tokens=ot,
                cache_read=cr,
                cache_write=cw,
                units=units,
            )
            row = AiUsage(
                provider=provider,
                model=model,
                pricing_id=pricing.pricing_id if pricing else None,
                purpose=purpose,
                prompt_template=prompt_template,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                provider_request_id=response.request_id if response else None,
                attempt_no=attempt_no,
                input_tokens=it,
                output_tokens=ot,
                cache_read_tokens=cr,
                cache_creation_tokens=cw,
                units=units,
                cost=cost,
                currency=pricing.currency if pricing else "USD",
                latency_ms=latency_ms,
                status=status,
                stop_reason=response.stop_reason if response else None,
                error_detail=(error_detail or "")[:500] or None,
                bd_id=attribution.bd_id,
                inbound_id=attribution.inbound_id,
                attachment_id=attribution.attachment_id,
            )
            async with self._factory() as s:
                s.add(row)
                await s.commit()
                return row.usage_id
        except Exception:
            log.exception("ai_usage_record_failed", model=model, purpose=purpose)
            return None


# --- the wrapper -------------------------------------------------------------------------------
class MeteredLLM:
    def __init__(self, client: LLMClient, factory: SessionFactory, settings: Settings) -> None:
        self.client = client
        self.meter = Meter(factory)
        self.settings = settings

    async def extract(
        self,
        *,
        purpose: Purpose,
        prompt: PromptTemplate,
        variables: dict[str, str],
        output_model: type[T],
        model: str,
        attribution: Attribution,
        extra_parts: list[LLMPart] | None = None,
        max_output_tokens: int = 4096,
        max_validation_retries: int = 1,
    ) -> LLMOutcome[T]:
        """Structured extraction into ``output_model``. Raises LLMFailed."""
        user_text = prompt.render_user(variables)
        parts = [LLMPart(text=user_text), *(extra_parts or [])]
        sha = hashlib.sha256()
        sha.update(prompt.system.encode())
        sha.update(b"\x00")
        sha.update(user_text.encode())
        for p in extra_parts or []:
            if p.data is not None:
                sha.update(hashlib.sha256(p.data).digest())
        prompt_sha = sha.hexdigest()
        request = LLMRequest(
            model=model,
            system=prompt.system,
            parts=parts,
            response_schema=to_response_schema(output_model),
            max_output_tokens=max_output_tokens,
            timeout_seconds=self.settings.llm_timeout_seconds,
        )
        api_failures = 0
        validation_failures = 0
        attempt = 0
        usage_ids: list[int] = []
        last_error = "no attempt made"

        async def record(
            status: UsageStatus,
            *,
            latency_ms: int,
            response: LLMResponse | None = None,
            error_detail: str | None = None,
        ) -> None:
            uid = await self.meter.record(
                provider=self.client.provider,
                model=model,
                purpose=purpose,
                prompt_template=prompt.name,
                prompt_version=prompt.version,
                prompt_sha256=prompt_sha,
                attempt_no=attempt,
                status=status,
                attribution=attribution,
                latency_ms=latency_ms,
                response=response,
                error_detail=error_detail,
            )
            if uid:
                usage_ids.append(uid)

        while True:
            attempt += 1
            started = time.monotonic()
            try:
                resp = await self.client.generate(request)
            except LLMAPIError as e:
                status: UsageStatus = "TIMEOUT" if isinstance(e, LLMTimeoutError) else "API_ERROR"
                await record(
                    status, latency_ms=int((time.monotonic() - started) * 1000), error_detail=str(e)
                )
                api_failures += 1
                last_error = str(e)
                if not e.retryable or api_failures > self.settings.llm_max_api_retries:
                    raise LLMFailed(last_error) from e
                await asyncio.sleep(min(2**api_failures, 8) * 0.25)
                continue
            latency = int((time.monotonic() - started) * 1000)
            try:
                value = output_model.model_validate_json(_strip_fences(resp.text))
            except (ValidationError, json.JSONDecodeError, ValueError) as e:
                await record(
                    "VALIDATION_FAILED",
                    latency_ms=latency,
                    response=resp,
                    error_detail=type(e).__name__,
                )
                validation_failures += 1
                last_error = "invalid structured output"
                if validation_failures > max_validation_retries:
                    raise LLMFailed(last_error) from e
                continue
            await record("SUCCESS", latency_ms=latency, response=resp)
            return LLMOutcome(
                value=value, model=model, attempts=attempt, usage_ids=usage_ids, response=resp
            )

    async def parse_document(
        self, parser: DocumentParser, data: bytes, mime_type: str, *, attribution: Attribution
    ) -> ParsedDocument:
        """Local parsing/OCR, metered as PAGE units (purpose OCR) like any other model call."""
        started = time.monotonic()
        common = {
            "purpose": "OCR",
            "prompt_template": "document_parse",
            "prompt_version": "n/a",
            "attempt_no": 1,
            "attribution": attribution,
        }
        try:
            result = await parser.parse(data, mime_type)
        except Exception as e:
            await self.meter.record(
                provider=parser.provider,
                model=parser.model,
                prompt_sha256=None,
                status="API_ERROR",
                latency_ms=int((time.monotonic() - started) * 1000),
                error_detail=type(e).__name__,
                **common,  # type: ignore[arg-type]
            )
            raise
        await self.meter.record(
            provider=result.provider,
            model=result.model,
            prompt_sha256=hashlib.sha256(data).hexdigest(),
            status="SUCCESS",
            latency_ms=int((time.monotonic() - started) * 1000),
            units=result.pages,
            **common,  # type: ignore[arg-type]
        )
        return result

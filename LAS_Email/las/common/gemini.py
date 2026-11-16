"""Google Gemini adapter implementing ``LLMClient``. Only ``las/common/llm.py`` may use it."""

from __future__ import annotations

from typing import Any

from las.common.interfaces import LLMAPIError, LLMRequest, LLMResponse, LLMTimeoutError


class GeminiClient:
    provider = "google_gemini"

    def __init__(
        self,
        *,
        api_key: str = "",
        vertex: bool = False,
        project: str = "",
        location: str = "asia-south1",
        provider_name: str = "google_gemini",
    ) -> None:
        from google import genai

        self.provider = provider_name
        if vertex:
            self._client = genai.Client(vertexai=True, project=project, location=location)
        else:
            self._client = genai.Client(api_key=api_key)

    async def generate(self, request: LLMRequest) -> LLMResponse:
        from google.genai import errors, types

        parts: list[Any] = []
        for p in request.parts:
            if p.data is not None:
                parts.append(
                    types.Part.from_bytes(
                        data=p.data, mime_type=p.mime_type or "application/octet-stream"
                    )
                )
            elif p.text is not None:
                parts.append(types.Part.from_text(text=p.text))
        config = types.GenerateContentConfig(
            system_instruction=request.system,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
            http_options=types.HttpOptions(timeout=int(request.timeout_seconds * 1000)),
        )
        if request.response_schema is not None:
            config.response_mime_type = "application/json"
            config.response_json_schema = request.response_schema
        try:
            resp = await self._client.aio.models.generate_content(
                model=request.model,
                contents=types.Content(role="user", parts=parts),
                config=config,
            )
        except errors.APIError as e:
            code = getattr(e, "code", None) or 0
            raise LLMAPIError(
                f"gemini {code}: {getattr(e, 'message', e)}",
                retryable=code in (408, 429) or code >= 500,
            ) from e
        except TimeoutError as e:
            raise LLMTimeoutError("gemini timeout") from e
        except Exception as e:
            if "timeout" in type(e).__name__.lower():
                raise LLMTimeoutError(f"gemini timeout: {e}") from e
            raise LLMAPIError(f"gemini error: {type(e).__name__}") from e
        usage = resp.usage_metadata
        prompt_tokens = (usage.prompt_token_count or 0) if usage else 0
        cached = (usage.cached_content_token_count or 0) if usage else 0
        output = (
            ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0))
            if usage
            else 0
        )
        finish = None
        if resp.candidates and resp.candidates[0].finish_reason is not None:
            finish = str(
                resp.candidates[0].finish_reason.value
                if hasattr(resp.candidates[0].finish_reason, "value")
                else resp.candidates[0].finish_reason
            )
        return LLMResponse(
            text=resp.text or "",
            # Gemini's prompt_token_count includes cached tokens; report them separately.
            input_tokens=max(prompt_tokens - cached, 0),
            output_tokens=output,
            cache_read_tokens=cached,
            cache_creation_tokens=0,
            stop_reason=finish,
            request_id=getattr(resp, "response_id", None),
        )

"""Action extraction through the metered LLM wrapper (structured output into ``Extraction``)."""

from __future__ import annotations

import re

from las.common.llm import Attribution, LLMOutcome, MeteredLLM, load_prompt
from las.parse.candidates import Candidates
from las.parse.context import MailContext
from las.parse.schema import Extraction

PROMPT_NAME = "intent_extract"
PROMPT_VERSION = "v4"

_DELIMS = re.compile(r"</?\s*(mail|quoted_history|attachments)\s*>", re.IGNORECASE)
MAX_BODY_CHARS = 12_000
MAX_ATTACHMENT_CHARS = 1_500


def neutralise(text: str, limit: int) -> str:
    """Untrusted text can't close our delimiters or blow the context window."""
    return _DELIMS.sub(lambda m: m.group(0).replace("<", "‹").replace(">", "›"), text)[:limit]


def render_catalog(ctx: MailContext) -> str:
    lines = []
    for s in ctx.catalog:
        syn = ", ".join(ctx.synonyms.get(s.service_code, [])[:12])
        route = " (routed to IFPL)" if s.requires_re_referral else ""
        lines.append(f"{s.service_code} — {s.display_name}{route} — {syn}")
    return "\n".join(lines)


def render_attachments(ctx: MailContext) -> str:
    if not ctx.attachments:
        return "none"
    return "\n".join(
        f"- {a.filename} ({a.mime}, class {a.doc_class or 'unknown'}"
        f"{', password-protected' if a.is_password_protected else ''}): "
        f"{neutralise(a.text, MAX_ATTACHMENT_CHARS) or '[no text]'}"
        for a in ctx.attachments
    )


def prompt_variables(ctx: MailContext, cands: Candidates) -> dict[str, str]:
    return {
        "catalog": render_catalog(ctx),
        "candidates": cands.as_prompt(),
        # Prompt v4 still has these sections; cases and questions to the BD live in Core LAS,
        # so this service has nothing to show there.
        "bd_state": "none",
        "clarifications": "none",
        "thread": neutralise(ctx.thread_summary, 2000),
        "attachments": render_attachments(ctx),
        "quoted": neutralise(ctx.quoted_text, 4000) or "none",
        "subject": neutralise(ctx.subject, 500),
        "body": neutralise(ctx.new_text, MAX_BODY_CHARS),
    }


async def extract_actions(
    llm: MeteredLLM, ctx: MailContext, cands: Candidates, *, model: str, attribution: Attribution
) -> LLMOutcome[Extraction]:
    return await llm.extract(
        purpose="INTENT_EXTRACTION",
        prompt=load_prompt(PROMPT_NAME, PROMPT_VERSION),
        variables=prompt_variables(ctx, cands),
        output_model=Extraction,
        model=model,
        attribution=attribution,
        max_output_tokens=8192,
    )

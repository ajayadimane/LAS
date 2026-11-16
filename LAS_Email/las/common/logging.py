"""structlog configuration with a PII-masking processor. Nothing unmasked reaches a log sink."""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from las.common.pii import mask_any

# Keys whose values are never logged at all (bodies, prompts, raw payloads).
_DROP_KEYS = frozenset({"body", "body_text", "body_html", "prompt", "response", "raw", "raw_eml"})


def pii_mask_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict.keys()):
        if key in _DROP_KEYS:
            event_dict[key] = "[redacted]"
        else:
            event_dict[key] = mask_any(event_dict[key])
    return event_dict


def configure_logging(level: str = "INFO", *, json: bool = False) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    renderer: Any = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            pii_mask_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]

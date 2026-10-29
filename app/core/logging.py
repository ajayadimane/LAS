"""Logging setup and redaction.

Nothing in this module may ever emit a password or an access token. Every
value that leaves here passes through `redact` first.
"""

import logging
import sys
from typing import Any

REDACTED = "***REDACTED***"

# Compared case-insensitively against dict keys, with "-" and "_" stripped,
# so "access_token", "accessToken" and "Access-Token" all match.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "newpassword",
        "oldpassword",
        "currentpassword",
        "temporarypassword",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "token",
        "authorization",
        "secret",
        "clientsecret",
        "apikey",
        "otp",
        "twofactorauthcode",
        # Custom field values carry customer documents and reports as base64
        # (see app/services/enrolment.py): personal data, and megabytes long.
        "customfields",
        "customfieldvalue",
    }
)


def _is_sensitive(key: str) -> bool:
    return key.replace("-", "").replace("_", "").lower() in SENSITIVE_KEYS


def redact(value: Any) -> Any:
    """Recursively replace sensitive values. Safe to call on anything."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if _is_sensitive(str(k)) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for existing in list(root.handlers):
        root.removeHandler(existing)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

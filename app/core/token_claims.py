"""Read claims from the vendor JWT without verifying it.

The vendor (Keycloak behind KrakenD) verifies the signature on every call, so
we never need to. We only peek at the payload for display and housekeeping,
such as telling the dashboard when the token expires. Never make an
authorization decision on these claims alone: they are unverified.
"""

import base64
import binascii
import json
from typing import Any


def read_claims(token: str) -> dict[str, Any] | None:
    """The JWT payload as a dict, or None if the token is not a readable JWT."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    segment = parts[1]
    try:
        raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        claims = json.loads(raw)
    except (binascii.Error, ValueError):
        return None
    return claims if isinstance(claims, dict) else None


def int_claim(claims: dict[str, Any] | None, name: str) -> int | None:
    value = (claims or {}).get(name)
    # bool is an int subclass; a boolean exp is not a timestamp.
    return value if isinstance(value, int) and not isinstance(value, bool) else None

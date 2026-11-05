"""Small HMAC-signed, expiring tokens for state we hand the dashboard.

We have no database, so a fact established in one request ("this mobile was
verified by OTP") is carried to the next as a token only we can sign. The
claims are readable by whoever holds the token (base64, not encrypted): put
nothing secret in them.

Format: base64url(json claims) "." base64url(HMAC-SHA256(secret, part 1)).
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(secret: bytes, body: str) -> str:
    return _b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())


def sign(claims: dict[str, Any], secret: bytes, typ: str, ttl_seconds: int) -> str:
    """A token of type `typ` carrying `claims`, valid for `ttl_seconds`."""
    payload = {**claims, "typ": typ, "exp": int(time.time()) + ttl_seconds}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{_mac(secret, body)}"


def unsign(token: str | None, secret: bytes, typ: str) -> dict[str, Any] | None:
    """The claims, or None if the token is malformed, forged, of another type
    or expired. Never raises."""
    if not token or token.count(".") != 1:
        return None
    body, mac = token.split(".")
    if not hmac.compare_digest(mac, _mac(secret, body)):
        return None
    try:
        claims = json.loads(_unb64(body))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(claims, dict) or claims.get("typ") != typ:
        return None
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp <= time.time():
        return None
    return claims

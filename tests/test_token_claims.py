"""Unit tests for reading (not verifying) vendor JWT claims."""

import base64
import json

import pytest

from app.core.token_claims import int_claim, read_claims


def _seg(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def test_reads_the_payload_of_an_unpadded_jwt():
    token = f"{_seg({'alg': 'RS256'})}.{_seg({'exp': 1793035702, 'sub': 'x'})}.sig"
    assert read_claims(token) == {"exp": 1793035702, "sub": "x"}


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not-a-jwt",
        "a.b",
        "a.!!!.c",
        f"h.{base64.urlsafe_b64encode(b'not json').decode()}.s",
        f"h.{_seg([1, 2])}.s",
    ],
)
def test_unreadable_tokens_give_none(token):
    assert read_claims(token) is None


def test_int_claim_ignores_missing_and_non_integer_values():
    assert int_claim({"exp": 5}, "exp") == 5
    assert int_claim({"exp": "5"}, "exp") is None
    assert int_claim({"exp": True}, "exp") is None
    assert int_claim({}, "exp") is None
    assert int_claim(None, "exp") is None

"""Temporary passwords for logins we create on a customer's behalf."""

import secrets

# No 0/O, 1/l/I: the BD may read the password out or copy it by hand.
_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_LOWER = "abcdefghijkmnopqrstuvwxyz"
_DIGITS = "23456789"
_SYMBOLS = "@#$%"
_ALL = _UPPER + _LOWER + _DIGITS + _SYMBOLS


def temporary_password(length: int = 12) -> str:
    """A random password with at least one character of each class.

    Uses `secrets`, never `random`. Never log the result.
    """
    if length < 4:
        raise ValueError("length must be at least 4")
    chars = [secrets.choice(cls) for cls in (_UPPER, _LOWER, _DIGITS, _SYMBOLS)]
    chars += [secrets.choice(_ALL) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)

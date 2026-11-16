"""Clock, IST helpers and business-hour arithmetic."""

from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


_FRACTION = re.compile(r"(\.\d+)(?=[+-]\d{2}:?\d{2}$|$)")


def parse_iso(value: str) -> dt.datetime:
    """ISO-8601 the way partners send it, on Python 3.10.

    3.10's fromisoformat rejects a trailing "Z" and fractional seconds that
    are not exactly 3 or 6 digits (3.11 accepts both).
    """
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    def six(m: re.Match[str]) -> str:
        return (m.group(1) + "000000")[:7]

    return dt.datetime.fromisoformat(_FRACTION.sub(six, text, count=1))


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def ist_day_bounds(at: dt.datetime | None = None) -> tuple[dt.datetime, dt.datetime]:
    """[start, end) of the IST calendar day containing ``at``, as aware UTC datetimes."""
    local = (at or utcnow()).astimezone(IST)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(dt.timezone.utc), (start + dt.timedelta(days=1)).astimezone(dt.timezone.utc)


def add_business_hours(
    start: dt.datetime,
    hours: int,
    *,
    day_start: int = 10,
    day_end: int = 18,
    weekdays: frozenset[int] | set[int] | list[int] = frozenset({0, 1, 2, 3, 4, 5}),
) -> dt.datetime:
    """Advance ``hours`` of business time (IST, [day_start, day_end) on ``weekdays``)."""
    if hours <= 0:
        return start
    days = set(weekdays)
    cur = start.astimezone(IST)
    remaining = dt.timedelta(hours=hours)
    while remaining > dt.timedelta(0):
        open_ = cur.replace(hour=day_start, minute=0, second=0, microsecond=0)
        close = cur.replace(hour=day_end, minute=0, second=0, microsecond=0)
        if cur.weekday() not in days or cur >= close:
            cur = (cur + dt.timedelta(days=1)).replace(
                hour=day_start, minute=0, second=0, microsecond=0
            )
            continue
        cur = max(cur, open_)
        available = close - cur
        step = min(available, remaining)
        cur += step
        remaining -= step
    return cur.astimezone(dt.timezone.utc)

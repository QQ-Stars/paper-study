from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import re


MAX_RETRY_AFTER_SECONDS = 900
_INTEGER_SECONDS = re.compile(r"[+-]?\d+\Z")


def normalize_retry_after(value: str | None, *, now: datetime) -> int | None:
    """Normalize an HTTP Retry-After value without performing transport work."""
    if value is None or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if _INTEGER_SECONDS.fullmatch(raw):
        return _bounded(int(raw))
    try:
        retry_at = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at is None:
        return None
    if retry_at.tzinfo is None or retry_at.utcoffset() is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    seconds = math.ceil(
        (retry_at.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds()
    )
    return _bounded(seconds)


def _bounded(seconds: int) -> int:
    return min(MAX_RETRY_AFTER_SECONDS, max(0, seconds))

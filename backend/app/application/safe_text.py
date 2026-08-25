"""Small helpers for keeping provider diagnostics free of credentials."""

from __future__ import annotations

import re


_SENSITIVE_ERROR_VALUE = re.compile(
    r"(?i)(?P<prefix>[\"']?(?:authorization|api[-_ ]?key|password|secret|token|cookie)"
    r"[\"']?\s*[:=]\s*)"
    r"(?P<value>Bearer\s+[^\s,;}\"']+|\"[^\"]*\"|'[^']*'|[^\s,;}\"']+)"
)
_BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[^\s,;}\"']+")


def redact_sensitive_text(value: object, *, limit: int | None = None) -> str:
    """Redact common credential-shaped values without exposing raw exceptions."""
    rendered = _SENSITIVE_ERROR_VALUE.sub(
        lambda match: f"{match.group('prefix')}[redacted]", str(value)
    )
    rendered = _BEARER_VALUE.sub("Bearer [redacted]", rendered)
    return rendered if limit is None else rendered[:limit]


__all__ = ["redact_sensitive_text"]

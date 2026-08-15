from __future__ import annotations


def normalize_canonical_text(value: str) -> str:
    normalized_newlines = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip(" \t") for line in normalized_newlines.split("\n")]
    return "\n".join(lines).rstrip("\n") + "\n"

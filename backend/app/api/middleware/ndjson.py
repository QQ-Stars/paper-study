from __future__ import annotations

"""One byte-stable NDJSON response encoder for legacy streaming routes."""

import json
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Mapping
from typing import Any

from starlette.responses import StreamingResponse


TERMINAL_TYPES = frozenset({"result", "done"})


def encode_event(event: Mapping[str, Any]) -> bytes:
    if not isinstance(event, Mapping):
        raise TypeError("NDJSON event must be an object")
    return (json.dumps(dict(event), ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


async def _encode_events(
    events: AsyncIterable[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
) -> AsyncIterator[bytes]:
    terminal_seen = False
    if hasattr(events, "__aiter__"):
        iterator = events.__aiter__()  # type: ignore[union-attr]
        async for event in iterator:
            if terminal_seen:
                raise RuntimeError("NDJSON stream emitted after terminal event")
            event_type = event.get("type")
            if event_type not in {"progress", "result", "done"}:
                raise ValueError("NDJSON event type is invalid")
            terminal_seen = event_type in TERMINAL_TYPES
            yield encode_event(event)
    else:
        for event in events:
            if terminal_seen:
                raise RuntimeError("NDJSON stream emitted after terminal event")
            event_type = event.get("type")
            if event_type not in {"progress", "result", "done"}:
                raise ValueError("NDJSON event type is invalid")
            terminal_seen = event_type in TERMINAL_TYPES
            yield encode_event(event)
    if not terminal_seen:
        raise RuntimeError("NDJSON stream ended without a terminal event")


def ndjson_response(
    events: AsyncIterable[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
) -> StreamingResponse:
    return StreamingResponse(
        _encode_events(events),
        headers={"Content-Type": "application/x-ndjson; charset=utf-8"},
    )


__all__ = ["TERMINAL_TYPES", "encode_event", "ndjson_response"]

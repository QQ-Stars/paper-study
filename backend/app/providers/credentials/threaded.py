from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeVar


_Result = TypeVar("_Result")


async def run_thread_to_completion(
    function: Callable[..., _Result],
    *args: object,
) -> _Result:
    """Do not propagate cancellation until a submitted thread operation settles."""
    operation = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        try:
            await operation
        except Exception:
            pass
        raise

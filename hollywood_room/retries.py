"""Durable retries only when a turn is known not to have been submitted."""

import asyncio
import random

from .rpc import RpcError


def delay(attempt, *, uniform=random.uniform):
    """Equal jitter, capped at five minutes, without an attempt-count cutoff."""
    ceiling = min(300, 2 ** min(max(attempt, 1), 9))
    return uniform(ceiling / 2, ceiling)


def disposition(exc, phase):
    # Neither a timeout nor a lost response proves that a submitted turn failed.
    if phase != "preparingThread" and not (isinstance(exc, RpcError) and exc.not_sent):
        return "uncertain"
    reason = str(exc).lower()
    if any(word in reason for word in ("usage limit", "quota exceeded", "usage exhausted")):
        return "waiting_usage"
    if (
        isinstance(exc, ValueError)
        or any(
            word in reason
            for word in ("unauthorized", "authentication", "invalid api key", "forbidden")
        )
        or (isinstance(exc, RpcError) and exc.code in (-32600, -32601, -32602))
    ):
        return "blocked"
    if isinstance(exc, (OSError, asyncio.TimeoutError, RpcError)):
        return "queued"
    return "blocked"

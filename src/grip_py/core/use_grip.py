"""Drip consumption helpers."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from typing import Any, Literal, TypeVar

from .drip import Drip

T = TypeVar("T")


def use_grip(grok: Any, grip: Any, ctx: Any = None) -> T | None:
    """Return current value for the queried grip/context pair."""
    return grok.query(grip, ctx).get()


async def watch_drip(
    drip: Drip[T],
    *,
    emit_initial: bool = True,
    priority: bool = False,
    queue_size: int = 1,
    overflow: Literal["drop_oldest", "drop_newest"] = "drop_oldest",
) -> AsyncIterator[T | None]:
    """Yield drip updates as an async iterator."""
    if queue_size < 1:
        raise ValueError("queue_size must be >= 1")
    if overflow not in {"drop_oldest", "drop_newest"}:
        raise ValueError(f"Unsupported overflow policy: {overflow!r}")

    queue: asyncio.Queue[T | None] = asyncio.Queue(maxsize=queue_size)
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    seen_first = False

    def put_with_overflow(value: T | None) -> None:
        while True:
            try:
                queue.put_nowait(value)
                return
            except asyncio.QueueFull:
                if overflow == "drop_newest":
                    return
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    return

    def on_value(value: T | None) -> None:
        nonlocal seen_first
        if not seen_first:
            seen_first = True
            if not emit_initial:
                return

        if threading.get_ident() == loop_thread:
            put_with_overflow(value)
        else:
            loop.call_soon_threadsafe(put_with_overflow, value)

    unsubscribe = drip.subscribe_priority(on_value) if priority else drip.subscribe(on_value)
    try:
        while True:
            value = await queue.get()
            yield value
    finally:
        unsubscribe()

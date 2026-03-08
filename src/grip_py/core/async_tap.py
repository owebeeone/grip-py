"""Async tap implementations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .base_tap import BaseTap
from .grip import Grip
from .interfaces import GripContext


@dataclass(init=False, eq=False)
class AsyncTap(BaseTap):
    """Runs async fetch and publishes results."""

    _fetcher: Callable[[GripContext], Awaitable[dict[Grip[Any], Any]]]
    _tasks: dict[str, asyncio.Task[None]]

    def __init__(
        self,
        *,
        provides: Iterable[Grip[Any]],
        fetcher: Callable[[GripContext], Awaitable[dict[Grip[Any], Any]]],
    ):
        super().__init__(provides=provides)
        self._fetcher = fetcher
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def on_detach(self) -> None:
        for task in tuple(self._tasks.values()):
            if not task.done():
                task.cancel()
        self._tasks.clear()
        super().on_detach()

    def produce(self, *, dest_context: GripContext | None = None) -> None:
        if dest_context is not None:
            self._schedule_fetch(dest_context)
            return

        if self._producer is None:
            return
        for node in tuple(self._producer.get_destinations().keys()):
            ctx = node.get_context()
            if ctx is not None:
                self._schedule_fetch(ctx)

    def _schedule_fetch(self, ctx: GripContext) -> None:
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        prior = self._tasks.get(ctx.id)
        if prior is not None and not prior.done():
            prior.cancel()

        async def run() -> None:
            try:
                values = await self._fetcher(ctx)
            except asyncio.CancelledError:
                return
            except Exception:
                return
            self.publish(values, dest_context=ctx)

        self._tasks[ctx.id] = loop.create_task(run())


def create_async_tap(
    *,
    provides: Iterable[Grip[Any]],
    fetcher: Callable[[GripContext], Awaitable[dict[Grip[Any], Any]]],
) -> AsyncTap:
    return AsyncTap(provides=provides, fetcher=fetcher)

from __future__ import annotations

import asyncio
from typing import Any

from grip_py.core.async_tap import AsyncTapParams, create_async_tap
from grip_py.core.grok import Grok
from grip_py.core.grip import Grip, GripRegistry


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    start = asyncio.get_running_loop().time()
    while not predicate():
        if asyncio.get_running_loop().time() - start > timeout:
            raise TimeoutError("condition not reached")
        await asyncio.sleep(0.01)


def test_async_tap_history_size_limits_entries() -> None:
    async def scenario() -> None:
        registry = GripRegistry()
        out = registry.add("Out", 0)
        state_grip = registry.add("State", value_type=object)
        controller_grip = registry.add("Controller", value_type=object)
        grok = Grok(registry)

        fetch_count = 0

        async def fetcher(_: AsyncTapParams) -> dict[Grip[Any], Any]:
            nonlocal fetch_count
            fetch_count += 1
            await asyncio.sleep(0.01)
            return {out: fetch_count}

        tap = create_async_tap(
            provides=[out],
            state_grip=state_grip,
            controller_grip=controller_grip,
            history_size=2,
            fetcher=fetcher,
        )
        grok.main_home_context.register_tap(tap)

        ctx = grok.main_presentation_context.create_child("ctx_1")
        out_drip = grok.query(out, ctx)
        state_drip = grok.query(state_grip, ctx)
        controller = grok.query(controller_grip, ctx).get()

        await _wait_until(lambda: getattr(state_drip.get().state, "type", None) == "success")
        await _wait_until(lambda: out_drip.get() == 1)

        assert controller is not None
        controller.refresh(force_refetch=True)
        await _wait_until(lambda: fetch_count >= 2)
        await _wait_until(lambda: out_drip.get() == 2)
        controller.refresh(force_refetch=True)
        await _wait_until(lambda: fetch_count >= 3)
        await _wait_until(lambda: out_drip.get() == 3)

        state = state_drip.get()
        assert len(state.history) <= 2

    asyncio.run(scenario())


def test_async_tap_history_size_zero_disables_history() -> None:
    async def scenario() -> None:
        registry = GripRegistry()
        out = registry.add("Out", 0)
        state_grip = registry.add("State", value_type=object)
        controller_grip = registry.add("Controller", value_type=object)
        grok = Grok(registry)

        async def fetcher(_: AsyncTapParams) -> dict[Grip[Any], Any]:
            await asyncio.sleep(0.01)
            return {out: 1}

        tap = create_async_tap(
            provides=[out],
            state_grip=state_grip,
            controller_grip=controller_grip,
            history_size=0,
            fetcher=fetcher,
        )
        grok.main_home_context.register_tap(tap)

        ctx = grok.main_presentation_context.create_child("ctx_2")
        grok.query(out, ctx)
        state_drip = grok.query(state_grip, ctx)
        controller = grok.query(controller_grip, ctx).get()

        await _wait_until(lambda: getattr(state_drip.get().state, "type", None) == "success")
        assert controller is not None
        controller.refresh(force_refetch=True)
        await _wait_until(lambda: getattr(state_drip.get().state, "type", None) == "success")

        assert len(state_drip.get().history) == 0

    asyncio.run(scenario())

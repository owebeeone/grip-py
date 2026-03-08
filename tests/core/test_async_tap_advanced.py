from __future__ import annotations

import asyncio
from typing import Any

from grip_py.core.async_tap import AsyncTapParams, RetryConfig, create_async_tap
from grip_py.core.atom_tap import create_atom_value_tap
from grip_py.core.drip import Drip
from grip_py.core.grok import Grok
from grip_py.core.grip import Grip, GripRegistry


async def _wait_for_value(drip: Drip[Any], expected: Any, timeout: float = 1.0) -> None:
    done = asyncio.Event()

    def on_value(value: Any) -> None:
        if value == expected:
            done.set()

    drip.subscribe_priority(on_value)
    if drip.get() == expected:
        done.set()
    await asyncio.wait_for(done.wait(), timeout=timeout)


async def _wait_for_state_type(state_drip: Drip[Any], expected_type: str, timeout: float = 1.0) -> None:
    done = asyncio.Event()

    def on_state(value: Any) -> None:
        state = getattr(value, "state", None)
        if state is not None and getattr(state, "type", None) == expected_type:
            done.set()

    state_drip.subscribe_priority(on_state)
    current = state_drip.get()
    current_state = getattr(current, "state", None)
    if current_state is not None and getattr(current_state, "type", None) == expected_type:
        done.set()
    await asyncio.wait_for(done.wait(), timeout=timeout)


def test_async_tap_publishes_state_and_controller_grips() -> None:
    async def scenario() -> None:
        registry = GripRegistry()
        out = registry.add("Out", 0)
        local = registry.add("Local", 0)
        state_grip = registry.add("AsyncState", value_type=object)
        controller_grip = registry.add("AsyncController", value_type=object)
        grok = Grok(registry)

        ctx = grok.main_presentation_context.create_child()
        local_source = create_atom_value_tap(local, initial=1)
        ctx.register_tap(local_source)

        fetch_count = 0

        async def fetcher(params: AsyncTapParams) -> dict[Grip[Any], Any]:
            nonlocal fetch_count
            fetch_count += 1
            await asyncio.sleep(0.01)
            return {out: int(params.destination_params[local] or 0) * 2}

        tap = create_async_tap(
            provides=[out],
            destination_param_grips=[local],
            state_grip=state_grip,
            controller_grip=controller_grip,
            fetcher=fetcher,
        )
        grok.main_home_context.register_tap(tap)

        out_drip = grok.query(out, ctx)
        state_drip = grok.query(state_grip, ctx)
        controller_drip = grok.query(controller_grip, ctx)

        await _wait_for_value(out_drip, 2)
        await _wait_for_state_type(state_drip, "success")
        assert fetch_count == 1

        controller = controller_drip.get()
        assert controller is not None
        controller.refresh(force_refetch=True)

        await asyncio.sleep(0.03)
        assert fetch_count >= 2

    asyncio.run(scenario())


def test_async_tap_retry_backoff_recovers_after_error() -> None:
    async def scenario() -> None:
        registry = GripRegistry()
        out = registry.add("Out", 0)
        state_grip = registry.add("AsyncState", value_type=object)
        grok = Grok(registry)

        fetch_count = 0

        async def fetcher(_: AsyncTapParams) -> dict[Grip[Any], Any]:
            nonlocal fetch_count
            fetch_count += 1
            await asyncio.sleep(0.01)
            if fetch_count == 1:
                raise RuntimeError("boom")
            return {out: 7}

        tap = create_async_tap(
            provides=[out],
            state_grip=state_grip,
            retry=RetryConfig(max_retries=1, initial_delay_ms=20),
            fetcher=fetcher,
        )
        grok.main_home_context.register_tap(tap)

        ctx = grok.main_presentation_context.create_child()
        out_drip = grok.query(out, ctx)
        state_drip = grok.query(state_grip, ctx)

        await _wait_for_value(out_drip, 7)
        await _wait_for_state_type(state_drip, "success")
        assert fetch_count == 2

    asyncio.run(scenario())


def test_async_tap_cleanup_delay_reuses_inflight_after_detach_and_reattach() -> None:
    async def scenario() -> None:
        registry = GripRegistry()
        out = registry.add("Out", 0)
        request_id = registry.add("RequestId", 0)
        grok = Grok(registry)

        ctx = grok.main_presentation_context.create_child()
        req_source = create_atom_value_tap(request_id, initial=1)
        ctx.register_tap(req_source)

        fetch_count = 0

        async def fetcher(_: AsyncTapParams) -> dict[Grip[Any], Any]:
            nonlocal fetch_count
            fetch_count += 1
            await asyncio.sleep(0.08)
            return {out: 99}

        tap = create_async_tap(
            provides=[out],
            destination_param_grips=[request_id],
            request_key_of=lambda params: str(params.destination_params[request_id]),
            cleanup_delay_ms=120,
            fetcher=fetcher,
        )
        grok.main_home_context.register_tap(tap)

        d1 = grok.query(out, ctx)
        await asyncio.sleep(0.02)
        grok.resolver.remove_consumer(ctx, out)
        await asyncio.sleep(0.02)

        d2 = grok.query(out, ctx)
        await _wait_for_value(d2, 99)

        assert d1 is not d2
        assert fetch_count == 1

    asyncio.run(scenario())

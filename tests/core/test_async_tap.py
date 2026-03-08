import asyncio
from typing import Any

from grip_py.core.atom_tap import create_atom_value_tap
from grip_py.core.async_tap import AsyncTapParams, create_async_tap
from grip_py.core.drip import Drip
from grip_py.core.grok import Grok
from grip_py.core.grip import Grip, GripRegistry


async def _wait_for_value(
    drip: Drip[Any],
    expected: Any,
    timeout: float = 1.0,
) -> None:
    done = asyncio.Event()

    def on_value(value: Any) -> None:
        if value == expected:
            done.set()

    drip.subscribe_priority(on_value)
    await asyncio.wait_for(done.wait(), timeout=timeout)


def test_async_tap_fetch_and_publish() -> None:
    async def scenario():
        registry = GripRegistry()
        out = registry.add("Out", 0)
        grok = Grok(registry)

        async def fetcher(_: AsyncTapParams) -> dict[Grip[Any], Any]:
            await asyncio.sleep(0.01)
            return {out: 123}

        tap = create_async_tap(provides=[out], fetcher=fetcher)
        grok.main_home_context.register_tap(tap)

        ctx = grok.main_presentation_context.create_child()
        drip = grok.query(out, ctx)

        done = asyncio.Event()

        def on_value(v: int | None) -> None:
            if v == 123:
                done.set()

        drip.subscribe_priority(on_value)
        await asyncio.wait_for(done.wait(), timeout=1.0)
        assert drip.get() == 123

    asyncio.run(scenario())


def test_async_tap_destination_params_only_shares_request_by_request_key() -> None:
    async def scenario():
        registry = GripRegistry()
        out = registry.add("Out", 0)
        request_id = registry.add("RequestId", 0)
        grok = Grok(registry)

        c1 = grok.main_presentation_context.create_child()
        c2 = grok.main_presentation_context.create_child()
        c1_source = create_atom_value_tap(request_id, initial=1)
        c2_source = create_atom_value_tap(request_id, initial=1)
        c1.register_tap(c1_source)
        c2.register_tap(c2_source)

        fetch_count = 0
        async def fetcher(params: AsyncTapParams) -> dict[Grip[Any], Any]:
            nonlocal fetch_count
            fetch_count += 1
            await asyncio.sleep(0.02)
            key_value = int(params.destination_params[request_id] or 0)
            return {out: key_value * 10}

        tap = create_async_tap(
            provides=[out],
            destination_param_grips=[request_id],
            request_key_of=lambda params: str(params.destination_params[request_id]),
            fetcher=fetcher,
        )
        grok.main_home_context.register_tap(tap)

        d1 = grok.query(out, c1)
        d2 = grok.query(out, c2)
        await _wait_for_value(d1, 10)
        await _wait_for_value(d2, 10)
        assert fetch_count == 1

    asyncio.run(scenario())


def test_async_tap_home_params_only_recomputes_and_shares() -> None:
    async def scenario():
        registry = GripRegistry()
        out = registry.add("Out", 0)
        home = registry.add("Home", 0)
        grok = Grok(registry)

        home_source = create_atom_value_tap(home, initial=3)
        grok.main_home_context.register_tap(home_source)

        c1 = grok.main_presentation_context.create_child()
        c2 = grok.main_presentation_context.create_child()

        fetch_count = 0

        async def fetcher(params: AsyncTapParams) -> dict[Grip[Any], Any]:
            nonlocal fetch_count
            fetch_count += 1
            await asyncio.sleep(0.02)
            value = int(params.home_params[home] or 0)
            return {out: value * 10}

        tap = create_async_tap(
            provides=[out],
            home_param_grips=[home],
            request_key_of=lambda params: str(params.home_params[home]),
            fetcher=fetcher,
        )
        grok.main_home_context.register_tap(tap)

        d1 = grok.query(out, c1)
        d2 = grok.query(out, c2)
        await _wait_for_value(d1, 30)
        await _wait_for_value(d2, 30)
        assert fetch_count == 1

        home_source.set(4)
        await _wait_for_value(d1, 40)
        await _wait_for_value(d2, 40)
        assert fetch_count == 2

    asyncio.run(scenario())


def test_async_tap_combined_home_and_destination_params() -> None:
    async def scenario():
        registry = GripRegistry()
        out = registry.add("Out", 0)
        home = registry.add("Home", 0)
        local = registry.add("Local", 0)
        grok = Grok(registry)

        home_source = create_atom_value_tap(home, initial=100)
        grok.main_home_context.register_tap(home_source)

        c1 = grok.main_presentation_context.create_child()
        c2 = grok.main_presentation_context.create_child()
        c1_local = create_atom_value_tap(local, initial=1)
        c2_local = create_atom_value_tap(local, initial=2)
        c1.register_tap(c1_local)
        c2.register_tap(c2_local)

        fetch_count = 0

        async def fetcher(params: AsyncTapParams) -> dict[Grip[Any], Any]:
            nonlocal fetch_count
            fetch_count += 1
            await asyncio.sleep(0.01)
            home_value = int(params.home_params[home] or 0)
            local_value = int(params.destination_params[local] or 0)
            return {out: home_value + local_value}

        tap = create_async_tap(
            provides=[out],
            destination_param_grips=[local],
            home_param_grips=[home],
            request_key_of=lambda params: (
                f"{params.home_params[home]}:{params.destination_params[local]}"
            ),
            fetcher=fetcher,
        )
        grok.main_home_context.register_tap(tap)

        d1 = grok.query(out, c1)
        d2 = grok.query(out, c2)
        await _wait_for_value(d1, 101)
        await _wait_for_value(d2, 102)
        assert fetch_count == 2

        home_source.set(200)
        await _wait_for_value(d1, 201)
        await _wait_for_value(d2, 202)
        assert fetch_count == 4

        c1_local.set(5)
        await _wait_for_value(d1, 205)
        await asyncio.sleep(0.03)
        assert d2.get() == 202
        assert fetch_count == 5

    asyncio.run(scenario())


def test_async_tap_cache_ttl_reuses_result_before_expiry() -> None:
    async def scenario():
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
            await asyncio.sleep(0.01)
            return {out: fetch_count}

        tap = create_async_tap(
            provides=[out],
            destination_param_grips=[request_id],
            request_key_of=lambda params: str(params.destination_params[request_id]),
            cache_ttl_ms=100,
            fetcher=fetcher,
        )
        grok.main_home_context.register_tap(tap)

        drip = grok.query(out, ctx)
        await _wait_for_value(drip, 1)
        assert fetch_count == 1

        tap.produce(dest_context=ctx)
        await asyncio.sleep(0.02)
        assert drip.get() == 1
        assert fetch_count == 1

        await asyncio.sleep(0.12)
        tap.produce(dest_context=ctx)
        await _wait_for_value(drip, 2)
        assert fetch_count == 2

    asyncio.run(scenario())


def test_async_tap_latest_only_drops_stale_completion() -> None:
    async def scenario():
        registry = GripRegistry()
        out = registry.add("Out", 0)
        seq = registry.add("Seq", 0)
        grok = Grok(registry)

        ctx = grok.main_presentation_context.create_child()
        seq_source = create_atom_value_tap(seq, initial=1)
        ctx.register_tap(seq_source)

        async def fetcher(params: AsyncTapParams) -> dict[Grip[Any], Any]:
            value = int(params.destination_params[seq] or 0)
            await asyncio.sleep(0.05 if value == 1 else 0.01)
            return {out: value}

        tap = create_async_tap(
            provides=[out],
            destination_param_grips=[seq],
            request_key_of=lambda params: str(params.destination_params[seq]),
            latest_only=True,
            fetcher=fetcher,
        )
        grok.main_home_context.register_tap(tap)

        drip = grok.query(out, ctx)
        seq_source.set(2)
        await _wait_for_value(drip, 2)
        await asyncio.sleep(0.08)
        assert drip.get() == 2

    asyncio.run(scenario())

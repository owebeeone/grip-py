from __future__ import annotations

import asyncio
import time
from typing import Any

from grip_py.core.async_tap import AsyncTapParams, create_async_tap
from grip_py.core.atom_tap import create_atom_value_tap
from grip_py.core.grok import Grok
from grip_py.core.grip import Grip, GripRegistry


def test_async_tap_throughput_smoke_0p1s() -> None:
    """Smoke benchmark to characterize async tap request throughput over 0.1s."""

    async def scenario() -> None:
        registry = GripRegistry()
        out = registry.add("Out", 0)
        seq = registry.add("Seq", 0)
        grok = Grok(registry)

        ctx = grok.main_presentation_context.create_child()
        seq_source = create_atom_value_tap(seq, initial=0)
        ctx.register_tap(seq_source)

        fetch_count = 0

        async def fetcher(params: AsyncTapParams) -> dict[Grip[Any], Any]:
            nonlocal fetch_count
            fetch_count += 1
            value = int(params.destination_params[seq] or 0)
            return {out: value}

        tap = create_async_tap(
            provides=[out],
            destination_param_grips=[seq],
            latest_only=False,
            request_key_of=lambda params: str(params.destination_params[seq]),
            fetcher=fetcher,
        )
        grok.main_home_context.register_tap(tap)

        grok.query(out, ctx)

        duration_s = 0.1
        start = time.perf_counter()
        sent = 0
        while (time.perf_counter() - start) < duration_s:
            sent += 1
            seq_source.set(sent)
            await asyncio.sleep(0)

        await asyncio.sleep(0.05)
        elapsed = max(time.perf_counter() - start, 1e-9)
        throughput = fetch_count / elapsed

        assert fetch_count > 0
        assert sent > 0
        print(f"async_tap_perf sent={sent} completed={fetch_count} throughput={throughput:.1f}/s")

    asyncio.run(scenario())

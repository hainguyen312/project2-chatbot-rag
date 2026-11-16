"""services/sse.with_heartbeat: chèn `: ping` khi generator im lặng, giữ nguyên sự kiện."""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from services.sse import PING, with_heartbeat


def _collect(agen, limit=100):
    async def run():
        out = []
        async for x in agen:
            out.append(x)
            if len(out) >= limit:
                break
        return out
    return asyncio.run(run())


def test_passthrough_without_pings_when_fast():
    events = ["data: 1\n\n", "data: 2\n\n", "data: 3\n\n"]
    assert _collect(with_heartbeat(iter(events), interval=5)) == events


def test_ping_inserted_during_silence():
    def slow():
        yield "data: start\n\n"
        time.sleep(0.35)
        yield "data: end\n\n"

    out = _collect(with_heartbeat(slow(), interval=0.1))
    assert out[0] == "data: start\n\n"
    assert out[-1] == "data: end\n\n"
    pings = out[1:-1]
    assert len(pings) >= 2 and set(pings) == {PING}


def test_ping_is_sse_comment():
    # Dòng bắt đầu ":" là comment SSE; parser frontend chỉ đọc dòng "data:".
    assert PING.startswith(":") and PING.endswith("\n\n")
    assert not any(line.startswith("data:") for line in PING.splitlines())


def test_generator_exception_propagates():
    def bad():
        yield "data: 1\n\n"
        raise ValueError("boom")

    async def run():
        out = []
        with pytest.raises(ValueError, match="boom"):
            async for x in with_heartbeat(bad(), interval=5):
                out.append(x)
        return out

    assert asyncio.run(run()) == ["data: 1\n\n"]


def test_client_disconnect_closes_source_generator():
    closed = threading.Event()

    def gen():
        try:
            yield "data: 1\n\n"
            yield "data: 2\n\n"
            yield "data: 3\n\n"
        finally:
            closed.set()

    async def run():
        agen = with_heartbeat(gen(), interval=5)
        first = await agen.__anext__()
        await agen.aclose()  # giống Starlette khi client ngắt
        return first

    assert asyncio.run(run()) == "data: 1\n\n"
    assert closed.wait(2)


def test_disconnect_while_source_busy_closes_after_step():
    closed = threading.Event()
    started = threading.Event()

    def gen():
        try:
            yield "data: 1\n\n"
            started.set()
            time.sleep(0.3)  # đang chạy dở trong thread lúc client ngắt
            yield "data: 2\n\n"
        finally:
            closed.set()

    async def run():
        agen = with_heartbeat(gen(), interval=0.05)
        assert await agen.__anext__() == "data: 1\n\n"
        assert await agen.__anext__() == PING
        assert started.wait(1)
        await agen.aclose()
        await asyncio.sleep(0.5)  # để bước đang chạy xong → callback đóng generator

    asyncio.run(run())
    assert closed.wait(2)

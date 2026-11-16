"""
Heartbeat cho Server-Sent Events.

Cloudflare cắt kết nối nếu ~100 s không có byte nào. Agent RAG / rà soát văn bản có thể im lặng
lâu (gọi LLM, tra web) → chèn dòng comment SSE `: ping` mỗi `interval` giây khi generator chưa
có sự kiện mới. Theo chuẩn SSE, dòng bắt đầu bằng ":" là comment — trình duyệt/parser bỏ qua
(các parser trong frontend chỉ đọc dòng `data:`).

Generator gốc (đồng bộ) chạy trong threadpool, từng bước `next()` một; không gọi đồng thời.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Iterable, Iterator

from starlette.concurrency import run_in_threadpool

PING = ": ping\n\n"
DEFAULT_INTERVAL_S = 15.0

_END = object()


def _next(it: Iterator[str]):
    return next(it, _END)


async def with_heartbeat(events: Iterable[str], interval: float = DEFAULT_INTERVAL_S) -> AsyncIterator[str]:
    """Bọc generator SSE đồng bộ; yield PING sau mỗi `interval` giây không có sự kiện."""
    it = iter(events)
    pending: asyncio.Future | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(run_in_threadpool(_next, it))
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield PING
                continue
            item = pending.result()  # lỗi trong generator → ném ra như cũ
            pending = None
            if item is _END:
                return
            yield item
    finally:
        # Client ngắt / lỗi: đóng generator gốc. Nếu nó đang chạy dở trong thread thì đóng
        # sau khi bước hiện tại xong (không thể close generator đang thực thi).
        if pending is not None and not pending.done():
            pending.add_done_callback(lambda _f: _close(it))
        else:
            _close(it)


def _close(it: Iterator[str]) -> None:
    close = getattr(it, "close", None)
    if close is not None:
        try:
            close()
        except Exception:  # noqa: BLE001
            pass

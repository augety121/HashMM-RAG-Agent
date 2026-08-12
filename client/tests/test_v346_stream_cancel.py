from __future__ import annotations

import asyncio
import threading
import time

from hashmm.api.streaming import _stream_llm_async


class _SlowProvider:
    def __init__(self, delay: float = 0.2):
        self.delay = delay
        self.closed = threading.Event()

    def stream(self, messages, temp_override=0.0):
        try:
            # More than the think-tag detection buffer so the first event is
            # observable before the provider finishes the whole response.
            yield "首" * 501
            time.sleep(self.delay)
            yield "尾"
        finally:
            self.closed.set()


def test_sync_provider_is_forwarded_before_response_completion():
    async def scenario():
        provider = _SlowProvider(delay=0.25)
        stream = _stream_llm_async(provider, [{"role": "user", "content": "hi"}], 0.0)
        started = time.perf_counter()
        first = await asyncio.wait_for(anext(stream), timeout=0.15)
        assert first["type"] == "token"
        assert first["content"].startswith("首")
        assert time.perf_counter() - started < 0.2
        await stream.aclose()
        assert await asyncio.to_thread(provider.closed.wait, 1.0)

    asyncio.run(scenario())


def test_closing_async_stream_stops_sync_provider_iteration():
    async def scenario():
        provider = _SlowProvider(delay=0.03)
        stream = _stream_llm_async(provider, [{"role": "user", "content": "hi"}], 0.0)
        await asyncio.wait_for(anext(stream), timeout=0.5)
        await stream.aclose()
        assert await asyncio.to_thread(provider.closed.wait, 1.0)

    asyncio.run(scenario())

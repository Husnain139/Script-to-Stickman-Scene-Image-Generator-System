"""Server-sent events for the review page (spec §12.1): plan reloads, state changes, job progress and budget
warnings. The page refetches GET /api/project on each; an event only says that something changed."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

QUEUE_SIZE = 100  # a page that stops reading loses events, not the server's memory; it refetches anyway
RETRY_MS = 2000  # how soon the browser reconnects after the server restarts


class EventHub:
    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[tuple[str, dict[str, Any]]]] = set()

    def subscribe(self) -> asyncio.Queue[tuple[str, dict[str, Any]]]:
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[tuple[str, dict[str, Any]]]) -> None:
        self._queues.discard(queue)

    def publish(self, kind: str, data: dict[str, Any] | None = None) -> None:
        for queue in list(self._queues):
            try:
                queue.put_nowait((kind, data or {}))
            except asyncio.QueueFull:
                pass


def sse(kind: str, data: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def event_stream(
    hub: EventHub, is_disconnected: Callable[[], Awaitable[bool]], *, keepalive_s: float = 15.0
) -> AsyncIterator[str]:
    queue = hub.subscribe()
    try:
        yield f"retry: {RETRY_MS}\n\n"
        while not await is_disconnected():
            try:
                kind, data = await asyncio.wait_for(queue.get(), keepalive_s)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield sse(kind, data)
    finally:
        hub.unsubscribe(queue)

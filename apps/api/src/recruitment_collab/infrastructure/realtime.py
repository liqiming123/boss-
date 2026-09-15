"""In-process fan-out for time-sensitive duplicate alerts.

A cross-BOSS-account duplicate is actionable while the viewer is still looking
at the candidate, so the browser extension keeps one long-lived SSE connection
per device and the API pushes the alert as soon as it is created.  This bus is
deliberately ephemeral and process-local:

* it carries no candidate text, chat content, cookie or attachment data — only
  the identifiers and business labels already shown on the duplicate card;
* a subscriber that is slow, idle or disconnected simply loses the event; the
  authoritative record is still the PostgreSQL alert row and the Feishu direct
  message, and the next page check re-derives it from the table.

Nothing here is a durable queue, so no business logic may depend on an event
having been delivered.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

# Bounded per subscriber. A device that cannot keep up drops its oldest events
# instead of growing without limit or blocking the publishing request.
SUBSCRIBER_QUEUE_SIZE = 64


class RealtimeBus:
    """Thread-safe publish side with asyncio delivery to SSE subscribers."""

    def __init__(self) -> None:
        # company_id -> list of (event loop, queue). The loop is captured when
        # the subscriber attaches, which is the loop that owns the queue.
        self._subscribers: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]]] = {}
        self._lock = threading.Lock()

    def subscribe(self, company_id: str) -> tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        with self._lock:
            self._subscribers.setdefault(company_id, []).append((loop, queue))
        return loop, queue

    def unsubscribe(self, company_id: str, entry: tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]) -> None:
        with self._lock:
            subscribers = self._subscribers.get(company_id)
            if not subscribers:
                return
            try:
                subscribers.remove(entry)
            except ValueError:
                # Already removed by a concurrent disconnect; never raise from
                # a cleanup path.
                pass
            if not subscribers:
                self._subscribers.pop(company_id, None)

    def publish(self, company_id: str, event: dict[str, Any]) -> None:
        """Deliver to every subscriber of one company.

        Called from synchronous request/worker code, so it must never block or
        raise into the caller: a realtime delivery failure is not a business
        failure.
        """
        with self._lock:
            targets = list(self._subscribers.get(company_id, ()))
        for loop, queue in targets:
            try:
                loop.call_soon_threadsafe(self._offer, queue, event)
            except RuntimeError:
                # The loop is shutting down; the subscriber will be cleaned up
                # when its stream generator is closed.
                logger.debug("realtime bus: subscriber loop is closed")
            except Exception:  # pragma: no cover - defensive, never fatal
                logger.exception("realtime bus: publish failed")

    @staticmethod
    def _offer(queue: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
        while True:
            try:
                queue.put_nowait(event)
                return
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - racing consumer
                    return

    def subscriber_count(self, company_id: str) -> int:
        with self._lock:
            return len(self._subscribers.get(company_id, ()))


realtime_bus = RealtimeBus()


async def stream_company_events(company_id: str, recruiter_id: str = "", *, keepalive_seconds: float = 20.0) -> AsyncIterator[str]:
    """Yield server-sent events for one company until the client disconnects."""
    entry = realtime_bus.subscribe(company_id)
    queue = entry[1]
    try:
        yield ": connected\n\n"
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
            except asyncio.TimeoutError:
                # Proxies and the browser close idle connections; a comment
                # frame keeps the stream demonstrably alive.
                yield ": keep-alive\n\n"
                continue
            recipient = str(event.get("recipient_recruiter_id") or "")
            if recipient and recruiter_id and recipient != recruiter_id:
                continue
            yield f"event: {event.get('event', 'alert')}\ndata: {_dumps(event)}\n\n"
    finally:
        realtime_bus.unsubscribe(company_id, entry)


def _dumps(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False, separators=(",", ":"))


def drain_queue(queue: asyncio.Queue[dict[str, Any]]) -> deque[dict[str, Any]]:
    """Test helper: remove and return everything currently queued."""
    items: deque[dict[str, Any]] = deque()
    while True:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return items

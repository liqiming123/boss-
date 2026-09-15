"""The live alert channel is a best-effort delivery path, never a durable queue."""

import asyncio

import pytest

from recruitment_collab.infrastructure.realtime import RealtimeBus, stream_company_events


def test_publish_reaches_only_the_matching_company():
    async def scenario() -> None:
        bus = RealtimeBus()
        mine = bus.subscribe("company-a")
        other = bus.subscribe("company-b")
        bus.publish("company-a", {"event": "duplicate_lookup", "candidate_name": "候选人"})
        # call_soon_threadsafe defers delivery, so let the loop run once.
        await asyncio.sleep(0)
        assert mine[1].get_nowait()["candidate_name"] == "候选人"
        assert other[1].empty()

    asyncio.run(scenario())


def test_slow_subscriber_drops_oldest_instead_of_blocking_publish():
    async def scenario() -> None:
        bus = RealtimeBus()
        _, queue = bus.subscribe("company-a")
        for index in range(200):
            bus.publish("company-a", {"event": "duplicate_lookup", "sequence": index})
        await asyncio.sleep(0)
        # The queue stays bounded and keeps the newest events.
        remaining = []
        while not queue.empty():
            remaining.append(queue.get_nowait()["sequence"])
        assert 0 < len(remaining) <= queue.maxsize
        assert remaining[-1] == 199

    asyncio.run(scenario())


def test_unsubscribe_stops_delivery():
    async def scenario() -> None:
        bus = RealtimeBus()
        entry = bus.subscribe("company-a")
        assert bus.subscriber_count("company-a") == 1
        bus.unsubscribe("company-a", entry)
        assert bus.subscriber_count("company-a") == 0
        bus.publish("company-a", {"event": "duplicate_lookup"})
        await asyncio.sleep(0)
        assert entry[1].empty()

    asyncio.run(scenario())


def test_stream_emits_connected_frame_then_alert_frames():
    async def scenario() -> None:
        async def drive() -> list[str]:
            frames: list[str] = []
            async for frame in stream_company_events("company-a", keepalive_seconds=0.01):
                frames.append(frame)
                if len(frames) == 3:
                    break
            return frames

        task = asyncio.ensure_future(drive())
        # Wait for the generator to attach before publishing.
        for _ in range(50):
            await asyncio.sleep(0.01)
            from recruitment_collab.infrastructure.realtime import realtime_bus

            if realtime_bus.subscriber_count("company-a"):
                break
        from recruitment_collab.infrastructure.realtime import realtime_bus

        realtime_bus.publish("company-a", {"event": "duplicate_lookup", "candidate_name": "候选人"})
        frames = await asyncio.wait_for(task, timeout=2)
        assert frames[0] == ": connected\n\n"
        assert any("duplicate_lookup" in frame for frame in frames)
        assert any("候选人" in frame for frame in frames)

    asyncio.run(scenario())

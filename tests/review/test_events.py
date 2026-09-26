import asyncio
import json

from stickman.plan.store import file_hash
from stickman.review.events import EventHub, event_stream, sse
from stickman.review.watch import PlanWatcher


def test_an_event_is_written_the_way_server_sent_events_are():
    assert sse("plan", {"hash": "sha256:1"}) == 'event: plan\ndata: {"hash": "sha256:1"}\n\n'


def test_every_subscriber_gets_each_event_until_it_unsubscribes():
    async def go():
        hub = EventHub()
        first, second = hub.subscribe(), hub.subscribe()
        hub.publish("state")
        hub.unsubscribe(second)
        hub.publish("job", {"state": "started"})
        return [first.get_nowait(), first.get_nowait()], second.qsize()

    got, left = asyncio.run(go())
    assert got == [("state", {}), ("job", {"state": "started"})] and left == 1


def test_a_full_queue_drops_events_instead_of_blocking():
    async def go():
        hub = EventHub()
        queue = hub.subscribe()
        for _ in range(500):
            hub.publish("state")
        return queue.qsize()

    assert asyncio.run(go()) == 100


def test_the_stream_sends_events_and_ends_when_the_page_goes_away():
    async def go():
        hub = EventHub()
        gone = False

        async def disconnected():
            return gone

        stream = event_stream(hub, disconnected, keepalive_s=0.01)
        chunks = [await anext(stream)]
        hub.publish("plan", {"hash": "h"})
        chunks.append(await anext(stream))
        chunks.append(await anext(stream))  # nothing happened: a keepalive comment
        gone = True
        rest = [chunk async for chunk in stream]
        return chunks, rest, hub

    chunks, rest, hub = asyncio.run(go())
    assert chunks[0].startswith("retry: ")
    assert chunks[1] == 'event: plan\ndata: {"hash": "h"}\n\n'
    assert chunks[2] == ": keepalive\n\n"
    assert rest == [] and hub._queues == set()


def test_a_plan_change_by_someone_else_sends_a_plan_event(tmp_path):
    hub = EventHub()
    queue = asyncio.run(_subscribe(hub))
    (tmp_path / "plan.yaml").write_bytes(b"a: 1\n")  # exact bytes: write_text would translate \n to \r\n on Windows
    watcher = PlanWatcher(tmp_path, hub)
    (tmp_path / "plan.yaml").write_bytes(b"a: 2\n")
    assert watcher.changed([tmp_path / "plan.yaml"]) == ["plan"]
    assert queue.get_nowait() == ("plan", {"hash": file_hash(b"a: 2\n")})


def test_the_servers_own_plan_write_is_ignored(tmp_path):
    hub = EventHub()
    (tmp_path / "plan.yaml").write_bytes(b"a: 1\n")
    watcher = PlanWatcher(tmp_path, hub)
    (tmp_path / "plan.yaml").write_bytes(b"a: 2\n")
    watcher.wrote(file_hash(b"a: 2\n"))
    assert watcher.changed([tmp_path / "plan.yaml"]) == []
    assert watcher.changed([tmp_path / "plan.yaml"]) == []  # the same content again: still nothing new


def test_a_state_change_sends_a_state_event_and_other_files_nothing(tmp_path):
    watcher = PlanWatcher(tmp_path, EventHub())
    assert watcher.changed([tmp_path / "state.json", tmp_path / "state.json.tmp", tmp_path / ".lock"]) == ["state"]


async def _subscribe(hub):
    return hub.subscribe()

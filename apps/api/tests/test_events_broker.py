"""Section 7: in-process EventBroker fan-out."""

from __future__ import annotations

import asyncio

import pytest
from app.events import EventBroker


@pytest.mark.asyncio
async def test_broker_fanout_to_multiple_subscribers():
    broker = EventBroker()
    q1 = await broker.subscribe()
    q2 = await broker.subscribe()

    await broker.publish({"kind": "event", "symbol": "AAPL"})

    got1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    got2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert got1 == {"kind": "event", "symbol": "AAPL"}
    assert got2 == {"kind": "event", "symbol": "AAPL"}


@pytest.mark.asyncio
async def test_broker_unsubscribe_stops_delivery():
    broker = EventBroker()
    q = await broker.subscribe()
    await broker.unsubscribe(q)
    await broker.publish({"kind": "event", "symbol": "AAPL"})
    # Nothing should arrive.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.1)


@pytest.mark.asyncio
async def test_broker_slow_subscriber_drops_oldest():
    broker = EventBroker(max_queue_size=2)
    q = await broker.subscribe()
    await broker.publish({"i": 1})
    await broker.publish({"i": 2})
    await broker.publish({"i": 3})  # should evict i=1
    got = [await q.get(), await q.get()]
    assert {"i": 2} in got
    assert {"i": 3} in got
    assert {"i": 1} not in got

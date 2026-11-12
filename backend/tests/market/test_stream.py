"""Tests for the SSE stream (no HTTP server needed)."""

import json

import pytest
from fastapi.responses import StreamingResponse

from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource
from app.market.stream import (
    KEEPALIVE,
    STALE_AFTER_SECONDS,
    build_payload,
    create_stream_router,
    generate_events,
    is_market_closed,
)


def parse(event: str) -> dict:
    assert event.startswith("data: ")
    assert event.endswith("\n\n")
    return json.loads(event[len("data: "):])


class FakeRequest:
    """Minimal stand-in for starlette's Request: disconnects after N checks."""

    def __init__(self, connected_checks: int = 1):
        self._remaining = connected_checks
        self.client = None

    async def is_disconnected(self) -> bool:
        if self._remaining <= 0:
            return True
        self._remaining -= 1
        return False


class TestPayload:
    def test_shape(self):
        cache = PriceCache()
        cache.update("AAPL", 191.0, baseline=190.0)
        cache.update("MSFT", 420.0)
        body = parse(build_payload(cache, SimulatorDataSource(cache)))
        assert set(body) == {"prices", "market_closed"}
        assert set(body["prices"]) == {"AAPL", "MSFT"}
        aapl = body["prices"]["AAPL"]
        assert set(aapl) == {
            "ticker", "price", "previous_price", "baseline", "timestamp",
            "change", "change_percent", "daily_change_percent", "direction",
        }
        assert aapl["daily_change_percent"] == round(1 / 190 * 100, 4)

    def test_empty_cache(self):
        cache = PriceCache()
        body = parse(build_payload(cache, SimulatorDataSource(cache)))
        assert body == {"prices": {}, "market_closed": False}

    def test_single_line_event(self):
        cache = PriceCache()
        cache.update("AAPL", 191.0)
        event = build_payload(cache, SimulatorDataSource(cache))
        assert event.count("\n") == 2


class TestMarketClosed:
    def test_simulator_never_closed(self, monkeypatch):
        cache = PriceCache()
        monkeypatch.setattr(cache, "seconds_since_change", lambda: 10_000.0)
        assert is_market_closed(cache, SimulatorDataSource(cache)) is False

    def test_massive_closed_when_stale(self, monkeypatch):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        monkeypatch.setattr(cache, "seconds_since_change", lambda: STALE_AFTER_SECONDS + 1)
        live = MassiveDataSource("k", cache)
        assert is_market_closed(cache, live) is True
        assert parse(build_payload(cache, live))["market_closed"] is True

    def test_massive_open_when_fresh(self, monkeypatch):
        cache = PriceCache()
        monkeypatch.setattr(cache, "seconds_since_change", lambda: STALE_AFTER_SECONDS - 1)
        assert is_market_closed(cache, MassiveDataSource("k", cache)) is False


@pytest.mark.asyncio
class TestGenerator:
    async def test_sends_retry_then_data(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        req = FakeRequest(connected_checks=1)
        events = [e async for e in generate_events(cache, SimulatorDataSource(cache), req, 0.01)]
        assert events[0] == "retry: 1000\n\n"
        assert len(events) == 2
        assert "AAPL" in parse(events[1])["prices"]

    async def test_sends_initial_event_even_when_empty(self):
        cache = PriceCache()
        req = FakeRequest(connected_checks=1)
        events = [e async for e in generate_events(cache, SimulatorDataSource(cache), req, 0.01)]
        assert parse(events[1]) == {"prices": {}, "market_closed": False}

    async def test_only_sends_on_version_change(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        req = FakeRequest(connected_checks=5)
        events = [e async for e in generate_events(cache, SimulatorDataSource(cache), req, 0.001)]
        assert len(events) == 2  # retry + one data event; nothing changed afterwards

    async def test_sends_again_after_update(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        sim = SimulatorDataSource(cache)
        gen = generate_events(cache, sim, FakeRequest(connected_checks=3), 0.001)
        assert await gen.__anext__() == "retry: 1000\n\n"
        first = parse(await gen.__anext__())
        cache.update("AAPL", 191.0)
        second = parse(await gen.__anext__())
        assert first["prices"]["AAPL"]["price"] == 190.0
        assert second["prices"]["AAPL"]["price"] == 191.0
        assert second["prices"]["AAPL"]["direction"] == "up"
        await gen.aclose()

    async def test_removal_is_pushed(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        cache.update("MSFT", 420.0)
        gen = generate_events(cache, SimulatorDataSource(cache), FakeRequest(3), 0.001)
        await gen.__anext__()
        assert set(parse(await gen.__anext__())["prices"]) == {"AAPL", "MSFT"}
        cache.remove("MSFT")
        assert set(parse(await gen.__anext__())["prices"]) == {"AAPL"}
        await gen.aclose()

    async def test_stops_immediately_when_disconnected(self):
        cache = PriceCache()
        events = [e async for e in generate_events(cache, SimulatorDataSource(cache),
                                                   FakeRequest(0), 0.01)]
        assert events == ["retry: 1000\n\n"]


class TestRouter:
    def test_route_registered_once_per_router(self):
        cache = PriceCache()
        sim = SimulatorDataSource(cache)
        r1 = create_stream_router(cache, sim)
        r2 = create_stream_router(cache, sim)
        assert [r.path for r in r1.routes] == ["/api/stream/prices"]
        assert [r.path for r in r2.routes] == ["/api/stream/prices"]
        assert r1 is not r2

    async def test_endpoint_returns_event_stream(self):
        cache = PriceCache()
        router = create_stream_router(cache, SimulatorDataSource(cache))
        endpoint = router.routes[0].endpoint
        response = await endpoint(FakeRequest(0))
        assert isinstance(response, StreamingResponse)
        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"


@pytest.mark.asyncio
class TestKeepaliveAndClosedFlag:
    async def test_keepalive_sent_when_idle(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        events = [e async for e in generate_events(
            cache, SimulatorDataSource(cache), FakeRequest(6), 0.01, keepalive=0.02)]
        assert KEEPALIVE in events

    async def test_no_keepalive_when_busy(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        events = [e async for e in generate_events(
            cache, SimulatorDataSource(cache), FakeRequest(3), 0.001, keepalive=60)]
        assert KEEPALIVE not in events

    async def test_event_sent_when_market_closed_flips(self, monkeypatch):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        age = {"s": 0.0}
        monkeypatch.setattr(cache, "seconds_since_change", lambda: age["s"])
        gen = generate_events(cache, MassiveDataSource("k", cache), FakeRequest(4), 0.001)
        await anext(gen)  # retry
        assert parse(await anext(gen))["market_closed"] is False
        age["s"] = STALE_AFTER_SECONDS + 1  # no cache write, only the age changes
        assert parse(await anext(gen))["market_closed"] is True

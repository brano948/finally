"""Integration tests for SimulatorDataSource."""

import asyncio

import pytest

from app.market.cache import PriceCache
from app.market.seed_prices import SEED_PRICES
from app.market.simulator import SimulatorDataSource


@pytest.mark.asyncio
class TestSimulatorDataSource:
    """Integration tests for the SimulatorDataSource."""

    async def test_start_populates_cache(self):
        """Test that start() immediately populates the cache."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "GOOGL"])

        # Cache should have seed prices immediately (before first loop tick)
        assert cache.get("AAPL") is not None
        assert cache.get("GOOGL") is not None

        await source.stop()

    async def test_prices_update_over_time(self):
        """Test that prices are updated periodically."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.05)
        await source.start(["AAPL"])

        initial_version = cache.version
        await asyncio.sleep(0.3)  # Several update cycles

        # Version should have incremented (prices updated)
        assert cache.version > initial_version

        await source.stop()

    async def test_stop_is_clean(self):
        """Test that stop() is clean and idempotent."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])
        await source.stop()
        # Double stop should not raise
        await source.stop()

    async def test_add_ticker(self):
        """Test adding a ticker dynamically."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])

        await source.add_ticker("TSLA")
        assert "TSLA" in source.get_tickers()
        assert cache.get("TSLA") is not None

        await source.stop()

    async def test_remove_ticker(self):
        """Test removing a ticker."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "TSLA"])

        await source.remove_ticker("TSLA")
        assert "TSLA" not in source.get_tickers()
        assert cache.get("TSLA") is None

        await source.stop()

    async def test_get_tickers(self):
        """Test getting the list of active tickers."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "GOOGL"])

        tickers = source.get_tickers()
        assert set(tickers) == {"AAPL", "GOOGL"}

        await source.stop()

    async def test_empty_start(self):
        """Test starting with no tickers."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start([])

        assert len(cache) == 0
        assert source.get_tickers() == []

        await source.stop()

    async def test_exception_resilience(self):
        """Test that simulator continues running after errors."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.05)

        # Start with a valid ticker
        await source.start(["AAPL"])

        # Wait for some updates
        await asyncio.sleep(0.15)

        # Task should still be running
        assert source._task is not None
        assert not source._task.done()

        await source.stop()

    async def test_custom_update_interval(self):
        """Test using a custom update interval."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL"])

        initial_version = cache.version
        await asyncio.sleep(0.05)  # Should get ~5 updates

        # Should have multiple updates with fast interval
        assert cache.version > initial_version + 2

        await source.stop()

    async def test_baseline_is_seed(self):
        """Baseline for daily change % is the seed price, kept across ticks."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL"])
        await asyncio.sleep(0.05)
        update = cache.get("AAPL")
        assert update.baseline == SEED_PRICES["AAPL"]
        expected = round((update.price - SEED_PRICES["AAPL"]) / SEED_PRICES["AAPL"] * 100, 4)
        assert update.daily_change_percent == expected
        await source.stop()

    async def test_initial_cache_entry_is_seed(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=10)
        await source.start(["AAPL"])
        update = cache.get("AAPL")
        assert update.price == SEED_PRICES["AAPL"]
        assert update.direction == "flat"
        assert update.daily_change_percent == 0.0
        await source.stop()

    async def test_added_unknown_ticker_baseline_is_its_seed(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start([])
        await source.add_ticker("ZZZZ")
        seed = cache.get("ZZZZ").price
        await asyncio.sleep(0.05)
        assert cache.get("ZZZZ").baseline == seed
        await source.stop()

    async def test_validate_any_ticker_prices_it(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache)
        await source.start([])
        assert await source.validate_ticker("ZZZZ") is True
        assert 50 <= cache.get_price("ZZZZ") <= 300
        assert "ZZZZ" in source.get_tickers()
        await source.stop()

    async def test_validate_cached_ticker_is_noop(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=10)
        await source.start(["AAPL"])
        await asyncio.sleep(0.02)  # let the first step run; the next is 10s away
        v = cache.version
        assert await source.validate_ticker("AAPL") is True
        assert cache.version == v
        await source.stop()

    async def test_add_ticker_before_start(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=10)
        await source.add_ticker("AAPL")
        assert cache.get_price("AAPL") == SEED_PRICES["AAPL"]
        await source.start(["MSFT"])
        assert set(source.get_tickers()) == {"AAPL", "MSFT"}
        await source.stop()

    async def test_add_existing_ticker_does_not_reset_price(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL"])
        await asyncio.sleep(0.05)
        before = cache.get("AAPL")
        await source.add_ticker("AAPL")
        assert cache.get("AAPL") == before
        assert source.get_tickers().count("AAPL") == 1
        await source.stop()

    async def test_duplicate_start_tickers(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=10)
        await source.start(["AAPL", "AAPL"])
        assert source.get_tickers() == ["AAPL"]
        await source.stop()

    async def test_no_writes_after_stop(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL"])
        await asyncio.sleep(0.03)
        await source.stop()
        v = cache.version
        await asyncio.sleep(0.05)
        assert cache.version == v

    async def test_step_failure_does_not_kill_loop(self, monkeypatch):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL"])
        calls = {"n": 0}
        real_step = source._sim.step

        def flaky_step():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return real_step()

        monkeypatch.setattr(source._sim, "step", flaky_step)
        v = cache.version
        await asyncio.sleep(0.1)
        assert calls["n"] > 1
        assert cache.version > v
        assert not source._task.done()
        await source.stop()

    async def test_is_not_live_market(self):
        assert SimulatorDataSource(price_cache=PriceCache()).is_live_market is False

    async def test_custom_event_probability(self):
        """Test creating source with custom event probability."""
        cache = PriceCache()
        # Very high event probability for testing
        source = SimulatorDataSource(
            price_cache=cache, update_interval=0.1, event_probability=1.0
        )
        await source.start(["AAPL"])

        # Just verify it starts and stops cleanly
        await asyncio.sleep(0.2)
        await source.stop()

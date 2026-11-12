"""Tests for market data source factory."""

import os
from unittest.mock import patch

import pytest

from app.market.cache import PriceCache
from app.market.factory import create_market_data_source
from app.market.interface import MarketDataSource
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


class TestFactory:
    """Tests for create_market_data_source factory."""

    def test_creates_simulator_when_no_api_key(self):
        """Test that simulator is created when MASSIVE_API_KEY is not set."""
        cache = PriceCache()

        with patch.dict(os.environ, {}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)

    def test_creates_simulator_when_api_key_empty(self):
        """Test that simulator is created when MASSIVE_API_KEY is empty."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": ""}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)

    def test_creates_simulator_when_api_key_whitespace(self):
        """Test that simulator is created when MASSIVE_API_KEY is whitespace."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "   "}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)

    def test_creates_massive_when_api_key_set(self):
        """Test that Massive client is created when MASSIVE_API_KEY is set."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key"}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, MassiveDataSource)
        assert source._interval == 15.0
        assert source._cache is cache

    def test_simulator_uses_given_cache(self):
        cache = PriceCache()
        with patch.dict(os.environ, {}, clear=True):
            source = create_market_data_source(cache)
        assert source._cache is cache

    def test_both_sources_implement_interface(self):
        cache = PriceCache()
        with patch.dict(os.environ, {}, clear=True):
            sim = create_market_data_source(cache)
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "k"}, clear=True):
            massive = create_market_data_source(cache)
        assert isinstance(sim, MarketDataSource)
        assert isinstance(massive, MarketDataSource)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("5", 5.0), ("2.5", 2.5), ("0.1", 2.0), ("abc", 15.0), ("", 15.0), ("  ", 15.0)],
    )
    def test_massive_poll_interval_env(self, raw, expected):
        env = {"MASSIVE_API_KEY": "k", "MASSIVE_POLL_INTERVAL": raw}
        with patch.dict(os.environ, env, clear=True):
            source = create_market_data_source(PriceCache())
        assert source._interval == expected


class TestInterface:
    """The ABC itself."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            MarketDataSource()  # type: ignore[abstract]

    def test_validate_ticker_is_abstract(self):
        assert "validate_ticker" in MarketDataSource.__abstractmethods__

    def test_abstract_methods(self):
        assert MarketDataSource.__abstractmethods__ == {
            "start", "stop", "add_ticker", "remove_ticker", "get_tickers", "validate_ticker",
        }

    def test_is_live_market_defaults_false(self):
        class Minimal(MarketDataSource):
            async def start(self, tickers):
                pass

            async def stop(self):
                pass

            async def add_ticker(self, ticker):
                pass

            async def remove_ticker(self, ticker):
                pass

            def get_tickers(self):
                return []

            async def validate_ticker(self, ticker):
                return False

        assert Minimal().is_live_market is False


class TestPublicApi:
    def test_package_exports(self):
        import app.market as market

        for name in market.__all__:
            assert hasattr(market, name)
        assert {"PriceUpdate", "PriceCache", "MarketDataSource",
                "create_market_data_source", "create_stream_router"} <= set(market.__all__)

    def test_massive_receives_api_key(self):
        """Test that Massive client receives the API key."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key-123"}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, MassiveDataSource)
        assert source._api_key == "test-key-123"

    def test_simulator_receives_cache(self):
        """Test that simulator receives the cache reference."""
        cache = PriceCache()

        with patch.dict(os.environ, {}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)
        assert source._cache is cache

    def test_massive_receives_cache(self):
        """Test that Massive client receives the cache reference."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key"}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, MassiveDataSource)
        assert source._cache is cache

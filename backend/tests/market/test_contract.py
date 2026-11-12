"""Contract tests: both MarketDataSource implementations behave the same way."""

import asyncio

import pytest
from massive.rest.models import TickerSnapshot

from app.market.cache import PriceCache
from app.market.interface import MarketDataSource
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


def snap(ticker: str, price: float, prev: float) -> TickerSnapshot:
    return TickerSnapshot.from_dict({
        "ticker": ticker,
        "prevDay": {"c": prev},
        "lastTrade": {"p": price, "t": 1605192894630916600},
    })


@pytest.fixture(params=["sim", "massive"])
async def source(request, monkeypatch):
    cache = PriceCache()
    if request.param == "sim":
        src: MarketDataSource = SimulatorDataSource(cache, update_interval=0.01)
    else:
        src = MassiveDataSource("k", cache, poll_interval=0.01)
        monkeypatch.setattr(src._client, "get_snapshot_all",
                            lambda **kw: [snap(t, 100.0, 99.0) for t in kw["tickers"]])
        monkeypatch.setattr(src._client, "get_snapshot_ticker",
                            lambda market_type, ticker, **kw: snap(ticker, 50.0, 49.0))
    yield src, cache
    await src.stop()


async def test_is_market_data_source(source):
    src, _ = source
    assert isinstance(src, MarketDataSource)


async def test_start_fills_cache(source):
    src, cache = source
    await src.start(["AAPL", "MSFT"])
    assert {"AAPL", "MSFT"} <= set(cache.get_all())
    assert set(src.get_tickers()) == {"AAPL", "MSFT"}
    for update in cache.get_all().values():
        assert update.price > 0
        assert update.baseline > 0


async def test_prices_keep_flowing(source):
    src, cache = source
    await src.start(["AAPL"])
    v = cache.version
    await asyncio.sleep(0.1)
    assert cache.version > v


async def test_validate_then_add_and_remove(source):
    src, cache = source
    await src.start(["AAPL", "MSFT"])

    assert await src.validate_ticker("PYPL")
    assert cache.get_price("PYPL") is not None

    await src.add_ticker("PYPL")
    await src.add_ticker("PYPL")
    assert src.get_tickers().count("PYPL") == 1

    await src.remove_ticker("MSFT")
    await asyncio.sleep(0.05)  # let the loop tick; MSFT must not come back
    assert "MSFT" not in cache
    assert "MSFT" not in src.get_tickers()

    await src.remove_ticker("NOPE")  # no-op


async def test_stop_is_idempotent_and_final(source):
    src, cache = source
    await src.start(["AAPL"])
    await src.stop()
    await src.stop()
    v = cache.version
    await asyncio.sleep(0.05)
    assert cache.version == v


async def test_empty_start(source):
    src, cache = source
    await src.start([])
    assert len(cache) == 0
    assert src.get_tickers() == []

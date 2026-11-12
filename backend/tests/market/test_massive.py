"""Tests for the Massive data source.

Snapshots are built with the real ``TickerSnapshot.from_dict`` (not MagicMock),
so a wrong attribute name fails the test. Only the network calls on the
RESTClient instance are patched.
"""

import asyncio
import logging

import pytest
from massive.rest.models import SnapshotMarketType, TickerSnapshot

from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource, Quote, parse_snapshot

TRADE_NS = 1605192894630916600  # Unix nanoseconds (lastTrade.t)
MINUTE_MS = 1684428600000  # Unix milliseconds (min.t)


def snap(ticker, price=None, prev=None, trade_ns=TRADE_NS, minute=None, day=None):
    """Build a real TickerSnapshot from the JSON shape in MASSIVE_API.md."""
    d: dict = {"ticker": ticker}
    if price is not None:
        d["lastTrade"] = {"p": price, "s": 100, "t": trade_ns, "x": 4}
    if prev is not None:
        d["prevDay"] = {"o": prev, "h": prev, "l": prev, "c": prev, "v": 1}
    if minute is not None:
        d["min"] = {"c": minute, "t": MINUTE_MS}
    if day is not None:
        d["day"] = {"c": day}
    return TickerSnapshot.from_dict(d)


def make_source(cache=None, poll_interval=60.0):
    return MassiveDataSource(api_key="test-key", price_cache=cache or PriceCache(),
                             poll_interval=poll_interval)


# --- parse_snapshot (pure function) ---


class TestParseSnapshot:
    def test_full_sample_from_docs(self):
        s = TickerSnapshot.from_dict({
            "ticker": "AAPL",
            "todaysChange": 1.52,
            "todaysChangePerc": 0.8,
            "updated": TRADE_NS,
            "day": {"o": 190.1, "h": 192.0, "l": 189.5, "c": 191.6, "v": 41000000, "vw": 190.9},
            "min": {"o": 191.5, "h": 191.7, "l": 191.4, "c": 191.6, "v": 5000, "t": MINUTE_MS},
            "prevDay": {"o": 189.0, "h": 190.5, "l": 188.2, "c": 190.08, "v": 52000000},
            "lastTrade": {"p": 191.6, "s": 100, "t": TRADE_NS, "x": 4, "i": "71675577320245"},
            "lastQuote": {"P": 191.62, "S": 2, "p": 191.58, "s": 3, "t": 1605192959994246100},
        })
        q = parse_snapshot(s)
        assert isinstance(q, Quote)
        assert (q.ticker, q.price, q.baseline) == ("AAPL", 191.6, 190.08)
        assert q.timestamp == pytest.approx(1605192894.6309166)

    def test_last_trade_ns_to_seconds(self):
        q = parse_snapshot(snap("AAPL", 191.6, 190.08))
        assert (q.price, q.baseline) == (191.6, 190.08)
        assert q.timestamp == pytest.approx(TRADE_NS / 1e9)

    def test_last_trade_preferred_over_minute_and_day(self):
        q = parse_snapshot(snap("AAPL", price=191.6, minute=191.0, day=190.5))
        assert q.price == 191.6

    def test_last_trade_without_timestamp_means_now(self):
        q = parse_snapshot(snap("AAPL", price=191.6, trade_ns=None))
        assert q.timestamp is None

    def test_falls_back_to_minute_bar_ms(self):
        q = parse_snapshot(snap("A", minute=10.5, prev=10.0))
        assert q.price == 10.5
        assert q.baseline == 10.0
        assert q.timestamp == pytest.approx(MINUTE_MS / 1e3)

    def test_falls_back_to_day_close(self):
        q = parse_snapshot(snap("X", day=10.0, prev=9.0))
        assert q == Quote(ticker="X", price=10.0, timestamp=None, baseline=9.0)

    def test_no_price_returns_none(self):
        assert parse_snapshot(snap("A")) is None
        assert parse_snapshot(snap("A", prev=10.0)) is None

    def test_zero_prices_are_ignored(self):
        assert parse_snapshot(snap("A", price=0.0, day=0.0)) is None

    def test_missing_prev_day_gives_no_baseline(self):
        assert parse_snapshot(snap("A", price=10.0)).baseline is None


# --- polling ---


@pytest.mark.asyncio
class TestPolling:
    async def test_poll_updates_cache_with_baseline(self, monkeypatch):
        cache = PriceCache()
        source = make_source(cache)
        source._tickers = ["AAPL", "GOOGL"]
        monkeypatch.setattr(source._client, "get_snapshot_all", lambda **kw: [
            snap("AAPL", 191.6, 190.08),
            snap("GOOGL", 175.25, 170.0),
        ])
        await source._poll_once()

        aapl = cache.get("AAPL")
        assert aapl.price == 191.6
        assert aapl.baseline == 190.08
        assert aapl.daily_change_percent == pytest.approx(0.7997, abs=1e-3)
        assert aapl.timestamp == pytest.approx(TRADE_NS / 1e9)
        assert cache.get_price("GOOGL") == 175.25

    async def test_poll_passes_market_type_and_tickers(self, monkeypatch):
        source = make_source()
        source._tickers = ["AAPL", "MSFT"]
        seen = {}

        def fake(**kw):
            seen.update(kw)
            return []

        monkeypatch.setattr(source._client, "get_snapshot_all", fake)
        await source._poll_once()
        assert seen["market_type"] == SnapshotMarketType.STOCKS
        assert seen["tickers"] == ["AAPL", "MSFT"]

    async def test_snapshot_without_price_is_skipped(self, monkeypatch):
        cache = PriceCache()
        source = make_source(cache)
        source._tickers = ["AAPL", "BAD"]
        monkeypatch.setattr(source._client, "get_snapshot_all",
                            lambda **kw: [snap("AAPL", 190.5), snap("BAD")])
        await source._poll_once()
        assert cache.get_price("AAPL") == 190.5
        assert cache.get_price("BAD") is None

    async def test_snapshot_without_last_trade_uses_day_close(self, monkeypatch):
        cache = PriceCache()
        source = make_source(cache)
        source._tickers = ["AAPL"]
        monkeypatch.setattr(source._client, "get_snapshot_all",
                            lambda **kw: [snap("AAPL", day=191.0, prev=190.0)])
        await source._poll_once()
        assert cache.get_price("AAPL") == 191.0
        assert cache.get("AAPL").baseline == 190.0

    async def test_untracked_snapshot_ignored(self, monkeypatch):
        """A ticker removed while a poll was in flight is not re-added."""
        cache = PriceCache()
        source = make_source(cache)
        source._tickers = ["AAPL"]
        monkeypatch.setattr(source._client, "get_snapshot_all",
                            lambda **kw: [snap("AAPL", 190.0), snap("MSFT", 420.0)])
        await source._poll_once()
        assert "MSFT" not in cache

    async def test_ticker_removed_mid_poll_not_written(self, monkeypatch):
        cache = PriceCache()
        source = make_source(cache)
        source._tickers = ["AAPL", "MSFT"]

        def slow_poll(**kw):
            # Simulate remove_ticker running while the HTTP call is in flight.
            source._tickers.remove("MSFT")
            return [snap("AAPL", 190.0), snap("MSFT", 420.0)]

        monkeypatch.setattr(source._client, "get_snapshot_all", slow_poll)
        await source._poll_once()
        assert "AAPL" in cache
        assert "MSFT" not in cache

    async def test_poll_failure_keeps_last_prices(self, monkeypatch):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        source = make_source(cache)
        source._tickers = ["AAPL"]

        def boom(**kw):
            raise RuntimeError("403 NOT_AUTHORIZED")

        monkeypatch.setattr(source._client, "get_snapshot_all", boom)
        await source._poll_once()  # must not raise
        assert cache.get_price("AAPL") == 190.0

    async def test_repeated_failures_logged_once_at_error(self, monkeypatch, caplog):
        source = make_source()
        source._tickers = ["AAPL"]

        def boom(**kw):
            raise RuntimeError("401")

        monkeypatch.setattr(source._client, "get_snapshot_all", boom)
        with caplog.at_level(logging.DEBUG, logger="app.market.massive_client"):
            for _ in range(3):
                await source._poll_once()
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1

    async def test_error_logged_again_after_recovery(self, monkeypatch, caplog):
        source = make_source()
        source._tickers = ["AAPL"]
        state = {"fail": True}

        def flaky(**kw):
            if state["fail"]:
                raise RuntimeError("down")
            return [snap("AAPL", 190.0)]

        monkeypatch.setattr(source._client, "get_snapshot_all", flaky)
        with caplog.at_level(logging.ERROR, logger="app.market.massive_client"):
            await source._poll_once()
            state["fail"] = False
            await source._poll_once()
            state["fail"] = True
            await source._poll_once()
        assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 2

    async def test_empty_tickers_skips_poll(self, monkeypatch):
        source = make_source()
        calls = []
        monkeypatch.setattr(source._client, "get_snapshot_all",
                            lambda **kw: calls.append(kw) or [])
        await source._poll_once()
        assert calls == []

    async def test_start_does_immediate_poll(self, monkeypatch):
        cache = PriceCache()
        source = make_source(cache)
        monkeypatch.setattr(source._client, "get_snapshot_all",
                            lambda **kw: [snap("AAPL", 190.5, 189.0)])
        await source.start(["AAPL"])
        assert cache.get_price("AAPL") == 190.5
        await source.stop()

    async def test_start_survives_failed_first_poll(self, monkeypatch):
        source = make_source()

        def boom(**kw):
            raise RuntimeError("network")

        monkeypatch.setattr(source._client, "get_snapshot_all", boom)
        await source.start(["AAPL"])
        assert source._task is not None and not source._task.done()
        await source.stop()

    async def test_start_dedupes_tickers(self, monkeypatch):
        source = make_source()
        monkeypatch.setattr(source._client, "get_snapshot_all", lambda **kw: [])
        await source.start(["AAPL", "AAPL", "MSFT"])
        assert source.get_tickers() == ["AAPL", "MSFT"]
        await source.stop()

    async def test_poll_loop_polls_repeatedly(self, monkeypatch):
        cache = PriceCache()
        source = make_source(cache, poll_interval=0.01)
        prices = iter([100.0 + i for i in range(1000)])
        monkeypatch.setattr(source._client, "get_snapshot_all",
                            lambda **kw: [snap("AAPL", next(prices))])
        await source.start(["AAPL"])
        await asyncio.sleep(0.1)
        await source.stop()
        assert cache.get_price("AAPL") > 100.0  # at least one poll after the initial one

    async def test_stop_cancels_task_and_is_idempotent(self, monkeypatch):
        source = make_source(poll_interval=10.0)
        monkeypatch.setattr(source._client, "get_snapshot_all", lambda **kw: [])
        await source.start(["AAPL"])
        assert not source._task.done()
        await source.stop()
        assert source._task is None
        await source.stop()  # Should not raise

    async def test_stop_before_start(self):
        await make_source().stop()


# --- ticker management and validation ---


@pytest.mark.asyncio
class TestTickers:
    async def test_add_ticker(self):
        source = make_source()
        await source.add_ticker("AAPL")
        await source.add_ticker("AAPL")
        assert source.get_tickers() == ["AAPL"]

    async def test_remove_ticker_drops_from_cache(self):
        cache = PriceCache()
        source = make_source(cache)
        source._tickers = ["AAPL", "GOOGL"]
        cache.update("AAPL", 190.00)
        await source.remove_ticker("AAPL")
        assert source.get_tickers() == ["GOOGL"]
        assert cache.get("AAPL") is None

    async def test_remove_absent_ticker_is_noop(self):
        source = make_source()
        await source.remove_ticker("NOPE")
        assert source.get_tickers() == []

    async def test_get_tickers_returns_copy(self):
        source = make_source()
        source._tickers = ["AAPL"]
        source.get_tickers().append("X")
        assert source.get_tickers() == ["AAPL"]

    async def test_is_live_market(self):
        assert make_source().is_live_market is True

    async def test_validate_cached_ticker_makes_no_call(self, monkeypatch):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        source = make_source(cache)
        calls = []
        monkeypatch.setattr(source._client, "get_snapshot_ticker",
                            lambda *a, **kw: calls.append(a))
        assert await source.validate_ticker("AAPL") is True
        assert calls == []

    async def test_validate_known_ticker_caches_and_tracks(self, monkeypatch):
        cache = PriceCache()
        source = make_source(cache)
        seen = []

        def fake(market_type, ticker, **kw):
            seen.append((market_type, ticker))
            return snap(ticker, 70.0, 69.0)

        monkeypatch.setattr(source._client, "get_snapshot_ticker", fake)
        assert await source.validate_ticker("PYPL") is True
        assert seen == [(SnapshotMarketType.STOCKS, "PYPL")]
        assert cache.get_price("PYPL") == 70.0
        assert cache.get("PYPL").baseline == 69.0
        assert "PYPL" in source.get_tickers()

    async def test_validate_unknown_ticker_false(self, monkeypatch):
        cache = PriceCache()
        source = make_source(cache)

        def not_found(*a, **kw):
            raise Exception("NOT_FOUND")

        monkeypatch.setattr(source._client, "get_snapshot_ticker", not_found)
        assert await source.validate_ticker("ZZZZZ") is False
        assert "ZZZZZ" not in cache
        assert "ZZZZZ" not in source.get_tickers()

    async def test_validate_ticker_without_price_false(self, monkeypatch):
        cache = PriceCache()
        source = make_source(cache)
        monkeypatch.setattr(source._client, "get_snapshot_ticker",
                            lambda *a, **kw: snap("ZZZ"))
        assert await source.validate_ticker("ZZZ") is False
        assert "ZZZ" not in cache
        assert source.get_tickers() == []

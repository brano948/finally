"""Tests for PriceCache."""

import threading

from app.market.cache import PriceCache


class TestPriceCache:
    """Unit tests for the PriceCache."""

    def test_update_and_get(self):
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.ticker == "AAPL"
        assert update.price == 190.50
        assert cache.get("AAPL") == update

    def test_get_unknown_is_none(self):
        assert PriceCache().get("NOPE") is None

    def test_first_update_is_flat(self):
        update = PriceCache().update("AAPL", 190.50)
        assert update.direction == "flat"
        assert update.previous_price == 190.50

    def test_direction_up(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 191.00)
        assert update.direction == "up"
        assert update.change == 1.00
        assert update.previous_price == 190.00

    def test_direction_down(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 189.00)
        assert update.direction == "down"
        assert update.change == -1.00

    def test_remove(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.remove("AAPL")
        assert cache.get("AAPL") is None
        assert "AAPL" not in cache

    def test_remove_nonexistent(self):
        PriceCache().remove("AAPL")  # Should not raise

    def test_remove_bumps_version(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        v = cache.version
        cache.remove("AAPL")
        assert cache.version == v + 1

    def test_readd_after_remove_starts_fresh(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00, baseline=180.0)
        cache.remove("AAPL")
        update = cache.update("AAPL", 200.00)
        assert update.previous_price == 200.00
        assert update.baseline == 200.00

    def test_get_all(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("GOOGL", 175.00)
        assert set(cache.get_all().keys()) == {"AAPL", "GOOGL"}

    def test_get_all_returns_copy(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        snapshot = cache.get_all()
        snapshot.clear()
        assert "AAPL" in cache

    def test_version_increments(self):
        cache = PriceCache()
        v0 = cache.version
        cache.update("AAPL", 190.00)
        assert cache.version == v0 + 1
        cache.update("AAPL", 191.00)
        assert cache.version == v0 + 2

    def test_version_bumps_even_when_price_unchanged(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        v = cache.version
        cache.update("AAPL", 190.00)
        assert cache.version == v + 1

    def test_get_price_convenience(self):
        cache = PriceCache()
        cache.update("AAPL", 190.50)
        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("NOPE") is None

    def test_len(self):
        cache = PriceCache()
        assert len(cache) == 0
        cache.update("AAPL", 190.00)
        assert len(cache) == 1
        cache.update("GOOGL", 175.00)
        assert len(cache) == 2

    def test_contains(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        assert "AAPL" in cache
        assert "GOOGL" not in cache

    def test_custom_timestamp(self):
        update = PriceCache().update("AAPL", 190.50, timestamp=1234567890.0)
        assert update.timestamp == 1234567890.0

    def test_zero_timestamp_kept(self):
        assert PriceCache().update("AAPL", 1.0, timestamp=0.0).timestamp == 0.0

    def test_price_rounding(self):
        assert PriceCache().update("AAPL", 190.12345).price == 190.12

    # --- baseline ---

    def test_baseline_defaults_to_first_price(self):
        assert PriceCache().update("AAPL", 190.0).baseline == 190.0

    def test_baseline_set_on_first_update_and_kept(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0, baseline=188.0)
        update = cache.update("AAPL", 191.0)
        assert update.baseline == 188.0
        assert update.daily_change_percent == round(3 / 188 * 100, 4)

    def test_explicit_baseline_replaces_old(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0, baseline=188.0)
        assert cache.update("AAPL", 191.0, baseline=189.0).baseline == 189.0

    def test_baseline_rounded(self):
        assert PriceCache().update("AAPL", 190.0, baseline=188.12345).baseline == 188.12

    # --- staleness clock ---

    def test_unchanged_price_does_not_reset_change_clock(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr("app.market.cache.time.time", lambda: now[0])
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        now[0] = 1100.0
        cache.update("AAPL", 190.0)  # same price, 100s later
        assert cache.seconds_since_change() == 100.0

    def test_changed_price_resets_change_clock(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr("app.market.cache.time.time", lambda: now[0])
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        now[0] = 1100.0
        cache.update("AAPL", 190.5)
        assert cache.seconds_since_change() == 0.0

    def test_new_ticker_resets_change_clock(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr("app.market.cache.time.time", lambda: now[0])
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        now[0] = 1100.0
        cache.update("MSFT", 420.0)
        assert cache.seconds_since_change() == 0.0

    # --- concurrency ---

    def test_concurrent_writers(self):
        cache = PriceCache()
        tickers = [f"T{i}" for i in range(8)]
        per_thread = 500

        def writer(ticker: str) -> None:
            for i in range(per_thread):
                cache.update(ticker, 100.0 + i)

        threads = [threading.Thread(target=writer, args=(t,)) for t in tickers]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert cache.version == len(tickers) * per_thread
        assert len(cache) == len(tickers)
        for t in tickers:
            assert cache.get_price(t) == 100.0 + per_thread - 1

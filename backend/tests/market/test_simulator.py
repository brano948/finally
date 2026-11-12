"""Tests for GBMSimulator."""

import math

import numpy as np
import pytest

from app.market.seed_prices import DEFAULT_PARAMS, SEED_PRICES
from app.market.simulator import GBMSimulator


class TestGBMSimulator:
    """Unit tests for the GBM price simulator."""

    def test_step_returns_all_tickers(self):
        """Test that step() returns prices for all tickers."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        result = sim.step()
        assert set(result.keys()) == {"AAPL", "GOOGL"}

    def test_prices_are_positive(self):
        """GBM prices can never go negative (exp() is always positive)."""
        sim = GBMSimulator(tickers=["AAPL"])
        for _ in range(10_000):
            prices = sim.step()
            assert prices["AAPL"] > 0

    def test_initial_prices_match_seeds(self):
        """Test that initial prices match seed prices."""
        sim = GBMSimulator(tickers=["AAPL"])
        # Before any step, price should be the seed price
        assert sim.get_price("AAPL") == SEED_PRICES["AAPL"]

    def test_add_ticker(self):
        """Test adding a ticker dynamically."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("TSLA")
        result = sim.step()
        assert "TSLA" in result

    def test_remove_ticker(self):
        """Test removing a ticker."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        sim.remove_ticker("GOOGL")
        result = sim.step()
        assert "GOOGL" not in result
        assert "AAPL" in result

    def test_add_duplicate_is_noop(self):
        """Test that adding a duplicate ticker is a no-op."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("AAPL")
        assert len(sim._tickers) == 1

    def test_remove_nonexistent_is_noop(self):
        """Test that removing a non-existent ticker is a no-op."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.remove_ticker("NOPE")  # Should not raise

    def test_unknown_ticker_gets_random_seed_price(self):
        """Test that unknown tickers get random seed prices."""
        sim = GBMSimulator(tickers=["ZZZZ"])
        price = sim.get_price("ZZZZ")
        assert price is not None
        assert 50.0 <= price <= 300.0

    def test_empty_step(self):
        """Test stepping with no tickers."""
        sim = GBMSimulator(tickers=[])
        result = sim.step()
        assert result == {}

    def test_prices_change_over_time(self):
        """After many steps, prices should have drifted from their seeds."""
        sim = GBMSimulator(tickers=["AAPL"])
        initial_price = sim.get_price("AAPL")

        for _ in range(1000):
            sim.step()

        final_price = sim.get_price("AAPL")
        # Price should have changed (extremely unlikely to be exactly the seed)
        assert final_price != initial_price

    def test_cholesky_rebuilds_on_add(self):
        """Test that Cholesky matrix is rebuilt when tickers are added."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None  # Only 1 ticker, no correlation matrix
        sim.add_ticker("GOOGL")
        assert sim._cholesky is not None  # Now 2 tickers, matrix exists

    def test_cholesky_none_with_one_ticker(self):
        """Test that Cholesky is None with only one ticker."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None

    def test_get_price_returns_none_for_unknown(self):
        """Test that get_price returns None for unknown ticker."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim.get_price("UNKNOWN") is None

    def test_pairwise_correlation_tech_stocks(self):
        """Test that tech stocks have high correlation."""
        corr = GBMSimulator._pairwise_correlation("AAPL", "GOOGL")
        assert corr == 0.6

    def test_pairwise_correlation_finance_stocks(self):
        """Test that finance stocks have moderate correlation."""
        corr = GBMSimulator._pairwise_correlation("JPM", "V")
        assert corr == 0.5

    def test_pairwise_correlation_tsla(self):
        """Test that TSLA has lower correlation with everything."""
        corr = GBMSimulator._pairwise_correlation("TSLA", "AAPL")
        assert corr == 0.3
        corr = GBMSimulator._pairwise_correlation("TSLA", "JPM")
        assert corr == 0.3

    def test_pairwise_correlation_cross_sector(self):
        """Test cross-sector correlation."""
        corr = GBMSimulator._pairwise_correlation("AAPL", "JPM")
        assert corr == 0.3

    def test_default_dt_is_reasonable(self):
        """Test that default dt is a reasonable small value."""
        assert 0 < GBMSimulator.DEFAULT_DT < 0.0001

    def test_prices_rounded_to_two_decimals(self):
        """Test that prices are rounded to 2 decimal places."""
        sim = GBMSimulator(tickers=["AAPL"])
        result = sim.step()
        price_str = str(result["AAPL"])
        # Check that we have at most 2 decimal places
        if "." in price_str:
            decimal_part = price_str.split(".")[1]
            assert len(decimal_part) <= 2


class TestGBMSeedsAndMath:
    """Seed tracking and statistical checks on the GBM math."""

    def test_get_seed_known_ticker(self):
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim.get_seed("AAPL") == SEED_PRICES["AAPL"]

    def test_seed_unchanged_after_steps(self):
        sim = GBMSimulator(tickers=["AAPL"])
        for _ in range(100):
            sim.step()
        assert sim.get_seed("AAPL") == SEED_PRICES["AAPL"]

    def test_get_seed_unknown_ticker_matches_initial_price(self):
        sim = GBMSimulator(tickers=["ZZZZ"])
        seed = sim.get_seed("ZZZZ")
        assert seed == sim.get_price("ZZZZ")
        assert 50.0 <= seed <= 300.0
        assert seed == round(seed, 2)

    def test_get_seed_none_for_untracked(self):
        assert GBMSimulator(tickers=["AAPL"]).get_seed("NOPE") is None

    def test_remove_ticker_clears_seed(self):
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        sim.remove_ticker("AAPL")
        assert sim.get_seed("AAPL") is None
        assert sim.get_tickers() == ["GOOGL"]

    def test_unknown_ticker_gets_default_params(self):
        sim = GBMSimulator(tickers=["ZZZZ"])
        assert sim._params["ZZZZ"] == DEFAULT_PARAMS

    def test_unknown_ticker_params_are_independent_copies(self):
        sim = GBMSimulator(tickers=["ZZZZ", "YYYY"])
        sim._params["ZZZZ"]["sigma"] = 9.9
        assert sim._params["YYYY"] == DEFAULT_PARAMS

    def test_unknown_ticker_correlation_is_cross_group(self):
        assert GBMSimulator._pairwise_correlation("ZZZZ", "AAPL") == 0.3

    def test_zero_volatility_is_pure_drift(self):
        """With sigma=0 and no events, S(t+dt) = S(t) * exp(mu * dt) exactly."""
        sim = GBMSimulator(tickers=["AAPL"], dt=0.01, event_probability=0.0)
        sim._params["AAPL"] = {"sigma": 0.0, "mu": 0.1}
        sim.step()
        expected = SEED_PRICES["AAPL"] * math.exp(0.1 * 0.01)
        assert math.isclose(sim.get_price("AAPL"), expected, rel_tol=1e-12)

    def test_log_return_volatility_matches_sigma(self):
        """Std of one-step log returns is sigma * sqrt(dt)."""
        np.random.seed(42)
        dt = 1e-4
        sim = GBMSimulator(tickers=["AAPL"], dt=dt, event_probability=0.0)
        sigma = sim._params["AAPL"]["sigma"]
        prev = sim.get_price("AAPL")
        returns = []
        for _ in range(20_000):
            sim.step()
            cur = sim.get_price("AAPL")
            returns.append(math.log(cur / prev))
            prev = cur
        assert float(np.std(returns)) == pytest.approx(sigma * math.sqrt(dt), rel=0.05)

    def test_tech_stocks_are_correlated(self):
        """Sample correlation of AAPL/GOOGL log returns is about 0.6."""
        np.random.seed(7)
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"], dt=1e-4, event_probability=0.0)
        prev = {t: sim.get_price(t) for t in ("AAPL", "GOOGL")}
        rets: dict[str, list[float]] = {"AAPL": [], "GOOGL": []}
        for _ in range(10_000):
            sim.step()
            for t in rets:
                cur = sim.get_price(t)
                rets[t].append(math.log(cur / prev[t]))
                prev[t] = cur
        corr = float(np.corrcoef(rets["AAPL"], rets["GOOGL"])[0, 1])
        assert corr == pytest.approx(0.6, abs=0.05)

    def test_cholesky_valid_for_default_watchlist(self):
        """The full default correlation matrix is positive definite and unit-diagonal."""
        sim = GBMSimulator(tickers=list(SEED_PRICES))
        assert sim._cholesky is not None
        corr = sim._cholesky @ sim._cholesky.T
        assert np.allclose(np.diag(corr), 1.0)
        i, j = sim.get_tickers().index("AAPL"), sim.get_tickers().index("GOOGL")
        assert corr[i, j] == pytest.approx(0.6)

    def test_cholesky_rebuilds_on_remove(self):
        sim = GBMSimulator(tickers=["AAPL", "GOOGL", "MSFT"])
        assert sim._cholesky.shape == (3, 3)
        sim.remove_ticker("MSFT")
        assert sim._cholesky.shape == (2, 2)
        sim.remove_ticker("GOOGL")
        assert sim._cholesky is None

    def test_event_probability_one_always_shocks(self):
        """With event_probability=1 every tick moves roughly 2-5%."""
        sim = GBMSimulator(tickers=["AAPL"], event_probability=1.0)
        before = sim.get_price("AAPL")
        sim.step()
        move = abs(sim.get_price("AAPL") / before - 1)
        assert 0.019 <= move <= 0.051

    def test_event_probability_zero_has_tiny_moves(self):
        """Default dt gives sub-percent moves per tick when there are no events."""
        sim = GBMSimulator(tickers=["AAPL"], event_probability=0.0)
        for _ in range(100):
            before = sim.get_price("AAPL")
            sim.step()
            assert abs(sim.get_price("AAPL") / before - 1) < 0.01

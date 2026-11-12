"""Tests for PriceUpdate dataclass."""

import time

import pytest

from app.market.models import PriceUpdate

TS = 1234567890.0


def make(price: float, previous: float, baseline: float = 100.0) -> PriceUpdate:
    return PriceUpdate(
        ticker="AAPL", price=price, previous_price=previous, baseline=baseline, timestamp=TS
    )


class TestPriceUpdate:
    """Unit tests for the PriceUpdate model."""

    def test_creation(self):
        update = make(190.50, 190.00, 188.0)
        assert update.ticker == "AAPL"
        assert update.price == 190.50
        assert update.previous_price == 190.00
        assert update.baseline == 188.0
        assert update.timestamp == TS

    def test_default_timestamp_is_now(self):
        before = time.time()
        update = PriceUpdate(ticker="AAPL", price=1.0, previous_price=1.0, baseline=1.0)
        assert before <= update.timestamp <= time.time()

    def test_change_positive(self):
        assert make(190.50, 190.00).change == 0.50

    def test_change_negative(self):
        assert make(189.50, 190.00).change == -0.50

    def test_change_percent_up(self):
        assert make(190.00, 100.00).change_percent == 90.0

    def test_change_percent_down(self):
        assert make(100.00, 200.00).change_percent == -50.0

    def test_change_percent_zero_previous(self):
        assert make(100.00, 0.00).change_percent == 0.0

    def test_daily_change_percent_uses_baseline(self):
        update = make(200.0, 199.0, baseline=190.0)
        assert update.daily_change_percent == round(10 / 190 * 100, 4)

    def test_daily_change_percent_negative(self):
        assert make(90.0, 91.0, baseline=100.0).daily_change_percent == -10.0

    def test_daily_change_percent_zero_baseline(self):
        assert make(100.0, 100.0, baseline=0.0).daily_change_percent == 0.0

    def test_daily_change_is_independent_of_tick_change(self):
        update = make(110.0, 110.0, baseline=100.0)
        assert update.change_percent == 0.0
        assert update.daily_change_percent == 10.0

    def test_direction_up(self):
        assert make(191.00, 190.00).direction == "up"

    def test_direction_down(self):
        assert make(189.00, 190.00).direction == "down"

    def test_direction_flat(self):
        assert make(190.00, 190.00).direction == "flat"

    def test_to_dict(self):
        result = make(190.50, 190.00, baseline=190.08).to_dict()
        assert result == {
            "ticker": "AAPL",
            "price": 190.50,
            "previous_price": 190.00,
            "baseline": 190.08,
            "timestamp": TS,
            "change": 0.50,
            "change_percent": 0.2632,  # 0.50 / 190.00 * 100
            "daily_change_percent": 0.2210,  # 0.42 / 190.08 * 100
            "direction": "up",
        }

    def test_immutability(self):
        update = make(190.50, 190.00)
        with pytest.raises(AttributeError):
            update.price = 200.00  # type: ignore[misc]

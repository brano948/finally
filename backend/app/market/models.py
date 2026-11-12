"""Data models for market data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of one ticker's price.

    baseline is the reference for daily change: the seed price (simulator)
    or the previous close (Massive).
    """

    ticker: str
    price: float
    previous_price: float
    baseline: float
    timestamp: float = field(default_factory=time.time)  # Unix seconds

    @property
    def change(self) -> float:
        """Tick-to-tick absolute change."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        """Tick-to-tick percent change."""
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def daily_change_percent(self) -> float:
        """Percent change versus the baseline (seed price or previous close)."""
        if self.baseline == 0:
            return 0.0
        return round((self.price - self.baseline) / self.baseline * 100, 4)

    @property
    def direction(self) -> str:
        """'up', 'down' or 'flat' versus the previous price."""
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    def to_dict(self) -> dict:
        """JSON form used by SSE and REST responses."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "baseline": self.baseline,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "daily_change_percent": self.daily_change_percent,
            "direction": self.direction,
        }

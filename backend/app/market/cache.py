"""Thread-safe in-memory price cache."""

from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """Latest PriceUpdate per ticker.

    Writers: the active MarketDataSource (simulator loop or Massive poller; both write on the event loop).
    Readers: SSE, portfolio valuation, trade execution, watchlist.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version = 0
        self._last_change_at = time.time()

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        baseline: float | None = None,
    ) -> PriceUpdate:
        """Store a new price and return the resulting PriceUpdate.

        previous_price is the prior cached price (or the price itself on the
        first update). baseline defaults to the prior baseline, then to price.
        """
        price = round(price, 2)
        with self._lock:
            prev = self._prices.get(ticker)
            previous_price = prev.price if prev else price
            if baseline is None:
                baseline = prev.baseline if prev else price
            update = PriceUpdate(
                ticker=ticker,
                price=price,
                previous_price=previous_price,
                baseline=round(baseline, 2),
                timestamp=time.time() if timestamp is None else timestamp,
            )
            self._prices[ticker] = update
            self._version += 1
            if prev is None or prev.price != price:
                self._last_change_at = time.time()
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        """Latest PriceUpdate for a ticker, or None if not cached."""
        with self._lock:
            return self._prices.get(ticker)

    def get_price(self, ticker: str) -> float | None:
        """Latest price for a ticker, or None if not cached."""
        update = self.get(ticker)
        return update.price if update else None

    def get_all(self) -> dict[str, PriceUpdate]:
        """Shallow copy of all prices."""
        with self._lock:
            return dict(self._prices)

    def remove(self, ticker: str) -> None:
        """Drop a ticker. Bumps the version so SSE clients see the removal."""
        with self._lock:
            self._prices.pop(ticker, None)
            self._version += 1

    @property
    def version(self) -> int:
        """Bumps on every write; SSE sends only when it changes."""
        with self._lock:
            return self._version

    def seconds_since_change(self) -> float:
        """Seconds since any cached price last changed value."""
        with self._lock:
            return time.time() - self._last_change_at

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._prices

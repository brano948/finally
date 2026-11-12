"""Abstract interface for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations write into a shared PriceCache on their own schedule.
    Consumers read prices from the cache, never from the source.

    Lifecycle:
        source = create_market_data_source(cache)
        await source.start(["AAPL", "GOOGL", ...])
        await source.validate_ticker("PYPL")   # before adding / trading a new ticker
        await source.add_ticker("PYPL")
        await source.remove_ticker("GOOGL")
        await source.stop()

    Tickers reaching this layer are already normalised (uppercased, trimmed,
    regex-validated) at the API boundary.
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Start the background task. Call once. First prices are cached on return."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the background task. Idempotent."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Track a ticker. No-op if already tracked."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking a ticker and drop it from the cache. No-op if absent."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers."""

    @abstractmethod
    async def validate_ticker(self, ticker: str) -> bool:
        """True if the ticker has a price.

        Contract: on True, the ticker is tracked and cache.get_price(ticker) is
        not None, so it can be traded straight away. Callers that do not keep
        the ticker (e.g. a rejected trade) re-sync the tracked set afterwards.
        On False, nothing changes.
        """

    @property
    def is_live_market(self) -> bool:
        """True for real market data, where prices can go stale outside market hours."""
        return False

"""Massive (formerly Polygon.io) REST client for real market data."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from massive import RESTClient
from massive.exceptions import BadResponse
from massive.rest.models import SnapshotMarketType, TickerSnapshot

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)

NOT_FOUND = "NOT_FOUND"  # status in the body of a Massive 404
NOT_AUTHORIZED = "NOT_AUTHORIZED"  # status in the body of a Massive 403


@dataclass(frozen=True, slots=True)
class Quote:
    """Price fields extracted from one Massive snapshot."""

    ticker: str
    price: float
    timestamp: float | None  # Unix seconds; None means "now"
    baseline: float | None  # previous close; None keeps the cached baseline


def parse_snapshot(snap: TickerSnapshot) -> Quote | None:
    """Pick the best available price from a snapshot, or None if it has none.

    Preference order:
      1. last_trade.price, time from last_trade.sip_timestamp (nanoseconds)
      2. min.close, time from min.timestamp (milliseconds)
      3. day.close, time = now
      4. prev_day.close, time = now (before the open, when the day bar is empty)

    last_trade and min are None when the plan lacks trade access or before the
    open, hence the fallbacks. The baseline is prev_day.close.
    """
    prev_day = getattr(snap, "prev_day", None)
    baseline = prev_day.close if prev_day and prev_day.close else None

    last_trade = getattr(snap, "last_trade", None)
    if last_trade and last_trade.price:
        ts = last_trade.sip_timestamp
        return Quote(snap.ticker, last_trade.price, ts / 1e9 if ts else None, baseline)

    minute = getattr(snap, "min", None)
    if minute and minute.close:
        ts = minute.timestamp
        return Quote(snap.ticker, minute.close, ts / 1e3 if ts else None, baseline)

    day = getattr(snap, "day", None)
    if day and day.close:
        return Quote(snap.ticker, day.close, None, baseline)

    if baseline:
        return Quote(snap.ticker, baseline, None, baseline)

    return None


class MassiveDataSource(MarketDataSource):
    """MarketDataSource polling the Massive full-market snapshot endpoint.

    Polls GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=... for
    all tracked tickers in a single call, then writes results to the PriceCache.
    The synchronous RESTClient runs in a worker thread (asyncio.to_thread).

    Rate limits:
      - Free tier: 5 req/min -> poll every 15s (default), leaving ~1 call/min
        for validate_ticker
      - Paid tiers: poll every 2-5s
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
    ) -> None:
        self._client = RESTClient(api_key=api_key)
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._failing = False
        self._validate_lock = asyncio.Lock()

    @property
    def is_live_market(self) -> bool:
        return True

    async def start(self, tickers: list[str]) -> None:
        """Poll once, then keep polling. No-op if already started.

        If the first poll fails the app still starts with an empty cache;
        prices appear on the next successful poll.
        """
        if self._task is not None:
            return
        self._tickers = list(dict.fromkeys(tickers))
        await self._poll_once()
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, %.1fs interval",
            len(self._tickers),
            self._interval,
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        if ticker not in self._tickers:
            self._tickers.append(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        if ticker in self._tickers:
            self._tickers.remove(ticker)
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    async def validate_ticker(self, ticker: str) -> bool:
        """True if the ticker has a quote; one single-ticker snapshot call if not cached.

        Returns False only when Massive says the ticker does not exist or has no
        price. Any other failure (rate limit, bad key, plan, network) is raised,
        so the caller does not report it as an unknown ticker.

        A valid ticker is written to the cache and tracked, so it can be traded
        straight away instead of waiting up to one poll interval. Calls are
        serialised so concurrent requests for one ticker cost a single API call.
        """
        async with self._validate_lock:
            if ticker in self._cache:
                return True
            try:
                snap = await asyncio.to_thread(
                    self._client.get_snapshot_ticker, SnapshotMarketType.STOCKS, ticker
                )
            except BadResponse as e:
                if NOT_FOUND in str(e):
                    logger.info("Massive: ticker %s not found", ticker)
                    return False
                raise
            quote = parse_snapshot(snap) if snap is not None else None
            if quote is None:
                logger.info("Massive: ticker %s has no price data", ticker)
                return False
            await self.add_ticker(ticker)
            self._write(ticker, quote)
            return True

    # --- Internal ---

    async def _poll_loop(self) -> None:
        """Poll on interval. The first poll already happened in start()."""
        while True:
            await asyncio.sleep(self._interval)
            await self._poll_once()

    async def _poll_once(self) -> None:
        """Fetch all tracked tickers in one call and write them to the cache."""
        if not self._tickers:
            return
        try:
            snaps = await asyncio.to_thread(
                self._client.get_snapshot_all,
                market_type=SnapshotMarketType.STOCKS,
                tickers=list(self._tickers),
            )
        except Exception as e:
            self._log_failure(e)
            return
        self._failing = False

        updated = 0
        for snap in snaps or []:
            ticker = getattr(snap, "ticker", None)
            # Ignore tickers removed while the call was in flight (and any
            # unrequested ticker), so a stale entry is not re-added.
            if ticker not in self._tickers:
                continue
            quote = parse_snapshot(snap)
            if quote is None:
                logger.warning("Massive: no price in snapshot for %s", ticker)
                continue
            self._write(ticker, quote)
            updated += 1
        logger.debug("Massive poll: updated %d/%d tickers", updated, len(self._tickers))

    def _write(self, ticker: str, quote: Quote) -> None:
        self._cache.update(ticker, quote.price, timestamp=quote.timestamp, baseline=quote.baseline)

    def _log_failure(self, e: Exception) -> None:
        """Log the first failure at error level, repeats at debug."""
        if self._failing:
            logger.debug("Massive poll still failing: %s", e)
        elif NOT_AUTHORIZED in str(e):
            logger.error(
                "Massive poll refused: your plan does not include the snapshot "
                "endpoint (free plans do not). Use a paid plan or unset MASSIVE_API_KEY: %s",
                e,
            )
        else:
            logger.error("Massive poll failed: %s", e)
        self._failing = True

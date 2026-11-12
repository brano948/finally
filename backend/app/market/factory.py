"""Factory for creating market data sources."""

from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)

DEFAULT_MASSIVE_POLL_INTERVAL = 15.0  # free tier: 4 calls/min
MIN_MASSIVE_POLL_INTERVAL = 2.0


def _massive_poll_interval() -> float:
    """MASSIVE_POLL_INTERVAL (seconds) for paid tiers; 15s if unset or invalid."""
    raw = os.environ.get("MASSIVE_POLL_INTERVAL", "").strip()
    if not raw:
        return DEFAULT_MASSIVE_POLL_INTERVAL
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Ignoring invalid MASSIVE_POLL_INTERVAL=%r", raw)
        return DEFAULT_MASSIVE_POLL_INTERVAL
    return max(value, MIN_MASSIVE_POLL_INTERVAL)


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Create the market data source selected by environment variables.

    - MASSIVE_API_KEY set and non-blank -> MassiveDataSource (real market data)
    - Otherwise -> SimulatorDataSource (GBM simulation)

    This is the only place that reads MASSIVE_API_KEY. Returns an unstarted
    source; the caller must await source.start(tickers).
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        interval = _massive_poll_interval()
        logger.info("Market data source: Massive API (poll every %.1fs)", interval)
        return MassiveDataSource(api_key=api_key, price_cache=price_cache, poll_interval=interval)
    logger.info("Market data source: GBM simulator")
    return SimulatorDataSource(price_cache=price_cache)

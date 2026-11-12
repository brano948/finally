"""SSE endpoint streaming the price cache."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)

STALE_AFTER_SECONDS = 60.0
RETRY_MS = 1000
KEEPALIVE_SECONDS = 15.0
KEEPALIVE = ": keepalive\n\n"


def create_stream_router(
    cache: PriceCache,
    source: MarketDataSource,
    interval: float = 0.5,
) -> APIRouter:
    """Router exposing GET /api/stream/prices.

    Built fresh on every call (no module-level state), so creating it twice,
    e.g. in tests, does not register the route twice.
    """
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        """Long-lived SSE stream; one batched event per cache change."""
        return StreamingResponse(
            generate_events(cache, source, request, interval),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # disable proxy buffering
            },
        )

    return router


def is_market_closed(cache: PriceCache, source: MarketDataSource) -> bool:
    """True when live (Massive) prices have not changed for STALE_AFTER_SECONDS.

    The simulator runs 24/7, so it is never "closed".
    """
    return source.is_live_market and cache.seconds_since_change() > STALE_AFTER_SECONDS


def build_payload(cache: PriceCache, source: MarketDataSource) -> str:
    """One SSE data event holding every cached ticker."""
    prices = {ticker: update.to_dict() for ticker, update in cache.get_all().items()}
    body = {"prices": prices, "market_closed": is_market_closed(cache, source)}
    return f"data: {json.dumps(body)}\n\n"


async def generate_events(
    cache: PriceCache,
    source: MarketDataSource,
    request: Request,
    interval: float = 0.5,
    keepalive: float = KEEPALIVE_SECONDS,
) -> AsyncGenerator[str, None]:
    """Yield an event when the cache version or market_closed flag changes.

    A comment line is sent after `keepalive` seconds of silence so proxies and
    browsers do not drop an idle connection. Stops on client disconnect.
    """
    yield f"retry: {RETRY_MS}\n\n"

    last_version = -1
    last_closed = None
    last_sent = time.monotonic()
    client = request.client.host if getattr(request, "client", None) else "unknown"
    logger.info("SSE client connected: %s", client)
    try:
        while not await request.is_disconnected():
            version = cache.version
            closed = is_market_closed(cache, source)
            if version != last_version or closed != last_closed:
                last_version, last_closed = version, closed
                last_sent = time.monotonic()
                yield build_payload(cache, source)
            elif time.monotonic() - last_sent >= keepalive:
                last_sent = time.monotonic()
                yield KEEPALIVE
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("SSE stream cancelled: %s", client)
        raise
    logger.info("SSE client disconnected: %s", client)

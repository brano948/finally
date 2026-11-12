# Market Data Backend — Summary

**Status:** Complete. Gaps against `PLAN.md` listed in `MARKET_DATA_DESIGN.md` §1 (items 1-7) are closed as of 2026-11-12; item 8 (tracked-set service) belongs to the watchlist/portfolio increments.

## What Was Built

A complete market data subsystem in `backend/app/market/` (8 modules) providing live price simulation and real market data via a unified interface.

### Architecture

```
MarketDataSource (ABC)
├── SimulatorDataSource  →  GBM simulator (default, no API key needed); baseline = seed price
└── MassiveDataSource    →  Massive (Polygon.io) REST poller (when MASSIVE_API_KEY set); baseline = prev close
        │
        ▼
   PriceCache (thread-safe, in-memory, version counter, last-change clock)
        │
        ├──→ SSE stream endpoint (/api/stream/prices) → {"prices": {...}, "market_closed": bool}
        ├──→ Portfolio valuation
        └──→ Trade execution
```

### Modules

| File | Purpose |
|------|---------|
| `models.py` | `PriceUpdate` — frozen dataclass (ticker, price, previous_price, baseline, timestamp; change, change_percent, daily_change_percent, direction) |
| `interface.py` | `MarketDataSource` — ABC: `start/stop/add_ticker/remove_ticker/get_tickers/validate_ticker`, `is_live_market` |
| `cache.py` | `PriceCache` — thread-safe store; version counter for SSE change detection; `seconds_since_change()` for "market closed" |
| `seed_prices.py` | Realistic seed prices, per-ticker GBM params (drift/volatility), correlation groups |
| `simulator.py` | `GBMSimulator` (GBM with Cholesky-correlated moves, seed tracking) + `SimulatorDataSource` |
| `massive_client.py` | `parse_snapshot()` (last trade → minute bar → day close) + `MassiveDataSource` REST poller |
| `factory.py` | `create_market_data_source()` — selects source from `MASSIVE_API_KEY`; optional `MASSIVE_POLL_INTERVAL` |
| `stream.py` | `create_stream_router(cache, source)` — SSE endpoint, `build_payload`, `generate_events` |

### Key Design Decisions

- **Strategy pattern** — both data sources implement the same ABC; downstream code is source-agnostic
- **PriceCache as single point of truth** — producers write, consumers read; no direct coupling
- **GBM with correlated moves** — Cholesky decomposition of sector-based correlation matrix; tech 0.6, finance 0.5, cross-sector / TSLA / unknown 0.3
- **Random shock events** — ~0.1% chance per tick per ticker of a 2-5% move
- **Validation before use** — `validate_ticker()` guarantees a cached price; Massive spends one single-ticker call only for uncached tickers
- **Staleness by "price unchanged"**, not by trade timestamp, so delayed Massive plans are not flagged as closed

## Test Suite

In `backend/tests/market/`: `test_models.py`, `test_cache.py`, `test_simulator.py`, `test_simulator_source.py`, `test_massive.py`, `test_factory.py`, `test_stream.py`, `test_contract.py` (both sources run through the same contract). Massive tests build real `TickerSnapshot.from_dict` objects instead of `MagicMock`, so attribute-name bugs fail the tests.

```bash
cd backend
uv run --extra dev pytest -v
uv run --extra dev ruff check app/ tests/
```

## Demo

```bash
cd backend
uv run --extra dev market_data_demo.py
```

Rich terminal dashboard with all 10 tickers, sparklines, direction arrows and an event log. Runs 60 seconds or until Ctrl+C.

## Usage for Downstream Code

```python
from app.market import PriceCache, create_market_data_source, create_stream_router

# Startup (FastAPI lifespan)
cache = PriceCache()
source = create_market_data_source(cache)  # Reads MASSIVE_API_KEY
await source.start(["AAPL", "GOOGL", "MSFT", ...])  # watchlist union positions
app.include_router(create_stream_router(cache, source))

# Read prices
update = cache.get("AAPL")          # PriceUpdate or None
price = cache.get_price("AAPL")     # float or None
update.daily_change_percent         # vs seed / previous close

# New ticker (watchlist add or trade)
if not await source.validate_ticker("PYPL"):
    ...  # 404 unknown_ticker (Massive only)

# Dynamic tracking (only remove when on neither watchlist nor positions)
await source.add_ticker("TSLA")
await source.remove_ticker("GOOGL")

# Shutdown
await source.stop()
```

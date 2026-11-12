# Backend — Developer Guide

## Project Setup

```bash
cd backend
uv sync --extra dev   # Install all dependencies including test/lint tools
```

## Market Data API

The market data subsystem lives in `app/market/`. Use these imports:

```python
from app.market import PriceCache, PriceUpdate, MarketDataSource, create_market_data_source
```

### Core Types

- **`PriceUpdate`** — Immutable dataclass: `ticker`, `price`, `previous_price`, `baseline`, `timestamp` (Unix seconds), plus properties `change` / `change_percent` (tick-to-tick), `daily_change_percent` (vs `baseline`), `direction` ("up"/"down"/"flat"), and `to_dict()` for JSON serialization. `baseline` is the seed price (simulator) or previous close (Massive).

- **`PriceCache`** — Thread-safe in-memory store. Key methods:
  - `update(ticker, price, timestamp=None, baseline=None) -> PriceUpdate` (baseline defaults to the prior baseline, then to the first price)
  - `get(ticker) -> PriceUpdate | None`
  - `get_price(ticker) -> float | None`
  - `get_all() -> dict[str, PriceUpdate]`
  - `remove(ticker)` (bumps `version`)
  - `version` property — monotonic counter, bumps on every write (for SSE change detection)
  - `seconds_since_change()` — seconds since any price last changed value (drives "market closed")

- **`MarketDataSource`** — Abstract interface implemented by `SimulatorDataSource` and `MassiveDataSource`. Lifecycle: `start(tickers)` -> `validate_ticker()` / `add_ticker()` / `remove_ticker()` -> `stop()`.
  - `validate_ticker(ticker) -> bool`: on `True` the ticker is tracked and priced in the cache. Simulator: always `True`. Massive: one single-ticker snapshot call if not cached; `False` -> `unknown_ticker` (404).
  - `is_live_market`: `True` only for Massive (prices can go stale).
  - Tickers must already be normalised (uppercased, trimmed, validated) at the API boundary.

- **`create_market_data_source(cache)`** — Factory. Returns `MassiveDataSource` if `MASSIVE_API_KEY` is set, otherwise `SimulatorDataSource`. Optional `MASSIVE_POLL_INTERVAL` (seconds, default 15, min 2).

### Tracked set rule

The source must track the union of watchlist and open-position tickers. `remove_ticker` drops the price from the cache, so only call it when a ticker is on neither list (planned `app/services/tracking.py`, see `planning/MARKET_DATA_DESIGN.md` §11).

### SSE Streaming

```python
from app.market import create_stream_router

router = create_stream_router(price_cache, source)  # Returns a fresh FastAPI APIRouter
# Endpoint: GET /api/stream/prices (text/event-stream)
# Event: data: {"prices": {"AAPL": {...PriceUpdate.to_dict()}, ...}, "market_closed": false}
```

### Seed Data

Default tickers: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX. Seed prices and per-ticker volatility/drift params are in `app/market/seed_prices.py`.

## Running Tests

```bash
uv run --extra dev pytest -v              # All tests
uv run --extra dev pytest --cov=app       # With coverage
uv run --extra dev ruff check app/ tests/ # Lint
```

## Demo

```bash
uv run market_data_demo.py   # Live terminal dashboard with simulated prices
```

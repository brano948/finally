# FinAlly Backend

FastAPI backend for the FinAlly AI Trading Workstation.

## Structure

- `app/` - Application code
  - `market/` - Market data subsystem
    - `models.py` - PriceUpdate dataclass (price, previous price, baseline, daily change %)
    - `cache.py` - Thread-safe price cache
    - `interface.py` - MarketDataSource abstract interface
    - `simulator.py` - GBM-based market simulator
    - `massive_client.py` - Massive/Polygon.io API client and snapshot parser
    - `factory.py` - Data source factory
    - `stream.py` - SSE streaming endpoint
    - `seed_prices.py` - Default ticker prices and parameters

- `tests/` - Unit and integration tests
  - `market/` - Market data tests (models, cache, simulator, Massive, factory, SSE, contract tests over both sources)

## Running Tests

```bash
# Install dependencies (dev tools are an optional extra, not a dependency group)
uv sync --extra dev

# Run all tests
uv run --extra dev pytest

# Run with coverage
uv run --extra dev pytest --cov=app --cov-report=html

# Run specific test file
uv run --extra dev pytest tests/market/test_simulator.py
```

## Environment Variables

- `MASSIVE_API_KEY` - Optional. If set, use real market data from Massive API. If not set, use the built-in simulator.
- `MASSIVE_POLL_INTERVAL` - Optional. Massive poll interval in seconds (default 15, minimum 2). Lower it only on paid tiers.

## Development

```bash
uv sync --extra dev
uv run --extra dev ruff check app/ tests/
uv run --extra dev ruff format app/ tests/
```

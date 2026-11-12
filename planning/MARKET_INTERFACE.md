# Market Data Interface

The unified Python API for stock prices. Code in `backend/app/market/` selects a source at startup: Massive if `MASSIVE_API_KEY` is set and non-empty, otherwise the simulator. Everything downstream reads from one in-memory cache and never knows which source is active. Massive endpoint details are in `MASSIVE_API.md`; simulator internals are in `MARKET_SIMULATOR.md`; the full design is in `MARKET_DATA_DESIGN.md`.

## Design

```
create_market_data_source(cache)
   ├── SimulatorDataSource   (no key)   -- GBM step every 0.5s, baseline = seed price
   └── MassiveDataSource     (key set)  -- snapshot poll every 15s (configurable), baseline = prev close
              │  writes
              ▼
          PriceCache  (thread-safe, latest PriceUpdate per ticker, version counter, last-change clock)
              │  reads
              ├── SSE  GET /api/stream/prices
              ├── portfolio valuation (GET /api/portfolio)
              ├── trade execution (fill price)
              └── watchlist (GET /api/watchlist)
```

Principles:
- Push into a cache, pull from the cache. Consumers never call a source for a price.
- One abstract class, two implementations (strategy pattern). The factory is the only place that reads the env var.
- Sources are started once, in the FastAPI lifespan, and stopped on shutdown. Exactly one worker process (the cache is in memory).
- Tickers are normalised (uppercased, trimmed, regex-validated) at the API boundary, before they reach the market layer.

## Public API (`from app.market import ...`)

### `PriceUpdate` (frozen dataclass)

| Field / property | Meaning |
|---|---|
| `ticker`, `price`, `previous_price` | Latest and prior price (2 decimals) |
| `baseline` | Reference for daily change: seed price (simulator) or previous close (Massive) |
| `timestamp` | Unix **seconds** (float) |
| `change`, `change_percent` | Versus `previous_price` (tick-to-tick) |
| `daily_change_percent` | Versus `baseline` |
| `direction` | `"up"`, `"down"` or `"flat"` |
| `to_dict()` | JSON form used by SSE |

### `PriceCache`

```python
cache = PriceCache()
cache.update("AAPL", 190.5, baseline=188.0)   # -> PriceUpdate; baseline kept on later updates
cache.update("AAPL", 190.7)                   # previous_price=190.5, baseline=188.0
cache.get("AAPL")                             # PriceUpdate | None
cache.get_price("AAPL")                       # float | None
cache.get_all()                               # dict[str, PriceUpdate] (copy)
cache.remove("AAPL")                          # bumps version
cache.version                                 # int, bumps on every write (SSE change detection)
cache.seconds_since_change()                  # seconds since any price changed value
"AAPL" in cache; len(cache)
```

### `MarketDataSource` (ABC)

```python
class MarketDataSource(ABC):
    async def start(self, tickers: list[str]) -> None    # once; first prices are in the cache on return
    async def stop(self) -> None                          # idempotent
    async def add_ticker(self, ticker: str) -> None       # no-op if present
    async def remove_ticker(self, ticker: str) -> None    # also drops it from the cache
    def get_tickers(self) -> list[str]
    async def validate_ticker(self, ticker: str) -> bool  # True => tracked and priced in the cache
    @property
    def is_live_market(self) -> bool                      # False (simulator), True (Massive)
```

`validate_ticker`: the simulator accepts any ticker (adds it with a random seed if new). Massive returns `True` immediately for a cached ticker, otherwise makes one `get_snapshot_ticker` call; a `NOT_FOUND` response or a snapshot with no price returns `False` (the API maps this to 404 `unknown_ticker`). Any other failure (rate limit, bad key, plan, network) is raised so the API can answer 502/503 instead of 404. Concurrent calls are serialised, so one uncached ticker costs one API call.

### Factory

```python
def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    # MASSIVE_POLL_INTERVAL (float seconds, default 15, min 2) is read for Massive
    return MassiveDataSource(api_key=key, price_cache=price_cache, poll_interval=...) if key \
        else SimulatorDataSource(price_cache=price_cache)
```

### Lifecycle in the app

```python
@asynccontextmanager
async def lifespan(app):
    cache = PriceCache()
    source = create_market_data_source(cache)
    await source.start(tracked_tickers(db))    # watchlist union open positions
    app.state.cache, app.state.source = cache, source
    app.include_router(create_stream_router(cache, source))   # GET /api/stream/prices
    yield
    await source.stop()
```

Usage by services:

```python
if not await request.app.state.source.validate_ticker(ticker):   # watchlist add / trade
    raise ApiError(404, "unknown_ticker", ...)
price = request.app.state.cache.get_price(ticker)                 # trade fill / valuation
await sync_tracking(db, request.app.state.source)                 # after every mutation
```

## SSE payload

One event per change of `cache.version` or of `market_closed`, checked every 0.5s, preceded by a `retry: 1000` directive. After 15s without an event a `: keepalive` comment line is sent. With Massive, events follow the poll interval, not 500ms. An event is always sent on connect, even when the cache is empty:

```
data: {"prices": {"AAPL": {"ticker": "AAPL", "price": 190.5, "previous_price": 190.4,
                           "baseline": 190.0, "timestamp": 1731200000.1, "change": 0.1,
                           "change_percent": 0.0525, "daily_change_percent": 0.2632,
                           "direction": "up"}, "GOOGL": {...}},
       "market_closed": false}
```

`market_closed` is `true` only for Massive when no cached price has changed for more than 60s (`STALE_AFTER_SECONDS`).

## Source behaviour differences

| | Simulator | Massive |
|---|---|---|
| Cadence | 0.5s | 15s default; 2-5s on paid plans (`MASSIVE_POLL_INTERVAL`) |
| Any ticker | Yes (random seed price 50-300, default params) | Only tickers with a quote |
| New ticker price | Immediately on `add_ticker` / `validate_ticker` | Immediately on `validate_ticker` (one call), else next poll |
| Baseline | Seed price | `prevDay.c` (previous close) |
| Offline/market closed | Runs 24/7, never "closed" | Stale; `market_closed: true` after 60s without a change |
| Tick-to-tick direction | Every tick | Between polls; `flat` when price unchanged |
| Poll failure | n/a | Logged once at error level, then debug; last prices kept |

## Resolved gaps (2026-11-12)

The gaps found earlier between the code and PLAN.md are fixed:

1. **Massive timestamp bug.** `parse_snapshot()` reads `last_trade.sip_timestamp` (ns), falling back to `min.close` (ms timestamp) then `day.close`. Tests use real `TickerSnapshot.from_dict` objects.
2. **Daily change baseline.** `PriceUpdate.baseline` and `daily_change_percent`; seed price for the simulator, `prev_day.close` for Massive.
3. **Ticker validation.** `validate_ticker()` on the ABC and both sources.
4. **Tracked set.** Still a service-layer rule (`app/services/tracking.py`, built with the watchlist/portfolio increments): only call `remove_ticker` when a ticker is on neither the watchlist nor a position.
5. **Stale detection.** `PriceCache.seconds_since_change()` plus `is_live_market` drive `market_closed` in the SSE payload.
6. **Noisy poller.** The first failure logs at error level, repeats at debug, and the next failure after a recovery logs at error again.

Also fixed: `cache.update(timestamp=0.0)` keeps `0.0`; `cache.remove()` bumps `version`; the SSE router is built inside `create_stream_router` (no module-level router); snapshots for tickers removed during an in-flight poll are ignored.

## Testing

All in `backend/tests/market/`:

- `test_models.py`, `test_cache.py`: fields, percentages, baseline rules, timestamp, version, staleness clock, concurrent writers.
- `test_simulator.py`: GBM math (pure-drift exactness, volatility and correlation statistics), seeds, Cholesky, events.
- `test_simulator_source.py`: lifecycle, baseline = seed, `validate_ticker`, loop resilience.
- `test_massive.py`: `parse_snapshot` with real models and all fallbacks, polling, failure logging, mid-poll removal, `validate_ticker`.
- `test_factory.py`: env var rules, poll interval, ABC shape, package exports.
- `test_stream.py`: payload shape, `market_closed`, generator behaviour, router factory.
- `test_contract.py`: the same contract tests run against both sources.

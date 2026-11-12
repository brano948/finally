# Market Data Backend Review

Reviewed 2026-11-12 on `main` at `34fdbaa` (clean tree). Scope: `backend/app/market/` (8 modules, about 600 lines) and `backend/tests/market/`, checked against `planning/PLAN.md` sections 6, 8 and 12.

## Verdict

The subsystem is complete and in good shape. It matches PLAN.md section 6, the structure is simple, the tests are meaningful, and I found no correctness bug in the simulator, cache or models. The real risks are in the Massive path, which no test has exercised against a live API. None of them block the next increment (backend skeleton), but findings 1 to 3 should be decided before Massive is advertised as supported.

## Test results (run today)

| Check | Result |
|---|---|
| `uv run pytest` | 178 passed in about 4.5s (Python 3.13.0) |
| `uv run --extra dev ruff check app/ tests/` | All checks passed |
| Coverage (`--cov=app`) | 99% (441 statements, 3 missed) |

Uncovered lines: `simulator.py:155` (duplicate guard in `_add_ticker_internal`, unreachable through the public methods because they check first) and `stream.py:85-86` (the `CancelledError` log branch). Neither matters.

## Findings

### Should fix

1. **Every Massive error is reported as "unknown ticker".** `massive_client.py:135` catches `Exception` in `validate_ticker` and returns `False`. A 429 (rate limit), 401 (bad key), 403 (plan) or network timeout therefore becomes a 404 `unknown_ticker` for the user. This is likely in practice: PLAN.md leaves only 1 spare call per minute on the free tier, so a user adding two tickers quickly hits 429. Fix: return `False` only for a real not-found (404), and let other errors propagate so the API can answer 502/503. Needs one small test per case.

2. **A free-tier key probably yields a permanently empty app.** `MASSIVE_API.md` states that Stocks Basic does not include snapshot endpoints (expect 403). If so, `start()` logs one error, the cache stays empty, every trade fails, and the SSE stream sends empty `prices` forever. PLAN.md promises free-tier support. Options, simplest first: (a) document that Massive needs a paid plan; (b) log a clear "plan does not include snapshots" error on 403; (c) implement the grouped-daily fallback described in `MASSIVE_API.md`. Verify with a real free key before choosing.

3. **No test or run against the real Massive API.** All Massive tests use `TickerSnapshot.from_dict` objects and a stubbed client. That is good (it caught the earlier `sip_timestamp` bug), but three assumptions remain unverified, all noted in `MASSIVE_API.md`: that `get_snapshot_all(tickers=[...])` accepts a list, that unknown tickers raise on `get_snapshot_ticker`, and the 5 calls/min limit. One manual smoke run with a real key would settle all three.

### Consider

4. **SSE sends nothing between version changes, so idle streams have no keepalive.** `stream.py:78-83` only yields when `cache.version` changes. With Massive at 15s polling, or an empty cache, the connection can sit silent long enough for a proxy or browser to close it. Cheap fix: yield a comment line (`: keepalive\n\n`) every 15s. Also, PLAN.md says updates are pushed "at a regular cadence (~500ms)"; for Massive the actual cadence is the poll interval, which is reasonable but is a small spec deviation worth a line in the docs.

5. **`market_closed` is only re-evaluated when the version changes** (`build_payload` is called only from the version branch). If Massive polls start failing, the version stops changing, so the flag never flips to `true`. Evaluate it each loop and send an event when it changes.

6. **Valid tickers can be rejected before the market opens.** `parse_snapshot` falls back last_trade, minute close, day close, then returns `None`. Around the daily reset (3:30 to 4:00 AM ET) `day.c` is empty while `prev_day.close` exists. Falling back to `prev_day.close` would keep validation working and show a flat price, consistent with "prices are stale outside market hours; acceptable". Low probability, so optional.

7. **`start()` on Massive does not guarantee prices on return,** unlike the ABC docstring ("First prices are cached on return"). If the first poll fails the app starts with an empty cache (deliberate, per the comment). Either soften the ABC wording or keep as is; the empty-cache behaviour is at least logged.

8. **`rich` is a runtime dependency** (`pyproject.toml`) but only `market_data_demo.py` uses it. Move it to the `dev` extra to keep the production image smaller.

9. **No double-start guard.** Calling `start()` twice on either source creates two background tasks. Only the lifespan calls it, so this is theoretical; skip unless it bites.

### Minor

10. `cache.py:14` docstring says Massive writes from a "poll thread". Only the HTTP call runs in a worker thread (`asyncio.to_thread`); the cache write happens on the event loop. The lock is still correct and harmless.
11. `simulator.py:198` comment says "TSLA is in tech set". It is not (`seed_prices.py` tech group has no TSLA). The code is right; fix the comment.
12. `SimulatorDataSource._event_prob` (`simulator.py:229`) is stored but never used after construction.
13. `MASSIVE_API.md:100` points to "Known issue in current code" in `MARKET_INTERFACE.md`; that section is now "Resolved gaps". Stale reference.
14. Two concurrent `validate_ticker` calls for the same uncached ticker each spend an API call. Rare, and it costs a rate-limit slot on the free tier.
15. The simulator uses the global `random` and `numpy.random` state. Fine for production; tests that need determinism must seed both (they appear to).

## What is good

- Clean strategy pattern: one ABC, two implementations, a single env-var read in the factory.
- The cache is the only coupling point; the `version` counter gives cheap SSE change detection.
- The GBM math is correct, prices stay unrounded internally, and Cholesky is rebuilt on add/remove. Rounding is applied only on output.
- Edge cases are handled and tested: `timestamp=0.0` kept, `remove()` bumps the version, mid-poll ticker removal ignored, log noise limited to the first failure.
- `test_contract.py` runs the same contract against both sources, which prevents the two implementations from drifting apart.
- Code and docs are consistent in the main (`MARKET_DATA_SUMMARY.md`, `MARKET_INTERFACE.md`, `backend/CLAUDE.md` all agree with the code).

## Plan compliance

| PLAN.md requirement | Status |
|---|---|
| Common interface, env-driven selection | Done |
| GBM, correlated moves, 2-5% events, ~500ms, any valid ticker | Done |
| Massive batch snapshot polling, 15s default | Done (unverified live) |
| Validate uncached tickers with one single-ticker call | Done; error mapping see finding 1 |
| Cache holds price, previous, timestamp, baseline | Done |
| SSE batched event with daily change % and market-closed flag | Done; see findings 4 and 5 |
| Union of watchlist and positions tracked | Not yet: belongs to the service layer (`app/services/tracking.py`), per the summary |

## Review scope and limits

I read all of the market-data source and the planning docs `MARKET_DATA_SUMMARY.md`, `MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md`, `MASSIVE_API.md`, `REVIEW.md`, `REVIEW_v1.md` and `PLAN.md`. I did **not** read `MARKET_DATA_DESIGN.md` (1,052 lines), `PLAN_01.bckp`, the `planning/archive/` files, or the test files line by line; test quality is judged from the passing run, 99% coverage, the summary description and file sizes. A second pass over those would be needed for a fully exhaustive review.

## Resolution (2026-11-12)

Fixes applied. `uv run --extra dev pytest`: 188 passed (10 new tests). `ruff check`: clean.

| # | Finding | Status |
|---|---|---|
| 1 | Every Massive error reported as unknown ticker | Fixed. `validate_ticker` returns False only for a `NOT_FOUND` body or a snapshot without a price; other `BadResponse` errors are raised. The Massive client exposes no status code, only the body text, so the match is on the body. |
| 2 | Free-tier key gives an empty app | Mitigated. A `NOT_AUTHORIZED` poll failure now logs a clear message saying the plan lacks snapshots and to use a paid plan or unset the key. The grouped-daily fallback is not built. Confirm with a real free key. |
| 3 | No live Massive run | Open. No `MASSIVE_API_KEY` in `.env`; needs a manual smoke run with a key. |
| 4 | SSE keepalive | Fixed. `: keepalive` comment after 15s idle. |
| 5 | `market_closed` only re-evaluated on version change | Fixed. Checked every loop; an event is sent when it flips. |
| 6 | Pre-open rejection | Fixed. `parse_snapshot` falls back to `prev_day.close`. |
| 7 | `start()` price guarantee | Fixed in the ABC docstring. |
| 8 | `rich` runtime dependency | Moved to the `dev` extra; demo command is now `uv run --extra dev market_data_demo.py`. |
| 9 | Double start | Fixed for Massive (no-op when started). Simulator still creates a second task if started twice. |
| 10-13 | Stale comments and doc reference | Fixed. |
| 14 | Concurrent validate cost | Fixed with a lock. |
| 12 | Unused `_event_prob` on the data source | Removed. |
| 15 | Global RNG state | Accepted, no change. |

Remaining before Massive is called supported: findings 2 and 3, both needing a real API key.

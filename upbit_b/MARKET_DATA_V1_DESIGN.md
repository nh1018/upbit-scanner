# Upbit B Market Data V1 — on-demand contract

No raw warehouse, archive, database, cloud storage or new workflow is introduced.
All public REST candle windows are transient memory inputs. This package performs
no production file writes and computes no feature, Trend Score, Chase Risk or B state.

## Universe and analysis boundary

Fetch the whole KRW universe, optionally bulk tickers in groups of 80. No momentum,
liquidity, A candidate or Binance listing filter is applied. The caller chooses an
explicit analysis universe; until a strategy defines eligibility, none is inferred.
One MarketData instance represents one fixed UTC cutoff/run and caches identical
provider/instrument/timeframe requests only inside that run. It is not a cross-run
cache: callers create a new instance for new observations.

## Input contracts

Windows: 1d=100, 4h=150, 1h=200 actual completed candles. Providers: UPBIT KRW and
BINANCE_SPOT USDT, never Binance Futures. Decimal values remain exact; no floats,
interpolation, synthetic candles or zero-fill. Candle intervals are [open,close),
UTC-aligned (day=00:00 UTC=09:00 KST; 4h=00/04/08/12/16/20 UTC; 1h=hour).
Binance inclusive source close must equal exclusive close minus one millisecond.
Upbit ticker timestamp is not candle close time. Forming rows are never returned.
OHLC positive/finite and consistent; both volume measures finite and nonnegative.

Each response has URL/query, received time, response byte SHA256 and retry attempt.
Each normalized window has fixed cutoff, deterministic input hash and explicit
availability. Hashing excludes retrieval clocks so identical inputs replay equally.
Actual future API revisions are not claimed to replay identically.

AVAILABLE requires the requested count, latest expected close and no unresolved
internal calendar holes. INSUFFICIENT_DATA means too few actual completed candles.
INCOMPLETE_COVERAGE identifies sparse/stale intervals; it does not assert a collector
gap or reject the asset from the universe. Upbit documents absent no-trade candles,
but an absent row alone does not establish historical listing/availability. Downstream
feature code must explicitly define sparse-window eligibility; no formula is defined
here. API_ERROR/INVALID_DATA never contain partial inputs for automatic calculation.
NOT_LISTED_YET and NO_TRADE_CANDLE are not invented without evidence.

## Mapping

Exact baseAsset + USDT + TRADING + explicit isSpotTradingAllowed=true is UNVERIFIED.
No symbol, inactive symbol, multiple eligible candidates, failed metadata are distinct.
VERIFIED additionally requires an explicitly approved caller-owned identity registry
entry with symbol, registry version and evidence reference. This package does not
approve entries or infer aliases. UNVERIFIED data may be fetched, but mapping status
must accompany downstream input and may not become strong confirmation implicitly.
Binance failure never changes Upbit universe or Upbit window availability.

## HTTP / costs

Public market-data endpoints only, no secrets. Per-host pacing: Upbit <=4/s, Spot
<=5/s per client; Upbit Remaining-Req exhausted second causes a wait. Up to three
attempts for network/429/selected 5xx; 418 stops. No infinite retry. Different clients
or runners do not share a global IP limiter; same-run callers should share a client.
Universe=one request, tickers=ceil(N/80), exchangeInfo=one reusable run observation.
For K Upbit analysis assets and M requested Spot counterparts, three windows require
normally 3(K+M) requests/run plus metadata. A fixed cutoff and intra-run memoization
avoid repetition. This policy saves disk, not inherently API calls; hourly full
291/192 analysis would mean 34,776 window requests/day before retry/metadata.
No strong prefilter is invented to reduce that number. Frequency/analysis scope is
an explicit later strategy/operations decision, not a hidden collector parameter.

## Future observation envelope (not implemented)

observation_id; actual observation UTC/KST; instrument/current-price observation;
source cutoff; per-TF availability/input hashes/response evidence; mapping status
and registry reference; feature/schema/strategy/parameter versions. Reserved B state,
Trend Score, Chase Risk, TF states and confirmation fields stay absent/null until
approved feature/strategy contracts exist. Never invent readiness or past availability.
Persist compact prospective observations only in a later independently authorized
stage. A response hash without bytes cannot reconstruct the historical input:
full raw replay is intentionally not promised under the no-raw-retention policy.

## Protection and acceptance

Only upbit_b and its tests change. No A/BTC/Worker/Pine/Alert/workflow writes.
Offline fixtures cover full windows, exact boundaries, forming exclusion, decimal,
duplicates, malformed data, sparse history, network failures, mapping and replay.
Public smoke tests fetch a few actual assets only and print summaries, not raw files.

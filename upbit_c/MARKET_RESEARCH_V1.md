# Upbit C Market Research / Prospective Outcomes V1

RESEARCH_ONLY. No candidates, orders or validated entry claims. Initial V0 score
weights/gates remain hypotheses; no performance optimization is introduced.

## Existing PR and boundaries

PR #27 head at start: `74a2f2dbdc3d3177445d44016308dbe048b63508`.
Existing five-market smoke is unchanged. Source path:
`upbit_b.market_data.universe` -> `MarketData.window` ->
`upbit_b.features.snapshot` -> `upbit_c.evidence.observe` ->
`upbit_c.research_score.evaluate`. Shared B modules are imported read-only, never edited.
Score parameter SHA256 remains
`d0b423bf1e00e03ac57858402e0fa5803df334236de2acf171647be41889f476`.

## Full market collection

`python -B -m upbit_c.research_runner` reads all currently returned KRW markets,
not a hardcoded market list. Default execution is memory-only dry-run.
Optional `--markets KRW-BTC,KRW-ETH` validates actual market membership.
`--batch-count N --batch-index I` selects sorted markets at stride N; the universe
response hash is retained. Market list changes between batches must be checked:
different universe revisions do not constitute a single exhaustive scan.

One frozen source cutoff per scan. 1h/4h/1d windows are respectively 200/150/100
completed candles, following the existing validated library. Pagination is bounded
to three requests/timeframe. Forming rows are excluded; missing time slots are not
filled, and stale/insufficient/missing/invalid/API/feature insufficiency are distinguished.
Sparse no-trade periods are a possible cause of missing candles, not proof of API failure.
Every available source window, feature/readiness snapshot, C evidence, response URL,
response SHA256, actual receipt clock and row open-time mapping are retained.

C-only client caps logical requests at about 2/sec; existing retries (3), timeout (15s),
429 Retry-After and transient retry behavior are reused. HTTP 418 stops subsequent
requests and records unattempted markets. A single market error cannot erase other
market results. Request count is logical requests, not network retry attempts.
Nominal volume: one universe request + 3M window requests; bounded pagination can
increase this to 1+9M before retry multiplication. At 293 markets the pacing lower
bound is about 440 seconds, excluding response latency, pagination and retries.
Use deterministic batches if runtime approaches the manual workflow's 40-minute limit.

## Structured append-only storage

`python -B -m upbit_c.research_runner --output research_artifacts`
stores JSON with `record` plus `record_sha256` envelopes:

- `scans/<scan_id>.json`: complete universe/clock/batch coverage, per-market source
  OHLCV windows, features, hashes, research score/group scores/gates or failures.
- `signals/<signal_id>.json`: only actual research_setup PASS observations,
  explicit RESEARCH_ONLY, first observation clock, diagnostic reference, score,
  source references and outcome contract. These are research cases, not entry signals.
- `evaluations/<id>.json`: horizon outcomes or pending/unverifiable observations.

Exclusive atomic hard-link publication prevents partial final files and replacement.
Existing envelopes are hash-checked. Same bytes are no-op; conflicts fail.
Signal identity includes market, engine version, parameter hash and all three raw
input hashes. Repeated retrieval of the same inputs preserves first clock/anchor.
Source revisions have distinct identities and must not be treated as independent
trade wins in later aggregation. Research setup persisting across new completed
candles can create correlated observations; no deduplicated trade/episode claim is made.

No historical C signals are reconstructed. Old scans cannot be loaded as new live
signals by the CLI. Source cutoff, receipt, feature generation, scan completion and
signal observation clocks are separate. The signal is observed after collection
completes; the older completed candle reference is diagnostic, not executable.

## Outcome contract

`python -B -m upbit_c.research_runner --output research_artifacts --outcomes-only`
or `--evaluate-history` after a new scan. At most 30 observations per invocation
(configurable 1..100); due outstanding horizons are prioritized.

Signal computation uses only data at its frozen source cutoff. Outcome data are
separate and can never feed the score. Instrument is Upbit KRW spot, horizon path
is completed 1h candles. Anchor is the open of the first 1h candle whose open
boundary is STRICTLY after the actual research observation clock. This is a
post-observation interval-open proxy, not actual entry/first tick/execution price.
Horizon endpoints are exactly anchor+24h/+72h/+168h. All 24/72/168 hourly candles
must exist, be completed and have official response/receipt/row coverage evidence.
REST count stays within the official 200 row limit; no gap filling is permitted.

Decimal precision 34, gross hypothetical long-side price metrics:

- return_pct = 100*(endpoint_close/anchor_open - 1)
- Hmax/Lmin = max high/min low across the entire anchor-inclusive, endpoint-exclusive path
- MFE_pct = max(0,100*(Hmax/anchor_open - 1))
- MAE_pct = min(0,100*(Lmin/anchor_open - 1)) (signed nonpositive)

No fees, slippage, spread, funding or realized trade P&L is included. Anchor,
endpoint, full normalized path, path hash and REST response evidence are stored.
PENDING means horizon time not complete and has null return/excursions; it is not
a loss. UNVERIFIABLE means missing/conflicting/forming/bad price/evidence/API data.
MATURED requires exact endpoint and gap/duplicate-free entire path, not clock alone.
Matured records have one stable signal+horizon+contract ID; subsequent runs skip
them without REST rerequest or overwrite. Pending/unverifiable events append,
so later successful resolution never erases earlier failures.
Future evaluation scans do not require markets to still be listed in today's universe;
API failure/delisting is retained as unverifiable (avoid silent survivorship deletion).
Later aggregate analysis can use gross returns/MFE/MAE but must handle unresolved
cases, delistings, overlapping observations, selection bias and actual costs.

## Manual workflow and retention

New workflow `.github/workflows/upbit-c-market-research.yml` has only
workflow_dispatch, contents:read/actions:read, independent concurrency and artifacts.
No schedule, push-to-main, candidate publishing, secrets or collector triggers.
Existing A/B/BTC/C smoke workflows remain byte-identical.
Workflow is normally discoverable for dispatch only after the file reaches the
default branch; this PR is not merged by this task. Local runner works before merge.

Optional `previous_run_id` downloads an explicit successful same-workflow artifact
and continues its immutable records. Wrong workflow/missing artifact fails, not an
implicit fresh lineage. Previous run ID must be the most recent intended lineage;
parallel branches of artifacts are not silently merged. Concurrency prevents this
workflow's executions from overlapping but does not select the parent for users.
Artifacts retained 90 days; carry them forward/download before expiry. This is
research tracking infrastructure, not indefinite archival or automatic production ON.
Full raw/feature windows deliberately increase storage; actual scan JSON bytes
must be measured before choosing a schedule. Git repository receives code/docs/tests
only, not growing research data. Compressed artifact quota is still finite.

Official source docs:
https://docs.upbit.com/kr/reference/list-candles-minutes
https://docs.upbit.com/kr/reference/list-candles-days
Minute API: up to 200 rows, 10 requests/sec/IP shared candle group; no-trade candles
are absent. C uses a conservative separate path, but shared egress IP still has a
shared upstream quota with any other caller.

## Validation limitations

Synthetic fixtures prove contracts/calculations, not profitable C parameters.
Real current scans prove actual source coverage, not future +1/+3/+7 day outcomes.
Prospective matured real outcomes require elapsed horizons and retained observations;
they must not be invented or substituted with reconstructed historical signals.

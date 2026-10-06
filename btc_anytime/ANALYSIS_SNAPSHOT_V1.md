# BTC ChatGPT Analysis Snapshot V1

Single mutable latest file: `output_btc_anytime/latest_analysis.json`.
Schema `btc-analysis-snapshot-v1`. No additional historical snapshots/raw archive,
external server, database, API request, Feature calculation or engine replay.
JSON stores exact original numeric strings. Eight recent completed OHLCV rows per
timeframe are a bounded latest-file view, not a new long-term raw ledger.

## Inputs and meaning

All inputs come from the same checked-out Git revision. Raw series are loaded with
the existing duplicate/hash/reference contract. Latest as-of completed rows use
UTC Binance boundaries (15m/1h/4h/1d), explicit `is_closed=false` is excluded,
future source clocks are excluded. Legacy flat 15m lacks a closed flag: its
completion basis is explicitly time-boundary/production legacy contract.
All completed rows are checked for OHLCV and continuity; gaps are reported, never
filled. Observation evidence hashes are checked against immutable raw. Missing
historical first availability is not inferred. Current reader time is separate.

Feature values/quality/pivots/OI metadata are copied from the latest stored
prospective Direction decision's `input_snapshot`. Its identity, Feature schema,
algorithm, parameters, available times and source row hashes are validated using
the existing contracts. No new indicator formulas. Feature snapshot can lag raw;
the file explicitly names each Feature candle and `feature_matches_latest_market`.
Do not pretend newer OHLCV has already been analyzed by an older stored decision.

Direction fields, timeframe scores/components/conflicts/reasons are projected
unchanged from its sealed decision. Entry copies its sealed evaluation, including
setup, lifecycle, chase, conflict and reasons. Entry input manifest/hash is verified.
Each has independent time and source references. An Entry upstream Direction ID
mismatch is reported. Candidate is research evidence, NOT trade authorization.
No Evaluation/future outcome or actual execution is synthesized.

Historical Binance reference versus TradingView live source labels are preserved.
OI raw value, period/time basis and validated Feature OI contract are kept separate.
No unit conversion, normalization, cross-basis change or raw overwrite. Current
legacy 15m `oi_basis_unverified` null change remains null; HTF basis stays explicit.

## Freshness and status

Expected last completed open = floor(T / duration) * duration - duration.
Market is stale if an expected candle is missing more than 20 minutes after its
close; this allows the current HTF 15-minute delivery delay. Direction/Entry age
over 30 minutes is stale. READY requires available results, current matching
Feature/market candles and no continuity/availability/alignment warnings.
Missing evidence/results or Feature lag -> PARTIAL; stale required data -> STALE.
Optional null Feature values retain their quality reason, not fabricated readiness.
These thresholds are a Snapshot delivery policy, not strategy parameters.

`generated_at_utc` is current projection time, never Feature computation time.
`source_cutoff_utc` is the newest selected market close; per-TF clocks remain explicit.
`latest_available_tf` means present, not necessarily fresh. A consumer must compare
its actual current time with generated/source/result clocks: a static READY label
can become stale without another successful writer. Failure preserves the prior
file and cannot refresh its timestamp. Access status is always ACCESS_UNVERIFIED
until independently verified in the user's actual ChatGPT analysis environment.

## Atomic publication and isolation

Entire in-memory object is validated/sealed with SHA256, written to a same-directory
temporary file, fsynced/revalidated then atomically replaced. Invalid source/schema,
hash, write/replace failure and older/conflicting timestamp refuse overwrite.
One workflow-specific concurrency serializes normal writers. Single latest file
does not remove old Git versions; historical Git content still exists.

New independent workflow runs after successful BTC Entry Timing workflow, plus
15-minute schedule fallback/manual dispatch. Existing workflows are unchanged.
Failure cannot fail or block upstream collector/availability/Direction/Entry jobs.
Only this latest file is staged. Normal push retries/rebase are bounded; latest-file
rebase conflicts fail closed, no force push. A failed publication may leave a complete
local runner file but cannot partially replace the GitHub published artifact.

## Analysis chat compatibility

Existing EMA20/50/slopes, RSI14, ATR14/%, volume MA20/ratio, OI, confirmed pivot
timing, HH/HL/LH/LL/EQ, rolling levels and breakout/breakdown are available when
their Feature quality is ready. Pivot `pivot_time` and `confirmed_at` stay separate.
Full 200-bar OHLCV charts, arbitrary discretionary pattern review, unimplemented
indicators and the legacy chat's separate scoring rules are unavailable. The
legacy prompt itself was not provided; exact formula-by-formula equivalence is
not claimed. Snapshot is sufficient for reviewing the existing engines' evidence,
not a promise to reproduce any arbitrary 200-bar analysis.

Access surfaces: GitHub blob URL, raw.githubusercontent.com, Contents API and
GitHub connector must be evaluated independently. Authenticated local Git/API
success or Actions checkout does NOT prove ChatGPT analysis access. If that
environment cannot read the repository, download/attach this one JSON or paste
its contents there; connector permissions can be checked by the user. No new
hosting infrastructure is established by this implementation.

## Validation

Unit fixtures cover reuse, missing/stale/future inputs, boundary/closed exclusion,
OI null preservation, source/hash conflicts, serialization, no engine invocation,
atomic failure/rollback, old-file protection, read-only and access honesty.
Full existing BTC and Upbit B tests/Node Worker tests are required. Production
smoke reads a revision-pinned checkout and writes no files unless `--write` is
explicit. Protection is audited by the Git diff; only Snapshot code/test/doc,
new workflow and initial latest file belong to its development commit.

Pre-publication validation 2026-10-06: 31 new fixtures PASS; BTC Python 383,
Upbit B Python 231, Node Worker 70 (684 total) PASS. Revision-pinned read-only
smoke found rows 872/442/335/305 for 15m/1h/4h/1d, all duplicate/missing/abnormal/
OHLCV-invalid counts zero; protected input hashes unchanged and Snapshot files
created zero. At 09:46:10 UTC, newly generated Direction and older Entry correctly
produced PARTIAL with ENTRY_DIRECTION_MISMATCH/ENTRY_LAGS_MARKET. After Entry
publication caught up, initial latest Snapshot at 09:46:57.038 UTC was READY,
129,187 bytes, Direction NEUTRAL/TRANSITION and Entry NO_ENTRY. Latest webhook
received 09:45:19.601 UTC; latest 15m candle open 09:30 UTC. This is a projection
health check, not profitability or actual trade execution evidence.

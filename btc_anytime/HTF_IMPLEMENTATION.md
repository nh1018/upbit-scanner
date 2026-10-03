# BTC Anytime HTF local implementation candidate

This adds direct TradingView completed 1h/4h/1d candles. It does not deploy, change alerts, enable Actions, or change existing bootstrap/15m/A V1.3 files. The full production Worker source is now supplied. Its pasted Markdown escapes are normalized in `tests/production_worker.fixture.mjs`. Direct differential tests compare response status/body, headers, logs, GitHub calls, commit messages, saved UTF-8 bytes and background errors. The legacy 15m storage path is retained independently of HTF storage.

## Files and contract

- `tradingview/btc_anytime_htf.pine`: Pine v6, standard BINANCE:BTCUSDT.P 15-minute chart only. Keep the legacy flat 15m payload and optionally add `higher_timeframe_candles` array. One `alert()` per confirmed realtime 15m bar. Each of six HTF price/OI requests uses the previous requested bar `[1]` inside `request.security`, with `lookahead_on`.
- `cloudflare/worker.mjs`: Cloudflare ES module candidate. Secret bindings remain `TV_WEBHOOK_SECRET` and `GITHUB_TOKEN`; optional `GITHUB_REPOSITORY` defaults to nh1018/upbit-scanner and `GITHUB_BRANCH` to main. No secret values are stored. Accepts legacy flat payload, flat payload with HTF array, or authenticated `candles` envelope. HTF additions are bounded to three beside a legacy row, or four in an envelope. Invalid/oversized additions cannot block a valid legacy row. There is no new body-size limit on legacy requests.
- `backfill_htf.py`: official Binance USD-M daily archive and optional historical OI tool; dry-run by default.
- `integrity.py` / `validate_history.py`: read-only whole-series validation and optional decimal-exact OHLCV cross-check.
- `tests/`: isolated mock network and temporary-data tests.

Natural key is `time` (UTC open milliseconds) within a timeframe. UTC boundaries are multiples of 3600000, 14400000 and 86400000. `close_time_ms` is open + duration - 1. TradingView `time_close` and `oi_time` use the exclusive end, open + duration. Collection time `received_at_utc` is separate.

HTF paths: `data_market/btc_anytime/{tf}/btc_{tf}_YYYYMMDD.jsonl`, partitioned by UTC open date, and `output/btc_anytime_{tf}_latest.json`. Original seed `btc_{tf}_history.jsonl` is checked for duplicate keys but never written. Legacy 15m retains `output/btc_anytime_webhook_latest.json` and its existing daily schema.

## Completed candles and OI

The previous HTF bar is stable during the next HTF period. It is emitted on the first confirmed 15m bar after its boundary: normally about 15 minutes after HTF close. An initial alert may send the most recent previous HTF bar; duplicate checks handle repeats. Chart/alert downtime can lose intervening HTF bars; the Pine candidate does not replay an arbitrary backlog.

For HTF, Worker validates boundary, clock completion, OHLC logic, nonnegative finite volume/OI and explicit OI period metadata. Legacy 15m keeps the production finite-only validation and ignores extra metadata. HTF unavailable OI is null with status and reason. It never substitutes 15m OI or estimates OI. Existing 15m behavior requires its normal numeric OI.

TradingView OI is the `close[1]` value of the confirmed OI bar matching the price period. `oi_time_basis=confirmed_oi_bar_close_boundary` denotes the period's nominal close boundary, not proof that the provider sampled exactly at that millisecond. Actual provider update time is unavailable. Historical Binance OI is joined only when its actual timestamp equals candle open, recorded as `oi_alignment=source_timestamp_equals_candle_open`. These meanings differ and must not be mixed as equivalent observations. Missing historical OI remains unavailable. Existing seed OI absence is reported without rewriting seeds.

## Duplicate, conflict and GitHub handling

HTF ledger is written before latest. Legacy 15m preserves production behavior: latest first, then daily append; duplicate checks compare time only, malformed existing rows are skipped during duplicate scanning, older candles append without sorting and may replace latest. Legacy commits remain `Update BTC Anytime latest data` and `Append BTC 15m candle <UTC>`. Legacy GitHub errors are logged and rethrown, with no new retry or timeout. Same natural key and same normalized values/OI timing metadata skips append. Conflicting values log timeframe/key/path and preserve existing rows. History and daily file are both checked. Existing daily row text is preserved on insertion; file newline formatting is canonical LF. Latest never moves backwards. GitHub SHA 409/422 conflicts re-read and re-evaluate, up to three attempts. Fetch including response body has a four-second timeout; whole batch has a 25-second budget. One invalid HTF does not block a valid 15m row. HTF safety, retry and timeout policies do not apply to legacy 15m. If legacy background storage fails, HTF is still attempted and the legacy error is then rethrown. Legacy response/authentication/diagnostic logs are retained.

HTTP 200 acknowledges acceptance, not durable storage. `ctx.waitUntil` retains the existing asynchronous pattern, not a durable queue. Four candles require up to eight GitHub commits plus reads; rate limits, the 25-second deadline, concurrent alerts and the Cloudflare background lifetime can prevent some writes. Retries are bounded, not guaranteed delivery. Inspect logs and validate the ledger; repair genuine gaps from official data. A durable queue/transactional Git update would require a separately approved design. A crash after ledger write may leave latest stale until a duplicate is retried.

## Backfill and validator commands

Run from repository root. Replace CUTOVER_UTC with the actual earliest live candle OPEN timestamp for each timeframe; it is excluded. Do not guess cutover. The plan rejects going beyond an existing first live row.

```powershell
python -B -m btc_anytime.backfill_htf --timeframe 1h --end-exclusive CUTOVER_UTC --fetch-oi
python -B -m btc_anytime.backfill_htf --timeframe 4h --end-exclusive CUTOVER_UTC --fetch-oi
python -B -m btc_anytime.backfill_htf --timeframe 1d --end-exclusive CUTOVER_UTC --fetch-oi
```

Review each dry-run. Only a separately authorized execution adds `--write`. The tool uses official ZIP plus CHECKSUM, verifies all expected slots and conflicts before writing daily files, and reports post-write validation. Never writes bootstrap or 15m files. Archive publication may lag. Explicit `--allow-rest-fallback` permits official Futures REST only when the ZIP itself returns 404; missing CHECKSUM, checksum failure or malformed archives still stop. REST response SHA256 is not a published archive checksum. No synthetic fallback is allowed. Historical OI errors/missing exact samples leave null/status/reason. Recent Binance historical OI retention and regional HTTP 451 may prevent OI access.

Use an isolated checkout with no concurrent git pull/collector while writing. Writes are atomic per file and check byte changes; the operation is not a multi-file transaction or distributed lock. A late conflict can leave earlier files written; re-run dry-run after inspection. Existing live keys are skipped and never overwritten.

```powershell
python -B -m btc_anytime.validate_history --cross-check --official-cross-check
python -B -m btc_anytime.validate_history --summary --cross-check --official-cross-check
node --test btc_anytime/tests/worker.test.mjs btc_anytime/tests/production_compatibility.test.mjs
python -B -m unittest discover -s btc_anytime/tests -p 'test_*.py' -v
```

Validator prints JSON to stdout, creates no files. Exit codes: PASS 0, WARNING 1, FAIL 2. Mandatory PASS requires structural validity, verified backfill provenance and complete exact official-to-official comparison for all backfilled rows. Existing seed OI absence and mixed observation bases remain explicit coverage warnings; user policy permits unavailable OI. Without `--official-cross-check`, mandatory verification is incomplete and status is WARNING. Cross-check computes only in memory, writes no synthetic candles, and excludes incomplete 15m buckets. Full report includes source transitions, file boundaries, every missing slot, conflicts and row locations.

## Manual deployment gates

1. Production source parity is locally tested; still verify deployed route and secret bindings match the candidate. Keep a rollback copy outside this implementation.
2. Compile Pine in TradingView without changing the live alert. Verify 1h/4h/1d price and OI symbols/periods against actual completed Binance bars, including midnight, invalid OI symbol and simultaneous closes. Local tests are static checks, not a Pine compiler or live feed proof.
3. After separate approval, deploy Worker first with existing secrets and route, then create a replacement alert from the new indicator snapshot. Do not run old/new alerts together indefinitely. Define first-live cutovers and backfill reviewed official data in an isolated checkout; validate full continuity and logs. TradingView alerts use script/input snapshots, so changing chart code alone does not update the existing alert.

Worker deployment, alert modification and Actions activation remain prohibited for this task. Actual backfill and Git publication are authorized only after mandatory audits pass. TradingView Pine compilation/Add to chart and numeric 1h/4h/1d price/OI return were verified by the user; deployment bindings and actual live cutover remain separate checks.

## References

- TradingView confirmed HTF requests: https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/
- TradingView alerts/snapshots: https://www.tradingview.com/pine-script-docs/concepts/alerts/
- Cloudflare waitUntil lifetime: https://developers.cloudflare.com/workers/runtime-apis/context/
- GitHub Contents API: https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents
- Binance public archives/checksums: https://github.com/binance/binance-public-data

## Confirmed source policy and audit

`source_policy.json` records the user-approved policy and the known 2026-10-01 09:15 UTC Low/Volume mismatch. Cause remains unconfirmed. TradingView live 15m is immutable raw observation; never correct, overwrite or delete it to match Binance. Binance official archive/REST is finalized reference and controls HTF historical OHLCV.

`source_audit.py` verifies backfilled HTF archive checksums or exact REST response hashes/row values, plus exact historical OI response hashes and observation timestamps. It downloads official 15m references into memory and checks every backfilled upper candle against 4/16/96 original candles with Decimal arithmetic. Any official mismatch, missing coverage, invalid source/checksum/OI provenance or structural error fails mandatory validation. Existing immutable bootstrap source is preserved; its original archive checksum was not recorded, and old seed OI remains unlabelled absent.

TradingView raw-only aggregation excludes seed/reference rows. Its differences are reported separately as `SOURCE_MISMATCH`, with exact candle/field/values. Raw mismatch never makes a valid official backfill fail. Partially covered raw buckets are identified, not filled. The known mismatch remains Low 83632.3 vs 83630.00 and Volume 937.328 vs 1062.730; it propagates a 125.402 Volume difference into one 1h, one 4h and one 1d bucket.

`HTF_BACKFILL_REPORT.json` records cutoffs, exact source audits, raw mismatches, OI coverage, source errors and original JSONL hashes. Cutoffs are a completed-data snapshot, not an actual live HTF switch. Before future deployment, extend official backfill through the eventual first-live open without changing raw observations or seed, then repeat mandatory validation. Do not mistake today's snapshot for continuity through a later alert start.

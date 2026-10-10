# A/B/C recorded-observation performance research V1

Manual-only, gross long-side proxy evaluation. This describes conditional future
price paths, not realized trade P&L, entry execution or proven strategy returns.
No workflow, schedule, signal publisher or collector is added/changed.
Base repository revision: dc0a532d (origin/main when the two independent branches began).

## Existing implementation inventory

| Strategy | Actual source and existing capabilities | Adapter policy |
|---|---|---|
| A V1.3 Cloud | upbit_binance_scanner.main → completed daily D1..D5 plus rolling24h/ticker → score_row/state_row → output/latest_scan.json, latest_candidates.csv, scan_*.csv; data/scan_history_v13.csv appends all scanned rows | Import only already-published latest snapshot candidates; namespaced deterministic observation ID, no score/selection recomputation. scan_time_kst/generated_at_kst is captured at scan START, not API receipt. signal_observed_at and proxy anchor remain null and performance UNVERIFIABLE. No invented past availability or candidate-to-entry conversion |
| B | Market Data/Feature/Trend-State plus production Compact V1 journal and latest summary; hourly history workflow is already ON with :07/:17/:27 fallback, unchanged | Reuse history.validate_cycle (Compact decoder included), history.advance to validate actual complete parent/cohort/transition chain. Import only TRUE candidates with transition_verified positive ENTER events. Exclude baseline, heartbeat, control, UNKNOWN and state-reobserved-after-gap |
| C | research_history sealed prospective PASS observations; research_outcomes.evaluate/fetch_outcome +1/3/7 days; Archive Active State tracks pending work | Validate original signal and envelope; call unchanged C evaluator. c_outcome imports existing sealed outcomes using stored path/evidence, compares replay status/metrics/boundaries. Never synthesize old signals from scan scores |

No new A/B outcome implementation already meeting this contract was found.
BTC performance remains separate and unchanged. C signal identity/weights/outcome
formulas/Active State and storage remain unchanged. Common outcome events do not
write into C original history, B journals or A output.

## Versioned common contract

Modules: contracts (schema/policy), adapters (source contracts), engine (pure
measurement), aggregate (selection/statistics), storage (isolated append-only),
runner (manual inventory/pinned offline bundle and optional explicit price fetch).

| Field | Definition |
|---|---|
| strategy / strategy_version / cohort | A/B/C and original algorithm, parameter/storage cohort; never pool different cohorts |
| signal_id | Existing B observation ID or C signal ID; A's namespaced market+recorded scan-time observation identity |
| market | Original KRW instrument, never mapped across strategies |
| signal_observed_at | Actual B engine observation/C observed_at in UTC milliseconds; null for legacy A |
| source_cutoff | B recorded cutoff; A max(scan start,ticker timestamp), not availability; C trigger completed close (exact scan cutoff absent from stored signal) |
| source_cutoff_semantics | Explicitly labels the above distinction; no backdated availability assertion |
| signal_price_reference | Original diagnostic price/type/time, not proxy or actual fill |
| evaluation_anchor / type | First whole UTC hour strictly AFTER known observation; NEXT_1H_OPEN_PROXY; null when availability unknown |
| horizon / endpoint_boundary | 1/3/7 * 24 hours after proxy open, exclusive right boundary; not a KST-calendar-day trade |
| status | PENDING until horizon elapsed; elapsed but missing/invalid path/evidence → UNVERIFIABLE; full verified path → MATURED |
| return_pct / mfe_pct / mae_pct | Decimal gross price measurements, null if not MATURED |
| data_evidence / source_hash | Original signal-source file SHA256; price response URL/SHA/received time/row times; normalized price path SHA256 |
| evaluation_version / policy_sha256 | abc-gross-research-evaluation-1; separately hashed POLICY |
| evaluation_id / event_id | Stable strategy+version+cohort+signal+anchor type+horizon key; event content hash includes as_of/evidence/status |

Normalized signals are content sealed; they retain original file hashes/references.
Existing source hash/clock evidence is an attestation model, not proof of execution
or cryptographic authorship. The actual smoke bundle additionally retains exact
official response bytes and external file hashes for offline replay. Do not accept
untrusted operator-supplied hashes as independent proof of origin.

## Measurement and leakage rules

Let p0 be the next-hour candle open, p1 the final hour's close, H=max(hourly high),
L=min(hourly low) over exactly days*24 hours from anchor inclusive to endpoint
exclusive. return=100*(p1/p0-1), MFE=max(0,100*(H/p0-1)),
MAE=min(0,100*(L/p0-1)). Decimal precision34, HALF_EVEN.
The proxy is a subsequently observed interval-opening price; it is **not** actual
entry, first observed tick or a realizable execution price. Fees, slippage, spread
and position sizing are not included. No order/execution evaluation is supported.

Only UPBIT matching-market completed1h candles are allowed. Exact ordered natural
keys/boundaries and OHLCV logic are required; duplicates, missing rows or malformed
values reject maturity. Used candles must have actual official1h response evidence
observed after their close and no later than evaluation as_of. A response for
another market cannot cover a candle. Finalized prices obtained today can evaluate
an old prospective signal's outcome, but cannot change its stored signal cutoff,
score, availability or anchor. Future suffix rows outside the measured path do not
change the result. UTC00:00 equals KST09:00; horizon is elapsed24h, not KST midnight.

## Statistics and dependence policy

Within each strategy/version/cohort/anchor/horizon, deduplicate evaluation IDs and
select the earliest observed signal per market; exclude later anchors inside its
measurement interval. This rule uses no realized return/maturity. Exact endpoint
starts do not overlap. Status counts/coverage and overlap exclusions are retained.
PENDING/UNVERIFIABLE are neither wins nor losses; do not report a win rate when no
mature outcomes exist. A unavailable observation is counted as unavailable only.

- Coverage = matured / all nonoverlapping selected observations.
- Win rate = positive returns / matured, including zero-return flats in denominator.
- Average gain/loss use positive/negative matured subsets respectively.
- Payoff ratio = mean gain / abs(mean loss); null if either subset absent.
- Expectancy = mean all matured returns (equivalent to p(win)*mean gain + p(loss)*mean loss; flats contribute0).
- MFE/MAE mean and nearest-rank p25/p50/p75/p95 use matured only.
- Score bands <45/45..<65/65..<80/>=80 are descriptive strata, not changed strategy gates. C's original0..1 score is scaled100 only for descriptive band labels; raw score is retained.
- Regime breakdown is explicitly UNSUPPORTED: no validated contemporaneous regime contract was supplied.

This does not make markets, horizons or distinct strategies statistically
independent. Do not sum cross-horizon sample sizes or treat continuous observations
as independent trades. C's prospective signal is still research-only; B transitions
are recorded state events, not verified entry signals. Selection limitations must
accompany all reports.

## Append-only events and manual operation

Default CLI emits stdout only. No production evaluation file is created by the
real-data validation. For an externally pinned bundle containing signal, days,
candles, evidence, as_of_ms (and original_c_signal for C):

    python -B -m strategy_evaluation.runner --bundle <file> --bundle-sha256 <external SHA>

Explicit optional recording:

    python -B -m strategy_evaluation.runner --bundle <file> --bundle-sha256 <SHA> --record-root D:/repos/abc-research-results

Storage path is strategy/evaluation_id/event_id.json. Events use exclusive atomic
hard-link publication. A per-evaluation directory lock serializes writers; a stale
lock fails closed and requires inspection, never silent reset. Exact replay is a
no-op; a matching matured re-verification is also a no-op; changed terminal metrics,
original observation or later terminal regression fails. PENDING/UNVERIFIABLE →
MATURED adds a new event without overwriting old bytes. Source/production namespaces
and symlinks are guarded. No default persistence or workflow is supplied.

Inventory only:

    python -B -m strategy_evaluation.runner --repo D:/repos/upbit-scanner --c-scan <verified original> --c-sha256 <external SHA>

Never use a legacy A scan start as actual receipt; an A prospective capture contract
requires separate design/approval before A can have eligible future outcomes.
Do not insert observations into existing A/B/C stores. B import requires full
original lineage; missing parent fails rather than silently initializing.

## Real-data validation (read-only)

See audits/real_validation_20261010.json and real_replay_20261010.json.

- A: actual snapshot30 candidates, eligible0; all outcomes unavailable rather than invented.
- B: 27 existing production journals fully validated, 41 positive verified transitions; baseline/control/heartbeat excluded.
- C: verified run38000031787, 294 markets/159 scoreable/135 unevaluable; actual PASS0. No C signal/outcome/win rate fabricated.
- Oldest B sample: KRW-JST observation86979b49e4f61650d0820084029bd93c230925087d548f90a618d4ce2371958b.
  Anchor2026-10-07T14:00Z, endpoint2026-10-08T14:00Z. Official completed hourly path24 rows; anchor189, endpoint190.
  Gross return0.529100529100529100529100529100500%; MFE3.174603174603174603174603174603200%; MAE-2.645502645502645502645502645502650%.
- Two bounded read-only requests (initial verification + raw-response replay capture), no universe scan. Exact response SHA6610bfc53fdc8667d6f4b8a67819120b88783797e8478c5a844766369ade20bc; response bytes retained.
- Independent Decimal calculation from original API fields matches evaluator before rounding. Repeated official request matches the first path. Sealed bundle permits offline replay; no Direction/Trend-State re-execution.
- No future-price path supplied for the other B cases: the inventory's statuses are missing-input diagnostics, not completed outcome claims. No full-cohort win-rate estimate is asserted from this one sample.
- Original A snapshot, B journal and C original file hashes remained unchanged. Strategy/collector/Worker/Pine/Alert/Release/workflow changes0.

## Tests and operational prerequisites

Synthetic tests cover all horizons and exact metrics, invalid/forming/gap/duplicate
candles, UTC boundaries, source hash/market/receipt violations, future suffix
invariance, raw immutability, baseline exclusion and complete B lineage, existing C
outcome replay, unknown A availability, append-only maturation/replay/conflicts,
writer locks, nonoverlap, status denominators and deterministic Decimal behavior.
Regression counts and Actions checks are recorded in the Draft PR; no failures are
hidden or bypassed. Production workflows were not run or changed.

Before automatic operation: approve A availability capture separately; approve
which B/C observation cohorts to evaluate; establish result retention and external
hash trust; bound API load; approve evaluation schedule/output policy and regime
contract if wanted. No automatic integration is authorized by this implementation.

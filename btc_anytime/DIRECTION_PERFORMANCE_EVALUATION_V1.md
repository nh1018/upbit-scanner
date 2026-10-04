# BTC Direction Performance Evaluation V1

## Scope and protection

Approved predictive evaluation only, not entries, fills, leverage, SL/TP or trading PnL. Only existing prospective append-only signal publications are inputs. No Direction replay/recomputation, historical reconstruction, parameter optimization, source substitution or production writes occur in the dry-run. Raw/provenance, Feature/Direction formulas and parameters, existing Signal History IDs/decisions, Worker/Pine/collector/Alert and Upbit A V1.3 are protected. Schedule fallback PARTIAL is a separate operational issue and is unchanged.

## Architecture and versions

`direction/evaluation/engine.py` is a pure transformation; `aggregate.py` builds descriptive reports; `storage.py` provides opt-in immutable persistence; `runner.py` exposes only read-only dry-run. No evaluation workflow or recording CLI is enabled. Evaluation algorithm/schema 1.0.0 and evaluation-initial-hypothesis-1 parameters are separate from Direction parameters. The parameter SHA256 is canonical JSON without the hash field. Decision, parameter, feature, evaluator, anchor and source identities partition reports.

## Anchors and horizons

TRIGGER_CLOSE_DIAGNOSTIC uses the trigger15m close saved in the decision and its existing reference/availability. Its price time is trigger open +15m. It is a diagnostic market-state reference, not an entry price; it can include predecision latency in endpoint return.

NEXT_15M_OPEN_PROXY uses open of the completed production15m candle at B=ceil(decision_time/900000)*900000. Exact boundary T uses B=T. It is the retrospectively verified start price of the first full post-decision15m interval, not first observed tick, realizable price or execution price. Missing B is not replaced by a later candle. Tick/quote anchors require a new future contract.

Horizons 1H/4H/12H/24H are elapsed time from each anchor price time. Endpoint price is close of candle opening endpoint-15m. No nearest-price lookup or interpolation. Decision time, source price time, received time, repository observed time and label recorded time remain distinct; Git committer clock is not publication evidence.

## Exact metrics

raw_return_pct=100*(endpoint_price/anchor_price-1). LONG/STRONG_LONG use +raw; SHORT/STRONG_SHORT use -raw; NEUTRAL directional return is null. Core metric is continuous directional return. Sign hit uses >0, miss <0, flat =0; flat remains in hit denominator and is counted separately.

Minimum move threshold=100*decision15m ATR14/anchor_price, multiplier1.0. Only stored15m ATR14 whose quality is ready and available at decision time is permitted. Missing ATR leaves only ATR-dependent metrics unavailable; other-TF ATR is never substituted. Threshold equality counts as hit. Parameters are unvalidated initial evaluation hypotheses.

Completed15m path [B,endpoint) uses high max/low min. up=max(0,100*(Hmax/P-1)); down=max(0,100*(1-Lmin/P)). LONG MFE=up/MAE=down; SHORT MFE=down/MAE=up. MAE is positive adverse magnitude. Endpoint-start candle is excluded. Progress bars, missing evidence, duplicate/conflicting rows and invalid OHLCV are not accepted as full paths.

Diagnostic excursion always PARTIAL. The T-containing candle is excluded; unknown initial intrabar interval is not assumed zero. Observed extrema are lower bounds. Proxy excursion is MATURED only with every expected completed candle and cutoff-valid evidence. Partial and complete excursions are reported separately.

NEUTRAL stores absolute endpoint return, up/down, max absolute excursion, range100*(Hmax-Lmin)/P, endpoint/path band retention and up/down crossings. Band is decision15m ATR14, multiplier1.0. Complete path retention is unavailable for partial excursions. Large movement after NEUTRAL does not imply failed entry or reconstruct a new direction.

## Sources and availability

Primary source contract is tradingview-production-15m-v1. Existing explicit TradingView rows and legacy received_at webhook rows (legacy_webhook_inferred, explicitly retained) are permitted. Binance historical/backfill rows are not silently substituted. The legacy source inference is inherited from the existing Feature contract, not a newly normalized raw field. Every future dependency needs canonical row hash, raw reference and real consumer-first-observed evidence at/before cutoff. The production runner loads existing observation events plus immutable inline observation events from published decisions; it creates no fake evidence.

Late observations may mature a label now, never at the old cutoff. Future suffixes beyond endpoint/cutoff cannot change the same evaluation. Evidence is insufficient to assert exact realtime tick availability or GitHub publication time; those claims remain unavailable.

## Logical states and physical artifacts

PENDING: endpoint future, no persistent artifact. SOURCE_MISSING: elapsed endpoint but anchor/endpoint observation absent. PARTIAL: endpoint return valid but incomplete/diagnostic excursion. MATURED: full proxy path. SOURCE_CONFLICT: duplicate/hash disagreement. INVALID: decision/clock/instrument/numeric contract error. Return and excursion status are distinct.

Persistent layout under output_direction/btc_anytime/v1/evaluation: events/{key}/{hash}.json and labels/{key}/{hash}.json. Key includes decision ID, evaluator, evaluation-parameter hash, anchor/version, source and horizon. Payload hash excludes actual recording time; envelope retains first actual recorded_at. Same payload replay is no-op. PENDING never creates directories. Same natural key with different finalized payload adds a conflict event and never overwrites. Missing/partial gap events may be followed by a finalized label; original events remain. Diagnostic return plus complete observable path is finalized with excursion PARTIAL. Local exclusive lock prevents concurrent same-key writers; abandoned lock fails closed. Distributed Git publication needs a separately approved workflow and conflict guard.

No snapshot/manifest is copied into labels. Only decision/content hash, small cohorts, necessary raw/evidence references, expected/observed counts, missing slots, extrema references and metrics are retained. Aggregate reports return immutable content-addressed objects; automatic report persistence is not exposed in this version.

## Aggregates and independence

Version/source/anchor/horizon partitioning precedes cohorts: class, bias, confidence [0,.5)/[.5,.65)/[.65,.8)/[.8,.9)/[.9,1], regime and direction×regime, TF score buckets, component sign (NULL differs from ZERO), OI unit-validation and derivative-change availability, UTC daily/4H cohorts. STRONG classes remain distinct; sparse comparisons are descriptive, not profitability claims.

Mean/median returns, interpolated p05/p95 tails, hit rates, complete versus partial MFE/MAE, neutral range, zero-MAE count and MFE/MAE ratio with MAE>0 are supported. Confidence remains evidence quality, not probability. Counts for all logical states are retained. Missing/invalid are excluded only from metric denominators, not observation counts.

Non-overlapping greedy view sorts anchor time then decision ID, accepts next anchor>=prior endpoint, and never substitutes invalid results with favorable neighbors. Selection occurs before metrics. This is not a proven effective independent sample count. 30 matured returns is a sparse-warning hypothesis, not significance; non-overlap sparse and overlap warnings accompany reports. Reports never automatically alter parameters.

## Commands and activation

Read-only: `python -B -m btc_anytime.direction.evaluation.runner --repo .` (optional explicit --cutoff-ms for as-of audit). No --record option, new workflow or production evaluation label is generated. Future activation requires separate approval, validated remote append-only publication and maturity/coverage review. This delivery can be implementation-tested even if all real horizons are still PENDING.

## Tests

Fixtures cover diagnostic/proxy anchors, exact boundaries/endpoints, unknown intrabar exclusion, ATR15m-only, unavailable ATR, direction symmetry, NEUTRAL, sparse/overlap cohorts, future suffix and late evidence, PENDING no writes, first append/replay/finalized conflict, source gaps/conflicts, progress bars, immutable raw/decision objects, lock exclusion and no snapshot duplication. Full existing Python/Node regression remains mandatory. Production read-only hashes protect market/provenance/Feature/Direction/Signal History/Worker/Pine/A V1.3.

## Implementation verification, 2026-10-04

Final regression: Python246 (existing194 + new52) and Node70, total316 PASS. Evaluation parameter SHA256 b99621d4847a9f48c19913b5b4d2a28a326f91f22cb9701015fbbf65054a69c9. Production read-only dry-run at 05:40:06.109 UTC on main46f3361 observed three existing prospective decisions and24 PENDING outcomes (2 anchors ×4 horizons ×3 decisions). No matured production return is claimed. First diagnostic1H endpoint is06:00 UTC; first proxy1H endpoint06:15 UTC, both require later real observation evidence. Protected hashes unchanged; no evaluation namespace/files created. Automatic evaluation remains unconfigured, pending separate approval and real maturity checks.

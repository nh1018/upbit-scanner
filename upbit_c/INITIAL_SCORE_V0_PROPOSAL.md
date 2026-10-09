# Upbit C — Initial Score and Candidate Research Design V0 (PROPOSAL)

Status: **research proposal only**. No production activation, orders, candidate publication, or modification to Upbit A/B or BTC. This document follows successful real-data evidence smoke run [37847965581](https://github.com/nh1018/upbit-scanner/actions/runs/37847965581): 5 KRW markets × 3 completed-candle windows = 15/15 EVALUATED; all defined evidence fields READY in those windows. This is **coverage**, not strategy profitability validation.

## Purpose and independent strategy boundary

C detects a potential **post-selloff rebound**, not any large negative return. Keep C score, status and historical performance separate from A pre-launch and B trend continuation. A/B/BTC contracts remain frozen. The existing `upbit_c.evidence.observe` is the source; do not introduce another candle collector. Every observation must preserve completed-candle close/cutoff, feature hashes, readiness and source clocks.

## V0 score hypothesis (0–100; NOT calibrated probability)

Five independent groups, each in [0, 1]; suggested weights are provisional and must not be optimized against a handful of observations:

| Group | Weight | Required evidence and intended interpretation |
| --- | ---: | --- |
| Selloff severity | 20 | Negative 3/5/10-bar returns normalized by ATR%; an extreme decline alone cannot make a candidate |
| Capitulation | 20 | Relative quote volume against MA20/median20 plus close-location; distinguish actual turnover from an illiquid drop |
| Deceleration | 20 | Return3 acceleration and return1; identify slowing selling rather than accelerating losses |
| Low defense | 25 | Confirmed-low ATR distance, low structure, breakdown20; require defensible price structure |
| Recovery | 15 | Positive body, upward EMA20 recross, recent3 quote participation; require observable buying recovery |

Score = 100 × weighted mean of the five group strengths **only if all mandatory groups have sufficient READY evidence**. Do not silently renormalize missing required groups or replace missing values with zero. The numeric group transfer functions and threshold boundaries are deliberately **not approved** in V0; define and fixture-test them before calculating any score. The weights above are hypotheses, not a claim of predictive performance.

## Mandatory quality and exclusion gates

1. Use actual **completed** 1h/4h/1d Upbit candles, never forming candles or rolling 24h ticker as completed candles. Reject stale/mismatched source close, invalid hashes, and API errors. A missing group is UNKNOWN, not bearish.
2. Require an objectively observed selloff on 4h and at least one additional timeframe; a mild dip cannot qualify solely due to an oversold oscillator. Define the ATR-normalized selloff threshold before implementation.
3. Require both **deceleration** and **low defense** evidence on 1h/4h. Ongoing 4h breakdown or confirmed-low failure blocks candidate status regardless of score. Do not assume missing evidence means low defended.
4. Require a separate recovery confirmation; high capitulation volume and negative returns alone cannot issue a rebound candidate.
5. Use liquidity/turnover guard based on verifiable Upbit quote value, not a hardcoded asset whitelist; explicitly document coverage and denominator. Binance Spot is optional context, never required for C eligibility.
6. Track chase/late-entry risk independently; C score is a research-fit score, not a LONG/BUY instruction.

## Proposed state vocabulary (not activated)

- `INSUFFICIENT_EVIDENCE`: mandatory evidence unavailable or invalid.
- `SELLOFF_ACTIVE`: observed decline but low defense/deceleration unconfirmed.
- `CAPITULATION_WATCH`: high selling participation; recovery unconfirmed.
- `LOW_DEFENSE_WATCH`: slowing decline with defensible low.
- `REBOUND_CONFIRMATION`: independently confirmed price and participation recovery after selloff.
- `REBOUND_INVALIDATED`: new structural damage after a valid observed setup.
- `NO_C_SETUP`: no qualifying post-selloff setup.

Do not infer a temporal state transition from a single snapshot. Distinguish current geometry from observed prospective transitions.

## Next implementation gate (before enabling candidates)

1. Add pure, deterministic C score/state functions **on a research branch**, with explicit Decimal parameters, parameter hash, schema version, and strict input/readiness validation.
2. Add offline fixtures for normal rebound, ongoing selloff, weak bounce, illiquid candle, sparse/stale windows, missing features, and contradictory structure; test boundaries and stable replay.
3. Run read-only research over a wider KRW sample including historical dump-and-rebound episodes and negative controls; preserve observation clocks and avoid future leakage.
4. Compare subsequent +1d/+3d/+7d returns, MFE, MAE, drawdown, expectancy and false positives in prospective records **before** activation.
5. Activate only after separate review/approval. Do not change existing production A/B/BTC, and never interpret this design as a production candidate signal.

## Evidence limitation

The smoke run validated availability on five liquid markets only. It did **not** establish readiness for the full KRW universe, threshold calibration, signal precision, or profitability. Those remain open acceptance gates.

# Entry Timing Engine V1 — confirmed implementation contract

Status: implementation and read-only preview only. Production recording, activation,
workflow creation and order execution are OFF. Parameters are INITIAL_HYPOTHESIS,
not profitability-validated strategy values.

## Boundary and architecture

Published prospective Direction + unchanged Feature V1 + actual observation evidence
→ compact Entry input manifest → pure Entry evaluation/lifecycle → append-only storage
contract. Only the read-only runner is exposed as a CLI. No Direction reconstruction.
1D/4H influence Entry through the published Direction; 15m is the setup/confirmation
timeframe and 1H provides short-term conflict context. No Direction score changes.

Modules: `entry/engine.py`, `parameters.json`, `storage.py`, `runner.py`.
Output namespace reserved for separate future approval: `output_entry/btc_anytime/v1`.
No production artifact is created in this implementation stage.

## Inputs, clocks and identity

Each compact input preserves raw OHLCV/source/OI metadata, file/line/raw canonical
hash, consumer first-observation evidence, Feature values actually used, individual
quality/dependency-availability fields and actual feature generation time.
The manifest references the immutable Direction ID/path/content hash/parameter
version/hash and actual time this consumer read it. It does not duplicate its snapshot.

All candle close, received, observed, generated and ready-feature clocks must be
consistent and no later than evaluation time E. Candle boundaries are exact. Older
historical inventory first observed now is known-by-now only. No backdated availability.
The runner recomputes unchanged Feature formulas in memory from rows available at its
actual observation cutoff, then records actual generation time in its memory manifest.
No persisted evidence or Feature calculations are changed.

Hash identities use existing canonical sorted JSON SHA256. The consumed manifest is
sorted by timeframe/open time and excludes unavailable future suffix rows; changing a
future row cannot change a present evaluation. Evaluation IDs cover the entire encoded
result including E and previous evaluation ID. Decimal arithmetic uses precision 50,
ROUND_HALF_EVEN, existing Feature encoding at 18 decimal places. Replay is an exact
stored-result no-op, preserving original E and authorization; it is not a fresh decision.

## Authorization and states

LONG/STRONG_LONG authorize long; SHORT/STRONG_SHORT authorize short. NEUTRAL yields
NO_ENTRY / ENTRY.NO_DIRECTIONAL_ENTRY. Direction age must be ≤30m and its trigger may
lag current trigger by at most one completed 15m interval. Future Direction is rejected.
POSSIBLE_REVERSAL, INSUFFICIENT_EVIDENCE or confidence <0.50 veto authorization;
exactly 0.50 is allowed. Entry never reclassifies Direction.

Same-side newer Direction updates authorizing_direction_decision_id while preserving
origin_direction_decision_id, setup ID, A0 and protective reference. Opposite/NEUTRAL
invalidates the active setup. A previously consumed boundary cannot be reconsidered.

Business: ENTRY_CANDIDATE / WAIT / NO_ENTRY. Operational failure or skipped input has
entry_state=null, never a disguised NO_ENTRY. Failure reasons distinguish hash/source
conflict, contract failure, unavailable features, stale Direction and missed boundary.
No historical reconstruction. Missed intervals invalidate active continuity and skip.

## Signed derived measurements

Let s=+1 long, −1 short; C=close, H=high, L=low; A=current ATR14.
Direction-independent prices are never modified. All side-dependent formulas use s.

| Measurement | Definition |
|---|---|
| movement3_atr | s(C[t]−C[t−3])/A |
| EMA20 extension | s(C−EMA20)/A |
| close location | (C−L)/(H−L) long; (H−C)/(H−L) short; null when H=L |
| impulse amplitude | s(extreme−impulse starting close) |
| pullback depth | s(extreme−C)/A |
| retracement ratio | s(extreme−C)/impulse amplitude; unavailable/failed if no positive amplitude |
| location distance | min absolute distance to EMA20/50 and an available prior breakout reference, divided by A |
| breakout magnitude | s(C−prior rolling level)/A |
| breakout extension after arming | s(C−frozen breakout reference)/A0 |

After arming A0 is frozen. Active setup chase normalization, breakout hold and reference
failure use A0, including movement3. The current ATR remains reported as input context.

## Independent routes

PULLBACK: search the six prior completed origins, excluding current candle, for a
three-bar signed close movement ≥1.00 ATR at origin. Use the most recent eligible
origin; its three-bar high/low extreme and starting close define impulse. Require depth
in [0.30,1.20] ATR, retracement/amplitude ≤0.60, location distance ≤0.50 ATR, at least
one adverse close step after impulse, and intact protective reference.
Protective reference: confirmed low pivot long/high pivot short first, rolling low/high20
fallback. Pivot confirmation close and actual confirmed_at must be ≤E. No unconfirmed
or retrospective pivot. A missing reference makes only this route UNAVAILABLE.

BREAKOUT: new rolling20 breakout/breakdown flag with previous flag false. This rising
edge avoids continuously rearming the same unbroken episode. Require signed magnitude
≥0.10 ATR, volume ratio≥1.20 and directional close location≥0.65. Its prior rolling20
level is frozen as reference. Missing mandatory evidence makes this route UNAVAILABLE.

Each route returns PASS / FAIL / UNAVAILABLE independently. PASS on either remains
eligible when the other is UNAVAILABLE. Both unavailable → skipped. If both pass,
PULLBACK has deterministic priority; no extra combined setup is created.

## Gates and confirmation

Chase veto: EMA20 extension >1.50 ATR, OR movement3 >2.00 ATR AND EMA20 extension
>1.00 ATR; armed breakout additionally extension >0.75 A0. Equality does not veto.
Chase keeps an otherwise valid ARMED setup in WAIT, never creates a separate setup.

Strong short-term conflict: opposite confirmed pivot pair (LH+LL against long,
HH+HL against short), opposite rolling20 breakout/breakdown AND signed one-bar close
movement/ATR ≤−0.25. Evaluated separately on 15m and 1H; unavailable evidence remains
UNAVAILABLE, not false certainty. −0.25 is an explicitly versioned initial context
hypothesis. A strong conflict vetoes candidate/invalidate active setup.

Volume ratio uses unchanged Feature V1 MA20 (current candle included). ≤0.80 is only
contraction context; confirmation requires ≥1.00. OI is optional: validated same-segment
Feature oi_change with positive OI and side-positive/negative price step is confirming/
conflicting; negative OI is declining context; exact zero flat. Unknown OI is UNAVAILABLE
without veto or synthetic fill. Existing basis/provider/unit boundaries are preserved.

Confirmation must be a separate later completed candle, have side-positive one-bar
close movement, close location≥0.65, volume≥1.00, and pass gates. Pullback additionally
closes beyond previous candle high long/low short (REACCELERATION metadata only).
Breakout must hold ≥0.05 A0 beyond frozen reference. No next open is read.

## Lifecycle and references

ARMED → CONFIRMED once, or INVALIDATED, or EXPIRED. Origin, A0 and reference never
move. Reference fails strictly beyond a 0.10 A0 buffer. Equality remains intact.
Expires at detection candle close +6 intervals PULLBACK/+4 BREAKOUT; E≥expiry expires
before confirmation. Post-terminal rearm requires ≥2 completed intervals and a distinct
episode identity. No repeated candidate from a confirmed setup.

Candidate reference_price_type=CONFIRMATION_CANDLE_CLOSE; reference_price is the actual
confirmation close, reference_time its close boundary. It is not fill, actual entry,
first tick or realizable execution price. Protective reference is not a stop order.

## Append-only schema and storage

Evaluation includes algorithm/schema/parameter versions/hash, upstream ID and immutable
Direction context, E, trigger boundary, execution_status, entry_state, routes, derived
measurements, chase/conflict/volume/OI state, setup quality, reasons/warnings, input
manifest ID, previous evaluation ID, lifecycle state and candidate reference when present.

Exclusive writer lock; immutable content-addressed input/evaluation files; candidate
index keyed by setup ID; boundary index written last. Same publication returns REPLAY_NOOP;
different content for an occupied boundary or setup fails closed. Incomplete writes can
finish the same immutable publication; damaged or divergent artifacts require manual
investigation. Stale lock is not automatically broken. No latest overwrite.
Parameter changes require explicit future rollout, not automatic reinterpretation of
old state or existing boundaries.

## Verification and exclusions

Tests cover independently hand-authored fixture inputs, exact thresholds, same-side
authorization, opposite/neutral invalidation, expiry precedence, frozen references,
pivot clocks, unavailable routes, future suffix, late observation, missed boundaries,
mirror symmetry, append-only conflicts/tampering and raw immutability. Existing Node/
Python regression must pass. Production dry-run returns only memory results and
checks protected hashes before/after, revision consistency and zero Entry artifacts.

Excluded: execution, account APIs, leverage, sizing, stops/targets, fills, PnL, slippage,
fees, optimization/ML, tick/orderbook/liquidation data, production workflow/history.
Future activation needs its own baseline, prospective observation loop and operational
approval. This implementation does not assert an Entry strategy is profitable.

## Implementation verification (2026-10-04)

402 tests PASS: existing 333 + 69 Entry tests (Python332 / Node70).
Read-only production revision: dd0f20f0a6af5669f79c8c3159ce0aca3a766272.
Evaluation time: 2026-10-04T07:08:06.501Z (16:08:06.501 KST).
Published Direction: 1cea6dc9f84260d1684bdcf8487337f71a1546603dc82dcfd94577e97769d594.
NEUTRAL, score 0.334031794972122145, confidence 0.666431818181818182,
ALIGNED_TREND; observed age 456303ms. Latest selected 15m opened06:45 UTC;
1H opened05:00 UTC. Result EVALUATED / NO_ENTRY / ENTRY.NO_DIRECTIONAL_ENTRY.
No directional setup, reference, chase or conflict is fabricated for NEUTRAL.
Input context still reports ATR/EMA/volume/OI, with volume ratios 2.763709777815306783
(15m) and 0.630312768703229859 (1H). The current 15m OI basis is unverified and its
change remains unavailable; 1H OI contract is validated and change is 244.39 BTC.
Protected inputs unchanged. Production Entry artifact count0; no Entry workflow.

Parameter version entry-initial-hypothesis-1; canonical SHA256
da3b45ac1b3aa2ae0e40e75d570bcab1f9da7ba9296dcb7cae088b82cdd2cff8.
Dry-run has no prior production Entry state because recording remains disabled.
Real-world PULLBACK/BREAKOUT candidate publication and operational recovery remain
future prospective activation checks; their code contracts are covered by fixtures.

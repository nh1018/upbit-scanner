# B Feature Layer V1 — approved initial calculation specification

Market Data baseline: 8cb0cf8b716401e317ee17015f4a6c9f53dcfa6c.
Algorithm: upbit-b-features-1. Schema: upbit-b-feature-snapshot-1.
Parameter version: initial-calculation-1; SHA256 is computed from the canonical
PARAMETERS object in feature_contracts.py. These are initial calculation choices,
not profitability-tested strategy parameters.

## Scope and interface

snapshot(Window, generated_at_ms=None) returns 41 latest per-timeframe fields,
per-field readiness, metadata and a measurement SHA256. bundle(upbit_windows,
binance_windows=None, mapping=None, generated_at_ms=None) separates provider/TF
outputs and preserves mapping status. Neither API fetches, writes files nor computes
Trend Score, Chase Risk, B State, confirmation weights, trades or performance.
1d=Macro Context; 4h=Primary Trend/Structure; 1h=Pullback/Reacceleration/Extension.
No timeframe alters another. No 15m/30m. No raw storage or new workflow.

Validated input is a chronological tuple of completed Decimal Candle objects.
The adapter checks provider/instrument/TF identity, ordering, UTC boundaries,
OHLC positivity/logic, nonnegative volume/quote amount and input SHA256. Source
response URL/hash/received UTC are required. Hash mismatch or invalid evidence
fails closed. The snapshot generation clock must follow cutoff and response receipt.

## Exact formulas

Let t be the latest closed candle, O/H/L/C the prices, Q quote trade amount.
All windows below are inside the latest calendar-contiguous segment. Percentages
are multiplied by 100; ratios are dimensionless; acceleration is percentage points.

Trend (8): EMA20, EMA50; each EMA slope3=100*(E[t]/E[t-3]-1);
close-to-EMA20/50=100*(C[t]/E[t]-1); EMA spread=100*(E20[t]/E50[t]-1);
return5=100*(C[t]/C[t-5]-1).

Momentum (7): return1/3/10 with the same formula; acceleration=return3(t)-return3(t-3)
(nonoverlapping intervals); positive return streak counts consecutive C[i]>C[i-1];
signed body=(C-O)/(H-L); close location=(C-L)/(H-L). Equal close breaks streak.
Streak reaching segment start has streak_truncated=true; no unknown prior streak
is inferred. Flat H=L makes candle ratios null.

Volatility (2): TR[i]=max(H-L,abs(H-C[i-1]),abs(L-C[i-1])); segment first TR is
unavailable. ATR14 seed is the first 14 valid TR average (15 candles), followed by
(13*previous_ATR+TR)/14. ATR%=100*ATR/C. Zero ATR is a real zero; division by it is null.

Structure (9): strict confirmed high/low L2/R2; high classification HH/LH/EQ,
low classification HL/LL/EQ from the latest two same-kind confirmed pivots;
rolling high20=max(H[t-20:t]), rolling low20=min(L[t-20:t]); high distance percent
=100*(C/rolling_high-1); breakout=C>rolling_high, breakdown=C<rolling_low.
Ranges exclude current candle. Equality and wick-only crossings are not close breakouts.

Participation (5): mean and median of Q[t-20:t]; current Q divided by each;
mean(Q[t-2:t+1])/mean(Q[t-22:t-2]). The latter is recent3 vs preceding20 without overlap.
For even median, average sorted positions 9/10 (zero-based). Extreme spikes affect
mean more than median; both observations are retained without combining scores.

Extension (2): (C-E20)/ATR; (C[t]-C[t-3])/ATR. Trend is never discounted for extension.

Swing geometry (7): latest confirmed high p and latest preceding confirmed low l,
l<p and H[p]>L[l], in the same segment. Return=100*(H[p]/L[l]-1);
close retracement=(H[p]-C[t])/(H[p]-L[l]); peak-to-close=(H[p]-C[t])/ATR;
bars since high=t-p; post-peak Q ratio=mean(Q[p+1:t+1])/mean(Q[l:p+1]);
confirmed-low distance=(C[t]-latest_confirmed_low_price)/ATR;
last-breakout distance=(C[t]-latest_breakout_event_level)/ATR.
Last two distances can exist independently of a valid upward swing. Retracement
is not clipped: negative and >1 remain measurements. Post-peak interval is not
automatically called a healthy pullback. Anchor metadata records exact times/prices.

Reacceleration input (1): EMA20 upward recross is C[t-1]<=E20[t-1] and C[t]>E20[t].
No success or entry interpretation. Existing return, acceleration, quote ratio,
breakout and extension inputs support later interpretation without another score.

## Seeds, warm-up and on-demand limits

EMA n has SMA(first n segment closes) seed at index n-1; alpha=2/(n+1),
then alpha*C+(1-alpha)*previous. Minimum EMA20/50=20/50; slopes=23/53;
returns k=k+1; acceleration=7; ATR=15; rolling/participation baseline=21;
recent3 participation=23; recross=21. Pivots need 5 contiguous candles and actual
strict extrema; structure needs two confirmed same-kind pivots, not a 200-bar gate.
Event-derived measurements require their actual anchors, not an arbitrary readiness count.

Seed metadata includes segment/window start/end, seed boundary/method/update count,
EMA residual seed weight (1-alpha)^updates and history_truncated=true. The residual
is not an estimate of actual price error. Moving finite API windows change seeds.
No equality to infinite-history TradingView indicators or cross-run fixed-seed
history is promised. Determinism holds for identical input/cutoff/parameters;
future-suffix invariance assumes fixed seed/start, not a moving window start.

## Sparse, availability and timing

No synthetic candles. A calendar gap discards earlier segment state for latest
EMA/ATR/return/pivot/structure/rolling/participation/swing/streak calculations.
Enough latest contiguous history can produce partial READY under INCOMPLETE_COVERAGE.
Too few total API candles also permits feature-specific readiness. Missing latest
expected boundary makes all current fields STALE_INPUT/null. Missing candle is not
declared no-trade, prelisting or collector failure without evidence.

Null is not zero or false. Readiness includes READY, WARMUP, GAP_RESET,
MISSING_STRUCTURE, ZERO_DENOMINATOR, STALE_INPUT, INSUFFICIENT_DATA, API_ERROR,
INVALID_DATA, INVALID_INPUT, INPUT_HASH_MISMATCH, INVALID_EVIDENCE, NO_COUNTERPART.
required_lookback and available_history are shared metadata to avoid per-field
duplication. Structure/event fields additionally explain missing anchors through
MISSING_STRUCTURE. Source status is preserved even when some fields are READY.

Pivot p needs strict >/< both neighbors on each side; ties excluded.
pivot_open_ms differs from confirmation_boundary_ms (p+2 close). observed_at_ms
is conservatively the latest response receipt for the current input, never a fake
past availability. Both high/low may qualify on one outside candle; intrabar order
is not inferred and same-index pivots cannot form an ordered swing leg.
Historical breakout levels are calculated only from prior20 at their event candle,
then retained as references. Reconstructed event geometry is not past signal history.

## Decimal, hashes and provider contracts

All public calculation uses local Decimal precision34, ROUND_HALF_EVEN. No floats,
NaN/Infinity. Canonical output uses fixed decimal strings, strips trailing zeros,
normalizes negative zero and retains bool/int/null. JSON keys sorted, separators
fixed. Measurement hash includes features/readiness/input hash/cutoff/version but
excludes observed clocks; actual clocks/evidence remain in the envelope. Response
byte hash cannot reconstruct raw bytes that were intentionally not retained.

Upbit ratios stay KRW-based; Spot ratios stay USDT-based. No absolute conversion
or comparison. VERIFIED/UNVERIFIED can carry Spot features; UNVERIFIED remains
UNVERIFIED, never a strong confirmation label. Other mapping states have null
counterpart fields. Binance absence/error leaves Upbit computations unchanged.
Bundle validates provider/TF and mapped instrument identity; unknown mapping fails.

Returns3/5/10, EMA slopes, swing return and EMA/ATR extension are correlated inputs.
Later engines must not treat them as automatically independent evidence to add.
RSI/MACD/ADX/Bollinger/Stochastic, strong-bar thresholds, rolling50, lead-lag statistics
and intrabar execution measures are deferred. A scores, synthetic raw and semantic
trade classifications are excluded.

## Verification and Pine research

Hand fixtures, warm-up, zero cases, pivot confirmation/ties/classification, swing
order, reference exclusion, sparse restart/staleness, canonical replay, suffix
invariance, availability, identity/provider/TF independence and raw immutability
tests accompany implementation. Read-only actual altcoin smoke includes sparse
inputs; readiness nulls are expected evidence, not forced PASS values.

Future Pine research must match source, finite seed, boundaries, gap/tie rules and
quote-amount input. Floating point comparisons may need reported tolerance, but
Python Decimal calculations remain authoritative for this implementation contract.
Pine research cannot recover historical repository receipt evidence. No Pine is added.

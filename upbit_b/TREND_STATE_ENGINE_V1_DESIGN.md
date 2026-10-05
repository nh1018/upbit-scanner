# B Trend/State Engine V1 — approved implementation contract

Algorithm `upbit-b-trend-state-1`; schema `upbit-b-trend-state-output-1`;
parameter `initial-hypothesis-1`. The actual canonical Decimal parameter object
and its SHA256 are in `trend_contracts.py`. Every weight, saturation, threshold,
cap and boundary is a V1 INITIAL HYPOTHESIS, never a profitability claim.
Feature contract is unchanged: `upbit-b-features-1`, `initial-calculation-1`,
SHA256 `7dc9a941df84e364f66412ce326ef8d917335d71c3c28edf2c6dd2e033001227`.

## Architecture and interfaces

Market Data → independent Feature 1d/4h/1h → pure `trend_state.evaluate(bundle,
instrument, observation_time_ms, price_observation=None)` → memory-only output.
Trend, State, Chase, Binance confirmation and Data Confidence are separate fields.
No fetching in the engine, persistence, Entry, order, exit, ML, history, performance,
workflow, Pine or changes to A/BTC. `trend_dry_run` is an explicit public-API audit
command only; it prints to stdout and never writes raw or production output.

## Evidence and scores

V=ATR% must be positive. clip(x)=min(1,max(-1,x)). Every required input must be READY.

A=.30 clip(EMA20-to-EMA50%/V) + .25 clip(close-to-EMA50%/V)
 + .30 clip(EMA20 slope3%/(.50V)) + .15 clip(EMA50 slope3%/(.25V)).
S=.50 high class + .50 low class, HH/HL=1, EQ=0, LH/LL=-1.
Breakout raises S to max(S,.50); breakdown lowers S to min(S,-.75).
Both breakout and breakdown must be READY; simultaneous true is invalid.
M=clip(return3%/(2V)). No extra return1/5/10 bonus.
P: q=quote_recent3_vs_prior20, r=return3%. r>0: clip((q-1)/.50);
r<0: -clamp((q-1)/.50,0,1); r=0:0. Drying on a decline is not bullish.

Group weights A=.40, S=.35, M=.15, P=.10. Gtf is weighted average over available
groups only. 4h requires A/S/M; 1d/1h require A/M. P null is excluded, not zero.
TF weights 1d=.15, 4h=.65, 1h=.20. G is eligible TF weighted mean.
Trend=100 max(0,G); signed G and all groups remain visible. Score is not probability.

Gate: primary 4h core READY, eligible TF weight >=.80, evidence coverage >=.65,
fresh valid sources and matching Feature versions/hash. .80 (1d+4h) and .85
(4h+1h) pass; .65 (4h alone) fails. No 4h always fails. Null is distinct from zero.

Coverage=sum TF_weight*sum(group_weight*I(READY)), never renormalized.
HIGH>=.90, MEDIUM>=.65, otherwise LOW; missing primary core or contract rejection
is LOW. Feature count is diagnostic only. Binance coverage is independent.

## Damage and states

Damage=(4h breakdown AND (LL OR confirmed-low distance<0)) OR
(4h LH AND LL AND close-to-EMA50%<0). Cap=49, eligible=false; preserve uncapped
score. Missing conditions are not true. Chase, Spot conflict and 1h correction
cannot cause damage. This is candidate suitability, never an order veto.

Priority: INSUFFICIENT_EVIDENCE → TREND_WEAKENING → REACCELERATION →
PULLBACK_WATCH → TREND_CONTINUATION → TREND_BUILDING → NO_UPTREND → MIXED.
Weakening: damage OR A4<=0 AND (S4<0 OR M4<0) AND upward background.
Background: 4h EMA spread>0 OR A1d>=.50.
Continuation: score>=65, A4>=.55, S4>=.25, M4>=0, no damage;
if P4 available it must be >=-.50.
Building: score>=45, A4>=.35, S4>=-.25, positive 4h EMA spread OR breakout.
No-uptrend: A4<.35 if no earlier state. Otherwise mixed.
Eligible means passed data gate, no damage and Building/Continuation/Pullback/Recovery.
Low Chase alone never grants eligibility. OVEREXTENDED is not a state.

Healthy primary for context: A4>=.55, S4>=0, breakdown=false, confirmed-low distance>=0,
no damage. Pullback also requires 1h retracement [.10,.60], peak distance [.50,3] ATR,
age [3,20], confirmed-low distance>=0, return3<0 and breakdown=false. Quote ratio
post-peak<=1 adds PARTICIPATION_CONTRACTION. Missing swing anchors yield context
unavailable, not fabricated pullback. Recovery requires the same healthy primary,
1h retracement (0,.60], age [3,20], intact low, no breakdown, return1>0,
acceleration3>0, EMA upward recross OR breakout, median quote ratio>=1.20.
Recovery basis is CURRENT_GEOMETRY_AND_RECOVERY_EVIDENCE, with
prior_state_transition_verified=false. No past state transition is inferred.

## Chase and Binance

Chase location=max(close-to-EMA20 ATR, optional last-breakout distance).
Breakout reference is usable only if READY and age<=20 own-TF bars; age is
(latest candle open-event candle open)/duration. Velocity=price_change3_atr.
ChaseTF=100 max(clamp(location/4,0,1),clamp(velocity/4,0,1)). 1h=.80,4h=.20;
renormalize available Chase weights, but missing required 1h makes overall null.
LOW<35, MEDIUM [35,70), HIGH>=70. Downward extension clamps to zero.
Separate coverage preserves missing Chase evidence. No score/state adjustment.

Spot required core is 4h A/M. Missing counterpart/core → UNAVAILABLE.
UNVERIFIED with core → UNVERIFIED regardless diagnostic direction.
VERIFIED additionally requires explicit mapping registry/evidence reference.
CONFLICTING first: Spot damage OR A4<=-.35 AND (S4<0 OR M4<=-.25).
CONFIRMED: Upbit A4>=.35, no Upbit damage; Spot G4>=.65,A4>=.55,S4>=.25;
Spot1h A/M both READY/nonnegative. SUPPORTIVE: Spot A4>=.35,M4>=0.
Otherwise NEUTRAL. All comparisons use ratios and identical TF UTC boundaries;
no absolute KRW/USDT price comparison, lead claim or Upbit score adjustment.

## Source, null, timing and output

Recheck Feature version, parameter hash, measurement hash, provider/instrument/TF,
source cutoff, current completed boundary, source evidence and receipt/generation
clocks. Observation must follow generation and receipt. Mixed cutoffs are rejected.
Unavailable/stale optional TF is excluded; a mismatching Upbit version/hash or
invalid feature contract blocks the aggregate. Spot rejection cannot block Upbit.
Source AVAILABLE/INSUFFICIENT_DATA/INCOMPLETE_COVERAGE can carry valid READY groups;
old gaps are not penalized after a sufficiently long latest contiguous segment.
Event context needs actual confirmed/observed anchor references. Future pivots,
future generation and late retrieval cannot become available at a past observation.
No timestamp is reconstructed into prospective history. This engine evaluates
only the supplied current available bundle; it does not replay old market windows.

Canonical output: fixed Decimal strings (precision34 HALF_EVEN), bool/int/null;
no floats/NaN. Observation ID hashes the complete envelope excluding ID itself.
Output contains actual observation/cutoff clocks and optional caller-supplied
ticker price (provider, instrument, price, source_time_ms, received_at_ms,
source_reference). Price never comes from reversing an EMA or feature.
Vendor ticker time and local receipt are separate clocks. If vendor time is ahead
of receipt, both original values are retained with TICKER_SOURCE_CLOCK_AFTER_LOCAL_RECEIPT;
no time correction is invented. Price availability uses actual local receipt, not
vendor timestamp. This optional ticker does not enter any Trend/State calculation.
Trend stores score/signed score/before gate/TFs/groups/masks/weight/damage/eligible.
State stores primary/tags/reason codes/predicate results/recovery metadata.
Chase stores score/category/TFs/coverage/reasons. Spot stores mapping/confirmation/
diagnostic groups/coverage/reasons. Data stores coverage/category/source/readiness.
Versions preserve Engine and Feature identities. Source preserves Feature hashes,
readiness, actual clocks, evidence and anchor metadata for subsequent audit.

## Validation and limits

Tests cover the 12 approved situations, formula fixtures, .80/.85/.65 gates,
.65 coverage, null/zero, cap49, state priority, independent Chase/Spot, UNVERIFIED,
availability/pivot/hash rejection, sparse continuation, replay and immutability.
Five real altcoins and one full KRW-market pass are public-API, read-only checks.
The full pass records distributions, source errors, sparse examples and technical
assertions in stdout only. No tuning to a preferred distribution. All-market API
fetching is sequential with existing pacing/retries; cutoff stays fixed while
receipt clocks differ. Scores are not simultaneous executable quotes.
Finite-window Feature seeds, missing strict pivots, sparse history, unavailable
counterparts and unverified identity limit coverage. Prospective performance is
needed before revising initial parameters or adding Entry/Signal History.

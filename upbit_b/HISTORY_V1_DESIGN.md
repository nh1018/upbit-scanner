# B Prospective Observation / Signal History V1

Algorithm: upbit-b-prospective-history-1. Schema: upbit-b-history-journal-1.
Operational policy: initial-operations-1, canonical object/hash in history_contracts.py.
Engine remains initial-hypothesis-1, SHA256
17515b811ab3c68a2ba9590c7ce9a3c43c587106570f2bfc0616cc5857a95d53.
Feature remains initial-calculation-1, SHA256
7dc9a941df84e364f66412ce326ef8d917335d71c3c28edf2c6dd2e033001227.
No strategy calculations or existing collector/workflow/data are modified.

## Scope and architecture

Public APIs in memory → existing Market Data/Feature/Trend → history selection →
validated complete cycle JSONL → optional activated write-once Git publication.
No raw OHLCV, full Feature ledger, future outcomes, Entry, PnL, database or cloud store.
One canonical journal includes its manifest, observations and embedded events.
Explicit --dry-run never creates canonical files or commits. --record additionally
requires UPBIT_B_HISTORY_ACTIVATED=true; workflow activation is initially false.

## Candidate and recording policy

TRUE requires Engine eligible and BUILDING/CONTINUATION/PULLBACK/REACCELERATION.
FALSE means a normally analyzed non-candidate. API failure or unavailable core
evidence is UNKNOWN, not a candidate exit. High Chase and absent Binance do not
exclude a candidate. Optional Binance errors do not turn valid Upbit analysis bearish.

First cohort cycle stores every universe member as compact BASELINE_STATE. Candidate
and sampled control baselines include detailed evidence. Following cycles store
meaningful state/candidate/Chase/damage/data-quality/Binance changes, failures,
candidate heartbeat on UTC hours divisible by four and controls every hour.
Unchanged candidates outside heartbeat hours produce no repeated ENTER signal.
No quantitative score-drift event is emitted. State-only records preserve labels,
not a claim that all numerical features stayed constant.

Controls: normally analyzed FALSE population, ordered by SHA256(policy hash, cycle
ID, instrument), first min(12,N). Population, K, selected instruments and K/N are
recorded. This is reproducible pseudo-random control selection, not future-outcome
selection. Histograms include the full attempted universe. Missing market membership
cannot silently be treated as FALSE. Universe baseline/deltas preserve historical
membership; removed markets are recorded without manufacturing candidate exits.

## Observation/Event and transitions

Record kinds: BASELINE_STATE, STATE_DELTA, FAILURE, CONTROL, HEARTBEAT.
Each includes logical ID, instrument, compact state/candidate/quality summary,
Engine ID/time where available, prior state/effective-cycle reference and events.
DETAIL/COMPACT include score/signed/before-gate scores, TF/group scores, masks,
coverage, reasons, quality clocks/hashes and price anchors. DETAIL additionally
includes only the approved evidence subset and references. Heartbeats reference the
previous detail while recording fresh current values; old values are not reused.

Events: ENTER_BUILDING/CONTINUATION/PULLBACK_WATCH/REACCELERATION/WEAKENING,
EXIT_B_CANDIDATE, CHASE_RISK_CHANGE, PRIMARY_DAMAGE/CLEARED, DATA_LOST/RECOVERED,
DATA_QUALITY_CHANGE, BINANCE_CONTEXT_CHANGE, BASELINE_STATE,
STATE_REOBSERVED_AFTER_GAP. Quality change covers group masks, TF readiness and
confidence category independently of candidate status.

Verified transition requires same cohort, exact adjacent one-hour cycles and valid
candidate classification at both endpoints. The journal loader also validates the
previous cycle chain. Failure, insufficient evidence, missed hour or new cohort
resets continuity. Recovered state is re-observed, never a reconstructed ENTER.
Engine prior_state_transition_verified remains false; History verification is separate.
Cold start/new listing records a baseline even if already CONTINUATION.

## Evidence subset and limitations

Upbit each TF: ATR%, EMA spread, close-to-EMA50%, EMA slopes3, high/low structure,
breakout/breakdown20, return3%, quote recent3 ratio. 4h adds confirmed-low distance;
1h adds approved swing/recovery geometry, return1/acceleration/recross/median quote.
1h/4h Chase adds EMA ATR distance, velocity and only a valid <=20-bar breakout
reference. Spot excludes 1d and retains minimum 4h and 1h confirmation evidence.
Quality preserves required readiness, actual contiguous segment, source status,
receipt/generation clocks, measurement/input hashes and response URL/hash references.
Raw windows are released after Feature/anchor extraction. Only one completed close
is retained as an explicit diagnostic price anchor, not an OHLCV archive.

Subset/hash evidence supports formula/reason audits; it cannot reconstruct missing
full Feature or original HTTP bytes. Re-fetched future API data must be labeled
later finalized-reference evaluation. No exact as-observed future path is promised.

## Clocks, anchors and cohort

Cycle cutoff is current UTC hour H. Collect full universe Features at fixed H,
then get fresh bulk tickers and evaluate with actual current clocks. Distinguish
scan_started, candle close, response received, feature generated, engine observed,
ticker received, completed/prepared and repository observed. Validate original
Engine envelope hash and matching Feature bundle hashes. Never backdate observation.
Vendor ticker clock skew remains preserved under the existing Engine contract.

OBSERVED_TICKER_DIAGNOSTIC uses actual ticker price/time/evidence;
COMPLETED_1H_CLOSE_DIAGNOSTIC uses the last exact closed 1h price/reference;
NEXT_1H_OPEN_PROXY stores future target boundary and price=null. None is entry.
Future exact ticker-horizon price cannot be recreated from coarse candle history.
Intrabar uncertainty and sparse future paths must remain PARTIAL in later evaluation.

Publication acknowledgement is printed with actual successful-push time. It is not
written retroactively into its own immutable observation. Next cycle may append a
receipt for the previous journal observed in checkout; this is an availability upper
bound, not the exact first commit time. Missing acknowledgement remains unavailable.
Git commit timestamp alone is never first repository availability evidence.

Cohort hashes Engine/Feature algorithms/schemas/parameters, Market schema,
History schema/policy and mapping registry version. New contracts create baseline.
Current runner has no identity approval registry: unapproved-none. No mapping is
promoted. Manifest preserves the full small operational policy object/hash so older
policy contracts remain identifiable. Unknown future schemas fail closed.

## IDs, storage and publication

cycle_id=hash(strategy, policy hash, cohort ID, hour boundary).
observation_id=hash(cycle ID,instrument,record kind).
event_id=hash(cohort,instrument,cycle,type,prior state reference,current observation).
Engine ID remains separate. Payload hash covers canonical sorted record lines.
JSONL uses existing exact Decimal strings, precision34 HALF_EVEN, no floats/NaN.

Path: output_upbit_b/v1/history/YYYY-MM-DD/HH.jsonl, one write-once cycle file.
Manifest first, instrument-sorted records following; newline terminated, UTF-8.
Same bytes→NOOP; different bytes→conflict. Entire payload is validated before a
same-directory temp is fsynced and atomically installed without overwriting.
Windows uses no-clobber rename; POSIX uses atomic no-clobber hard-link installation.
Partial temp files are not canonical and are cleaned. No daily file rewrites.

Manifest includes universe membership/hash, attempt/success/insufficient/failure/
unattempted counts, failures, complete/partial flag, histograms, controls, versions,
clocks, prior cycle, record/event counts and payload SHA256. Insufficient is not API
failure. Partial successful market results remain usable. Failed markets cannot
produce candidate exits. Hard universe/process failure produces no sealed journal.

## Operations and activation

Full hourly deep scan; no A filter/hard prefilter. Nominal minute07, start grace20m,
publish deadline45m, timeout25m. These are operational choices, not strategy weights.
Delayed old cycles are not reconstructed. A later valid cycle records missing-hour
count and re-observation. Preview may run outside grace but is explicitly nonpublishable.

Workflow: schedule + manual dispatch only; one gate outputs active=false; record
job consumes that output and is skipped. Runner independently checks activation.
B-only concurrency, cancel-in-progress=false, contents:write only in recording job.
No push/self/upstream triggers or A/BTC dependencies. Default branch schedule can
delay/drop; no guaranteed real-time execution is claimed.

Activated publication stages only one new canonical journal, commits once and uses
at most three normal pushes. Disjoint upstream commits may rebase; equal remote
cycle bytes→NOOP, unequal→FAIL. Frozen payload is reused, never re-collected after
push failure. An isolated locally committed but unpublished cycle can retry inside
deadline. Unrelated local/staged modifications fail closed. No force/reset overwrite.
Raw collector availability is independent of B runner success.

## Tests, dry-run and size forecasts

Tests cover 12 approved fixtures, TRUE/FALSE/UNKNOWN, sampling, baseline/heartbeat,
no repeated ENTER, clock/contract/hash rejection, gaps/cohorts/membership, partial
failure, canonical bytes/counts, no raw/full Feature/future outcomes, temp cleanup,
atomic no-overwrite, replay and mock Git publication conflict/retry paths.
Existing Market/Feature/Engine/A/BTC/Worker/Pine remain unchanged.

Full-market --dry-run records actual universe/candidate/control/unknown counts,
runtime/API counts, cold JSONL bytes and detail/state record averages in memory.
Compact control/heartbeat byte forecasts project actual current values into the
normal record shape; they are size scenarios, not fabricated future observations.
Forecast assumptions: observed candidate count, six candidate heartbeats/day,
12 controls/hour, 60 detailed events/day, 24 state changes/hour, 24 manifests/day.
Future event rate is unavailable until prospective operation. Git pack overhead
and initial baseline are separate. Material increase over design's 474MB/year is
reported without tuning/deleting records. Production activation remains OFF.

## Actual pre-activation smoke (2026-10-05)

Memory-only preview started 13:38:32.081 UTC and completed 13:44:21.081 UTC,
source cutoff 13:00 UTC, elapsed including validation/report 349.800 seconds.
It started outside the production start grace and was explicitly NONPUBLISHABLE;
no clocks were backdated. Code artifact SHA256:
8092bcc8b7878708c9dbf4287be57d7282f160cb82558cf92fbaa61032f323d5.
The working checkout contained the six new implementation files, so clean_checkout
was false; code_revision identifies the base revision, not a false committed build.

Universe/attempted/succeeded 291; API failures/unattempted 0; candidates 55;
insufficient/UNKNOWN 9; control population 227, selected 12 (12/227).
Requests: Upbit 914, Binance public Spot 593; transport errors 0.
Baseline observations/events 291/291; detail 67 (55 candidates + 12 controls),
state-only 224. Every event was a non-verified BASELINE_STATE, not an entry.
Production history files/raw files created: 0/0.

Cold payload 1,321,302 bytes; manifest 14,347 bytes. Average candidate detail
15,679.05 bytes, all detail 15,637.97 bytes; projected compact heartbeat 5,670.78
bytes, control 5,645 bytes. Cold state-only average 1,157.19 bytes is a conservative
forecast proxy including baseline event overhead, not a measured normal delta.
With 55 candidates, six heartbeats/day, 12 controls/hour, 60 details/day and 24
state changes/hour: normal-cycle estimate 226,927.78 bytes; daily 5,446,266.78;
30 days 163,388,003.41; 180 days 980,328,020.47; 365 days 1,987,887,374.84 bytes.
These are scenario estimates, excluding Git overhead and initial baseline.

This is about 4.19 times the initial 474MB/year estimate. Explicit nested evidence,
source URL/hash/clock metadata, readiness and diagnostic references make details
about 15.6KB rather than the assumed 3KB, controls/heartbeats about 5.6KB rather
than 1.5KB, and manifests 14.3KB rather than 2KB. No record/sampling policy was
reduced to hide the increase. DRY-RUN technical checks passed; storage is WARNING.
Actual prospective transition frequency, hosted Actions runtime and Git publication
are not established by a single baseline smoke. Production activation is still OFF.

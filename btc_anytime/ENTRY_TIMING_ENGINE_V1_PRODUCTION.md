# Entry Timing Engine V1 — prospective activation

This phase adds only production wiring. `entry/engine.py`, `parameters.json` and all
upstream calculations/contracts remain at the approved implementation commit03a445c.
Version1.0.0, parameter entry-initial-hypothesis-1, SHA256
da3b45ac1b3aa2ae0e40e75d570bcab1f9da7ba9296dcb7cae088b82cdd2cff8.

## Activation and clock

`python -B -m btc_anytime.entry.production --repo . --record` first writes only
`output_entry/btc_anytime/v1/activation.json`. It records actual UTC start, latest
completed live15m baseline, revision, Entry/Feature/Direction versions and parameter
hashes, plus explicit no-reconstruction policy. The baseline is never evaluated.
Without --record, initialization/evaluation is a read-only preview.

Subsequent runs consume only the latest new completed production15m raw with existing
validated observation evidence. Wall clock alone does not create a boundary. Missing
evidence returns AWAITING_TRIGGER_EVIDENCE without fabricating an observation.
Only records after baseline and a close later than activation can be published.
Skipped older boundaries are listed, not reconstructed. An active setup loses
continuity under the existing evaluator's missed-boundary policy.

Stored prospective Direction decisions are hash-checked and tied to their original
Direction activation. The latest decision actually present in the checkout and whose
decision/trigger are not in the future is selected; this consumer's actual read time
is recorded. Git committer time is never treated as exact publication availability.
The existing max-age30m/trigger-lag1 policy is applied by unchanged Entry code.
Once a boundary is consumed, a later Direction cannot cause a second evaluation.
Missing Direction consumes an operational-only SKIPPED_NO_DIRECTION boundary.

Feature formulas and OI registry are reused unchanged. Data is filtered by actual
observation evidence before generation; generation is performed now and recorded
as real evidence, never backdated. Only the used ten15m/two1H compact feature/raw
records are kept; full upstream Direction/snapshots are not duplicated.

## Publication and identity

Production publication_id = SHA256 of production schema, activation ID, symbol,
timeframe, candle open boundary, Entry algorithm and parameter hash.
Generation/observation/evaluation timestamps do NOT enter this production identity.
Existing evaluator entry_evaluation_id is retained only as an engine audit reference;
the unchanged evaluator's identity is not the production publication key.

`records/<publication_id>.json` stores compact evaluator result/lifecycle and observed
clocks. `inputs/<publication_id>.json` stores its exact compact manifest/evidence.
`boundaries/<publication_id>.json` is the immutable publication marker, written last.
`candidates/<setup_id>.json` enforces one confirmed publication per terminal setup and
records confirmation boundary. Same-boundary replay reads the original record and
manifest before selecting a new Direction or regenerating Feature inputs.
Integrity hashes cover all bytes including audit metadata; integrity protection is
separate from timestamp-free publication identity. Different content cannot overwrite
the occupied key. Input/record integrity failures fail closed.

The previous evaluation/state is referenced by previous_publication_id and passed
unchanged to the evaluator. Frozen A0/reference/origin/setup identity persist. A
same-side new authorizing decision updates only the existing evaluator's authorized
fields. Candidate/NO_ENTRY/WAIT contracts and all numeric thresholds remain unchanged.

Independent replay uses exactly saved E, source values, Feature values, evidence,
Direction ID and previous state. Entire evaluator output must match, not merely the
classification. No current raw, current Direction or current generation time enters
that replay. The already recorded observed clocks are frozen historical evidence.

## Workflow

`btc-entry-timing.yml` independently follows successful `BTC Direction Signal History V1`
workflow_run on main; it also supports manual dispatch and cron14,29,44,59 minutes.
Schedule/dispatch do not depend on workflow_run-only fields. Concurrency has a dedicated
Entry group and cancel-in-progress=false. A local exclusive writer lock covers all
publication. Entry failure cannot stop the independent upstream workflows.

Checkout main; full history; contents:write; only NEW JSON under Entry namespace may
be staged. Normal push followed by fetch/rebase retry handles disjoint ingestion commits.
A same-key conflict fails; there is no force push, deletion or modification staging.
An interrupted publication can complete its existing immutable record's index; corrupt
records/inputs or abandoned locks require investigation, never silent replacement.

## Validation distinction

Activation PASS requires a real post-baseline observation, normal business state,
stored raw/Feature/Direction/evidence references, deterministic replay equality,
repeat run REPLAY_NOOP with no new artifacts/commit, no duplicates/overwrites and
unchanged protected contracts. NEUTRAL/EVALUATED/NO_ENTRY is normal production.
Actual ARMED/CONFIRMED paths remain NOT_YET_OBSERVED until naturally seen in production;
fixtures do not substitute for those observations. No threshold tuning to manufacture
a candidate. Schedule evidence is separately reported; cron syntax is not proof of a run.

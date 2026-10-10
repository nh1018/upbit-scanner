# BTC Analysis Consumer Contract V1

Purpose: make the BTC analysis chat consume one stable Production artifact instead of
searching GitHub for the newest Direction/Entry file.

## Canonical input

Read this exact path first:

`output_btc_anytime/latest_analysis.json`

Do not use GitHub code search to discover a newer Direction/Entry record. The snapshot
already contains immutable source references for the Direction and Entry records it used.

## Mandatory consumer-time gate

The snapshot's generated freshness is not enough. Recompute freshness at analysis time
by running `python -B analysis_health.py --repo .` without `--write`.
A stored `CURRENT` status is only valid at its `generated_at_utc`.
For cached health use `analysis_health.at_consumer_time(snapshot, now)`;
CURRENT entries without `valid_until_utc` fail closed. This conservative expiry
check never upgrades blocked inputs and cannot discover later upstream changes:
rebuild from the current source artifacts whenever available.
The Health snapshot checks the existing BTC decision/Entry 30-minute age policy,
TF continuity and alignment, alongside the unchanged 45-minute snapshot limit.
A complete B scan means manifest `completeness == "COMPLETE"`; insufficient-data
markets remain explicitly insufficient, and partial cycles cannot authorize a
complete current-market analysis.

1. If BTC health is CURRENT, use the snapshot Direction + Entry as Production evidence.
2. If health is STALE/DEGRADED/MISSING/INVALID, explicitly label Production evidence
   unavailable/stale. Do not describe the embedded old Direction as current.
3. Web/current-price evidence may supplement a CURRENT Production snapshot. It must not
   be presented as an engine output.
4. If Production is stale, a web-based market interpretation may be reported only as a
   separate temporary interpretation, with the Production failure stated first.

## Required report fields

- snapshot generated_at_utc
- latest completed market boundaries / freshness
- Production Direction: class, score, confidence, regime, decision_time
- Production Entry: entry_state, setup_side/type, chase/conflict, evaluation_time
- current market overlay if used
- final ChatGPT judgment, kept distinct from engine outputs

## Prohibited behavior

- searching commit messages as the primary read path
- reconstructing a past Production decision from current data
- calling a web interpretation an official Direction Engine record
- silently using stale embedded Direction/Entry
- treating reference price as an execution fill

This contract changes no Direction or Entry parameters.

## Historical gaps and active input evidence

Health must not require all-history missing_slots/abnormal_intervals to be zero.
Feature V1 deliberately preserves price indicator state across absent slots; OI
strict segment continuity is unchanged. Stored Feature readiness, Direction core
components, Entry execution status and its consumed input manifest govern current
usability. Health verifies snapshot/source hashes and existing input validators,
without recalculating Feature, Direction or Entry. Missing evidence fails closed.
Directional Entry path gaps block use; validated NEUTRAL/NO_ENTRY and authorization
veto outputs do not claim a directional entry path was evaluated.

Historical counters, snapshot warnings and provenance remain exposed even when
current inputs are usable. CURRENT is therefore not a claim of gap-free raw history.
all_timeframes_fresh_at_generation contains only stored snapshot freshness flags;
all_timeframes_fresh_at_consumer_time and timeframes_valid_until_utc separately
describe request-time expiry. Cached revalidation preserves generation flags.

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
(or read `output_system_health/latest.json` when it is current).

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

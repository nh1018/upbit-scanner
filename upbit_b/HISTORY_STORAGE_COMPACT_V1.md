# Upbit B History Storage Compact V1

Production activation remains **false**. This is a storage-only extension. A/BTC,
Market Data, Feature/Trend formulas/parameters, full-market scan, sampling maximum
12/hour, 4h heartbeat, UNKNOWN/recovery/transition semantics are unchanged.

## Lossless JSONL representation

Physical schema: `upbit-b-history-compact-1`; codec:
`named-record-layout-and-value-dictionaries-1`. The first JSONL manifest contains
cycle identity, cutoff, cohort, mode, scan counts, field layouts, strings, shared
values and the encoded logical manifest. Every subsequent line retains named
`instrument`, `record_kind`, `candidate`, `state`, plus encoded `data`. These named
fields are validated against decoded contents, not independent unaudited labels.

Cycle-local encoding of JSON trees:

- `[layout_index, value1, ...]`: object; layout lists its sorted original field names.
- `[-1, item1, ...]`: original list, including empty/literal numeric lists.
- `[-2, string_index]`: exact original repeated string.
- `[-3, shared_index]`: exact repeated object/list. Shared values refer backward only.
- Other primitive values retain their original type and value.

Dictionary entries are chosen deterministically from exact equality/repetition,
never from similar values. Distinct instrument clocks, source hashes, pivot refs,
quality flags, numbers, nulls and reasons remain distinct. Decimal strings retain
their full original precision. Repeated metadata/readiness/scores/anchors/references
share values within ONE cycle only. No external/cross-cycle dictionary dependency.
Layouts remove repeated field-name bytes; strings/shared trees remove repeated
values. No field is discarded, renamed in the logical view or recomputed.

Unpacking must reproduce original canonical logical JSONL bytes exactly, verified
by SHA256. Encoded lines/header have separate hashes; unsupported schema, duplicate
keys, out-of-range/cyclic refs, altered views/hash/counts/arity fail closed. All
original History validations also run on expanded logical records.

## Reading and audit/performance compatibility

Use `upbit_b.history.validate_cycle(bytes)` for legacy OR Compact: it returns the
same named manifest/record dictionaries. For an individual GitHub file, instrument/
record/state/candidate are directly visible. Full named details can be viewed with:

```
python -B -m upbit_b.history_compact path/to/HH.jsonl
```

This is a read-only stdout viewer, not a migration/writer. Candidate DETAIL,
HEARTBEAT, CONTROL, STATE_DELTA, FAILURE and UNKNOWN keep ALL original logical
fields, source clocks, actual anchors, evidence subset, IDs, reasons, quality,
prior refs and events. Future outcome and raw/full Feature archives remain absent.
Evaluation receives exactly the former logical representation, so performance
audit capability is neither expanded nor weakened. Original evidence limitations
(no complete as-observed HTTP/Feature archive or realizable ticker entry) persist.

JSONL stays text; gzip is measured but not adopted. Binary compression would impede
GitHub line inspection/text diff and require decompression before normal readers.
The tradeoff here is a small single-file decoder rather than native fully named
nested JSON. An old external reader must update to validate_cycle/the viewer.

## Identity, legacy continuity and publication

Compact adds `history_storage_schema` and `history_storage_hash` to the version
contract used by cohort identity. Operational policy hash and Engine/Feature hashes
remain unchanged. The new storage cohort's first observation is BASELINE_STATE,
transition_verified=false. No old-cohort ENTER is manufactured.

The mixed journal loader validates old bytes in place and new bytes through the
codec. Old logical files never get rewritten or deleted. Same canonical cycle path
is write-once; old/new same-hour collision remains conflict/replay, never migration.
Packing an old payload can measure losslessness in memory, but publication rejects
Compact without its own storage cohort contract. Preview remains nonpublishable.
--compact is explicit; default History CLI behavior remains legacy compatible.
The gated workflow recording command selects --compact, with active=false unchanged.
No workflow is dispatched and no production history file is created in this task.
Frozen compact bytes use existing atomic no-clobber writer and normal-push/replay
mechanism; original deadlines, retry counts and no-force-push policy are unchanged.

## Measurement and growth risk

Full-market dry-run compares old logical bytes and Compact bytes from identical
current observations. It reports cold baseline per-type line bytes separately from
shared dictionary/header cost; line savings alone are NOT total storage savings.

Normal-cycle sizes are nonpublishable projections of actual observations, not
historical reconstruction, new signals or measured future event frequencies.
Four hourly phases are modeled with 4h candidate heartbeat, 12 controls, 24 state
changes/hour and alternating 2/3 detailed observations/hour (60/day if sufficient
candidates exist). A detail overrides the same instrument's heartbeat. State/detail
rows retain actual baseline event overhead as a size proxy, not an assertion of
future event type/count. Multiple events/failures/UNKNOWN can increase real volume.
All metadata, dictionaries and headers are counted for EVERY modeled cycle.
Original 1.99GB estimate is also re-evaluated with the original estimator, so changes
in candidate count/model overhead are separated from codec savings.

Report 30/180/365-day estimates as assumptions, not year-long measurements.
Cold baseline is additional; no assumed repack/pruning of existing history.
Gzip size comparison and isolated temporary Git loose-blob sizes measure the same
single cold payload; temporary repositories are removed. Git annual pack/delta/tree
growth is unknown: 8760 hourly cycle files/commits/year, trees, retained history,
clones and Actions checkout cost remain. JSONL target <=1GB is not a guarantee of
remote disk/quota/clone size. No Git history rewrite, external storage or retention
policy change is introduced.

## Actual 2026-10-06 full-market measurement

Read-only scan 10:17:43.423–10:23:18.572 UTC; total collection/codec/report runtime
338.478 seconds. 291 markets/attempted/succeeded, candidates 39, controls 12,
UNKNOWN/insufficient 9, API failures/unattempted 0; requests Upbit 914/Binance 593,
transport errors 0. Baseline records/events 291/291, no verified entry. All inputs
and outputs stayed in memory; production history/raw files created 0. B activation
remains false; no workflow dispatch. Exact logical-byte roundtrip PASS.

An earlier full collection completed but report serialization failed on a float
2.5 scenario-rate constant. It wrote no production files. That report-only bug was
fixed using Decimal and covered by a 3+-candidate regression; the measurement here
comes from the subsequent complete successful run, not a fabricated repaired report.

Cold-start old logical JSONL 1,092,517 bytes; Compact 441,027 bytes: 59.632% saving.
Compact shared header 97,630 bytes; layouts 47, shared values 262, strings 416.

| Cold baseline type | Count | Old record bytes | Compact record-line bytes |
|---|---:|---:|---:|
| Candidate DETAIL | 39 | 616,955 | 185,863 |
| CONTROL (first-cycle DETAIL) | 12 | 182,898 | 53,928 |
| BASELINE_STATE state-only | 231 | 267,446 | 98,642 |
| UNKNOWN | 9 | 10,711 | 4,964 |

These line costs exclude the shared header, which is included in total savings.
No real API FAILURE occurred; its preservation is tested using failure fixtures,
not asserted to be a measured market-failure sample.

Normal size-only examples using the SAME observed values:

| Type | Example count | Old record bytes | Compact record-line bytes |
|---|---:|---:|---:|
| Candidate DETAIL | 2 | 33,137 | 10,793 |
| HEARTBEAT | 37 | 209,224 | 75,934 |
| CONTROL | 12 | 67,565 | 25,294 |
| STATE_DELTA | 24 | 30,448 | 11,046 |

This heartbeat-phase example includes header 28,140 bytes: total 348,683 old versus
151,207 Compact. Non-heartbeat-phase example: 139,459 old versus 68,176 Compact,
including 20,720-byte header. Eight phase/detail-rate cases give mean normal cycle
199,312.625 old versus 91,826.75 Compact: **53.928% same-scenario codec saving**.

| Period | Compact scenario bytes |
|---|---:|
| Day | 2,203,842 |
| 30 days | 66,115,260 |
| 180 days | 396,691,560 |
| 365 days | 804,402,330 |

Primary <=1GB/year target met under these assumptions; preferred 500–800MB range
is narrowly exceeded (decimal MB). Add the first baseline separately (~0.441MB).
Same-scenario legacy annual estimate is 1,745,978,595 bytes; original estimator on
current observations is 1,789,763,750 bytes. Prior 1.99GB used 55 candidates rather
than current 39 plus a different baseline-event/heartbeat-overlap approximation.
Do NOT attribute that whole difference to compression. More candidates, multiple
events, larger manifests, failures and different URLs/evidence can exceed this
forecast. Future event rate remains unavailable until prospective operation.

Single cold payload gzip: old 173,675 versus Compact 152,559 bytes (not adopted).
Temporary Git loose-blob sizes: old 237,655 versus Compact 173,004 bytes (~27.2%
reduction, NOT 59.6%). This confirms JSONL savings differ from Git object growth.
Annual Git pack/tree/commit growth was not measured and has no guaranteed bound.

Tests: 50 new Compact tests (including all 12 approved fixtures through Compact),
281 B Python + 383 BTC Python + 70 Node = **734 PASS**. Source code protection is
also checked through the staged diff. New storage contract SHA256:
da5add39a24d3a133742b35c3224e9fa786dab275cc938265c9909e3dd9d25a5.

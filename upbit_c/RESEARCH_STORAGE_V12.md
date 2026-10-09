# Upbit C Research Storage V1.2

Status: manual, RESEARCH_ONLY. Local storage implementation; permanent remote
backend and Actions integration are **not activated**. PR #27 remains unmerged.
This does not change score parameters, data quality gates, or outcome formulas.

## Baseline and preserved contracts

At the start of this task GitHub PR #27 and the clean local branch both pointed
to `9e8699a09499f5d42ccc0c3834414ae67d8ed648`. PR was open and mergeable;
main was `5d3cb5d12786142a799d945ec886b318a3a98c0a`.
MASTER is managed outside this repository in the ChatGPT project; it was not
available here and is not modified. V1.1 operating evidence remains in
[RESEARCH_OPERATIONS_V11.md](RESEARCH_OPERATIONS_V11.md).

Reused unchanged: research_history record envelopes/IDs/first-observation rules,
research_segments source hashing and V1.1 chain/expiry validation,
research_scan full KRW collector and scoring, research_outcomes calculations.
The existing workflow continues to use V1.1. No new trigger, schedule, label,
permission, Release, secret, service, trading candidate or order is introduced.

## Layout and commit boundary

Outside Git and all protected production namespaces:

```
cold/
  objects/<uncompressed-chunk-sha256>.gz
  descriptors/<descriptor-file-sha256>.json
  imports/<original-V1.1-manifest-file-sha256>.json
  active/<sealed-active-sha256>.json
  runs/<explicit-run-id>.json
```

Transport schema `upbit-c-cold-archive-1.2`; derived index schema
`upbit-c-active-state-1.2`. Original V1 record schemas remain unchanged.

Each record's original UTF-8 bytes, including envelope and newline, are split
into at most 1MiB chunks and gzip compressed (level6; mtime0). The descriptor
holds chunk hashes, compressed hashes and sizes, full record SHA and size.
Different encoder output is accepted only by reusing an existing object whose
decompressed bytes match exactly. Corrupt existing objects are never replaced.
There is no rounding, field removal, OHLCV regeneration or semantic compaction.

Hash deduplication applies to **identical bytes/chunks only**. A new scan with
changed timestamps/values is a new observation; do not assume most overlapping
candle data will deduplicate. Every original record remains independently
addressable. Descriptors avoid repeating all chunk metadata in every run.

A sealed run manifest holds cumulative compact record references, delta names,
parent run+trusted hash, lineage ID, import evidence, active hash and scan summaries.
Zero-qualified scans are retained and counted, not treated as collection failure.
An explicit independently pinned manifest is required; directory ordering or
an automatically chosen "latest" file is not an authority.

Original chunks/descriptors/import evidence and a new active index are published
first; the immutable run manifest is published **last**. Files use staged fsync
and exclusive hard-link publication. Interrupted writes may leave unreachable
objects; retrying identical inputs is safe. No garbage collection is implemented.
Conflicting run IDs/bytes fail. Concurrent different children are explicit forks;
serialize operational runs and select the intended parent, never silently merge
or treat forks as a single continuous history.

## Active index and everyday reads

The active index stores the full, small original signal contract (market, first
observation, diagnostic reference price, score/gates, engine+parameter hash,
source evidence, fixed next-1h proxy anchor) and original-record reference/hash.
For each 1/3/7d horizon it holds status, original outcome reference/hash, as-of
time; signal-level last-evaluation time is also retained.

`pending_work` produces due-first PENDING/UNVERIFIABLE work; `evaluate_active`
calls the **unchanged** fetch_outcome calculator. MATURED results are excluded
from work and never recalculated/overwritten. Pending/unverifiable originals stay
archived after recovery. Identical signal retrieval preserves first availability
and proxy anchor using the same V1.1 score-comparison rule.

Prepare/evaluation needs only the pinned manifest and its active JSON, plus new
official future candle responses when due. It never reads old scan payloads or
old outcome paths. Commit reads new signal/outcome objects and old descriptor
metadata/existence checks, not old scan payloads. A full audit is deliberately
separate and reads all original records. Metadata checks do not prove every
unchanged compressed byte is still intact; schedule no integrity promise from
stat/existence checks. Run full audit before accepting backups or recovery.

All cumulative manifests and versioned active indexes are append-only. Their
metadata growth is not zero: inventory repetition grows approximately with the
square of run count; all-signal index retention also grows. At high cadence a
future segmented metadata index is advisable. This task does not invent a
cadence, sample reduction, deletion policy or unverified infinite capacity.

## Integrity and restoration

Checks: trusted manifest pin, sealed index, complete signal-ID coverage, valid
signal identity/anchor, outcome references, inventory hash, descriptor hash,
compressed hash/size, bounded decompression, original hash/size, V1 record envelope,
stored normalized source-input hashes, completed candle identity/cutoff.
New scans with missing emitted signals fail instead of reconstructing signals.
Fixture archives/signals are refused by the normal CLI/real namespace.

Audit rebuilds the derived index from all original signals/outcomes and compares
it exactly. Full restoration stages every requested record to temporary disk,
validates before publication and refuses overwrites. Per-record restore selects
only that record's chunks. It does not require previous run manifests and has no
aggregate 5GiB restore ceiling; memory is bounded by a record rather than full
history (large individual records still require memory/disk).

Checkpoint copies each unique compressed object/descriptor and original import
manifest once, plus the selected run/index. It is self-contained without ancestor
run manifests, but still copies the whole preserved cold dataset; it is a backup,
not a daily-operation dependency. No checkpoint can recover a missing sole copy.

If active state is corrupt: ordinary operations fail; rebuild compares against
the originally pinned active hash. `recover-checkpoint` validates all cold data
and creates a separate verified working copy with the exact recovered active
bytes, leaving the damaged original untouched. Missing/corrupt original data
blocks recovery. Hashes detect integrity loss, not an attacker replacing both
data and trust anchor; preserve manifest pins independently.

## Explicit V1.1 migration

`import-v11` requires a complete verified state plus its original manifest bytes.
Inventory/source hashes must match; records are copied byte-exact. Import metadata
preserves original schema, run, lineage, manifest digest and original-byte hash.
Python callers lacking original manifest bytes explicitly record that limitation.
Repeated matching records do not create another object copy. Separate V1.1
lineage IDs stay in import evidence; archival co-location is not cohort equivalence.

`import-remote-v11` reuses the V1.1 authenticated loader/restore checks: successful
same-workflow runs, current artifact hashes and expiry, complete parent chain,
14-day retention guard, no fresh-start fallback. It retains every downloaded
chain manifest and its transport metadata. It is a **one-time migration**, so
V1.1's 5GiB import limit still applies. Offline independently verified backups
remain usable after remote expiry; never claim an expired remote source is live.

## Manual commands

Run from repository root; choose isolated directories outside Git. Substitute
the actual returned manifest SHA. No code below invokes a workflow or Release.

```powershell
python -B -m upbit_c.research_storage import-v11 --archive D:/research/c-cold --run import-1 --state D:/research/verified-v11 --v11-manifest D:/research/manifest.json
python -B -m upbit_c.research_storage import-remote-v11 --archive D:/research/c-cold --run import-1 --source-run 37885517481
# Remote import only uses an explicitly supplied existing GH_TOKEN; never print it.
python -B -m upbit_c.research_storage audit --archive D:/research/c-cold --run import-1 --manifest-sha <trusted-sha>
python -B -m upbit_c.research_storage prepare --archive D:/research/c-cold --run import-1 --manifest-sha <trusted-sha>
python -B -m upbit_c.research_storage evaluate --archive D:/research/c-cold --run import-1 --manifest-sha <trusted-sha> --next-run evaluate-2
python -B -m upbit_c.research_storage scan --archive D:/research/c-cold --run evaluate-2 --manifest-sha <trusted-sha> --next-run scan-3
python -B -m upbit_c.research_storage checkpoint --archive D:/research/c-cold --run scan-3 --manifest-sha <trusted-sha> --destination E:/research/c-backup
python -B -m upbit_c.research_storage recover-checkpoint --archive D:/research/c-cold --run scan-3 --manifest-sha <trusted-sha> --destination E:/research/c-recovered
python -B -m upbit_c.research_storage restore --archive D:/research/c-cold --run scan-3 --manifest-sha <trusted-sha> --destination D:/research/restored --record scans/<scan-id>.json
```

Use **one** import example on an empty destination, not both as unrelated roots.
Additional imports require `--parent-run` plus its `--manifest-sha`. No implicit
initialization after missing history. Native Actions rerun is still prohibited
as a preservation method; keep V1.1 artifacts and independent backups intact.

## Backend comparison and pending approval

Checked official documentation 2026-10-09; actual billing depends on account plan,
visibility, quotas and accepted service terms. No account billing API was changed.

| Backend | Cost/limits and permanence | Read/recovery characteristics | Decision |
|---|---|---|---|
| Actions Artifact | Bounded retention, default90d; Free private-plan artifact allowance500MB shared with Packages; no permanent guarantee | Good temporary run transfer; whole-artifact downloads and deletion/native-rerun risks | Keep existing V1.1 only; not the sole archive |
| Release assets | Official per-file under2GiB; up to1000 assets/release; no stated total release size/bandwidth cap | Addressable assets; pack shards to avoid thousands of API calls; deletion/repo loss still possible | Potential GitHub-native cold backend; publication/permissions require approval; NOT implemented/activated |
| Git repository | Files above100MiB blocked; GitHub recommends ideally below1GB, strongly below5GB | Easy pins/review but raw binaries permanently enlarge clones/history | Keep code and small audit summaries only |
| External object store | Provider-specific storage/requests/egress fees and credentials; no quote asserted | Good per-object access, versioning/replication options | Requires service/credential/budget approval; not introduced |
| Local archive + existing GitHub artifacts | Uses current disk; no new paid service; needs independently managed backups | Implemented content-addressed local store, selective restore; GitHub-hosted runners cannot see local D: | Implemented manual path; no unattended durable synchronization |

Official references:
- [Artifact retention](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/remove-workflow-artifacts)
- [Actions billing/allowances](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [Release limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
- [Repository file/size guidance](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)

Recommendation: keep verified local cold originals now; choose a separately
approved durable remote backend with independent backup before unattended use.
Do not publish one Release asset per1MiB chunk without designing a pack index.
Do not copy all cold data into each daily Artifact. Future Actions integration
would transfer only new cold packs plus the small active/index commit and check
remote durability receipts before admitting a new operational parent. That
integration is not claimed complete and is not silently enabled in this PR.

## Verification and remaining limits

Measured against real root37883347024 and continuation37885517481 via standalone
checkpoint37884201003 (all current API success/hash/expiry checks passed):

| Item | Measured bytes |
|---|---:|
| Original full scan record, byte-exact restore | 46,163,231 |
| Existing one-scan ZIP | 7,934,900 |
| V1.2 unique compressed chunks (45) | 8,132,054 |
| One-time record descriptor | 9,417 |
| Selected manifest | 3,661 |
| Active index (0 real signals) | 250 |
| V1.1 current two-artifact daily restore download | 7,935,595 |
| V1.2 prepare reads (manifest + active) | 3,911 |

Prepare reads fall **99.9507%** in this zero-signal sample; cold payload reads0.
This is NOT a measured nonzero-signal throughput benchmark. Chunk compression
uses **2.48% more bytes** than the existing single ZIP, while using about82.38%
less than raw JSON. The primary benefit is selective daily access and avoiding
repeated cold checkpoint uploads, not a claimed improvement to ZIP compression.
No new full-market collection was executed; existing actual scans were migrated.

Illustrative annual estimates (decimalGB), NOT measured annual usage/cadence:

| Hypothetical scans/day | Cold payload + descriptors | Versioned manifests + zero-signal indexes | Total |
|---:|---:|---:|---:|
| 1 | 2.972GB | 0.020GB | 2.992GB |
| 4 | 11.887GB | 0.309GB | 12.196GB |
| 24 | 71.319GB | 10.968GB | 82.287GB |

Assumes one similarly sized new full scan and one run/index per scan; no signal
or outcome rows, identical-chunk savings, backup copies, extra evaluation-only
runs, network/transport/filesystem overhead. All such additions change usage.
The audit JSON also records30/180-day estimates. High-cadence metadata growth is
reported explicitly rather than hidden behind compressed payload figures.

See `research_audits/storage_v12_20261009.json` for measured real-run hashes,
retention metadata, byte counts and read-cost results. Real qualified signals
remain0, so real matured outcomes/active nonzero-signal throughput are unverified.
Marked fixtures separately cover all horizons, pending/failure/recovery, frozen
matured outcomes and active-only processing. No new full-market API scan is
necessary to re-archive existing originals; none is claimed here.

The independently copied local recovery tests use separate directories on the
same physical disk. This proves logical independence, **not disaster recovery**.
Storage hardware failure, lost trust anchors, expired sole artifacts and missing
approved remote backend remain operational risks. Manifest/index checkpoint size
and restore time still grow; no record is silently truncated to meet a quota.

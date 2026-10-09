# Upbit C Research Operations V1.1

RESEARCH_ONLY. Same initial score/gates/parameters; no strategy tuning, fake live
cases, orders, main merge, operating workflow edits or automatic cadence.

## Manual Actions validation before merge

At task start PR #27 was open/mergeable, head `8c519a72`, working tree clean.
Both workflow metadata and default-branch file lookup returned 404 for the new
full-market workflow. GitHub requires default-branch presence for workflow_dispatch.
No default-branch workaround commit/merge is made.

This research-only workflow additionally accepts **explicit owner-applied PR labels**:

- `c-research-run-root`: manually start a full current KRW scan/new lineage.
- `c-research-scan-<successful_run_id>`: restore then run a new full scan and evaluate prospective cases; preserve previous observations.
- `c-research-evaluate-<successful_run_id>`: manually restore/evaluate, no new scan.
- `c-research-checkpoint-<successful_run_id>`: manually restore/evaluate/export an independent checkpoint.
- `c-research-failproof-<successful_run_id>`: same as evaluate, then deliberately
  fail after export to prove failure-log/artifact retention. Not a real collection fault.

Only labeled activity is subscribed (not synchronize/opened/schedule); job requires
repository owner actor and same-repository PR. Arbitrary labels are skipped;
malformed research labels fail validation. No pull_request_target, PAT/production
secrets, contents write or production collector call. Checkout is pinned to actual
PR head and persist-credentials=false. Manifest uses that head SOURCE_REVISION,
not the synthetic PR merge SHA. Manual workflow_dispatch remains available after
approved default-branch registration, with explicit parent/checkpoint/outcome inputs.

Existing C five-market smoke, A/B/BTC workflows are unchanged. Public API pacing
remains conservative; per-market quality failures stay explicit, not zero scores.
Actions integration must be judged from actual jobs, summaries/artifacts and source
hash verification, not merely the absence of an exception.

## Incremental records and safe restoration

Payload record schemas/formulas are unchanged. Transport schema is
`upbit-c-research-segment-1`; each immutable artifact contains:

- `manifest.json`: run ID, source revision, lineage ID, parent run/manifest hash,
  cumulative file hash inventory, inventory hash, delta file list and manifest hash.
- `records/{scans,signals,evaluations}/<id>.json`: **only newly added records**.

Normal continuation uploads no prior payload bytes. Working state is reconstructed
from explicit parent chain into runner temp, all ancestor hashes verified before
publication. Source scans also verify every stored normalized source-input hash
and completed/identity/cutoff constraints. Older research records remain byte-for-byte
identical; reusing first observation clocks never shifts the proxy anchor.
Same-input research cases and matured outcomes use the existing stable IDs.
Pending/unverifiable events remain preserved when later resolution succeeds.

Missing/expired/failed/unrelated parent, wrong manifest reference, omitted previous
file, changed existing file, duplicate/extra zip entry, unsafe path, malformed JSON,
record/file/manifest/source hash mismatch are fatal. No silent fresh lineage or
historical signal reconstruction. Restore stages records only after chain validation;
atomic exclusive publication refuses replacement. Limits: 1000 segments, 100000 zip
entries, 5GiB restored payload. Hitting limits fails; it never truncates records.

Artifact names include run_attempt for diagnosis, but native GitHub **Re-run jobs**
is prohibited as a preservation mechanism. In actual run 37881437902, the first
successful artifacts disappeared from the run/repository lists and direct artifact
ID GET returned 404 after native rerun. The workflow itself cannot stop a platform
operation occurring before the job starts. The adapter rejects GITHUB_RUN_ATTEMPT>1
with UNSAFE_NATIVE_RERUN; it neither rescans nor invents a replay without evidence.

Use a **new explicit manual run** with a successful parent instead. Verify/download
a self-contained checkpoint before changing or rerunning any ancestor. Do not
rerun, delete or expire an artifact-bearing ancestor merely because a later delta
artifact exists: that delta depends on its ancestor. Missing ancestors fail closed,
not as a fresh root. Independent checkpoint artifacts and offline backups are the
preservation boundary. Failed prior attempts are never promoted silently.

The Actions adapter preserves a safe failure summary. If valid state already exists,
it attempts an incremental export even after an operation fails. Upload steps use
always(). Failed runs are inspectable evidence but not automatically eligible parents.
Synthetic transfer/recovery fixtures are isolated in temporary test directories and
marked test_fixture; default real-lineage exports refuse fixture records.

## Retention — bounded artifacts are not permanent storage

Configured retention: 90 days, subject to repository/organization policy. Every
restore checks actual expires_at for every ancestor. If earliest expiry is less
than 14 days away, regular continuation fails with RETENTION_GUARD. An explicit
checkpoint can restore still-present ancestors and export **all verified records**
in a standalone parent-free artifact, resetting dependency expiry after successful
upload. Its lineage ID and original record clocks/hashes are preserved.

Checkpoint/download must happen **before** expiration. No software can recover an
already deleted sole copy. Without human checkpoint/export action, loss is still
possible. The guard stops future research from falsely claiming a complete history;
it does not watch artifacts while no workflow runs. No automatic retention job is
introduced. Download a verified checkpoint and its manifest to independently backed-up
local storage; keep record bytes and hash evidence. This is necessary for indefinite
performance history. A downloaded incremental zip alone is NOT a complete backup;
obtain the whole chain or a self-contained checkpoint.

Payload storage is linear in new records rather than cumulative full-history uploads.
Cumulative inventory metadata remains repeated and small; restore/download cost
still grows with chain length. Periodic explicit checkpoints bound dependency depth
but themselves contain full preserved history. Do not claim zero repeated bytes or
constant indefinite storage. Preserve loss/unverifiable cases and all original cases.

Future permanent archive options (proposal only; none introduced):

| Option | Cost/complexity | Access / GitHub fit |
|---|---|---|
| Verified local checkpoint plus independent backup | No new service subscription; disk and backup management required | Easy Python/zip access; user must manage durable copies |
| Approved immutable GitHub Release checkpoint assets | No new service account; account limits/policy must be reviewed; contents-write permission needed | Repository-adjacent downloads; separate manual publish approval and retention/export discipline |
| S3/R2 or equivalent object storage | Usage/retention/transfer cost, account/credentials/operating overhead | Durable object archives possible; introduces new paid-service dependency, requires approval |

No paid service, release publishing, DB or production authorization is added.

## Original 126 non-evaluated market diagnosis

`research_diagnostics.py` uses the original verified scan and writes separate
derived audit evidence. It changes neither raw records nor eligibility.
`research_audits/coverage_20261009_0320.json` lists each affected market/timeframe,
observed history length, boundaries, contiguous segment readiness, missing C fields,
source input/response hashes, example missing slots and evidence limitations.

- MISSING_CANDLES 106 timeframe cases: all omitted slots were absent from received
  source-row mappings. Official APIs create candles only when trades occur; absence
  is consistent with no trades, but independent historical trade data are unavailable.
  All 106 had all 15 C fields ready on the latest contiguous segment. Current C
  requires a complete AVAILABLE window and rejects these by design; relaxing this
  requires a separately reviewed/versioned input contract, not imputation or a
  strategy threshold change. No such relaxation is applied here.
- INSUFFICIENT_DATA 39: more recent history was returned but earlier-query response
  hashes exactly match SHA256 of `[]`. Thus earlier accessible history was exhausted
  in those queries, not merely a failure to request another page. First observed
  candle date is not an official listing date; listing dates remain unavailable.
- STALE_DATA 3: KRW-PYUSD/KRW-USDG/KRW-USDS 1h lacked the expected final boundary.
  No synthetic candles are added.
- INSUFFICIENT_FEATURES 2: KRW-DOGE 4h low_structure=MISSING_STRUCTURE;
  KRW-SKY 1h close_location/signed_body_ratio=ZERO_DENOMINATOR. No invented pivot,
  default ratio or forced score.

These counts are timeframe cases across 126 distinct markets, not sums of markets.
Direct narrow official re-queries completed: 148 requests, comprising one representative
missing slot from each of 106 sparse windows, 39 earlier-history checks, and 3 stale
last-boundary checks. All 109 tested missing/stale slots remained absent; all 39
earlier-history responses remained empty. This does not independently prove zero
trades or an official listing date. Every new response hash and receipt clock is
preserved in `research_audits/source_probes_20261009.json`; original evidence is
unchanged. If a previously absent slot later appears, classify source change, not
retrospective signal eligibility.

## Outcome and BTC test verification

Outcome formulas and initial score parameters remain unchanged. Isolated fixture
tests cover transfer of first observation, next-1h-open proxy, exact +24/+72/+168h,
gross return/MFE/signed MAE, PENDING, API UNVERIFIABLE then recovery to MATURED,
stable-key no-op, old failure preservation, gap/evidence failure, future exclusion,
and restoration across incremental segments. Real mature C outcomes require actual
qualified cases; fixtures cannot establish real performance.

BTC failure reproduced at original HEAD: obsolete assertion expected literal
status!='A'. Actual workflow correctly whitelists new evaluation JSON and designated
performance latest files. Separate test-only commit replaces that assertion and
executes the exact real workflow guard against allowed/forbidden A/M/D paths.
Existing finalization tests still forbid modifying historical evaluation records.
No BTC workflow/engine/parameter/production data changes.

Official references:
https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow
https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository
https://docs.upbit.com/kr/reference/list-candles-minutes

## Actual GitHub Actions and storage measurements (2026-10-09)

Initial integration runs use PR-head ca2d069e, not main. The native-rerun safety correction is 4f8e1a3f; subsequent verification uses that PR head.

- Full-market run [37881437902](https://github.com/nh1018/upbit-scanner/actions/runs/37881437902/attempts/1), attempt 1: SUCCESS; scan 03:55:04.485 to 04:13:00.019 UTC, 1075.534 seconds (17m55.5s), 920 logical public API calls, KRW 293 markets / 879 timeframe windows, scores 167 available / 126 unavailable; 0 research PASS cases, 0 real outcome records or production candidates. All 879 stored normalized-input hashes passed; all 167 available research scores were independently replayed byte-equivalently after canonical encoding.
- Verified scan ID: e59ad66f7b3992a07cfd213a19f45b3901c6519104a8aa79316571ce2480eb6e. File SHA256: d7e854d00633613bd489d0ca33e6fab769c5da5558a3d140ccc6d45b398c068c. Manifest SHA256: fc03d5b1bc72db9438b640bb2c9670da9f5877603e68d1d65763dd9753fb7cda. GitHub ZIP digest also verified.
- Continuation [37882584388](https://github.com/nh1018/upbit-scanner/actions/runs/37882584388): SUCCESS; two-segment restoration independently checked afterward. Original record SHA unchanged; 0 new/exported records, 0 prior payload bytes uploaded, manifest-only increment. No live research cases existed, so real matured returns remain unavailable.
- Deliberate failure probe [37882941247](https://github.com/nh1018/upbit-scanner/actions/runs/37882941247): EXPECTED FAILURE exclusively at explicit probe step, after successful restore/operation/export and protected-file check. Actual log contains INTENTIONAL_FAILURE_RETENTION_PROBE_AFTER_EXPORT. Both always-upload steps succeeded; increment ZIP 694 bytes, summary ZIP 623 bytes, transport digests verified. Failed probe is not an eligible continuation parent.

Measured: fresh scan envelope 46,158,878 bytes; uploaded ZIP 7,931,535 bytes, plus summary ZIP 2,197 bytes. Continuation increment ZIP 695 bytes (no new records); compared with copying the same root ZIP, 99.991% less increment transport. This is a no-new-record continuation measurement, NOT a reduction in bytes required by each genuinely new full-market scan.

Assumption-based planning, decimal units, excluding outcome events/metadata/checkpoints/backup redundancy and assuming each fresh scan resembles the measured one: 30 daily scans approximately 237.95 MB; 180 approximately 1.428 GB; 365 approximately 2.895 GB of new compressed payload. Uploading cumulative history every day would instead copy approximately 529.79 GB across 365 uploads. At six-hourly cadence, fresh scan payload alone would be approximately 11.58 GB/year. These are capacity estimates, not a configured cadence or permanent GitHub storage guarantee; no automatic cadence is introduced. Explicit checkpoints repeat full history and add cost.

Repository growth: full scan payloads remain runner-temp/artifacts, never committed. Only research code/tests/documentation and approximately 305 KB of separate original-coverage/probe audit JSON are added to Git here. Do not put repeated 46 MB scan envelopes into repository history.

Local complete regression: B/C 366, BTC Python 389, common Python 10, Worker Node 70 = 835 passed, 0 failed. Actual research Actions at ca2d069e ran the then-current B/C 355 tests successfully, plus Ruby YAML syntax validation. Fixture transfer/API-failure/recovery and 1/3/7-day outcomes are automated tests, not actual C performance.

### Unexpected native-rerun integration failure and protected local recovery

Run 37881437902 attempt 2 was a real FAILED integration check, not the deliberate
failure probe: prior attempt metadata still said success, but original increment
and summary artifact IDs returned 404. Attempt 2 preserved a failure summary and
created no scan/signal/outcome. The initial replay implementation was therefore
removed rather than declared PASS. Native reruns are fail-closed and explicitly
unsafe for artifact preservation; new manual continuation is the supported replay.

The already verified original ZIP was preserved byte-exactly outside the repository
at `D:/repos/upbit-c-research-backups/run-37881437902-original/original-artifact.zip`,
with independent backup-evidence.json. Original ZIP SHA256 remains
7171925607d81edf7b8d792f5423f92407530f3b28923148d85b7dd23d1e9f56.
Original scan/manifest clocks and hashes remain unchanged; original summary bytes
not retained locally are explicitly unavailable. No original data are reconstructed.
There were 0 real qualified cases and 0 real outcome records in that run.

The original delta chain is now incomplete on GitHub and is NOT an eligible parent.
A new real scan starts a deliberately independent research lineage; continuity is
not claimed across it. Original raw research observation survives locally, but
GitHub-only preservation failed this native-rerun scenario. Artifact-only storage
cannot honestly guarantee indefinite retention against rerun/deletion/expiry.
A separate verified checkpoint and independently backed-up copy are mandatory for
durable research. No new paid service, release asset or hidden storage was introduced.

Related upstream report (corroborating context, not proof of platform internals): https://github.com/actions/upload-artifact/issues/585. Local observed API results are recorded independently in research_audits/native_rerun_20261009.json.

Manual modes are parsed by one tested Python contract (manual_mode), not duplicated shell parsing. In particular scan-parent performs a new full scan plus prior-case evaluation, while evaluate-parent never rescans. Malformed/zero/leading-zero/injected/newline IDs fail; workflow job independently gates owner and same-repository labels.

### Capacity limit is not a long-term durability solution

The 5GiB restoration cap is approximately 116 full scan envelopes at the measured
46,158,878 bytes each (fewer if signals/outcomes grow). A checkpoint severs ancestor
dependencies but does NOT shrink this preserved total. There is no automatic cadence
now; at daily future cadence this cap would arrive in approximately 116 days.
Restoration/download/verification time also grows; the actual 40-minute timeout must
be re-measured as history grows. Limits fail closed, never drop past cases.

Before sustained long-term automation, review durable cold archives plus a verified
active-case/index restoration design, or cohort-specific independent checkpoints
with explicit cross-cohort outcome tracking. Preserve all original scan/case/outcome
bytes and references; do not silently reset cohorts or discard loss/unverifiable
cases. This expansion is a proposal, not an untested implementation or new service.
Current V1.1 is suitable for bounded manual research with verified checkpoint/offline
backup discipline; indefinite unattended preservation is not claimed.

Legacy compatibility: original V1 record envelopes and IDs/formulas are unchanged.
Legacy evidence-smoke artifacts are not prospective history and cannot be used as
segment parents. A legacy full-copy artifact without a sealed segment manifest is
not silently promoted; explicit independently verified migration is required.
There was no registered full-market workflow artifact before this task. The original
local 03:20 scan is also backed up byte-exactly, as original-scan.json.gz under
D:/repos/upbit-c-research-backups/baseline-20261009-0320. Uncompressed SHA256
1eff3f7e28330f76d3559d6ef0b98ae75bf80104dc41d280df4e89a7379f8536;
raw bytes 46,158,864; gzip bytes 8,112,794. Decompression equality and all source-input
hashes passed; no signal reconstruction or replacement was performed. Local copies
on one disk alone are not independent disaster recovery; arrange a second backup.

## Bounded manual operating procedure

1. For a genuinely new study, owner applies run-root once. Otherwise start a **new**
   manual scan-parent request to carry prior records and evaluate pending cases.
2. Wait for success, then download/verify its manifest, record/source hashes, ZIP
   digest and actual ancestor expiry. Confirm universe/quality counts; a green job
   alone does not mean every market is evaluable or any strategy is profitable.
3. Manual evaluate-parent runs track existing cases without rescanning. Empty cases
   produce zero outcomes; never manufacture performance. Repeating as a new run is
   safe and duplicates no unchanged payload.
4. Before an ancestor is rerun/deleted/expires, and whenever the retention guard
   requires it, create checkpoint-parent, verify parent=null plus full inventory,
   download it and independently back it up. Continue from checkpoint's run ID.
5. Do not use native Re-run jobs on an artifact-bearing ancestor. The guard rejects
   it only after job startup; it cannot undo platform artifact invalidation.
6. If an ancestor is absent/corrupt, stop; do not silently replace its data or
   reinterpret a fresh root as continuous history. Use preserved verified originals
   for an explicit reviewed recovery.
7. After manual validation, remove request labels. This does not delete records or
   enable a schedule. workflow_dispatch/default-branch registration and any future
   automatic cadence remain subject to separate approved main integration.

All prospective anchors remain the next 1h open strictly AFTER actual first
observation (not the nominal old candle close). Gross return excludes fees and is
not a claim of realizable execution. 24/72/168-hour paths must be complete and
source-observed; pending/unverifiable cases are preserved separately from losses.

## Latest independent lineage: actual full-market verification

- [37883347024](https://github.com/nh1018/upbit-scanner/actions/runs/37883347024): SUCCESS; source cutoff/start 2026-10-09 04:19:56.150 UTC, finished 04:41:17.545 UTC, 1281.395 seconds (21m21.4s); KRW 293, timeframe windows 879, 920 logical requests, api_blocked=false. Research scores 169 evaluated / 124 unavailable, setup PASS 0, real signals/outcomes 0. Non-evaluated timeframe cases: MISSING_CANDLES 104, INSUFFICIENT_DATA 39, STALE_DATA 4, INSUFFICIENT_FEATURES 1. Different current completed candles explain why these counts need not equal the original 126-market diagnosis; original records are not reclassified or replaced. Every source-input hash and all 169 score replays passed.
- Latest scan ID 5c02c9ceaa552ceab362237786e62dee38c4763090f246cf6443bcd2c8025e6c; record SHA256 857ba40b37b5900c26e158928936ae3b02a3ecfaadcd4241e639e2fbd00669fe; raw bytes 46,163,231; artifact ZIP 7,934,900 bytes; ZIP SHA256 38442e8504e687b12b9c70241e17d2a78179a1f6e5c90e5f1e9c4f9fee358108. Verified original ZIP/summary/manifest are backed up under D:/repos/upbit-c-research-backups/run-37883347024.
- [37884201003](https://github.com/nh1018/upbit-scanner/actions/runs/37884201003): SUCCESS on latest adapter/workflow head 7655f368; actual B/C 365 tests and Ruby YAML check succeeded. Checkpoint parent=null, same lineage and inventory/record SHA; new_records=0, exported_records=1, ZIP 7,934,900 bytes. Manifest SHA256 12de33ef4b0c56be24a5e990ea6bcca2494adde52fc278409123cc79a3803012. Independent restore requested ONLY this run, not the root or any ancestor. Exact ZIP/summary/manifest backed up under D:/repos/upbit-c-research-backups/checkpoint-37884201003.
- [37885150014 attempt 1](https://github.com/nh1018/upbit-scanner/actions/runs/37885150014/attempts/1): SUCCESS; manual continuation from checkpoint, unchanged file inventory; new/exported records and payload bytes all 0, increment ZIP 697 bytes, summary ZIP 622 bytes. Original artifact bytes saved under D:/repos/upbit-c-research-backups/replay-probe-37885150014 before a native-rerun guard probe. No new case/outcome payload is placed at risk; independently verified checkpoint plus offline backups precede this probe.

The latest fresh scan changes observational input time, not scoring weights or
quality policy. The original 126-market audit remains the original observation.
None of these real scans produced eligible research cases, so actual matured
performance remains unverified; fixture calculations cannot substitute for it.

The adapter validates state, segment and summary paths against protected production namespaces BEFORE any parent restoration or file write (not only in the downstream runner). Regression verifies every forbidden-target case leaves all existing bytes unchanged and makes no API/scan call.

- Native guard [37885150014 attempt 2](https://github.com/nh1018/upbit-scanner/actions/runs/37885150014/attempts/2): EXPECTED FAILURE with exact UNSAFE_NATIVE_RERUN summary, no scan/API restoration or new record. This probe was performed only after an independent verified checkpoint and byte-exact offline backup of the empty-delta child existed.
- Supported replay [37885232028](https://github.com/nh1018/upbit-scanner/actions/runs/37885232028): SUCCESS as a NEW manual execution from unchanged checkpoint; original source SHA remains identical, new/exported records=0, payload bytes=0, increment ZIP 694 bytes. This follows the native guard without relying on the guard-probe run or invalidated original chain.
- Structured API/run/artifact observations and fixture-versus-real distinctions: research_audits/actions_20261009.json.

Final scope: no A/B/BTC operating code/workflow/raw/provenance or C score/feature/outcome formula changes. The BTC fix is a separate test-only commit. No main merge, automatic cadence, production candidate/order activation, paid service or release publishing. The manual scan-parent mode is unit-verified; two actual full scans used root mode, and actual continuation/checkpoint runs used evaluate/checkpoint modes. Do not claim a second fresh full scan in the same lineage was additionally performed.

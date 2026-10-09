# Upbit C V1.3 manual operations

RESEARCH_ONLY. V1.2 original records, score parameters and outcome formulas stay
unchanged. The only workflow modified is the explicitly requested C full-market
research workflow. No schedule, production signal/order, Release, external upload,
permission expansion or main merge is introduced.

## Data flow

Manual owner label or workflow_dispatch -> authenticated parent preflight ->
small active/index restore -> optional new full-KRW scan / outcome-only work ->
immutable V1.2 commit -> new cold-object Artifact + cumulative small index Artifact.

Artifacts (attempt1 only, native Re-run remains rejected):

- `upbit-c-research-cold-1`: only new gzip objects and sealed `cold-manifest.json`.
  Upload compression0 avoids recompressing gzip. An empty delta still emits a
  small cold manifest; this is not a missing-data error.
- `upbit-c-research-index-1`: sealed transport receipts, current pinned run
  manifest/active state, cumulative record descriptors and original import
  manifests. No old scan OHLCV or historical outcome paths.
- `upbit-c-research-summary-1`: success/failure, source/run references, bytes,
  clocks, request counts and verification scope.
- `upbit-c-research-recovery-1`: **failed runs only**, local partial state and
  completed new `pending-records` captured before archive commit. Diagnostic
  evidence, not an automatically admissible operational parent.

Transport version `upbit-c-actions-storage-1.3`, cold receipt version
`upbit-c-actions-cold-1.3`; original storage/index schemas remain V1.2. Every cold
object reference records compressed SHA/size and origin run. The current index
can be restored without reading prior index Artifacts. Cold origins are retained
until an explicit self-contained checkpoint rehomes all objects in the new run.

## Verification scope — not a hidden checksum bypass

Before accepting a parent: successful same-workflow run, original attempt1,
authenticated actual Artifact listing/digest/expiry, downloaded index ZIP SHA,
every index file SHA/size, sealed transport and pinned storage/index/inventory
hashes, signal/outcome identity and coverage, descriptor hashes, complete cold
object receipt coverage, and existence/expiry of **all** referenced cold Artifacts.

Normal evaluation does not download old cold bytes merely to rehash them. It
relies on their original full verification and authenticated current existence
receipts. Summaries explicitly distinguish this from a fresh full-cold-byte audit.
If old raw bytes are actually read, lazy download verifies official ZIP SHA,
cold-manifest hash, compressed object hashes, bounded decompression, original
record/envelope/source-input hashes. A full audit/checkpoint does this for all
records. Missing/expired cold sources stop even an active-only run. No invalid
hash is ignored or changed into PASS.

New records are fully read/validated before export, exported gzip hashes are
checked, and failed publication/upload fails the workflow. Parent receipts cannot
silently change. Operational restore never silently creates a new root after a
missing V1.3 index; V1.1 fallback also requires an actual valid original Artifact.

## Operation modes and V1.1 compatibility

Existing explicit manual labels remain:

- `c-research-run-root`: new full-universe scan in an explicitly new lineage.
- `c-research-scan-<successful-parent>`: continue, full new scan + prior active outcomes.
- `c-research-evaluate-<successful-parent>`: no market scan; active-only outcome work.
- `c-research-checkpoint-<successful-parent>`: explicit full cold audit/re-export.
- `c-research-failproof-<successful-parent>`: isolated post-export failure probe;
  never a valid parent after its expected failed conclusion.

Owner/same-repository PR guard, contents:read/actions:read, persist-credentials:false,
serialized concurrency, 40-minute timeout and90-day retention remain unchanged.
workflow_dispatch requires default-branch registration; at start the workflow
was absent from main. Existing approved owner-label route exercises actual branch
code without merging main or using pull_request_target. Labels are requests,
not periodic triggers. Remove request labels after validation; don't native-rerun
an artifact-bearing source.

If a parent has no V1.3 index, its V1.1 complete chain is restored and verified
**once**, migrated byte-exact and published in a new V1.3 cold/index run. The old
increment Artifacts and their original manifests are not changed or deleted.
Normal descendants use V1.3 only. Original source lineage IDs stay in import
evidence; storage continuity does not manufacture signal/cohort continuity.

## Outcome-only and failure behavior

Use the stored signal contract and fixed NEXT_1H_OPEN_PROXY, never a reconstructed
past signal. Due outstanding horizons first; only needed future1h candles are
requested, +1/+3/+7d formulas remain unchanged. MATURED horizons are excluded
from recalculation; PENDING/UNVERIFIABLE originals remain archived. No real
qualified cases means zero actual performance labels, not synthetic successes.

Completed newly calculated records are copied to `pending-records` before archive
publication. On failure, the workflow attempts recovery Artifact upload plus
normal summary/index/cold evidence. Failed runs are never eligible parents.
Recovery requires explicit review/hash verification and a new manual run/copy;
do not regenerate missing signals or relabel failed publication as success.
If a runner is killed before a completed report is captured, or Artifact upload
itself fails, this mechanism cannot guarantee preservation. Independent cold
backup remains necessary; originals are never overwritten to repair a failure.

## Retention and read/storage limits

Every parent index and cold dependency must exist and be unexpired. Normal
continuation stops inside14days of expiry. Explicit checkpoint can read still
present near-expiry sources and publish a new independent cold owner. It cannot
recover already expired/deleted sole copies. Checkpoint is intentionally costly
and is not an everyday evaluation dependency.

Daily outcome work downloads only the index ZIP. New scan commits also reuse
small metadata; a rare identical old gzip chunk may cause lazy download of that
origin pack to reuse its exact original container. Measure actual bytes rather
than promising zero cold reads for every possible scan. Index/descriptor/receipt
metadata and metadata API requests grow with cumulative history. Full checkpoint
work still grows with total cold bytes; individual transport ZIP/resource checks
retain a5GiB bound. Future long-lived pack sharding is needed before that limit,
not record truncation or silent loss. V1.1 one-time imports retain their original
chain/resource limits.

## Permanent storage recommendation — comparison only

Official sources checked2026-10-09. See final audit for measured one/four-scans-per-day
budgets. These are assumptions, not an enabled schedule or annual measurements.

| Option | Durability/cost/limits | Efficiency/security/management |
|---|---|---|
| Actions Artifacts | Configured90d, not permanent; plan/visibility-dependent quota and storage billing | Implemented temporary transfer; builtin read-only token, simple Actions integration; dependency preflight/checkpoints/manual expiry management required |
| Release assets | No Artifact TTL; official <2GiB/file and1000 assets/release; deletion/repository loss still possible | Pack by run/month, not one asset per1MiB chunk; requires separately approved publication and write permission; manageable GitHub-native restore |
| External object store | Provider-dependent durability/fees/retention; separate account and credentials | Efficient hash-addressed access and optional versioning; more authentication/billing/operations; not created or connected |
| Independent local backup | Disk/backup hardware cost, no new cloud charge; must be physically independent | Existing tools verify/selectively restore; offline/download process needs monitoring; D: copies on one disk are not disaster recovery |
| GitHub + independent backup | More than one failure domain if backups truly independent | Recommended architecture; manifest pins/checksums and restoration drills at both copies |

Example only: Cloudflare R2 Standard lists$0.015/GB-month,10GB-month free,
1M ClassA/10M ClassB operations free per month, free Internet egress; paid request
rates and billing-unit rounding still apply. Free quota is shared with the
account's other usage. No actual account bill, service activation or quote is
asserted. A storage-only estimate must include index/backup growth and actual
monthly average retained bytes, not confuse annual generated bytes with GB-month.

Recommendation: retain V1.3 Artifacts for manual operations and independent local
copies now. For the next approval, prefer GitHub-native Release packs + independent
backup if that operational policy is acceptable; choose scoped external object
storage if selective access/high cadence justifies additional credentials and
management. Approve backend, write permissions, backup/restore policy and budget
before connecting it. Do not imply current Artifacts provide unattended permanent
research history.

Sources:
- [GitHub Artifact retention](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/remove-workflow-artifacts)
- [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [Release limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
- [R2 pricing](https://developers.cloudflare.com/r2/pricing/)

## Evidence

Actual run IDs, original hashes, Artifact sizes/expiries, request counts, full
audits and byte comparisons will be recorded in
`research_audits/actions_storage_v13_20261009.json` after the manual runs finish.
Tests use isolated fixtures for destructive corruption/missing/expiry cases and
synthetic qualified outcomes; they are not actual market performance.

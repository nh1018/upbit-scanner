# Upbit C V1.4 — merge preparation and offline Release packages

RESEARCH_ONLY. No new scan, score tuning, outcome formula change, schedule,
Release publication, external upload, privilege expansion, Draft transition or
main merge. `research_release.py` is independent of the existing manual Actions
transport; no workflow invokes it automatically. All remote methods are GET.

## Merge diagnosis

Starting PR head `b9283e54ef27ea898f9cb55c5023dd9e79d6ba76` matched local/remote,
working tree clean. REST initially returned unknown/null mergeability; GraphQL
subsequently returned `MERGEABLE`, `CLEAN`, `isDraft=true`, `reviewDecision=null`.
Thus a prior `mergeable=false` observation does not establish a code conflict.
Main is receiving production commits; all comparisons must pin revisions.

Non-destructive `git merge-tree --write-tree HEAD origin/main` against main
`5bab43ea31733c3cb60fe3cabbe2716960e9074e` exited0, tree
`79adfb07b98efe94787086d6f2627981c13c5513`. This writes Git objects only, not a
merge commit, checkout, index or working tree. No conflict resolution needed.
PR API's sampled base was `6300f9b0f7c056ca1ecd7eb971c33c7fb83c3b90`; fetched main
had already advanced. The final audit records later pinned checks separately.

Repository APIs reported branch protection404 explicitly "Branch not protected",
rulesets[], effective main rules[], reviews[]. No required check/review rule was
observed; this is not a recommendation to remove governance. Existing
`contract-tests`/`audit` checks passed. Actions enabled, allowed_actions=all,
default workflow token read, can_approve_pull_request_reviews=false. Tokens'
private OAuth scope contents were not printed. A successful ordinary branch
push establishes that this session can update the PR; no hypothetical merge or
Release-write permission is asserted. Draft is the identified current procedural
barrier. It remains unchanged and needs explicit user authorization to advance.

## Package contract and compatibility

Schema: `upbit-c-release-package-1.4`.

- Reuses existing V1.2 descriptors, exact gzip objects, original import manifests,
  current run manifest and its pinned Active State. No indicator/score rerun.
- Self-contained checkpoint: original parent/lineage IDs stay unchanged, but old
  parent run manifests/Artifacts are not needed for restoration.
- Sealed `release-manifest.json` pins original run/lineage, manifest/inventory/
  active hashes, original record references, every included file SHA256/size.
- Deterministic ZIP_STORED (gzip chunks are already compressed), fixed ZIP
  timestamps, content-derived asset name. Local sidecar pins full ZIP SHA/size.
- Retain that external pin in a separate trusted receipt/catalog/backup. A hash
  downloaded beside an untrusted file detects accidental corruption, not an
  attacker replacing both. Nothing signs the package or guarantees authenticity.
- Full original/Active State audit before packaging; full package verification
  before local publication. Duplicate local retry preserves identical bytes.
- Restore stages/validates all bytes, paths, entry counts, bounded total sizes,
  seals, exact membership, gzip/raw hashes and Active State derivation before
  copying. Existing overlapping destination files are preflighted, never replaced.
  Manifest remains the commit boundary; interruption can leave uncommitted files,
  which identical retry can reuse. Do not use an incomplete directory as a parent.
- At most100,000 entries and strictly below2GiB per package, including ZIP
  overhead. Large snapshots fail and require a separately designed sharding
  policy; no truncation. This package adapter does not remove V1.3's5GiB limit.
- `publication_plan`: same name + uploaded state + exact server digest/size means
  already present, but still requires downloaded verification before claiming
  preservation. Missing digest, starter/partial upload or differing bytes fail;
  no automatic delete/replacement. Re-list after a network timeout, never blindly
  retry an upload or treat GitHub422 as successful evidence.
- `compare_originals` compares **fully verified** package manifests. Same original
  references in different checkpoint versions are explicitly reported; differing
  original identity fails. Overlap alone must not discard a new Active State.
  Metadata-only checkpoints can therefore intentionally repeat old cold bytes;
  package scheduling/delta catalogs need separate approval/design.

The adapter intentionally has no POST/PUT/PATCH/DELETE/create/upload function.
No credentials are accepted as CLI text; optional GET authentication uses
`GH_TOKEN`. Auth is never forwarded on signed download redirects. Failed reads
report sanitized HTTP codes. Actual Release download cannot be tested until a
separately approved Release asset exists; GET mechanics are fixture-tested.

## Local operations

Run from repository root, using the project Python runtime. Use an isolated
destination outside repository production namespaces. `<SHA>` values must come
from verified original manifests or an independently retained package receipt.

```text
python -B -m upbit_c.research_release prepare --archive D:/backup/cold --run-id <RUN> --manifest-sha <MANIFEST_SHA>
python -B -m upbit_c.research_release prepare --archive D:/backup/cold --run-id <RUN> --manifest-sha <MANIFEST_SHA> --write-package E:/c-research/packages
python -B -m upbit_c.research_release verify --package E:/c-research/packages/<ASSET>.zip --sha256 <ZIP_SHA>
python -B -m upbit_c.research_release restore --package E:/c-research/packages/<ASSET>.zip --sha256 <ZIP_SHA> --destination D:/recovery/cold
```

First command writes nothing. `--write-package` explicitly creates only local
package/receipt files. The E: examples are a proposed separate device, not a
claim that one exists or has been used. Restore creates a Cold Archive/Active
State checkpoint, not production scans/collector files. Raw record export can
then use the existing verified `Archive.restore` tool into another isolated path.

After a future approved Release exists:

```text
python -B -m upbit_c.research_release plan-publication --package-receipt <ASSET>.zip.receipt.json --repository nh1018/upbit-scanner --release-id <ID>
python -B -m upbit_c.research_release download --repository nh1018/upbit-scanner --asset-id <ID> --sha256 <TRUSTED_ZIP_SHA> --destination D:/recovery/download
```

Plan only lists remote assets. Download writes local verified bytes only. An
approval-required result never uploads anything. Future publisher should create
a draft, stage all assets, independently re-download/verify, then publish only
with approved rights/policy; unconfirmed partial publication remains a failure.

## GitHub Release limitations — checked 2026-10-09

Official GitHub docs: maximum1000 assets/release, each strictly below2GiB; no
stated aggregate release size or release bandwidth limit. This is not an unlimited
permanent-storage SLA or general object-storage contract. Release attachments
are separate from Git history; archives must not be committed to Git. Public
repository means publicly published research assets would be downloadable.
Actions compute/Artifact storage still have their own quotas/billing. No special
account-specific fee exemption or indefinite retention guarantee was verified.

List/download use REST Release/asset GET endpoints. Creating releases/uploading
assets requires contents:write (fine-grained token/GitHub App) and corresponding
repository write rights; never expand the existing research workflow's read-only
token automatically. Upload raw binary to the returned GitHub uploads endpoint,
with size/digest checks and bounded backoff only after a separate publisher is
approved. A matching filename alone is not integrity evidence.

Actual repository immutable-releases GET reported `enabled=false`,
`enforced_by_owner=false`; releases list was empty. Ordinary assets/releases can
be edited/deleted by authorized actors. Optional GitHub immutable releases can
protect published assets/tags and create attestations; enabling that repository
policy is a separate authorized change and was not performed. Even such policy
does not substitute for an independent backup or protection against repository/
account access loss. Draft assets must be fully prepared before immutable publish.

## Independent backup and disaster recovery

| Structure | Cost/operations | Recovery/security |
|---|---|---|
| Release + offline separate physical disk | Hardware/manual copy; no new cloud service | Recommended first step at current MB scale; offsite/encrypted disk, checksum receipts, periodic restore drill; same PC/disk is not independent |
| Release + object store | Storage/request/billing credentials, versioning/retention administration | Better automated/offsite retrieval and selective access; approval/account isolation required; no service connected |
| Release + independent cloud backup | Plan capacity/subscription, client and deletion/version policy | Offsite copy; verify exact bytes, exportability and separate credentials; sync alone can replicate deletion |

Proposed procedure: full audit -> local package+external receipt -> verified copy
to physical/offsite backup -> separately approved Release publication -> download
and full audit -> retain two independent receipts/catalog entries -> periodic
restore drill into a fresh isolated directory. Do not delete original Artifacts
on the strength of an upload response. Before90-day expiry, require confirmed
independent copies or explicit verified checkpoint; missing source fails closed.
Recover by external pinned SHA, package verification, staged restore, Active State
audit and byte-identical raw export. Stop on missing/corrupt/import mismatch;
never reconstruct old signals. Actual local verification on D: is a software
restore test only, not physical disaster recovery.

## Capacity and observed validation

Existing8.15MB scan delta implies~2.98GB/year at1/day or~11.90GB/year at4/day,
before indexes/backup/checkpoint duplication. A monthly self-contained Release
snapshot would repeat cumulative originals: with linear growth,12 snapshots sum
approximately6.5 times a year-end cold snapshot (~19.3/77.4GB/year), not just new
scan bytes. This is an assumption, not measured annual Release usage. Use a
separately approved cadence/catalog/pack policy before regular publication.

Exact real-package measurements, source hashes, lineage, duplicate detection,
original comparison and test counts are in
`research_audits/release_storage_v14_20261009.json`. No new full market scan is
required for preservation validation. Qualified real cases remain0; package
tests with nonzero signals/outcomes use explicitly synthetic fixtures.

Official references:
- https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
- https://docs.github.com/en/rest/releases/assets
- https://docs.github.com/en/rest/releases/releases
- https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases
- https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes

Review readiness is separate from permission to merge/publish/enable automation.
No such action is authorized by a successful local package test.

## Completed local preservation verification

| Actual source | Original bytes | Release-ready ZIP bytes | Result |
|---|---:|---:|---|
| v11_preserved_original | 46158878 | 8160143 | VERIFIED, byte-identical |
| v12_v11_migrated | 46163231 | 8167976 | VERIFIED, byte-identical |
| v13_independent_checkpoint | 92324350 | 16325276 | VERIFIED, byte-identical |

Three distinct real scan originals verified across the three packages (one shared
original explicitly detected). Active State and imported original lineage
manifests remained exact. Packages were replayed without overwrite; corrupt
copies rejected before restore publication. The initial V1.1 remote Artifact is
unavailable; its previously preserved ZIP was checked against the pre-existing
external SHA, never represented as a successful new remote retrieval. V1.1 local
import is a preservation-format migration with original manifest retained, not a
new scan or reconstructed historical signal.

Same original SHA/size but differing compressed descriptor hashes occur in the
actual V1.2/V1.3 sources. Original identity comparison deliberately uses raw SHA
and size; each package independently validates its exact gzip/descriptor bytes.
Do not collapse these two checks or claim different containers changed raw data.

Final regression:939 PASS /0 FAIL (B/C470,BTC389,common10,Node70),37 new Release
transport tests. Existing6,244 tracked files unchanged versus b9283e54. No existing
workflow modified. Nonzero outcome/Active State fixture round-trip passed; no
real qualified performance case is claimed.

Latest non-destructive main pin6bcae61f5602e020c94a1ad68fa9c2c20dddc15b was321
commits ahead of the PR merge base side; PR had23 unique commits. Merge-tree exit0,
tree9287343facecaf1e9b732dc7983e28afde6d4194. The full PR already contains the
previously approved BTC test assertion fix; V1.4 changes no BTC file. See
research_audits/merge_readiness_v14_20261009.json for API settings/GraphQL/check
snapshots. Main will continue advancing; re-pin and recheck immediately before
any separately approved merge. Draft stays Draft.

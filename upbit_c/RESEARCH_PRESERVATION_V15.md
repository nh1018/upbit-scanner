# Upbit C Research Preservation V1.5

## Scope and current authorization

RESEARCH_ONLY. V1.4 ZIP, Cold Archive, Active State, original record bytes and
outcome/scoring contracts are unchanged. V1.5 adds an independent transport
adapter, manual workflow and backup export. No market scan, evaluation or
historical reconstruction occurs during preservation.

This development does **not** publish a Release, upload an Asset, enable a
schedule, connect an external backup or merge main. Those remain separate user
approvals. Fixture uploads are in-memory only. Local D: copies are not an
independent disaster-recovery backup.

## Structure and identity

`research_preservation.py` reuses `research_release.py` full V1.4 verification
and restore. A package contains its original sealed Manifest, raw-record hash
references, compressed original chunks, import evidence and Active State.

- Release tag: `c-research-<V1.4 package_id>` (64 lowercase hex).
- ZIP Asset: `c-research-<package_id>.zip`; exactly one ZIP per managed Release.
- ZIP SHA256 is separate from package_id / Manifest hash / original-record hash.
- Release body records schema, RESEARCH_ONLY, source run ID, package ID,
  original Manifest SHA, ZIP SHA and original IDs. This metadata is discovery
  information; recovery must use a SHA retained outside the Release.
- New tags target an explicit 40-character commit SHA. No existing branch is
  updated. Future approved Releases are prereleases, `make_latest=false`.

The adapter inventories managed Releases, downloads and fully validates their
packages before comparing original IDs/bytes. Same package/name/hash is a
verified replay: no POST. Same name with different hash, missing official
digest, starter/partial Asset, invalid tag, extra Asset or original conflict
fails closed. No delete, replacement, PATCH or PUT API exists.

### Overlapping checkpoint limitation

V1.4 self-contained checkpoints can contain previous originals. V1.5 refuses
to publish a different package that overlaps an already archived original,
even if all bytes agree. It does **not** silently skip a new Active State or
pretend that the different checkpoint was archived. Thus deduplication is
conservative rejection, not arbitrary cross-package chunk sharing. Existing
overlapping local V1.1/V1.2/V1.3 packages remain fully readable/restorable.

For initial publication select one independently complete checkpoint; do not
publish all migration/checkpoint representations of the same originals.
Continuous cumulative checkpoint publication needs an explicit policy for
disjoint packages and Active State references, or a separately reviewed shared
object format. This release does not rewrite originals or invent that format.

## Manual preparation and read-only validation

From repository root, with a read-only `GH_TOKEN` in the environment:

```bash
python -B -m upbit_c.research_preservation prepare-source \
  --run-id <successful-C-research-run-id> --destination <isolated-package-dir> \
  --summary <isolated-reports>/prepare.json
```

The source must be an original successful attempt of the existing C research
workflow. Artifact identity, expiry, official digest, transport/index, all
referenced Cold data and original Manifest are checked. V1.1 conversion retains
import evidence. Missing/expired dependencies fail; there is no silent fresh
lineage. This uses GET-only source access and does not re-run the source Actions.

An existing local archive can also use the unchanged V1.4 command:

```bash
python -B -m upbit_c.research_release prepare --archive <isolated-archive> \
  --run-id <manifest-run-id> --manifest-sha <external-manifest-sha> \
  --write-package <isolated-package-dir>
python -B -m upbit_c.research_preservation plan --package <package.zip> \
  --sha256 <external-zip-sha256> --summary <isolated-reports>/plan.json
```

`APPROVAL_REQUIRED` is a successful dry-run, **not** verified remote storage.
Keep the original ZIP SHA and local receipt independently before publication.

## Manual Actions and future approved publication

`.github/workflows/upbit-c-preservation.yml` has **workflow_dispatch only**.
No push/PR/schedule triggers are added. Default mode is `dry-run`.

1. Run dry-run with an explicit successful `source_run_id`.
2. Inspect prepared ZIP, original identities, full verification and plan.
3. Save the ZIP SHA externally. Confirm remaining retention and backup plan.
4. Obtain separate user authorization for the concrete Release publication.
5. Only after approval, choose `publish`, supply the externally retained ZIP
   SHA and exact `PUBLISH:<sha256>` approval input.
6. Read-only job must pass. Its ZIP is handed off via same-run Artifact.
7. The write job re-verifies local bytes, re-lists the catalog, performs POSTs
   only if required, downloads the complete uploaded ZIP and audits it again.
8. Retain publication receipt with Release/Asset IDs, SHA and source run ID.

Global permissions are contents:read/actions:read. Only the gated publish job
has contents:write. Checkout credentials are not persisted. Concurrency
serializes preservation runs with cancel-in-progress:false. Existing C/A/B/BTC
workflows are untouched. Action major tags follow the existing repository
practice; SHA pinning is a separate supply-chain hardening task.

A new workflow may not be dispatchable before registration on the default
branch. Do not merge or use a PR-label workaround merely to execute it. Local
read-only source hydration and fixture tests are distinct from an actual
GitHub Actions execution of this new workflow.

Future CLI publication (not run as part of this development):

```bash
python -B -m upbit_c.research_preservation publish --package <package.zip> \
  --sha256 <external-zip-sha256> --target-commit <40-character-commit-sha> \
  --approval PUBLISH:<external-zip-sha256> --summary <isolated-reports>/publication.json
```

Use `--expected-release-id <previous-id>` for a retry when a Release ID was
already observed: missing/deleted/replaced Release then fails instead of being
silently recreated. Without a prior ID pin, absence cannot be distinguished
from a first publication. Keep receipts outside the Release.

## Download, lookup and restoration

```bash
python -B -m upbit_c.research_preservation locate --run-id <run-id>
python -B -m upbit_c.research_preservation locate --original-id scans/<original-id>.json
python -B -m upbit_c.research_preservation download --asset-id <asset-id> \
  --sha256 <externally-saved-zip-sha256> --destination <isolated-export-dir>
python -B -m upbit_c.research_release restore --package <downloaded.zip> \
  --sha256 <externally-saved-zip-sha256> --destination <isolated-restored-archive>
```

Lookup is GET-only and full-verifies managed packages. An observed GitHub
digest is not an independent trust pin. Missing result means NOT_FOUND, not
successful restoration. Asset deletion/HTTP failure/corruption cannot be
repaired by inventing data. Restore verifies ZIP/Manifest/original hashes and
Active State/lineage, preflights conflicts, and never overwrites existing bytes.
Once the self-contained ZIP and external SHA exist, restore needs no expired
Actions Artifact. This is tested by isolated restore; no Artifact is deleted
to simulate expiry in the real repository.

## Failure and retry

- Non-404 tag lookup errors (including 403) abort, not treated as absence.
- POSTs have no automatic retry: network uncertainty returns failure. On a new
  invocation, re-list and download; do not blindly POST again.
- Release created but upload not started: empty same-tag Release can be resumed.
- Starter/partial/different Asset: fail and report; no automatic deletion.
- Uploaded bytes but response/download failed: no verified-publication receipt;
  subsequent invocation can full-verify and return ALREADY_PRESENT.
- GitHub filename uniqueness plus deterministic tag/name handles racing POSTs
  by failure/recheck, not overwrite. Workflow concurrency covers its own runs,
  not arbitrary third-party writers.
- CLI failure emits stage, safe error type/reason/HTTP status, nonzero exit and
  `verified_publication=false`; no token/Authorization/URL exception text.
- Summary and backup publication use immutable local writes. Interrupted output
  has no completion receipt until the operation finishes; retry verifies overlaps.

## Independent backup export

```bash
python -B -m upbit_c.research_preservation backup --package <verified-package.zip> \
  --sha256 <external-zip-sha256> --destination <separate-device-directory>
```

Output: original ZIP, `release-manifest.json`, `SHA256SUMS`,
`backup-receipt.json` with source run, original IDs, hashes, creation clock and
restore command. Payload preflight prevents overwrite; identical replay
preserves the first timestamp. The receipt always says independent disaster
backup `NOT_VERIFIED`: code cannot prove a path uses another physical device.

An operator must verify physical device separation, safely detach/store it,
and test restore using externally retained hashes. D: to D: is only a local
recovery rehearsal. A different drive letter alone does not prove separation.
No cloud account/storage or paid service is configured. Release is one replica,
not the only backup and not immutable storage.

## Capacity and limits

Measured prior real packages: 8,160,143 / 8,167,976 / 16,325,276 bytes.
These overlap originals; summing them as independent daily observations is
incorrect. Assuming **8.16 MB of new disjoint data per scan**, 1/day is about
2.98 GB/year; 4/day about 11.91 GB/year (decimal). These are assumptions, not
measured long-term growth or a quota guarantee. Cumulative full checkpoints
would grow faster and are rejected on overlap by this adapter.

V1.4 limits ZIP and expanded payload below 2GiB, 100k entries; its integrity
checks are retained. V1.5 metadata reads are bounded; catalog and asset pagination
are bounded. Full catalog download dedup grows with retained packages and may
need a reviewed, externally pinned catalog/index in the future. Do not assume
current 40-minute workflow timeout scales indefinitely.

Official contracts: [Release REST API](https://docs.github.com/en/rest/releases/releases),
[Release assets API](https://docs.github.com/en/rest/releases/assets),
[About releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).
GitHub Assets provide state/size/digest and download APIs, but availability,
deletion resistance and preservation duration are not guaranteed by our code.

## Before automatic or regular operation

Separate approval is required for actual publication, independent backup
connection, scheduling and main merge. Before long-term recurring use resolve
the overlapping-checkpoint policy, retained external digest/receipt location,
expiry alarm, full-audit cadence, capacity/restore budget and backup owner.
No C scoring, outcome formula, RESEARCH_ONLY restriction or existing producer
behavior may change as a side effect of storage operations.

## Development verification (2026-10-09)

Base main: `8a27db37a0edce618131f5596cd4daeea4ca249b`, containing PR #27 merge
`1716f56c3961f112de724b768a6497755b7bd3fc`. Only new V1.5 files are added.
Existing A/B/BTC, C research workflows, scores/outcomes and source data are unchanged.
MASTER remains untracked and unmodified.

- Final regression: **984 PASS / 0 FAIL** (B/C 515, BTC Python 389, common 10, Node 70).
  Includes **42 new preservation tests**; no tests disabled.
- All 21 workflow YAML files parsed. New workflow is manual-only, read/write
  job permissions and default dry-run checked; embedded Python syntax compiled.
- Three real V1.1–V1.4 packages full-verified, locally exported/restored. Internal
  package files byte-identical, raw record hashes, Active State and lineage retained.
- Real source Actions run [37892289260](https://github.com/nh1018/upbit-scanner/actions/runs/37892289260)
  hydrated through read-only API: index 18,772 bytes, Cold 16,300,347 bytes.
  Generated V1.4 ZIP 16,325,276 bytes, SHA256
  `5bb5dcd1b385f51b7baa52d3f37c67a141f55e704fa50490fa854bc3ce9e6796`,
  exactly matching the previously preserved package. No market scan/evaluation.
- Actual GitHub Release catalog plans were GET-only, APPROVAL_REQUIRED;
  **actual Releases created 0, Assets uploaded 0**. Publication/retry/error
  scenarios are isolated transport fixtures, not real GitHub upload evidence.
- New preservation workflow itself has not been dispatched on GitHub.
  It is prepared, not claimed production-executed.
- Local restore works without fetching source Artifacts; real source Artifacts
  were not deleted or expired by this test. Export location is the same D: disk,
  so independent backup status stays NOT_VERIFIED.
- `run_id` inside an older package can be an import/no-op Manifest identifier.
  New prepare/publish separately records `source_actions_run_id`; these clocks
  and identities must not be conflated.

Detailed measurements and hashes: `research_audits/preservation_v15_20261009.json`.
Before first actual publication choose the intended checkpoint, retain its SHA
externally and obtain user approval. Repeated overlapping cumulative checkpoint
publication remains blocked; this is a documented limitation, not a dedup success
claim or an invitation to discard Active State.

## First authorized Release attempt: STOPPED (2026-10-09)

Dry-run Actions [37925770419](https://github.com/nh1018/upbit-scanner/actions/runs/37925770419)
completed successfully on main revision `87556e96b5ae0a588b21ffff38a31393a1a9c974`,
but the external SHA publication gate failed:

- Approved original: `5bb5dcd1b385f51b7baa52d3f37c67a141f55e704fa50490fa854bc3ce9e6796`
- Linux Actions ZIP: `594612071cca94d48b147047d19c8889e4f0febed46dd2aa34ed4a9de867aeed`
- ZIP bytes: 16,325,276 in both. All 97 internal files byte-identical.
- Exactly 97 bytes differ: each central-directory `create_system` byte,
  Windows 0 versus Linux 3. Manifest, original records, Active State and lineage
  are identical and full-verify successfully. No market-data change is indicated.

The cause is `research_release.prepare` creating `ZipInfo` without pinning its
OS origin. Python supplies a host-dependent default. The approved Windows ZIP
was deterministic only within that host convention.

A separate fix branch explicitly sets `create_system=0` when creating NEW ZIPs,
retaining the previously approved Windows bytes. Read/verify accepts existing
packages with their own externally pinned SHA; no original ZIP, receipt,
Artifact or provenance is rewritten. Two regressions check explicit origin and
byte equality under simulated Windows/Unix defaults; the latter fails before
the fix. This is a packaging metadata fix, not a source/formula/policy change.

**No publish workflow was dispatched. Releases created 0; Assets uploaded 0.**
No deletion, cleanup, retry publication or main modification occurred. The fix
requires separate review/merge approval. Do not label the production Release
exercise PASS until the approved SHA has passed a fresh Actions dry-run and
actual upload/download/restore verification. The original package approval does
not authorize substituting the Actions-generated different ZIP hash.

Evidence: `research_audits/release_attempt_v15_20261009.json`.

Separate-branch fix verification: 986/986 regression PASS (B/C 517, BTC 389, common 10, Node 70). Locally rebuilding the actual checkpoint with simulated Linux ZipInfo defaults produces a ZIP byte-identical to the approved original and exactly its approved SHA. This is not a new production Actions or Release publication result.

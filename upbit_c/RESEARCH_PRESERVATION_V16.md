# Upbit C V1.6 incremental preservation

## Design contract

RESEARCH_ONLY. V1.5 full ZIP is an immutable baseline. V1.6 stores new original
payloads plus exact source checkpoint/Active State metadata, referencing earlier
payloads by original ID, raw SHA256 and owning package SHA256. It never modifies
originals, recomputes strategy outputs or republishes the baseline.

The catalog is an externally hash-pinned ordered chain. Each increment pins its
baseline and immediate predecessor ZIP SHA, sequence and source Manifest. Same
original ID/hash references existing bytes; different hash or missing original
fails. Source ancestry proofs bridge skipped research checkpoints. A missing
proof, intermediate package, conflict or wrong order is a failure.

DATA contains new originals; METADATA_ONLY contains no new original payload and
retains changed Active/checkpoint metadata. An identical checkpoint is NOOP,
with no ZIP. Active State must still derive from unchanged V1.2 contracts;
fabricating an independent Active change is prohibited.

Inner manifests cannot contain the SHA of their own enclosing ZIP. The sealed
inner package ID and the outer ZIP SHA are separate. External package receipts
and catalog entries contain the ZIP SHA. Retry reuses the same explicit UTC
generation time; this time is preservation metadata, not market availability.

The format reuses existing descriptors, compressed chunks, source Manifests and
Active State bytes. ZIP origin metadata is pinned to 0 as in corrected V1.5.
Restore stages the complete verified dependency chain before immutable output.
Existing V1.5 readers remain unchanged; V1.6 requires its own reader.

## Files and compatibility

- `research_incremental.py`: catalog, strict source ancestry, deterministic delta,
  immutable cache, fully staged restore.
- `research_incremental_release.py`: GET-only plan and explicitly approved future
  publication, complete download verification, idempotent re-list on retry.
- `research_incremental_runner.py`: isolated prepare/source hydration/restore CLI.
- `.github/workflows/upbit-c-incremental-preservation.yml`: manual only, dry-run
  default, read-only verification job, conditional contents-write publication job.

V1.5 ZIP/package, V1.2 Archive/Active, V1.1 imported Manifest bytes and prior
workflows are unchanged. The V1.6 Release tag is `c-incremental-research-<inner ID>`;
it deliberately does not overlap the V1.5 `c-research-` transport namespace.
Old readers continue reading V1.5; they must not interpret a delta as a standalone
checkpoint. V1.6 needs an externally retained catalog and every dependency ZIP.

## Package and catalog contract

`upbit-c-incremental-package-1.6` seals base repository/Release/Asset/hash,
immediate parent ZIP hash, contiguous sequence, explicit UTC generation time,
source Actions run, target source Manifest/run/lineage/Active hashes, new original
ID/raw hash/byte count, old original ownership references, skipped source Manifest
proofs, Active change flags and reused/new file hashes. Target checkpoint run must
equal the explicit source Actions run. Inner `sha256` is a canonical manifest ID,
not the enclosing ZIP hash. The external receipt and catalog bind the ZIP hash.

`upbit-c-preservation-catalog-1.6` seals one V1.5 base plus ordered increments.
Entries include outer SHA, inner ID, sequence, kind, target Manifest, source run,
generation time, ZIP size, Release and Asset IDs. Prepared unpublished entries
have null GitHub IDs; they are not evidence of successful preservation. Only
verified full-download publication emits a catalog with actual IDs. Keep the
exact canonical catalog bytes and their independent SHA256 outside the Artifact
lifecycle. Do not edit an old catalog: retain a new snapshot for every append.

Source ID/hash/size equality allows references, never raw rewrites. An immutable
chunk path whose compressed bytes differ also fails; this adapter does not
re-encode it. Omitted originals, substituted hashes, wrong lineage, missing source
parent proofs, reordered/duplicate packages, untracked remote delta forks,
incomplete assets or conflicting destination files fail closed. Active State is
checked against existing Archive derivation, including when no raw is new.
Metadata-only checkpoints still preserve source identity; exact replay produces
neither a ZIP nor an output directory. No independently fabricated Active signals
are accepted.

## Manual preparation and restoration

Run commands from the repository root. Output/cache/restore paths must be isolated
outside production namespaces. Preserve the externally approved baseline hash:
`5bb5dcd1b385f51b7baa52d3f37c67a141f55e704fa50490fa854bc3ce9e6796`.

1. `python -B -m upbit_c.research_incremental_runner init-catalog` emits an
   **unverified template** for Release407878231/Asset624870458. Save the `catalog`
   object as canonical JSON plus newline and independently retain its file SHA.
   Verification begins only when the actual baseline ZIP is fetched and audited.
2. Prepare a successful source checkpoint (no market scan):

   ```text
   python -B -m upbit_c.research_incremental_runner prepare-source
     --run-id <successful_source_run> --catalog <parent_catalog.json>
     --catalog-sha256 <external_catalog_file_sha256>
     --generation-time <explicit_UTC_ISO_time> --destination <isolated_packages>
     --cache <isolated_hash_checked_cache> --summary <isolated_prepare.json>
   ```

   This hydrates verified source Artifact bytes and, if necessary, original
   intermediate parent indexes. Expired/missing source Artifacts stop preparation.
   Older V1.1 sources require an explicit existing migration before this path.
   `prepare-local` takes `--archive`, `--run-id`, `--manifest-sha256` instead.
3. The new workflow accepts parent canonical catalog JSON/file hash, source run
   and fixed generation time. Default `dry-run` performs read-only remote
   dependency, fork and duplicate inspection. Reuse the same clock for identical
   package bytes. GitHub dispatch registration requires this workflow on the
   default branch; it was **not executed** during this development task.
4. Actual publication is a separate, not-yet-approved operation. It requires
   `mode=publish`, exact ZIP SHA and `PUBLISH:<SHA>`; the implementation validates
   every parent and current remote dependency before any POST. Read-only mode
   cannot mutate Release/Assets. No DELETE/PATCH operation exists.
5. After a future successful publication, retain the returned catalog and its
   external SHA independently. The original catalog remains immutable. For a
   restore, supply this final catalog (or an earlier snapshot to restore earlier):

   ```text
   python -B -m upbit_c.research_incremental_runner restore
     --catalog <target_catalog.json> --catalog-sha256 <external_file_sha256>
     --destination <isolated_empty_or_identical_retry_directory>
     --cache <optional_isolated_verified_cache> --summary <isolated_restore.json>
   ```

All packages are verified in temporary staging before destination writes.
Destination conflicts are preflighted before copying; writes are exclusive and
never replace bytes. A filesystem interruption can leave partial destination
files, but yields no VERIFIED receipt. Retry the identical pinned catalog;
matching files are reused, differing bytes fail. The restored Archive exposes
the original envelopes through `Archive.read_record`, preserving byte equality.
Restoration needs Release ZIPs/catalog/cache only, not expired Actions Artifacts.

## Publication failure procedure

Never remove/re-upload a starter or conflicting asset. On uncertain POST,
permission failure, interruption or full-download mismatch, stop and inspect the
remote catalog. Retrying re-lists: an empty correctly named Release may receive
its first asset; a complete byte-identical asset is ALREADY_PRESENT; a partial or
different asset fails without mutation. Unknown V1.6 forks/stale parent catalogs
require explicit operator investigation. No automatic rollback/delete occurs.
An upload success response alone is never preservation success. Retain the CLI
failure receipt (`remote_success_unconfirmed`) and inspect actual GitHub state.

## Measured validation (2026-10-09)

Audit: `research_audits/preservation_v16_20261009.json`.

**Actual original:** Release407878231/Asset624870458 was downloaded completely,
16,325,276 bytes, approved SHA matched. Both source scan envelopes (46,163,231 and
46,161,119 bytes) restored exactly; Manifest, lineage37885517481 and Active State
matched. Re-preparing source37892289260 was NOOP, 0 ZIP bytes/0 new originals.
Latest successful research checkpoint remains this source; a later failed run
is not usable. There is no actual new increment to claim as production-tested.

Actual cold restore took33.25s including network/local full validation; warm same
checkpoint prepare took20.54s including source/full validation, 0 additional
network bytes. These are different operations, not a controlled CPU speedup
benchmark. Warm verification still reads/hashes/decompresses validated bytes.

**Synthetic only:** V1.5 base plus three increments restored4 originals/1 fixture
signal and exact target Active/lineage. Each added one fixture scan. Delta ZIPs
were7,987/8,858/9,735 bytes versus cumulative full ZIPs9,439/11,410/13,384 bytes
in the measured fixture run. Their tiny-data overhead is not an estimate for
production storage savings. A four-package chain fetched33,764 bytes cold and
0 additional bytes warm; both passes performed inner audits. Timings and exact
hashes are in the audit; fixture data is never published as research outcomes.

## Cost, limits and operational recommendation

With ZIP sizes B,D1..Dn, uncached full dependency validation downloads
`B + sum(Di)` (plus API metadata). This adapter never downloads unrelated Release
binaries simply because the repository has more Releases. Repeated uncached
validation remains O(chain bytes); an immutable hash-keyed cache eliminates
repeat network downloads of verified dependencies, **not** full CPU/inner audit.
Publication always checks current remote dependency IDs/digests, even with cache.
Offline recovery from a complete cache is possible after remote deletion, but
that does not prove the remote copies still exist. Record these claims separately.

Raw payload is preserved once per catalog chain, with exact existing chunks
reused. Current source Manifests/Active plus complete original-reference metadata
are intentionally retained each checkpoint; metadata can still grow with history.
No annual production savings claim is justified without actual new scans.

Limits:128 increments; <2GiB per ZIP;100,000 union files;5GiB staged file bytes;
16MiB inner Manifest/catalog; source ancestry depth128. Temporary staging and
destination require disk capacity; memory/CPU costs rise with chain/source size.
Crossing a bound fails and requires an explicitly approved sharding/new-baseline
plan; no implicit compaction or replacement of old Releases. Keep catalog/hash
receipts independently, periodically validate uncached remote dependencies, and
plan a separately approved physical/cloud backup. Current D: copies are **not**
independent disaster recovery. Artifact expiry, Release deletion and account
access remain operational risks. Release is not immutable guaranteed storage.

## Authorization boundary

No actual V1.6 Release publication, main merge, existing source deletion,
schedule activation or external backup connection is authorized or performed.
Existing A/B/BTC code, C score/outcome logic, originals and workflows are unchanged.

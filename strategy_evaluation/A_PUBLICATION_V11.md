# A publication evidence V1.1

## Scope and existing execution path

`upbit_binance_scanner.py` sets `now` before scanning. Candidate `scan_time_kst`
and payload `generated_at_kst` therefore identify scan start, not completion or
availability. The scanner writes CSV files, `output/latest_scan.json`, then the
history CSV. `.github/workflows/upbit-scanner.yml` verifies basic JSON fields,
commits output/history, and retries push with fetch/rebase up to three times.
Its hourly `:37` schedule, permissions, scanner, output format and scores are unchanged.

Existing artifacts do not expose a trustworthy output-completion or push-attempt
clock. A commit author date and a successful workflow alone do not prove when a
specific output became accessible. This version does not invent those clocks.

## Contract

`a-publication-evidence-1.1` is a manual, prospective, append-only witness contract.
An ARMED_BASELINE records an actual read of current main before the next scan.
It is not a signal. PUBLICATION_OBSERVED requires changed output bytes, a scan
start at/after arming, a pinned main revision, exact Git blob SHA-1 verification,
SHA256, valid A candidate schema, consistent scan generation, and coherent UTC
millisecond clocks. The source response is preserved byte-for-byte as `.source`.

`publication_observed_at_utc_ms` is the local successful Contents API response
receipt clock. It means **the exact pinned result was accessible by this time**.
It is a conservative availability upper bound, not the exact first global
publication instant. API pinning avoids mixing successive main revisions.
Branch head movement after pinning does not change the witnessed bytes.

The receipt separately records scan start, request start and successful read.
`result_generation_completed_at`, `publisher_attempt_at`, and
`publisher_push_succeeded_at` remain null and explicitly unavailable. An API
failure never yields a successful publication receipt or eligible signal.
There is no claim to observe all push attempts, retry timings, or output
completion inside the runner. Capturing those exact clocks would require a
separately approved publisher/workflow integration.

The clock source is the observer PC UTC system clock; use a synchronized clock.
Ordering regressions, future source clocks and mixed generation are rejected.
Absolute clock accuracy cannot be proven by ordering checks alone. Receipt
hashes detect accidental modification; they are not signatures against a
malicious operator who controls the observer and can rewrite all evidence.

## Performance adapter

`signals_from_receipt(session, receipt, raw)` replays all receipt checks against
the immutable baseline and exact source bytes. Existing A candidate IDs, score,
version, price reference and source cutoff are preserved. Observation time is
the successful read; cohort is the new evidence version. Evaluation anchor is
the next full 1H boundary strictly after this observation, via the existing V1
contract. This is a research price proxy, not execution. Historical legacy A
adapter stays ineligible. No old 30-candidate availability is backfilled.

Repeated observations of the same source in a session are no-op and retain the
first receipt. Conflicting bytes for the same scan generation fail. Corrupt or
missing source/events fail closed; no silent new lineage or overwrite occurs.
An interrupted writer lock requires operator inspection, never automatic reset.

## Manual operation (not activated)

Use an explicit isolated metadata directory outside production paths:

```powershell
python -B -m strategy_evaluation.a_publication inspect
python -B -m strategy_evaluation.a_publication arm --record-root D:/repos/a-publication-evidence-v11
# Preserve the printed session ID. Wait for the next normal A scan; do not trigger a retrospective scan.
python -B -m strategy_evaluation.a_publication observe --record-root D:/repos/a-publication-evidence-v11 --session <session-id>
```

`inspect` is the default and writes nothing. Optional `GITHUB_TOKEN` is read
from the environment and never logged. GitHub read access is sufficient. This
module never pushes, dispatches workflows, runs the scanner or evaluates returns.
No automatic workflow, production metadata or retrospective signal is enabled.
Use stored receipts rather than generating later receipt times for evaluation.

An unchanged baseline prints NO_NEW_OUTPUT. Changed output from a scan that
started before arming is rejected. Retry after a later genuinely prospective
scan; never change baseline clocks to make an old scan eligible.

Before automatic production integration, approve where the observer runs, UTC
clock synchronization/monitoring, append-only retention, and whether to add
publisher-side completion/attempt/success events. Any A scanner/workflow edit
and automatic performance recording require separate approval.

## Verification scope

Tests cover publication proof, old-scan rejection, incomplete outputs, blob and
receipt tampering, future clocks, retry no-op, absent/corrupt evidence, API failure,
and unchanged raw input. Manual production inspection is read-only; it does not
constitute verification of a future real prospective publication. A live
armed-to-next-scan witness remains an operational acceptance step.

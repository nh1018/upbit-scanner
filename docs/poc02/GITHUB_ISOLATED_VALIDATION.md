# PoC-02 isolated GitHub verification

Only branch `test/btc-snapshot-poc02-isolated-20261015` is eligible.
Base: `96d2c2050c4b8319c9e6fe09d9225ea2d7f484ea`.

This branch adds PoC-01/02 modules, tests, a compact public evidence index, and
an isolated read-only workflow. No raw archives, account records, local logs,
credentials, or original production files are added or modified.

The evidence index refers to 40 historical Snapshot revisions (12 boundaries)
and 39 immutable Direction/Entry/input files already in repository history.
CI reconstructs them with `git show`, checks hashes, then blocks sockets during
replay through the original Health validators. Missing Git evidence is reported
as NOT_EXECUTED; hash mismatch is a failure, never a successful replay.

An authenticated GET-only check reads the test branch head and current main,
pins the main Snapshot read to that revision, validates Git blob and payload
hash, and reports coherent/BLOCKED state without publishing anything.

Collision, concurrent writer, lost ACK, stale checkout and old-writer rejection
remain simulations using MemoryBackend. A real GitHub write transport is not
implemented or tested. No performance latency improvement is claimed.
The YAML under docs/poc02 is an uninstalled diagnostic design, not a publisher.

Production strategy, workflow, raw, prospective history, main and Bybit are
unchanged. A future production rollout and any PR merge require separate approval.

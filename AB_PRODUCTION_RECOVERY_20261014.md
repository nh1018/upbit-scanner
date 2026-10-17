# Upbit A/B Production freshness recovery — 2026-10-14

## Scope and revision
- Isolated branch: `fix/ab-production-freshness-20261014`.
- Base main: `cef0f9ccf0c0ea10a8cea836456f529ebb5e5db6`.
- API evidence revision: `802b74f6d6022b9e4ef27b8cde5fc4db1a56d556`.
- GET-only inspection; no workflow dispatch, deployment, timer activation or production data writes.

## Confirmed causes
1. Both workflows are active. Over the 48-hour window ending 2026-10-14T12:41:44Z, A has 8 schedule runs (48 expected slots), B has 23 (144 expected slots). Largest observed inter-run gaps: A 498.87 minutes, B 405.47 minutes. Counts measure missing run records, not proven permanent drops or correspondence to individual cron slots. GitHub internal scheduler cause remains unconfirmed.
2. A run 38484053974 collected and verified successfully but failed publication. Logs show two non-fast-forward rejections followed by `cannot lock ref ... is at ... but expected ...`. All three rebases succeeded; retry exhaustion, not a scanner/data failure.
3. B run 38483331218 was cancelled before any jobs were recorded. Concurrency pending replacement is possible but not proven by that API result.
4. Latest successful A run 38531913058 published output generated at 2026-10-14T07:59:09Z (16:59:09 KST); 260 scanned / 295 listed, 30 candidates, Binance OK. Latest successful B run 38547750969 verified the 10:00 UTC cycle. That cycle is COMPLETE with 0 failures and 0 unattempted, 13 insufficient markets. Successful run does not imply current data indefinitely.
5. Health A/B STALE matches their source clocks and unchanged 120-minute limits. During inspection BTC changed from the user's earlier CURRENT to DEGRADED with BTC.DIRECTION_ENTRY_TRANSITION; no BTC code changes were made.
6. Existing PR40 watcher cannot validate the current 1,614,938-byte B journal via Contents API alone: GitHub returns encoding=none and an empty content field for this size. This is a watchdog reader limitation, not corrupt B data.

## Minimal corrections
- `a_scan_publish.py` publishes an already committed output only. Before each of the existing maximum three pushes, fetch/rebase current origin/main. Wait 5/10 seconds between failed pushes, never force-push. Conflicts fail closed. No recollection or score recalculation. The workflow calls this helper after its existing commit.
- Reuse PR40 watchdog implementation directly from commit f3604ffd8c93dac7c28e27cf74853c5855a4f42c (not a second recovery engine). Add exact Git blob lookup when Contents omits the body. Verify Git SHA, metadata size, base64 and bounded decoded size. Limit 2,500,000 bytes; existing 4MB transport/40-request budget remains. Oversized/unverifiable files stay blocked.
- Workflow pins match this proposed checkout, including the modified A publisher workflow. They intentionally do not authorize a main workflow whose bytes differ. No production pin or original PR40 checkout was changed.
- Reuse PR40 read-only Linux validation workflow and include the new publisher/blob tests in its path filters. No new production schedule.

## Tests
- Common/A/health/watchdog: 123 PASS.
- B/C: 582 PASS.
- BTC Python: 389 PASS.
- Total Python: 1,094 PASS / 0 FAIL.
- Worker Node: 70 PASS / 0 FAIL.
- New local bare-Git integration tests reproduce unrelated remote changes and two consecutive publication races, then verify exact output bytes; continuous races, conflicting outputs and dirty checkout are rejected. No GitHub push in tests.
- Live GET pinned B blob: 1,614,938 bytes; SHA256 195cb9823757b4ccfb5c0ef868b0073d55ff9bb8fc1389747b8cbedf2d4935b2; COMPLETE validation succeeds.
- Linux CI is prepared but NOT executed: changes are local and no PR has been opened for this branch.

## Production impact, rollback and approvals
No strategy, score, collection, BTC/Bybit, raw, journal or freshness policy changes. Proposed A publication change adds at most 15 seconds of waiting, plus existing Git operations. It reduces transient contention; continuous contention can still fail. It does not repair missing schedule delivery.
After review/approval, publish a Draft PR and execute Linux CI. Merge/deploy requires separate approval. After any deployment observe real schedule runs, output bytes and publication success; do not claim recovery from fixture tests. Rollback is a revert of the A workflow/helper change and proposed watchdog reader/pins, preserving all outputs and journal history.
An independent timer is still needed to mitigate scheduler gaps; actual dispatch/automatic recovery activation requires explicit separate authorization. The old Windows trial and its failed automatic termination remain disabled; do not reactivate them as part of this change. No backfill of missing B journals.

## Status
PATCH VALIDATED LOCALLY; PRODUCTION RECOVERY NOT YET VERIFIED.

## PR40 integration review
PR40 remains Draft at f3604ffd8c93dac7c28e27cf74853c5855a4f42c. This branch includes that exact implementation with bounded blob decoding, CI path filters and updated workflow pins. It is standalone against main; PR40 does not need to be merged first. Both PRs contain the same 12 paths. Reader, pins and CI differ, so merging both unchanged risks add/add conflicts. Keep PR40 Draft until the user chooses which PR to retain; this work does not close or modify PR40. Strategy engines, B workflow, health, BTC/Bybit, raw and journal files are outside this diff.

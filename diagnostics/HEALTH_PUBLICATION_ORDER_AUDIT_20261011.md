# Production Health publication ordering audit — 2026-10-11

## Scope
Base main: d2a51e3f5315da17749b2804769ae49c0c5a9078; includes PR #38 merge e170224c2a0e88747bb029d1e05e2e359a9b2393. This change is proposed only; no production deployment, dispatch, strategy replay, raw/history rewrite, freshness change or B transition change was performed.

## Actual execution evidence (UTC)
| Run | Observed execution evidence |
| --- | --- |
| [38096997925](https://github.com/nh1018/upbit-scanner/actions/runs/38096997925) | Entry push initially rejected; final commit 324d1d29 published at 00:01:51.976. |
| [38097037095](https://github.com/nh1018/upbit-scanner/actions/runs/38097037095) | Health computed at 00:02:13.038 using Snapshot 23:48:11. Push rejected at 00:02:15, rebased without recalculation, published 583c987b317f4baa7adb3caa3a7f81261c629429 at 00:02:17. Cancelled at 00:02:20, after publication. |
| [38097058073](https://github.com/nh1018/upbit-scanner/actions/runs/38097058073) | Health computed at 00:02:33.155 from transitional Snapshot 00:02:04.949. New Direction 32ec8ca4b84b68ef2b6b4e39929e38d8441bd0cbdba7d6a275c40c1cf9fff84c versus Entry upstream b214c0ce4cf00cf0f10c2f58c4a046a5fd4bd8b9e242a9c289304e51f6474a60. Entry validation used the new Direction and raised `Direction unavailable at evaluation`; Health classified INVALID. Push rejected at 00:02:35, rebased without recalculation; published 328c0bd547e087ebe1ac03560e44069ac1f991d1 at 00:02:37.114. |
| [38097037081](https://github.com/nh1018/upbit-scanner/actions/runs/38097037081) | Coherent Snapshot generated at 00:02:42.218; commit d0da9cd5 published 00:02:52.370 after rebase. |
| [38097073432](https://github.com/nh1018/upbit-scanner/actions/runs/38097073432) | Coherent final Snapshot generated 00:03:22.019; commit 2711ee09c7f724615802d07b7c61dfecd03ed737 published 00:03:33.097. No immediate final Health refresh observed. |

The configured Availability → Direction → Entry → Snapshot → Health chain requires four workflow_run levels. GitHub supports at most three. The API does not expose the exact triggering parent payload for these runs, so the precise suppressed event ancestry cannot be independently asserted. The configuration exceeds the supported depth and cannot guarantee final refresh. Other shallower triggers explain why an earlier Health run can still occur. GITHUB_TOKEN pushes also do not independently trigger push workflows.

Sources: [workflow_run restrictions](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run), [GITHUB_TOKEN event behavior](https://docs.github.com/en/actions/concepts/security/github_token).

## Minimal correction
- Validate Entry against its own sealed upstream Direction. Independently validate both Direction snapshots, Entry evaluation, manifest, source hashes and projected Feature values. No engine replay.
- Legitimate different upstream emits BTC.DIRECTION_ENTRY_TRANSITION; lagging Entry emits BTC.ENTRY_LAGS_MARKET. DEGRADED always remains unusable. Damaged hashes/manifests remain INVALID.
- Final successful Snapshot publication executes the shared Health publisher inside the same job, requiring no additional workflow_run level.
- Both Health paths fetch current main and calculate in an isolated detached worktree. Each derived commit contains only output_system_health/latest.json and records input_repository_commit. A normal non-force push is a compare-and-swap against the fetched parent. On rejection/uncertain response, discard the result, refetch and recalculate (maximum three attempts); never rebase the old Health result.
- Reject changed Health execution code and refuse to overwrite a newer/equal Health generation timestamp. Disable in-progress cancellation for the standalone Health workflow. Existing schedule and permissions are unchanged.

## Verification
- Root/common Python: 88 PASS.
- Upbit B/C Python: 582 PASS.
- BTC Python: 389 PASS.
- Worker Node: 70 PASS.
- Total: 1,129 PASS / 0 FAIL.
- Real failed Snapshot + original immutable evidence replay: DEGRADED, unusable, BTC.DIRECTION_ENTRY_TRANSITION and BTC.ENTRY_LAGS_MARKET. This is historical read-only validation, not a new production record.
- Fixed source fixture: CURRENT → valid mismatched upstream DEGRADED/unusable → coherent CURRENT. Damaged manifest remains INVALID.
- Local bare-Git integration: only Health is committed; raw stays identical; concurrent remote change discards stale calculation; queued old event uses latest recovered source; newer Health survives older clock; persistent push failure is bounded; changed gate code fails closed; temporary worktrees cleaned.
- Linux PR CI: see PR checks; no production run claimed.

## Remaining operational limits
Production post-publication recovery is not yet deployed or proven by a new production run. Before approved deployment, drain any already-running legacy Health publishers: a new implementation cannot retroactively constrain old shell code. Snapshot's existing publisher remains unchanged and may rebase a projection after unrelated commits; Health validates whatever is actually on current main and blocks inconsistency. Concurrent updates can exhaust three retries, failing visibly rather than publishing stale output. A failed Health refresh after Snapshot publication leaves the Snapshot committed; the workflow fails, with standalone upstream/schedule paths retained for recovery. Git worktree checkout costs additional disk/time proportional to repository size. No timing guarantee is made for GitHub scheduled runs; schedule omission remains a separate follow-up.

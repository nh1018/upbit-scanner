# BTC Direction Signal History V1

## Scope and fixed contracts

Direction Engine V1 f3e5ddaffac231b8a59804c22aea4c9994d9f01b is unchanged. Feature/Direction formulas, parameters, raw/provenance, Worker/Pine/collector/Alert/Upbit A V1.3 are protected. No historical signals or performance returns are calculated.

Parameter version initial-hypothesis-1, SHA256 825a53ad1c2276a1596abe29e3456fa0823fdd426488c79a999e3b7f6b52ca18 is pinned. These remain unverified hypothesis strategy parameters.

## Architecture / activation

Independent workflow `BTC Direction Signal History V1` observes latest main after successful `BTC Feature Availability Evidence` workflow completion. Schedule minute12/27/42/57 provides a 15-minute backup; manual workflow dispatch uses the same runner. Workflow-level concurrency queues rather than cancelling. Ingestion workflows and the Worker remain independent.

**This delivery is disabled by a literal false job gate.** Schedule/workflow_run can create skipped runs, but the recording job does not execute. No Actions workflow is manually triggered, no production activation/decision is created. After user approval, Codex can remove the false gate while preserving the success-condition filter. No token, endpoint, Pine or Alert changes are needed. Repository Actions contents-write permission and branch rules must allow normal bot commits.

First enabled execution exclusively creates `output_direction/btc_anytime/v1/activation.json` with actual read time, pinned raw Git revision, and latest existing15m baseline. It records no decision for existing candles. Only a subsequent live observation with candle close after activation and actual received time after activation qualifies. Dry-run initialization is a proposal only, not persisted or operational activation.

## Cadence and non-retrospective policy

The latest newly available completed live15m candle is the decision clock, though its Direction aggregation weight is zero. Each boundary is evaluated at most once successfully. Trigger close/open time differs from actual decision time; decision time is sampled after actual feature generation. The runner does not backdate decision time to candle close.

If multiple boundaries passed while Actions was delayed/down, only the current latest live boundary is evaluated. `skipped_prior_15m_boundaries` records intermediate clock slots for audit; no old snapshot or fake signal is reconstructed. SKIPPED/FAILED attempts may retry while that trigger remains the latest; a successful stored boundary is never recalculated.

## Availability / reproducibility

Raw checkout is committed/tracked and revision-pinned. Existing append-only observation evidence is used. Any new rows are truly read now; that new observation event is kept inline inside the permanent decision, without changing existing provenance/evidence files. Feature manifest and complete synchronized snapshot are retained inside the decision for replay. Generation and snapshot clocks are real current clocks, not inferred source/commit publication times.

Feature dependency availability, registry validation, completed boundaries, schema/version and freshness are inherited. 4H/1H missing/stale/insufficient required features produce operational SKIPPED, not a normal NEUTRAL signal. Valid NEUTRAL from actual weak/conflicting market evidence remains a normal decision. No OI imputation or unit/basis changes.

Late backfill is known only from actual later observation. It can affect subsequent judgments if eligible, but stored past snapshots/decisions remain byte-for-byte unchanged. A backfill row does not become a live15m trigger.

## IDs / append-only files

Namespace:
- activation.json
- decisions/{decision_id}.json
- boundaries/{boundary_id}.json
- operational_events/{event_id}.json

Boundary ID is canonical SHA256 of ledger v1 / market / symbol / timeframe15m / candle open timestamp. Parameter version is deliberately excluded: even parameter changes cannot create a second signal for the same boundary in this ledger. Future parameter rollout requires an explicit policy/version deployment; this runner rejects a changed parameter hash.

Direction engine's original pure decision hash is preserved as `engine_decision_id`. Signal `decision_id` hashes the enriched JSON decision including trigger, actual generated time, manifest/snapshot and audit references. Timestamp-keyed dictionaries are normalized to JSON string keys before hashing/persistence. Identical boundary replay returns the original enriched record, not a newly generated timestamp.

Writes use exclusive create+fsync. Same content replay is no-op; ID/content or boundary conflict fails without overwrite. Decision is written before its boundary index; a crash between them is recovered by locating the existing unique boundary decision and appending only the missing index. More than one decision for a boundary, corrupt identities, or changed index content fail closed.

One exclusive local writer lock excludes simultaneous writes. An abandoned lock is a hard failure rather than guessing another writer has finished; ephemeral Actions workspaces disappear after a crash. This is not a distributed transaction across repositories. Workflow concurrency plus Git same-file add/add conflict rejection provides the remote boundary safety barrier.

Git stage guard allows only new JSON under this namespace. Ordinary push retries with rebase may incorporate disjoint raw commits. Any same-boundary conflict must fail; no force push or manual conflict override. JSON publication is atomic at the Git commit level. If a push ultimately fails, Actions reports failure and unpublished files are not claimed as remote signals. No past-boundary regeneration is attempted to hide publication failure.

## Failure categories

- STORED / DRY_RUN_READY: valid new decision
- REPLAY_NOOP: existing successful boundary
- INITIALIZED / INITIALIZATION_READY / NO_NEW_BOUNDARY: lifecycle, not direction signal
- SKIPPED REQUIRED_EVIDENCE_MISSING / REQUIRED_DATA_STALE / REQUIRED_FEATURES_MISSING
- SKIPPED NON_LIVE_OR_PRESTART_TRIGGER / TRIGGER_NOT_AVAILABLE_OR_STALE
- FAILED CONTRACT_VERSION_MISMATCH / RAW_INPUT_CONTRACT_FAILURE / AVAILABILITY_CONTRACT_FAILURE / SNAPSHOT_FAILURE / DIRECTION_ENGINE_EXCEPTION / LEDGER_WRITE_CONFLICT with stage contract/raw_read/trigger_gate/snapshot/direction/persist and exception type/message
- concurrency lock: hard execution failure
- GitHub publication conflict: failed Actions publish step, not a fabricated decision

Operational JSON is separate from decisions. A corrupt output event/storage failure fails closed. No secret/header/environment dumps or HTTP collector actions are performed.

## Evaluator metadata

Each decision preserves decision time, generated_at, trigger open/close/received/source/raw ref, full input manifest/snapshot, selectedTF candle times, scores/components/confidence/regime/reasons, registry and parameter identity, feature/raw references, unavailable/stale data and actual observed production close references.

Price references are labeled observation anchors, **not execution prices**. Existing official/reference source rows are preserved if actually selected; no official price is fetched or invented solely for an anchor. source/provenance and repository revision enable later audit. The evaluator must separately define future price source, horizon, latency/anchor conventions and MFE/MAE. No future labels or returns exist in this implementation.

## Validation / operational risks

Required tests cover initialization without retrospective signals, first append/replay, content conflict, boundary uniqueness, missing/stale gates, future availability, late backfill, parameter pin, writer lock, crash recovery, raw immutability, engine failure isolation, immutable stored signals under later data and workflow disabled state. Existing246 regressions remain mandatory.

GitHub scheduling is not an exact candle-close timer; workflow queue and availability lag may cause skipped opportunities. Direction latency and skipped boundary metadata must be retained for future evaluation. A partial multi-TF ingestion may be observed; snapshot reflects only data actually available at decision time, never a later correction of that signal. Full snapshots/manifests increase Git storage; V1 prioritizes auditability, with retention/archival a future separate policy.

Before activation: review this document and disabled workflow, confirm bot write/branch permissions, approve enabling, run the first initialization, then verify the next new15m signal and a replay on remote main. No user manual TradingView/Cloudflare action is required. Performance Evaluation design may start; production signal-based evaluation waits for real prospective records and sufficiently mature horizons.

## Remote Actions read-only audit

GitHub public Actions API confirmed the existing BTC Feature Availability Evidence workflow is active and its push runs created at 2026-10-04T03:15:08Z, 03:30:05Z and 03:45:10Z completed successfully. The new workflow remains job-gated and its successful recording/push is not yet claimed. FAILED runner exits nonzero, so its operational JSON remains in the failed workspace/stdout log rather than being published by the later commit step. SKIPPED events can be published normally. API workflow state active means registration, not removal of the explicit false execution gate.

## Final read-only validation

At 2026-10-04T04:02:43.538Z the history runner on main 5d5bc5801cbfc79e7ef9a08699e13b7c99621623 returned INITIALIZATION_READY, decision=null, protected_inputs_unchanged=true, with no activation or decision written. Baseline proposal15m open1791085500000 is not an activated production state. Separate Direction preview returned LONG/ALIGNED_TREND, score 0.454379840530001423, confidence evidence-quality index 0.989637559808612440. Full264 tests passed (Python194 + Node70; previous246 + new18).

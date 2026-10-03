# Append-only archive provenance v1

Original ledgers and original ZIP hashes are immutable. Revalidation creates a new content-addressed JSON event with exclusive creation, never replacing an event. Event IDs are SHA256 of canonical JSON excluding event_id. Repeating verification creates a new timestamped event. Git commits retain earlier events; deletion/replacement is prohibited operationally.

Record explicitly: `python -B -m btc_anytime.provenance --record --commit <original-commit> --day YYYY-MM-DD`. Normal validation is read-only and never creates events. A changed archive without a recorded, intact, matching event fails.

Events pin original commit/file/line and canonical full-row SHA256, original ZIP SHA256, current ZIP and CHECKSUM, current CSV bytes SHA256/size/header/count, retrieval UTC, HTTP Last-Modified/ETag, all 11 persisted kline fields, whole-day 12-field REST comparison, evidence, missing historical information, and validator version. OI remains separately verified by the existing audit.

The original Git blob is re-read and compared to each current complete row. Current official ZIP/CHECKSUM, used rows, completed timestamps, full-day boundaries, and REST are revalidated on every audit. All mandatory evidence must pass. An event is no permission to ignore later changes: fresh hashes and comparison results must match the recorded event or a new event is required.

Classifications: UNCHANGED; MARKET_DATA_CHANGED (used fields differ); ARCHIVE_REPACKAGED (only with verified original CSV bytes hash supplied); ARCHIVE_REPUBLISHED_WITH_EQUIVALENT_USED_DATA; UNRESOLVED_PROVENANCE_CHANGE. Missing original CSV bytes are explicitly unavailable, never reconstructed. HTTP timestamps do not prove the cause. Failed checks remain FAIL regardless of classification. ZIP checksum filename must match exactly.

2026-10-02: three separate events document 24/6/1 unchanged persisted rows and official REST equality; original observations remain at commit 6969440bb0f2667e5f95cc43c48786dbcc8b3210. This policy does not authorize backfill, Worker deployment, or Alert changes.

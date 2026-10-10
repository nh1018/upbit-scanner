# B Prospective V1.1 — population-first delayed context (DRAFT)

This is an incompatible research contract revision, not activation. V1 files,
discovery16, outcomes engine, signal IDs and strategy formulas remain unchanged.
The V1.1 contract records the previous contract hash and its own new hash.
No activation timestamp or prospective population is created by this release.

## Population before context
`population` calls the existing full-lineage B journal adapter, validates all
16 discovery observations against that lineage, excludes discovery IDs and
preactivation signals, and rejects a strategy/cohort change. Its sealed records
retain the entire immutable signal contract including observation/cutoff,
source journal SHA256/reference and original ID. No evaluation event is required.
`report` independently recomputes the entire roster from supplied verified journals
and refuses missing/reordered members. This guarantees coverage of recorded
eligible signals, not signals from scans that never ran or were never published.

## Delayed context and source vintage
Original source cutoff is retained; feature boundary is floor(cutoff/1h).
Only complete consecutive pre-boundary windows enter unchanged V1 H1/H2/H3
arithmetic. Future response suffixes are ignored, never used for features.
Registration may follow the proxy, but must follow original observation and
response receipt. Do not backdate any clock. Reference close must equal the
original signal reference; mismatch is SOURCE_REVISION_CONFLICT, never overwrite.
Use `context_from_responses` at ingestion: it verifies raw response hashes and
normalizes official Upbit/Binance spot rows. Keep raw responses as sidecars via
the existing isolated append-only mechanism (requires activation approval).

AS_OBSERVED is reserved for demonstrably retained original bytes: this revision
never assigns it merely because receipt precedes the signal. Its collector emits
HISTORICAL_AS_RETRIEVED, SOURCE_REVISION_CONFLICT or UNAVAILABLE. Historical
retrieval can exclude future candles but cannot prove the original source vintage.
Original historical API revisions outside the stored reference price cannot be
proven without the original response bytes; this remains an explicit limitation.

## Attempts and immutability
Population admission starts NOT_ATTEMPTED. Append SUCCESS/PARTIAL/FAILED/CONFLICT
attempts with request/response/completion clocks, source URL/hash, original cutoff,
error and sequential retry number/parent ID. Failed attempts remain visible.
First fully valid SUCCESS context is frozen; a later differing window records
CONFLICT. Earlier partial evidence is retained. The report uses frozen successful
context even if a later attempt fails. Exclusive atomic publication rejects
same-key different bytes; replay is no-op. No workflow/HTTP collector is added.

## Denominators
The entire journal-derived population is the first denominator. Nonoverlap is
selected BEFORE outcome availability, separately for 1/3/7 days with the existing
earliest-signal/market/version/cohort policy. Missing evaluation is NOT_EVALUATED,
not fabricated PENDING. Evaluator-supplied PENDING/MATURED/UNVERIFIABLE are preserved.
Reports show context states/reasons, hypothesis coverage, start clusters,
first-request delay samples and vintage coverage/groups. Existing engine stats
are conditional on actual evaluations; outer population/evaluation coverage
explicitly shows the unevaluated denominator. Gross proxy results are not fills.

## Before activation
Separate approval must finalize the new contract hash, clock procedure, real
activation boundary, original-source evidence eligibility and collection policy.
No automatic execution, A baseline operation or PR36 changes are included.

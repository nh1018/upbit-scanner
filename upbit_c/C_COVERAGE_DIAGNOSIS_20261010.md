# C coverage diagnosis — 2026-10-10

## Scope and verified evidence

Source run **38000031787**; scan
`de520ee78cf806e8aa4a0b4e6d8a5b39be345754f40dd486d07387d6cb448086`.
The original 46,155,404-byte envelope SHA256 is
`8df26d25379386d4717ee29e9f2fcf0da6bcc49ffa51ff646620398820cd65e3`.
Read-only input: the verified local backup's isolated `original-bytes/scans` file.
`research_history.read_record` independently checks its inner record hash.
Base revision: `dc0a532d` (origin/main at investigation start).
No new scan, source request, reconstructed signal or raw mutation was performed.

**294 markets; 159 evaluated; 135 unevaluated; 0 C PASS signals.**
The machine-readable companion [coverage_review_20261010.json](research_audits/coverage_review_20261010.json)
contains every failed market/timeframe, required/acquired counts, all missing
slots, evidence hashes, readiness reasons, confidence and improvement category.

## Market and timeframe denominators

| Recorded condition | Failing timeframes | Distinct affected markets (nonexclusive) | Evidence-based conclusion |
|---|---:|---:|---|
| MISSING_CANDLES | 103 | 100 | All recorded gap slots absent from the received source row timestamps; no-trade possible, not independently confirmed |
| INSUFFICIENT_DATA | 42 | 32 | Every case has an empty official response; accessible history exhausted for that query, listing date unconfirmed |
| STALE_DATA | 11 | 11 | Latest required completed boundary absent in the recorded window |
| INSUFFICIENT_FEATURES | 9 | 9 | Every case has high=low on latest candle; close_location and signed_body_ratio ZERO_DENOMINATOR |

165 failing timeframe windows are **not** 165 markets. The distinct market counts
also overlap and must not be summed to 135.

| TF | Missing | Insufficient history | Stale | Insufficient features |
|---|---:|---:|---:|---:|
| 1h | 98 | 4 | 11 | 7 |
| 4h | 5 | 6 | 0 | 2 |
| 1d | 0 | 32 | 0 | 0 |

Feature failures: KRW-AHT/4h, BIGTIME/1h, BOUNTY/4h, IOST/1h,
NXPC/1h, ONG/1h, OPEN/1h, SKY/1h, TFUEL/1h (all KRW).
These are valid flat-price candles, not proof of corrupt OHLC. Replacing undefined
ratios with invented values would change the Feature/C contract and is not done.

## Existing functionality versus this review

| Existing | Added here |
|---|---|
| research_diagnostics.diagnose: source omission/exhaustion/stale/feature classification; hashes and examples | Reuses diagnose, retains its classification and all evidence; full gap list, required counts and per-market confidence/improvement |
| research_diagnostics.probe_sources: optional new official queries | Not called: new observations cannot retroactively prove original no-trade/listing state |
| coverage.coverage: smoke evidence aggregation | Separate full scan market/window denominators; no reimplementation of feature/score |
| MarketData.window: 3-page bounded collection; WINDOWS 1h=200/4h=150/1d=100 | Page-limit flags and integrity contradictions reported; no collection changes |
| research_scan.scan and evidence.observe: AVAILABLE windows and all 15 C fields | Eligibility unchanged; no relaxation or replacement score |

## Defect and improvement assessment

**No collection defect established by this evidence.** All 42 short windows ended
with an empty page, before the three-page bound; increasing the page limit is not
an evidenced remedy for these cases. Zero earlier response does not prove listing
date. Example KRW-KAIA has 0 daily, 13 hourly, 3 four-hourly records; why its source
history starts there is unconfirmed. No API/validation/clock failure appears among
these 165 recorded failure statuses. The review also checks completed/boundary,
duplicate and sorting contradictions; none were found in the failed windows.

[Upbit's official candle rules](https://global-docs.upbit.com/docs/upbit-quotation-restful-api)
explain that intervals without trades produce no candles. This supports a possible
mechanism for the 103 omissions, but is not trade-history evidence for each slot.
The [day candle specification](https://global-docs.upbit.com/reference/list-candles-days)
uses UTC day boundaries; the existing code uses UTC 00:00 (KST 09:00), not KST midnight.

| Improvement class | Current evidence | Safe next step |
|---|---|---|
| Proven collector defect | None established | No production patch justified |
| Contract/strategy change | Full continuous windows and defined flat-range ratios are mandatory | Discuss separately; never silently relax, impute or use forming candles |
| Market/source limitation | Omitted slots; exhausted accessible history; latest absent | Wait for genuinely new complete history or obtain separately timestamped official probes; no guaranteed expansion count |
| Unresolved | Listing dates, independent trades and historical response corrections absent | Preserve uncertainty; targeted official evidence if separately needed |

The 159 currently evaluable markets are not expanded in this task. A ready trailing
feature segment cannot override the full-window contract. Future history can cure
some warm-ups, while calendar gaps/flat ranges may recur. Neither remedy counts nor
future eligible market totals can be inferred from this one scan.

## Reproduction and tests

`python -B -m upbit_c.coverage_review <original-envelope> --sha256 <above hash> --output <new-file>`
requires the external original hash, validates the inner record and refuses output
overwrite. Tests cover hash gates, read-only input, duplicate markets, forming
clock contradictions, source omission versus no-trade proof, exhaustion versus
listing proof, and write-once output. See the PR test results for full regression.

Production collector, score parameters, Feature calculations, workflows, raw data,
Release assets and A/B/BTC paths remain unchanged. This is diagnosis, not a claim
of C profitability.

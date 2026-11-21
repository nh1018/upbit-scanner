# B Prospective Research V1 — review draft, not activated

This is a research contract proposal, not a trading filter or profitability claim.
No workflow, schedule, strategy parameter, A baseline or PR #36 is changed.
No automatic HTTP client, activation command or production job is introduced.

## Reuse and isolation

- `adapters.b_journals`: full journal lineage/cohort/state-transition verification;
  no rescan, score recomputation, baseline/heartbeat/UNKNOWN reinterpretation.
- `engine.evaluate`, `validate_evaluation`: existing +1/+3/+7d gross long proxy
  outcomes, evidence/boundary checks and PENDING/MATURED/UNVERIFIABLE contracts.
- `aggregate.latest`, `aggregate.aggregate`: duplicate/terminal checks and earliest
  nonoverlap per market/version/cohort/anchor/horizon, before hypothesis grouping.
- `storage.append`: isolated append-only outcome retention. No second outcome engine.
- New `b_prospective.py`: context, eligibility, benchmark and research projections;
  no imports by collector, Feature, Trend/State engine or production workflow.

## Discovery exclusion

`research/b_discovery16_v1.json` pins the exact 16 original matured observations.
Each includes the complete normalized signal contract, original journal SHA256,
first observation time, proxy anchor, prior return/MFE/MAE and a current official
Upbit 1h verification event. All three metrics must match by Decimal value.
The file records the original evaluation cutoff 2026-10-10T02:26:56.743Z.
Its journal hashes are source evidence, not replacements for original journals.
`verify_discovery_sources` verifies the supplied complete lineage against those
observations. Discovery IDs are always excluded from confirmatory statistics.
Previously observed non-discovery signals also remain excluded by activation time.

## Contract hash and future approval

`research/b_prospective_contract_v1.json` has a canonical content hash and an exact
discovery-file byte SHA256. The contract is `DRAFT_NOT_ACTIVATED`; activation clocks,
approval and first eligible new observation are **null/not yet available**.
Creating this draft is not a verification start boundary.

After code/test review and **separate human approval**, a future activation event
must pin this exact contract hash, approval reference, actual contract-recorded
clock, approval clock and a strictly later activation clock. No activation-event
creator is supplied now. `validate_activation` only reads/checks a future event;
a self-declared approval string is not independent proof of human authorization.
The approved event must be retained with the external approval record.
Changing a policy/cohort later requires a new contract and prospective cohort;
no reassignment of already observed outcomes is allowed.

Only original B observations strictly after activation qualify. Their existing
strategy version and cohort must match the pinned discovery version/cohort.
First eligible observation time is derived from original eligible journal records
when they actually arrive, never fabricated during preparation.

## H1 — quote turnover

At cutoff, use 12 consecutive completed Upbit 1h candles. Sum base-market quote
trade amount (KRW) in last6 and preceding6. Ratio=last6/prior6. Strict `>1`, `<1`,
`=1` groups are INCREASE/DECREASE/EQUAL. Prior sum zero, missing/duplicate/forming
or invalid rows => UNAVAILABLE. No epsilon and no fitted cutoff.
Primary descriptive contrast is mean +24h return(INCREASE) minus
mean +24h return(DECREASE), also median, win rate, MFE/MAE and coverage.
INCREASE is not automatically buying pressure; DECREASE is not proof of sustained
turnover. These labels have no trading semantics.

## H2 — pre-signal BTC context

Only official Binance **BTCUSDT spot**, 1h UTC boundaries. Last completed close
divided by close 6/24 hours earlier minus1, times100. Requires7/25 consecutive
completed candles independently. POSITIVE/NEGATIVE/ZERO; otherwise UNAVAILABLE.
No switching to perpetual futures on API failure. The +24h future BTC return is
an outcome-only benchmark, never a pre-signal group. Benchmark per +1/+3/+7d is
same proxy start/end boundary, last close/first open minus1. Simple B minus BTC
percentage-point difference is not hedged PnL or beta-adjusted alpha.
KRW versus USDT and domestic price/FX effects remain confounders.

## H3 — completed price location

Conservative cutoff=floor(original signal source_cutoff/1h), not the later API
retrieval clock. It cannot exceed original signal observation time. Reference P
is last completed Upbit1h close, exactly matching the stored signal price reference.
Requires25 consecutive completed candles for 24h close-to-close return:

- return6/24 = `(P / close[-7/-25] - 1)*100`.
- high distance = `(P / max(high of last24) - 1)*100`.
- range position = `(P-min(low last24))/(max(high last24)-min(low last24))*100`.
- Flat range => range position UNAVAILABLE; no denominator substitution.

H3 uses continuous descriptive Spearman rank associations with return and adverse
magnitude `-MAE`, retaining ties. No high/low threshold search, p-value claim,
posthoc group redefinition or fitted entry rule. H1/H2/H3 missingness is separate.

## Registration clocks and source vintage

Proposed anti-hindsight safeguard for approval: context must be registered after
original observation and **before its NEXT_1H_OPEN_PROXY boundary**. A late manual
run cannot backdate registration. It can still evaluate the original signal via
the common outcome engine, but has no eligible preregistered context for hypotheses.
This narrow window is an explicit design choice requiring review: observations
seconds before an hour can be missed by manual operation. Do not relax it after
seeing outcomes. No automatic recorder is activated by this PR.

Price clocks, signal observation, API receipt, context registration, contract start
and evaluation as-of are separate. Completed close<=cutoff<=observation;
API receipt<=registration<proxy. API bytes received after observation are clearly
labelled as such: they do not prove exact source bytes were available at original
signal time. No historical API vintage is invented. Received timestamps supplied
by an operator are clock evidence, not cryptographic trusted time; production use
needs a reviewed clock/recorder procedure before approval.

`context_from_responses` checks raw response SHA256 and official normalization.
Low-level `context` is a pure trusted-candle fixture/replay API, not a new source
collector. It retains normalized pre-window rows and source evidence; validation
recomputes the context to detect resealed arithmetic tampering. Future suffix
cannot change feature values, though full API evidence hashes may change.

## Classification and clusters

STABLECOIN / GENERAL_ALT / UNKNOWN are separate strata. The initial registry is
empty; default UNKNOWN. A non-UNKNOWN claim requires instrument, category,
official HTTPS evidence URL, evidence SHA256, classified_at and approval reference,
fixed by registration. Do not infer classification from ticker spelling. A reviewed
classification list is required before interpreting stablecoin/alt subgroup results.
Report signal count **and distinct proxy-start count**; distinct timestamps are
time clusters, not statistically independent observations. Different markets and
overlapping horizon paths remain dependent.

## Outcomes, paths and missing data

Use common evaluation for +1/+3/+7d. Prefix diagnostics +1/+3/+6/+12/+24h are
computed only after the shared +1d engine validates a complete 24h path; until
then report PENDING/UNVERIFIABLE, not estimated path results. Endpoint=last completed
prefix close, MFE=max(0,high/anchor-1)*100, MAE=min(0,low/anchor-1)*100. Intrabar
high/low ordering is unknown. All arithmetic Decimal precision34 HALF_EVEN.
Proxy is not an actual fill/first tick; costs/spread excluded, gross research only.
Existing horizon-specific overlap selection occurs before groups, so missing
context, pending outcomes or bad returns cannot free an overlapping slot.

Statistics show input/selected/mature counts, overlap IDs, MATURED-only mean/median/
win rate, MFE/MAE distributions, BTC-relative matched coverage, context missingness
and cluster counts. Missing benchmark => no excess value. PENDING/UNVERIFIABLE
are never losses or zeros. No automatic significance or sample-size threshold.

## Future isolated retention — approval required

Suggested root (not created by this work): `D:/repos/b-prospective-evidence-v1/`:

```
contract/       exact draft bytes plus approved activation event
raw_responses/ <original response SHA256>.json (byte-identical public API body)
contexts/      append-only one original signal per activation, envelope/hash
outcomes/      existing strategy_evaluation.storage events
reports/       explicit manual reports; no automatic publishing
```

`append_response` and `append_context` refuse inactive contracts, repository paths
and symlinked paths. They publish atomically/exclusively: same bytes=>REPLAY_NOOP;
different bytes for an existing context=>conflict. No overwrites/deletes. Persist
raw sidecars and UTC request/receipt metadata before publishing a context; a failed
raw write must stop that registration. An orphan raw sidecar is harmless evidence,
not a signal. Outcomes reference original signal and context hashes separately.
No new production evidence directory or future outcomes are created now.

## Read-only preparation command

```
python -B -m strategy_evaluation.b_prospective --contract strategy_evaluation/research/b_prospective_contract_v1.json --discovery strategy_evaluation/research/b_discovery16_v1.json
```

Outputs draft hash, discovery16 count, null validation_start and production_writes0.
This is inspection only, not activation or collection.

## Approval checklist

1. Review exact draft content/hash and code/tests, including registration deadline.
2. Approve versioned classifications/evidence and actual clock/recording procedure.
3. Separately authorize real activation event and isolated manual evidence root.
4. Record actual contract publication/approval/start clocks in order, then wait for
   a new original journal observation; do not import pre-start observations.
5. Automatic collection/schedule requires an additional approval and implementation.

The current deliverable prepares validation; it does not confirm any hypothesis,
establish a real validation start time, or certify the strategy is profitable.

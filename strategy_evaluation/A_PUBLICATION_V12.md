# Manual A publication clock validation V1.2

## Scope

No Windows clock/service, registry, group policy, firewall, scanner, score,
production workflow or schedule is changed. `inspect` remains a read-only
inventory without NTP or writes. `arm` and `observe` now delegate to the V1.2
manual wrapper and cannot accept a user-supplied PASS or skip clock checks.
Existing V1.1 library/receipt replay remains available and its files are unchanged.
This branch is not an automatically activated observer.

## Clock evidence and equations

Two sequential UDP queries go to independent configured hostnames
`time.windows.com` and `time.cloudflare.com`, requiring distinct resolved IPv4
addresses and valid replies from both. Connected UDP sockets constrain the
response peer. Each query uses a new transmit timestamp as the echoed originate
check; full response bytes and all four UTC Unix timestamps are recorded.

Following the timestamp definitions in [RFC 5905, section 8](https://www.rfc-editor.org/rfc/rfc5905.html#section-8):

- t1: local request send; t2: server receive; t3: server transmit; t4: local receive.
- Offset theta = ((t2 - t1) + (t3 - t4)) / 2, positive when server is ahead.
- Network round trip delta = (t4 - t1) - (t3 - t2).
- Sample radius = delta/2 + root_delay/2 + root_dispersion + server_precision
  + local timestamp uncertainty (16ms).
- Sample error bound = abs(theta) + radius.

All sample bounds must be <=1 second, and their offset intervals must intersect.
No averaging, best-sample selection or silent exclusion of a failed server is
used. Invalid mode/version, leap-unsynchronized state, stratum zero/Kiss-of-Death,
missing/reversed timestamps, wrong originate, damaged packets, negative delay,
negative root delay, delay >500ms, timeout, same IP, disagreement and wall clock
regression fail closed. A monotonic clock detects wall clock steps >50ms.
NTP era is selected nearest the current local era; grossly wrong dates fail the
offset bound. This is not an OS synchronization service.

## Validity around publication

A fresh two-server round runs before the pinned GitHub read and another after.
Both must PASS. Their timestamps must bracket the exact read start/receipt;
total bracket duration must be <=120 seconds and wall/monotonic elapsed must
agree within 50ms. Effective bracket bound is the larger round bound plus
100ppm * bracket duration plus 50ms clock-change allowance; it too must be <=1s.
These are explicit conservative engineering policy assumptions, not calibrated
hardware guarantees. Writes must begin within 10s of the final response and
cannot use a future/expired clock proof. Replay validates the original bracket,
not today's time; fresh clock checks are mandatory for every new capture.

The actual successful GitHub receipt clock remains unchanged. NTP offsets are
metadata only: no subtraction, backdating, retrospective availability or
adjusted performance anchor. The original V1.1 provenance still distinguishes
unknown output completion/push attempt/push success clocks from actual access.

## Versioning and replay

An `a-publication-evidence-1.2` envelope contains the sealed V1.1 publication,
pre/post raw NTP evidence, policy, bracket elapsed time and error bound. The
envelope has its own hash/session ID. NTP numerical evidence uses finite JSON
binary64 values with a separately versioned canonical hash; existing strategy
Decimal canonicalization and calculations are unchanged.

V1.2 files live under `<record-root>/v12`, never overwriting V1.1 files.
`a_publication_v12.signals(session, receipt, raw)` verifies both clock envelopes
and delegates the actual candidate/source checks to V1.1. Candidate IDs, scores,
raw output SHA256 and actual receipt time are preserved; the cohort and outer
receipt reference identify V1.2. A V1.1 session is not silently upgraded.
Same source/session replay preserves the first receipt. Incomplete/corrupt
records, missing source and conflicting same-scan publication fail closed.

## Manual commands

```powershell
python -B -m strategy_evaluation.a_publication inspect
python -B -m strategy_evaluation.a_publication arm --record-root D:/repos/a-publication-evidence-v11
python -B -m strategy_evaluation.a_publication observe --record-root D:/repos/a-publication-evidence-v11 --session <V1.2-envelope-session-ID>
```

Keep the actual printed baseline ID. Wait for the existing next regular A scan;
never dispatch the scanner. NO_NEW_OUTPUT does not generate a receipt. A scan
already started before baseline cannot be retroactively accepted.

## Limitations and acceptance

UDP NTP is **unauthenticated**, not a cryptographic time proof. Malformed or
wrong-originate spoof packets are rejected, but a sophisticated matching forgery,
compromised server/DNS/network, or malicious local operator cannot be ruled out.
Distinct providers/IPs improve corroboration without proving independent clocks.
Bounds assume truthful server root quality and the configured local uncertainty
and drift allowance. DNS uses the OS resolver; UDP replies have a 3s timeout.
No HTTPS Date or alternate source replaces unavailable NTP. Failures leave prior
baseline/receipt bytes unchanged and emit structured clock failure diagnostics.

Offline packet fixtures test failures and replay. Actual PC NTP/baseline results
are recorded in `audits/a_publication_v12_validation_20261010.json`; this does not
prove the next regular scan was observed. Automatic observation, integration or
main merge still require approval. No return is manufactured before maturity.

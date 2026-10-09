"""Isolated full-KRW research scan. No production candidates or writes."""
import time
from collections import Counter
from dataclasses import asdict
from decimal import Decimal, localcontext, ROUND_HALF_EVEN

from upbit_b.feature_contracts import canonical, digest
from upbit_b.features import snapshot
from upbit_b.market_data import HTTPClient, MarketData, universe, DataError
from .evidence import observe
from .research_score import evaluate, VERSION, PARAMETER_SHA256

SCHEMA = "upbit-c-market-research-1"
TFS = ("1h", "4h", "1d")


def candle_record(candle):
    # Keep Decimal lexical representation used by the shared source-input hash.
    return {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(candle).items()}


class ResearchClient(HTTPClient):
    """C-only pacing; shared B client/collector behavior is unchanged."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.requests = 0

    def get(self, base, path, params):
        self.sleep(max(0, self.next_request.get(base, 0) - self.clock()))
        self.requests += 1
        try:
            return super().get(base, path, params)
        finally:
            # Max two logical requests/sec; Retry-After pacing remains honored.
            self.next_request[base] = max(self.next_request.get(base, 0), self.clock() + 0.5)


def scan(client=None, markets=None, batch_index=0, batch_count=1, clock=None, progress=None):
    clock = clock or (lambda: time.time_ns() // 1000000)
    if (type(batch_count) is not int or type(batch_index) is not int
            or not 1 <= batch_count <= 100 or not 0 <= batch_index < batch_count):
        raise ValueError("invalid deterministic batch")
    client = client or ResearchClient()
    started = clock()
    actual, universe_evidence = universe(client)
    valid = sorted(x["market"] for x in actual)
    selected = valid if markets is None else sorted(markets)
    if not selected or len(set(selected)) != len(selected) or set(selected) - set(valid):
        raise ValueError("invalid/duplicate/nonexistent Upbit KRW market")
    selected = selected[batch_index::batch_count]
    md = MarketData(client, cutoff_ms=started)
    results, blocked = [], False
    for market in selected:
        row = {"market": market, "timeframes": {}, "score_status": "NOT_EVALUATED",
               "score": None, "activation": "RESEARCH_ONLY"}
        observations = {}
        for tf in TFS:
            if blocked:
                row["timeframes"][tf] = {"status": "NOT_ATTEMPTED_API_BLOCK", "reason": "418"}
                continue
            try:
                window = md.window("UPBIT", market, tf)
                feature = snapshot(window, clock())
                last = window.candles[-1] if window.candles else None
                status = window.status
                if status == "INCOMPLETE_COVERAGE":
                    status = "STALE_DATA" if last and last.close_ms != started // (last.close_ms-last.open_ms) * (last.close_ms-last.open_ms) else "MISSING_CANDLES"
                item = {"status": status, "reason": window.reason,
                        "last_completed_candle_open_ms": last.open_ms if last else None,
                        "last_completed_candle_close_ms": last.close_ms if last else None,
                        "source_input_sha256": window.input_sha256,
                        "source_evidence": list(window.evidence),
                        "missing_slots": list(window.missing_slots),
                        "candles": [candle_record(c) for c in window.candles],
                        "feature_snapshot": feature}
                if window.status == "AVAILABLE":
                    try:
                        ev = observe(feature)
                        item["research_evidence"] = ev
                        observations[tf] = ev
                        item["status"] = "EVALUATED" if not ev["missing"] else "INSUFFICIENT_FEATURES"
                    except (ValueError, TypeError, KeyError) as exc:
                        item.update(status="INVALID_FEATURE_EVIDENCE", reason=str(exc))
                if window.reason and "418" in window.reason:
                    blocked = True
            except (DataError, ValueError, TypeError, KeyError, OSError) as exc:
                item = {"status": "VALIDATION_ERROR", "reason": str(exc)}
                if "418" in str(exc):
                    blocked = True
            row["timeframes"][tf] = item
        if len(observations) == 3:
            try:
                # Preserve V0's default arithmetic, independently of caller context.
                with localcontext() as ctx:
                    ctx.prec, ctx.rounding = 28, ROUND_HALF_EVEN
                    row["score"] = evaluate(observations)
                row["score_status"] = "EVALUATED"
            except (ValueError, TypeError, KeyError) as exc:
                row["score_status"], row["reason"] = "INSUFFICIENT_EVIDENCE", str(exc)
        else:
            row["reason"] = "THREE_VALID_TIMEFRAMES_REQUIRED"
        row["collected_at_ms"] = clock()
        row["measurement_sha256"] = digest({"market": market, "score": row["score"],
            "inputs": {tf: item.get("source_input_sha256") for tf, item in row["timeframes"].items()}})
        results.append(row)
        md.cache.clear()
        if progress:
            progress(len(results), len(selected), market, row["score_status"])
    finished = clock()
    report = {"schema_version": SCHEMA, "activation": "RESEARCH_ONLY",
        "score_engine_version": VERSION, "parameter_sha256": PARAMETER_SHA256,
        "started_at_ms": started, "finished_at_ms": finished, "source_cutoff_ms": started,
        "universe_size": len(valid), "selected_count": len(selected), "batch_index": batch_index,
        "batch_count": batch_count, "universe_evidence": universe_evidence,
        "success_count": sum(r["score_status"] == "EVALUATED" for r in results),
        "failure_count": sum(r["score_status"] != "EVALUATED" for r in results),
        "failure_reasons": dict(Counter(i["status"] for r in results for i in r["timeframes"].values() if i["status"] != "EVALUATED")),
        "logical_api_requests": getattr(client, "requests", None), "api_blocked": blocked,
        "candidates_created": 0, "entry_signals_created": 0, "results": results}
    report["scan_id"] = digest(report)
    return report

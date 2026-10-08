"""Read-only live Upbit C evidence coverage audit. No scores or candidate calls."""
import argparse
import time
from collections import Counter

from upbit_b.feature_contracts import dumps
from upbit_b.features import snapshot
from upbit_b.market_data import HTTPClient, MarketData, universe
from .evidence import observe, RESEARCH_GROUPS
from .research_score import evaluate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets", required=True, help="comma-separated actual KRW markets")
    args = parser.parse_args(argv)
    client = HTTPClient()
    actual, universe_evidence = universe(client)
    valid = {x["market"] for x in actual}
    markets = args.markets.split(",")
    if not markets or len(set(markets)) != len(markets) or set(markets) - valid:
        raise ValueError("invalid/duplicate/nonexistent Upbit KRW market")
    md = MarketData(client)
    results = []
    research_scores = []
    for market in markets:
        timeframes = {}
        observations = {}
        for tf in ("1h", "4h", "1d"):
            window = md.window("UPBIT", market, tf)
            if window.reason and "418" in window.reason:
                raise RuntimeError("API_BLOCKED_418")
            feature = snapshot(window, time.time_ns() // 1000000)
            if window.status != "AVAILABLE":
                timeframes[tf] = {"status": window.status, "reason": window.reason, "groups": None}
                continue
            try:
                evidence = observe(feature)
                observations[tf] = evidence
                timeframes[tf] = {"status": "EVALUATED", "groups": evidence["groups"],
                                  "missing_count": len(evidence["missing"]),
                                  "source_cutoff_ms": evidence["source_cutoff_ms"]}
            except ValueError as exc:
                timeframes[tf] = {"status": "NOT_EVALUATED", "reason": str(exc), "groups": None}
        results.append({"market": market, "timeframes": timeframes})
        if set(observations) == {"1h", "4h", "1d"}:
            try:
                score = evaluate(observations)
                research_scores.append({"market": market, "score": score["score"],
                                        "research_setup": score["research_setup"],
                                        "gates": score["gates"],
                                        "parameter_sha256": score["parameter_sha256"]})
            except ValueError as exc:
                research_scores.append({"market": market, "research_setup": "INSUFFICIENT_EVIDENCE",
                                        "reason": str(exc)})
        md.cache.clear()
    counts = dict(Counter(v["status"] for row in results for v in row["timeframes"].values()))
    report = {"schema_version": "upbit-c-evidence-smoke-0", "activation": "RESEARCH_ONLY",
              "markets": len(markets), "source_cutoff_ms": md.cutoff_ms,
              "timeframe_status_counts": counts, "results": results,
              "universe_evidence": universe_evidence, "research_scores": research_scores,
              "research_scores_evaluated": sum("score" in x for x in research_scores),
              "scores_created": 0,
              "candidates_created": 0, "production_files_created": 0}
    print(dumps(report))
    return report


if __name__ == "__main__":
    main()

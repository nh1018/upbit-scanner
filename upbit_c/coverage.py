"""Aggregate C research evidence coverage without treating missingness as zero."""
from collections import Counter

from .evidence import RESEARCH_GROUPS


def coverage(report):
    if report.get("schema_version") != "upbit-c-evidence-smoke-0" or report.get("activation") != "RESEARCH_ONLY":
        raise ValueError("unrecognized research report")
    expected = report["markets"] * 3
    if not isinstance(report["markets"], int) or report["markets"] < 1:
        raise ValueError("invalid market count")
    counts = Counter()
    group_totals = {tf: {g: {"available": 0, "total": 0, "evaluated": 0}
                         for g in RESEARCH_GROUPS} for tf in ("1h", "4h", "1d")}
    missing = Counter()
    seen = set()
    for row in report["results"]:
        market = row["market"]
        if market in seen:
            raise ValueError("duplicate market")
        seen.add(market)
        if set(row["timeframes"]) != {"1h", "4h", "1d"}:
            raise ValueError("missing timeframe")
        for tf, item in row["timeframes"].items():
            status = item["status"]
            counts[status] += 1
            if status != "EVALUATED":
                missing[(tf, status, item.get("reason") or "UNSPECIFIED")] += 1
                continue
            groups = item["groups"]
            if set(groups) != set(RESEARCH_GROUPS):
                raise ValueError("unexpected group coverage")
            for name, keys in RESEARCH_GROUPS.items():
                n, total = groups[name]["available"], groups[name]["total"]
                if not isinstance(n, int) or not isinstance(total, int) or total != len(keys) or not 0 <= n <= total:
                    raise ValueError("invalid group coverage")
                agg = group_totals[tf][name]
                agg["available"] += n
                agg["total"] += total
                agg["evaluated"] += 1
    if len(seen) != report["markets"] or sum(counts.values()) != expected:
        raise ValueError("incomplete market coverage")
    return {
        "schema_version": "upbit-c-coverage-summary-0",
        "source_cutoff_ms": report["source_cutoff_ms"],
        "market_count": report["markets"],
        "expected_windows": expected,
        "evaluated_windows": counts["EVALUATED"],
        "unevaluated_windows": expected - counts["EVALUATED"],
        "window_status": dict(sorted(counts.items())),
        "group_coverage_by_timeframe": group_totals,
        "unevaluated_reasons": [
            {"timeframe": tf, "status": status, "reason": reason, "count": count}
            for (tf, status, reason), count in sorted(missing.items())
        ],
        "scores_created": 0,
        "candidates_created": 0,
        "activation": "RESEARCH_ONLY",
        "coverage_denominator": "EVALUATED_WINDOWS_ONLY",
    }

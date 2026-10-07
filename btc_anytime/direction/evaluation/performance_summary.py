"""Compact reader-facing summary for BTC Direction Performance Aggregate V1.

This module does not recompute Direction signals or evaluation labels. It reads
the already-produced performance/latest.json and emits a compact subset of the
existing aggregate statistics for operational review.
"""
import argparse
import json
from pathlib import Path

from btc_anytime.features.engine import digest

SCHEMA_VERSION = "btc-direction-performance-summary-v1"
KEEP_DIMENSIONS = {"all", "direction_class", "confidence_bucket", "regime", "direction_regime"}

def _compact_stats(stats):
    if not isinstance(stats, dict):
        return stats
    keep = (
        "count", "matured_count", "partial_count", "source_missing_count",
        "source_conflict_count", "invalid_count", "sign_hit_rate",
        "minimum_move_hit_rate", "directional_return_pct",
        "raw_return_pct", "mfe_pct", "mae_pct", "mfe_mae_ratio_mean",
        "neutral_absolute_return_pct", "neutral_endpoint_within_band_rate",
        "neutral_path_within_band_rate", "neutral_up_band_crossed_rate",
        "neutral_down_band_crossed_rate",
    )
    return {k: stats[k] for k in keep if k in stats}

def build(repo: Path):
    source = repo / "output_direction/btc_anytime/v1/performance/latest.json"
    report = json.loads(source.read_text(encoding="utf-8"))
    cohorts = []
    for row in report.get("cohorts", []):
        p = row.get("partition", {})
        if p.get("dimension") not in KEEP_DIMENSIONS:
            continue
        cohorts.append({
            "partition": p,
            "all_observations": _compact_stats(row.get("all_observations", {})),
            "non_overlapping": _compact_stats(row.get("non_overlapping", {})),
            "warnings": row.get("warnings", []),
        })
    result = {
        "schema_version": SCHEMA_VERSION,
        "source_report_id": report["report_id"],
        "input_label_count": report["input_label_count"],
        "total_states": report.get("total_states", {}),
        "cohorts": cohorts,
    }
    result["summary_id"] = digest(result)
    return result

def write(repo: Path):
    summary = build(repo)
    base = repo / "output_direction/btc_anytime/v1/performance"
    reports = base / "summary_reports"
    reports.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n"
    immutable = reports / (summary["summary_id"] + ".json")
    if immutable.exists():
        if json.loads(immutable.read_text(encoding="utf-8")) != summary:
            raise ValueError("performance summary id collision/corruption")
    else:
        immutable.write_text(serialized, encoding="utf-8")
    latest = base / "summary_latest.json"
    if not latest.exists() or latest.read_text(encoding="utf-8") != serialized:
        latest.write_text(serialized, encoding="utf-8")
    return {"status":"READY","schema_version":SCHEMA_VERSION,"summary_id":summary["summary_id"],
            "input_label_count":summary["input_label_count"],"cohort_count":len(summary["cohorts"]),
            "latest":str(latest.relative_to(repo)),"immutable":str(immutable.relative_to(repo))}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,default=Path.cwd())
    parser.add_argument("--write",action="store_true")
    args=parser.parse_args()
    result=write(args.repo.resolve()) if args.write else build(args.repo.resolve())
    print(json.dumps(result,sort_keys=True,indent=2))
if __name__=="__main__":
    main()

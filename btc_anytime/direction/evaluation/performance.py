"""Deterministic cumulative scorecard from finalized BTC Direction V1 labels.

This module never recomputes Direction or evaluation outcomes. It only validates
already-finalized labels and summarizes them with the existing aggregate logic.
"""
import argparse
import json
from pathlib import Path

from btc_anytime.features.engine import digest
from .aggregate import aggregate
from .production import finalized


SCHEMA_VERSION = "btc-direction-performance-aggregate-v1"
KEEP_DIMENSIONS = {
    "all",
    "direction_class",
    "confidence_bucket",
    "regime",
    "direction_regime",
}


def build(repo: Path):
    envelopes = finalized(repo)
    labels = [envelopes[key]["payload"] for key in sorted(envelopes)]
    full = aggregate(labels)

    cohorts = []
    for row in full["cohorts"]:
        partition = json.loads(row["partition"])
        if partition.get("dimension") not in KEEP_DIMENSIONS:
            continue
        cohorts.append({
            "partition": partition,
            "all_observations": row["all_observations"],
            "non_overlapping": row["non_overlapping"],
            "warnings": row["warnings"],
        })

    result = {
        "schema_version": SCHEMA_VERSION,
        "source_schema_version": full["schema_version"],
        "evaluator_version": full["evaluator_version"],
        "input_label_count": len(labels),
        "input_label_payload_hashes": sorted(digest(x) for x in labels),
        "total_states": full["total_states"],
        "cohorts": cohorts,
    }
    result["report_id"] = digest(result)
    return result


def write(repo: Path):
    report = build(repo)
    base = repo / "output_direction/btc_anytime/v1/performance"
    reports = base / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    immutable = reports / (report["report_id"] + ".json")
    serialized = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if immutable.exists():
        existing = json.loads(immutable.read_text(encoding="utf-8"))
        if existing != report:
            raise ValueError("performance report id collision/corruption")
    else:
        immutable.write_text(serialized, encoding="utf-8")

    latest = base / "latest.json"
    if not latest.exists() or latest.read_text(encoding="utf-8") != serialized:
        latest.write_text(serialized, encoding="utf-8")

    return {
        "status": "READY",
        "schema_version": SCHEMA_VERSION,
        "report_id": report["report_id"],
        "input_label_count": report["input_label_count"],
        "cohort_count": len(report["cohorts"]),
        "latest": str(latest.relative_to(repo)),
        "immutable": str(immutable.relative_to(repo)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    repo = args.repo.resolve()
    result = write(repo) if args.write else build(repo)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()

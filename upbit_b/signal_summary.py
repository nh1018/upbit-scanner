"""Read-only, deterministic Upbit B candidate summary from an existing verified cycle.

No API calls, no trading, no journal writes. Never treat missing scores as zero.
"""
import argparse
import json
from pathlib import Path

from .history import validate_cycle
from .history_compact import unpack
from . import feature_contracts as F


def summarize(payload):
    if not isinstance(payload, bytes):
        raise TypeError("journal bytes required")
    if not payload.endswith(b"\n"):
        raise ValueError("incomplete journal")
    header = json.loads(payload.split(b"\n", 1)[0])
    if header.get("schema_version") == "upbit-b-history-compact-1":
        payload = unpack(payload)
    manifest, records = validate_cycle(payload)
    candidates = []
    for record in records:
        summary = record["summary"]
        if summary["candidate"] != "TRUE":
            continue
        observation = record.get("observation") or {}
        score = observation.get("score")
        if score is None:
            # A valid candidate without its recorded score is not a ranked signal.
            continue
        candidates.append({
            "market": record["instrument"],
            "state": summary["state"],
            "score": score,
            "chase": summary["chase"],
            "confidence": summary["confidence"],
            "binance_confirmation": summary["confirmation"],
            "observation_id": record["observation_id"],
            "evidence_level": record["evidence_level"],
        })
    from decimal import Decimal
    candidates.sort(key=lambda x: (-Decimal(x["score"]), x["market"]))
    return {
        "schema_version": "upbit-b-signal-summary-1",
        "cycle_id": manifest["cycle_id"],
        "source_cutoff": manifest["source_cutoff"],
        "universe_count": manifest["universe_count"],
        "candidate_count": manifest["candidate_count"],
        "ranked_candidate_count": len(candidates),
        "unranked_candidate_count": manifest["candidate_count"] - len(candidates),
        "candidates": candidates,
        "advice": "OBSERVATION_ONLY_NOT_ENTRY_SIGNAL",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal", type=Path, help="existing hourly JSONL journal")
    args = parser.parse_args(argv)
    result = summarize(args.journal.read_bytes())
    print(F.dumps(result))


if __name__ == "__main__":
    main()

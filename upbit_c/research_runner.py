"""Manual research runner, optional append-only local/artifact storage."""
import argparse
import sys
import time
from pathlib import Path
from upbit_b.feature_contracts import digest, dumps
from .research_scan import scan, ResearchClient
from .research_history import record_scan, read_record, write_once
from .research_outcomes import fetch_outcome, DAY_HORIZONS, SCHEMA as OUTCOME_SCHEMA


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets", help="optional validated KRW subset; default entire current KRW universe")
    parser.add_argument("--batch-index", type=int, default=0)
    parser.add_argument("--batch-count", type=int, default=1)
    parser.add_argument("--output", type=Path, help="isolated research append-only directory")
    parser.add_argument("--evaluate-history", action="store_true")
    parser.add_argument("--outcome-limit", type=int, default=30, help="bounded research observations per invocation")
    parser.add_argument("--outcomes-only", action="store_true")
    args = parser.parse_args(argv)
    if (args.evaluate_history or args.outcomes_only) and not args.output:
        parser.error("history evaluation requires --output")
    if not 1 <= args.outcome_limit <= 100:
        parser.error("outcome-limit must be 1..100")
    if args.output:
        # Refuse protected production namespaces regardless of caller cwd.
        forbidden = {"data", "data_market", "output", "output_upbit_b", "output_direction", "output_entry", "btc_anytime", "upbit_b"}
        if forbidden.intersection(p.lower() for p in args.output.resolve().parts):
            parser.error("research output must be outside protected production namespaces")
    client = ResearchClient()
    summary = {"activation": "RESEARCH_ONLY", "production_files_created": 0}
    if not args.outcomes_only:
        report = scan(client, args.markets.split(",") if args.markets else None,
                      args.batch_index, args.batch_count,
                      progress=lambda i,n,m,s: print(f"C research {i}/{n} {m} {s}", file=sys.stderr) if i % 20 == 0 or i == n else None)
        summary.update({k:report[k] for k in ("scan_id", "universe_size", "selected_count", "success_count", "failure_count", "failure_reasons", "logical_api_requests", "started_at_ms", "finished_at_ms", "parameter_sha256")})
        summary["serialized_bytes"] = len(dumps(report).encode())
        summary["research_setup_pass_count"] = sum(r.get("score") is not None and r["score"]["research_setup"] == "PASS" for r in report["results"])
        if args.output:
            summary["storage"] = record_scan(args.output, report)
        else:
            summary["read_only"] = True
        summary["samples"] = [{"market":r["market"],"score":r["score"]} for r in report["results"] if r["score"]][:3]
    if args.evaluate_history or args.outcomes_only:
        statuses = {}
        paths = sorted((args.output / "signals").glob("*.json"))
        # Matureable outstanding horizons first; pending old records cannot starve newer mature ones.
        cutoff = time.time_ns() // 1000000
        def priority(path):
            item = read_record(path)
            due = any(cutoff >= item["proxy_anchor_open_ms"] + days*86400000 and not (
                args.output / "evaluations" / (digest({"signal_id":item["signal_id"],"days":days,"contract":OUTCOME_SCHEMA})+'.json')).exists()
                for days in DAY_HORIZONS)
            return not due, item["observed_at_ms"], path.name
        paths.sort(key=priority)
        processed = 0
        for path in paths:
            signal = read_record(path)
            ids = {days:digest({"signal_id":signal["signal_id"],"days":days,"contract":OUTCOME_SCHEMA}) for days in DAY_HORIZONS}
            if all((args.output / "evaluations" / (identifier + ".json")).exists() for identifier in ids.values()):
                continue
            if processed >= args.outcome_limit:
                break
            processed += 1
            for days, mature_id in ids.items():
                if (args.output / "evaluations" / (mature_id + ".json")).exists():
                    existing = read_record(args.output / "evaluations" / (mature_id + ".json"))
                    if existing.get("status") != "MATURED" or existing.get("signal_id") != signal["signal_id"]:
                        raise ValueError("invalid existing mature evaluation")
                    statuses["REPLAY_NOOP"] = statuses.get("REPLAY_NOOP",0)+1
                    continue
                outcome = fetch_outcome(client, signal, days, time.time_ns() // 1000000)
                identifier = mature_id if outcome["status"] == "MATURED" else digest(outcome)
                write_once(args.output, "evaluations", identifier, outcome)
                statuses[outcome["status"]] = statuses.get(outcome["status"],0)+1
        summary["outcomes"] = statuses
        summary["observations_considered"] = processed
    print(dumps(summary))
    return summary


if __name__ == "__main__":
    main()

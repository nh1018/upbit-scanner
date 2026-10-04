"""Read-only prospective outcome dry-run. No recording CLI or automation workflow."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from btc_anytime.features.build import load_raw,protected_hashes
from btc_anytime.features.availability import load_observations,evidence_map,now_ms
from btc_anytime.features.engine import digest
from .engine import evaluate,ANCHORS,parameters
from .aggregate import aggregate
from btc_anytime.integrity import utc_ms


def protected(repo):
    hashes=protected_hashes(repo)
    for folder in ("btc_anytime/features","btc_anytime/direction/parameters","metadata_features","output_direction"):
        for path in (repo/folder).rglob("*"):
            if path.is_file():hashes[path.relative_to(repo).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("btc_anytime/direction/engine.py","btc_anytime/direction/history.py"):
        path=repo/name
        if path.exists():hashes[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def dry_run(repo,cutoff=None):
    before=protected(repo);cutoff=now_ms() if cutoff is None else cutoff
    data,refs,_=load_raw(repo)
    decisions=[];events=load_observations(repo)
    activation_path=repo/"output_direction/btc_anytime/v1/activation.json"
    activation=json.loads(activation_path.read_text(encoding="utf-8")) if activation_path.exists() else None
    if activation and activation.get("activation_id")!=digest({k:v for k,v in activation.items() if k!="activation_id"}):
        raise ValueError("activation identity failure")
    for path in sorted((repo/"output_direction/btc_anytime/v1/decisions").glob("*.json")):
        d=json.loads(path.read_text(encoding="utf-8"))
        if path.stem!=d.get("decision_id"):raise ValueError("decision filename mismatch")
        if not activation or d.get("activation_ref")!=activation["activation_id"] or d["trigger_15m"]["candle_open_time_ms"]<=activation["baseline_15m_open_ms"] or utc_ms(d["decision_time_utc"])<=utc_ms(activation["started_at_utc"]):
            raise ValueError("decision not a post-activation prospective publication")
        decisions.append(d)
        if d.get("inline_observation_event"):events.append(d["inline_observation_event"])
    mapping=evidence_map(events,data)
    outcomes=[evaluate(d,data["15m"],mapping["15m"],refs["15m"],cutoff,a,h)
              for d in decisions for a in ANCHORS for h in parameters()["horizons_hours"]]
    report=aggregate(outcomes)
    if before!=protected(repo):raise ValueError("protected production inputs modified")
    return {"mode":"READ_ONLY_NO_PRODUCTION_LABELS","cutoff_ms":cutoff,"decision_count":len(decisions),
            "states":dict(Counter(x["status"] for x in outcomes)),"protected_inputs_unchanged":True,
            "outcomes":outcomes,"report":report}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,default=Path.cwd())
    parser.add_argument("--cutoff-ms",type=int)
    args=parser.parse_args();result=dry_run(args.repo.resolve(),args.cutoff_ms)
    # Reports/labels are returned, never automatically persisted.
    print(json.dumps({k:v for k,v in result.items() if k!="report"},sort_keys=True,indent=2))


if __name__=="__main__":main()

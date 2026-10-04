"""Independent append-only evaluation recording; never recompute Direction."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
from .engine import evaluate,parameters,VERSION,ANCHORS
from .storage import persist,root,NAMESPACE
from .runner import protected
from btc_anytime.features.build import load_raw
from btc_anytime.features.availability import load_observations,evidence_map,now_ms,git_revision
from btc_anytime.features.engine import digest
from btc_anytime.integrity import utc_ms

PARAMETER_HASH="b99621d4847a9f48c19913b5b4d2a28a326f91f22cb9701015fbbf65054a69c9"


def finalized(repo):
    result={}
    directory=root(repo)/"labels"
    for path in sorted(directory.glob("*/*.json")):
        if path.resolve()!=path:raise ValueError("label symlink refused")
        envelope=json.loads(path.read_text(encoding="utf-8"));p=envelope["payload"]
        if envelope.get("envelope_hash")!=digest({k:v for k,v in envelope.items() if k!="envelope_hash"}) or envelope.get("artifact_id")!=path.stem or digest(p)!=path.stem:
            raise ValueError("finalized label integrity failure")
        key=p["evaluation_key"]
        if path.parent.name!=key or key!=digest(p["contract"]) or p.get("return_status")!="MATURED" or p.get("missing_times") or p.get("unavailable_evidence_times"):
            raise ValueError("finalized label key/status failure")
        if key in result:raise ValueError("duplicate finalized natural key")
        result[key]=envelope
    return result


def evaluation_key(d,anchor,hours,p):
    return digest({"decision_id":d["decision_id"],"evaluator_version":VERSION,
        "evaluation_parameter_hash":p["parameter_hash"],"anchor":anchor,
        "anchor_contract_version":p["anchor_contract_version"],"source_contract":p["price_source_contract"],"horizon_hours":hours})


def inputs(repo):
    data,refs,_=load_raw(repo);events=load_observations(repo);decisions=[]
    folder=repo/"output_direction/btc_anytime/v1"
    activation=json.loads((folder/"activation.json").read_text(encoding="utf-8"))
    if activation.get("activation_id")!=digest({k:v for k,v in activation.items() if k!="activation_id"}):raise ValueError("activation integrity failure")
    for path in sorted((folder/"decisions").glob("*.json")):
        d=json.loads(path.read_text(encoding="utf-8"))
        if path.stem!=d.get("decision_id") or d["decision_id"]!=digest({k:v for k,v in d.items() if k!="decision_id"}):raise ValueError("stored decision hash failure")
        if d.get("signal_history_schema_version")!="btc-direction-signal-history-v1" or d.get("activation_ref")!=activation["activation_id"] or d["trigger_15m"]["candle_open_time_ms"]<=activation["baseline_15m_open_ms"] or utc_ms(d["decision_time_utc"])<=utc_ms(activation["started_at_utc"]):raise ValueError("not a prospective stored decision")
        decisions.append(d)
        if d.get("inline_observation_event"):events.append(d["inline_observation_event"])
    return sorted(decisions,key=lambda d:(utc_ms(d["decision_time_utc"]),d["decision_id"])),data,refs,evidence_map(events,data)


def record(repo,write=False):
    before=protected(repo);revision=git_revision(repo);p=parameters()
    if VERSION!="1.0.0" or p["parameter_hash"]!=PARAMETER_HASH:raise ValueError("pinned evaluator contract changed")
    args=["git","--no-optional-locks","-c","safe.directory="+str(repo.resolve())]
    if subprocess.check_output(args+["diff","HEAD","--","data_market/btc_anytime","metadata_features","output_direction/btc_anytime/v1/decisions","output_direction/btc_anytime/v1/activation.json"],cwd=repo):raise ValueError("uncommitted evaluation input")
    decisions,data,refs,evidence=inputs(repo);cutoff=now_ms();existing=finalized(repo)
    counts=Counter();writes=Counter();evaluations=[]
    for d in decisions:
        for anchor in ANCHORS:
            for hours in p["horizons_hours"]:
                key=evaluation_key(d,anchor,hours,p)
                if key in existing:
                    counts["FINALIZED_NOOP"]+=1
                    continue
                outcome=evaluate(d,data["15m"],evidence["15m"],refs["15m"],cutoff,anchor,hours)
                if outcome["evaluation_key"]!=key:raise ValueError("evaluation key contract mismatch")
                counts[outcome["status"]]+=1
                if write:
                    stored=persist(repo,outcome);writes[stored["status"]]+=1
                evaluations.append({"decision_id":d["decision_id"],"evaluation_key":key,"anchor":anchor,"horizon_hours":hours,"status":outcome["status"]})
    after=protected(repo)
    if any(after.get(path)!=hash for path,hash in before.items()):raise ValueError("existing protected file changed")
    extras=set(after)-set(before)
    if any(not path.startswith(NAMESPACE+"/") or not path.endswith(".json") for path in extras):raise ValueError("unexpected new protected file")
    if revision!=git_revision(repo):raise ValueError("input revision changed")
    failed=counts["INVALID"] or counts["SOURCE_CONFLICT"] or writes["SOURCE_CONFLICT"]
    return {"status":"FAILED" if failed else "RECORDED" if writes["APPENDED"] else "NOOP",
        "mode":"RECORD" if write else "READ_ONLY","repository_commit":revision,"cutoff_ms":cutoff,
        "decision_count":len(decisions),"evaluation_parameter_hash":p["parameter_hash"],"counts":dict(counts),
        "storage_counts":dict(writes),"new_files":len(extras),"protected_existing_unchanged":True,"evaluations":evaluations}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--repo",type=Path,default=Path.cwd());parser.add_argument("--record",action="store_true")
    args=parser.parse_args()
    try:result=record(args.repo.resolve(),args.record)
    except Exception as ex:result={"status":"FAILED","exception_name":type(ex).__name__,"message":str(ex)}
    print(json.dumps(result,sort_keys=True,indent=2))
    raise SystemExit(1 if result["status"]=="FAILED" else 0)


if __name__=="__main__":main()

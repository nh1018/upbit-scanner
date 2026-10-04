"""Read-only Entry preview; no recording flag, workflow or production activation."""
import argparse
import json
from pathlib import Path
from btc_anytime.direction.evaluation.runner import protected
from btc_anytime.features.build import load_raw,build_dataset
from btc_anytime.features.availability import load_observations,evidence_map,observation_event,now_ms,git_revision,mark_generated
from btc_anytime.features.registry import load_registry
from btc_anytime.integrity import utc_ms,DURATIONS
from .engine import digest,NAMES,seal_manifest,direction_ref,evaluate


def dry_run(repo):
    before=protected(repo);revision=git_revision(repo)
    data,refs,files=load_raw(repo);events=load_observations(repo)
    activation=json.loads((repo/"output_direction/btc_anytime/v1/activation.json").read_text(encoding="utf-8"))
    if activation["activation_id"]!=digest({k:v for k,v in activation.items() if k!="activation_id"}):raise ValueError("activation altered")
    decisions=[]
    for path in (repo/"output_direction/btc_anytime/v1/decisions").glob("*.json"):
        d=json.loads(path.read_text(encoding="utf-8"))
        if path.stem!=d["decision_id"] or d["decision_id"]!=digest({k:v for k,v in d.items() if k!="decision_id"}):raise ValueError("Direction altered")
        if d["activation_ref"]!=activation["activation_id"] or d["trigger_15m"]["candle_open_time_ms"]<=activation["baseline_15m_open_ms"]:raise ValueError("not prospective")
        decisions.append(d)
        if d.get("inline_observation_event"):events.append(d["inline_observation_event"])
    observed=now_ms()
    eligible=[d for d in decisions if utc_ms(d["decision_time_utc"])<=observed]
    if not eligible:raise ValueError("no published prospective Direction")
    d=max(eligible,key=lambda x:utc_ms(x["decision_time_utc"]))
    data={tf:[r for r in rows if r["time"]+DURATIONS[tf]<=observed] for tf,rows in data.items()}
    # Current read is real evidence, never a retroactive availability assertion.
    event=observation_event(data,refs,revision,observed,previous=events)
    if event:events.append(event)
    mapping=evidence_map(events,data);_,units=load_registry(repo)
    _,series=build_dataset(data,observed,files,refs,units,revision,mapping)
    generated=now_ms();series=mark_generated(series,generated,"entry-dry-run-in-memory");E=now_ms()
    items=[]
    for tf,count in (("15m",10),("1h",2)):
        raw={r["time"]:r for r in data[tf]}
        for r in [r for r in series[tf] if r["available_at_ms"] is not None and r["available_at_ms"]<=E][-count:]:
            items.append({"timeframe":tf,"time":r["time"],"raw":raw[r["time"]],"raw_ref":refs[tf][r["time"]],
                "available_at_ms":r["available_at_ms"],"generation_time_ms":generated,
                "availability_evidence":mapping[tf][r["time"]],"oi_metadata":r["oi_metadata"],
                "features":{n:r["features"].get(n) for n in NAMES},"quality":{n:r["feature_quality"].get(n,{}) for n in NAMES}})
    manifest=seal_manifest(items,direction_ref(d,observed),generated);result=evaluate(d,manifest,E)
    if before!=protected(repo) or revision!=git_revision(repo):raise ValueError("protected inputs changed during dry-run")
    artifact_count=sum(1 for p in (repo/"output_entry").rglob("*") if p.is_file())
    return {"mode":"READ_ONLY_NO_ENTRY_ACTIVATION","revision":revision,"evaluation":result,
            "selected_inputs":[{"tf":x["timeframe"],"time":x["time"],"features":x["features"],"oi_metadata":x["oi_metadata"],"evidence":x["availability_evidence"]} for x in items],
            "protected_unchanged":True,"production_entry_artifact_count":artifact_count}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--repo",type=Path,default=Path.cwd())
    print(json.dumps(dry_run(parser.parse_args().repo.resolve()),sort_keys=True,indent=2))


if __name__=="__main__":main()

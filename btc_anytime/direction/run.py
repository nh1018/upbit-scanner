"""Production read-only Direction dry-run, no implicit output or evidence writes."""
import argparse
import json
from pathlib import Path
import subprocess
from btc_anytime.features.build import load_raw,build_dataset,protected_hashes
from btc_anytime.features.availability import (now_ms,git_revision,load_observations,observation_event,evidence_map,mark_generated)
from btc_anytime.features.registry import load_registry
from btc_anytime.features.snapshot import synchronize
from btc_anytime.integrity import DURATIONS,iso
from .engine import evaluate,load_parameters


def dry_run(repo):
    protected=protected_hashes(repo)
    feature_hashes={p.as_posix():p.read_bytes() for p in (repo/"btc_anytime/features").rglob("*") if p.is_file()}
    revision=git_revision(repo)
    datasets,refs,files=load_raw(repo)
    observed=now_ms()
    args=["git","--no-optional-locks","-c","safe.directory="+str(repo.resolve())]
    if subprocess.check_output(args+["diff","HEAD","--","data_market/btc_anytime"],cwd=repo):raise ValueError("uncommitted raw input")
    tracked=set(subprocess.check_output(args+["ls-files","--","data_market/btc_anytime"],cwd=repo,text=True).splitlines())
    if not set(files).issubset(tracked):raise ValueError("untracked raw input")
    registry,units=load_registry(repo)
    events=load_observations(repo)
    current=observation_event(datasets,refs,revision,observed,previous=events)
    if current:events=events+[current] # In-memory actual read evidence only; no metadata written.
    mapping=evidence_map(events,datasets)
    manifest,series=build_dataset(datasets,observed,files,refs,units,revision,mapping)
    integrity={}
    for tf,records in series.items():
        times=[r["time"] for r in records]
        integrity[tf]={"rows":len(records),"duplicates":len(times)-len(set(times)),
          "missing_slots":sum(max((b-a)//DURATIONS[tf]-1,0) for a,b in zip(times,times[1:])),
          "boundary_violations":sum(t%DURATIONS[tf]!=0 for t in times),
          "abnormal_intervals":sum(b-a!=DURATIONS[tf] for a,b in zip(times,times[1:])),
          "invalid_rows":sum(not all(r["input_validity"].values()) or r["time"]+DURATIONS[tf]>observed for r in records)}
    if any(v[k] for v in integrity.values() for k in ("duplicates","abnormal_intervals","invalid_rows")):raise ValueError("production raw integrity failure")
    generated=now_ms()
    series=mark_generated(series,generated,"direction-dry-run:"+manifest["manifest_id"]+":"+str(generated))
    decision_time=now_ms()
    snapshot=synchronize(series,decision_time,{"kind":"read_only_direction_dry_run","repository_commit":revision})
    result=evaluate(snapshot,load_parameters())
    direction_generated=now_ms()
    if protected!=protected_hashes(repo):raise ValueError("protected files changed")
    if feature_hashes!={p.as_posix():p.read_bytes() for p in (repo/"btc_anytime/features").rglob("*") if p.is_file()}:raise ValueError("Feature files changed")
    if revision!=git_revision(repo):raise ValueError("revision changed during dry-run")
    return {"status":"PASS","repository_commit":revision,"repository_observed_at_utc":iso(observed),
      "feature_generated_at_utc":iso(generated),"snapshot_decision_time_utc":iso(decision_time),
      "direction_generated_at_utc":iso(direction_generated),
      "registry_id":registry["registry_id"],"manifest_id":manifest["manifest_id"],"raw_integrity":integrity,
      "protected_files_unchanged":True,"feature_files_unchanged":True,"written_files":[],
      "decision":result,"ephemeral_observation_event":current}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,default=Path.cwd())
    args=parser.parse_args()
    print(json.dumps(dry_run(args.repo.resolve()),sort_keys=True,indent=2))


if __name__=="__main__":main()

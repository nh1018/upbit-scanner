"""Observe immutable repository rows; only --record appends separate evidence metadata."""
import argparse
import json
from pathlib import Path
import subprocess
import hashlib
from .availability import (now_ms,git_revision,load_observations,observation_event,evidence_map,
                           append_event,mark_generated,event_id,EVENT_VERSION)
from .build import load_raw,build_dataset,protected_hashes
from .registry import load_registry
from .snapshot import synchronize
from btc_anytime.integrity import iso


def observe(repo,record=False):
    protected=protected_hashes(repo)
    revision=git_revision(repo)
    datasets,refs,files=load_raw(repo)
    observed=now_ms()  # After every row has been read, never derived from a past candle.
    if git_revision(repo)!=revision:raise ValueError("repository revision changed during read")
    changes=subprocess.run(["git","--no-optional-locks","-c","safe.directory="+str(repo.resolve()),
                            "diff","HEAD","--","data_market/btc_anytime"],cwd=repo,capture_output=True,text=True,check=True)
    if changes.stdout:raise ValueError("raw checkout differs from pinned revision")
    tracked=subprocess.run(["git","--no-optional-locks","-c","safe.directory="+str(repo.resolve()),
                            "ls-files","--","data_market/btc_anytime"],cwd=repo,capture_output=True,text=True,check=True)
    if not set(files).issubset(set(tracked.stdout.splitlines())):
        raise ValueError("untracked raw input cannot reference pinned revision")
    committed=subprocess.run(["git","--no-optional-locks","-c","safe.directory="+str(repo.resolve()),
                              "show","-s","--format=%cI",revision],cwd=repo,capture_output=True,text=True,check=True).stdout.strip()
    # Git may express +09:00; preserve its clock as UTC, never use it for availability.
    from datetime import datetime,timezone
    committed=datetime.fromisoformat(committed).astimezone(timezone.utc).isoformat().replace("+00:00","Z")
    previous=load_observations(repo)
    registry,units=load_registry(repo)
    implementation_hashes={p.name:hashlib.sha256(p.read_bytes().replace(b"\r\n",b"\n")).hexdigest()
                           for p in sorted((repo/"btc_anytime/features").glob("*.py"))}
    event=observation_event(datasets,refs,revision,observed,committed,previous)
    new_count=len(event["observations"]) if event else 0
    recovery=False
    if event is None:
        completed=set()
        from .availability import NAMESPACE,validate_generation
        for path in (repo/NAMESPACE/"generation_events").glob("*.json"):
            run=json.loads(path.read_text(encoding="utf-8"));validate_generation(run)
            if path.stem!=run["event_id"]:raise ValueError("generation filename mismatch")
            if run.get("registry_id")==registry["registry_id"] and run.get("implementation_hashes")==implementation_hashes:
                completed.add(run["observation_event_id"])
        pending=[e for e in previous if e["event_id"] not in completed]
        if not pending:return {"status":"PASS","new_observations":0,"written":[],"repository_commit":revision}
        event=max(pending,key=lambda e:e["repository_observed_at_utc"])
        recovery=True
    mapping=evidence_map(previous if recovery else previous+[event],datasets)
    manifest,series=build_dataset(datasets,observed,files,refs,units,revision,mapping)
    if any(not all(r["input_validity"].values()) for records in series.values() for r in records):raise ValueError("invalid raw input")
    # Missing provider intervals remain visible in raw provenance and must not
    # block evidence for subsequent observed completed candles. No synthetic
    # candle is inserted. Invalid observed rows still fail the validity gate.
    generated=now_ms()
    generation_ref="generation-input:"+manifest["manifest_id"]+":"+str(generated)
    series=mark_generated(series,generated,generation_ref)
    decision=now_ms()
    snapshot=synchronize(series,decision,{"kind":"observer_generated_snapshot","observation_event":event["event_id"]})
    anchors={}
    for tf,selection in snapshot["timeframes"].items():
        r=selection["record"]
        anchors[tf]=None if r is None else {"candle_time_utc":r["candle_time_utc"],
            "candle_close_exclusive_utc":r["candle_close_exclusive_utc"],"raw_available_at_ms":r["available_at_ms"],
            "feature_generated_at_ms":r["feature_generated_at_ms"],"input_feature_result_hash":r["input_feature_result_hash"],
            "maximum_ready_dependency_available_at_ms":max((q["available_at_ms"] for q in r["feature_quality"].values() if q["ready"]),default=None),
            "stale":selection["stale"]}
    run={"schema_version":EVENT_VERSION,"kind":"feature_generation","repository_commit":revision,
         "observation_event_id":event["event_id"],
         "manifest_reference":{"manifest_id":manifest["manifest_id"],"manifest_version":manifest["manifest_version"],
                               "parameter_hash":manifest["parameter_hash"],"file_hashes":files},
         "registry_id":registry["registry_id"],"algorithm_version":"1.0.0","feature_schema_version":"btc-feature-v1",
         "implementation_hashes":implementation_hashes,"implementation_hash_basis":"utf8_text_lf",
         "feature_generated_at_utc":iso(generated),"snapshot_decision_time_utc":iso(decision),
         "generation_ref":generation_ref,"snapshot_reference":{"snapshot_id":snapshot["snapshot_id"],"anchors":anchors},
         "latest_result_hashes":{tf:records[-1]["result_hash"] for tf,records in series.items()}}
    run["event_id"]=event_id(run)
    from .availability import validate_generation
    validate_generation(run)
    if protected!=protected_hashes(repo):raise ValueError("protected files changed during observation")
    written=[]
    if record:
        written=[str(append_event(repo,event)),str(append_event(repo,run))]
    return {"status":"PASS","repository_commit":revision,"new_observations":new_count,"recovered_generation":recovery,
            "repository_observed_at_utc":iso(observed),"feature_generated_at_utc":iso(generated),
            "snapshot_decision_time_utc":iso(decision),"written":written,"registry_id":registry["registry_id"]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,default=Path.cwd())
    parser.add_argument("--record",action="store_true")
    args=parser.parse_args()
    print(json.dumps(observe(args.repo.resolve(),args.record),sort_keys=True,indent=2))


if __name__=="__main__":main()

"""Prospective 15m-clock Direction History; --record writes only new ledger files."""
import argparse
import json
from pathlib import Path
import subprocess
from btc_anytime.features.build import load_raw,build_dataset,protected_hashes
from btc_anytime.features.availability import now_ms,git_revision,load_observations,observation_event,evidence_map,mark_generated
from btc_anytime.features.registry import load_registry
from btc_anytime.features.snapshot import synchronize
from btc_anytime.features.engine import digest
from btc_anytime.integrity import DURATIONS,iso,utc_ms
from btc_anytime.direction.engine import evaluate,load_parameters,VERSION
from .storage import root,append_json,boundary_id,indexed_decision,persist,writer_lock

EXPECTED_PARAMETER_HASH="825a53ad1c2276a1596abe29e3456fa0823fdd426488c79a999e3b7f6b52ca18"


def operational(repo,status,reason,revision,details,record):
    event={"schema_version":"btc-direction-operation-v1","status":status,"reason":reason,
      "observed_at_utc":iso(now_ms()),"repository_commit":revision,"details":details}
    event["event_id"]=digest(event)
    if record:append_json(repo,"operational_events/"+event["event_id"]+".json",event)
    return {"status":status,"reason":reason,"operational_event":event,"decision":None}


def execute(repo,record):
    stage="contract";revision=git_revision(repo)
    try:
        parameters=load_parameters()
        if VERSION!="1.0.0" or parameters["parameter_version"]!="initial-hypothesis-1" or parameters["parameter_hash"]!=EXPECTED_PARAMETER_HASH:raise ValueError("pinned direction version/parameter mismatch")
        stage="raw_read";data,refs,files=load_raw(repo);observed=now_ms()
        args=["git","--no-optional-locks","-c","safe.directory="+str(repo.resolve())]
        if subprocess.check_output(args+["diff","HEAD","--","data_market/btc_anytime"],cwd=repo):raise ValueError("raw differs from committed input")
        tracked=set(subprocess.check_output(args+["ls-files","--","data_market/btc_anytime"],cwd=repo,text=True).splitlines())
        if not set(files).issubset(tracked) or revision!=git_revision(repo):raise ValueError("raw revision contract mismatch")
        latest=max(data["15m"],key=lambda r:r["time"]);trigger=latest["time"]
        if trigger%DURATIONS["15m"] or trigger+DURATIONS["15m"]>observed:raise ValueError("uncompleted trigger")
        activation_path=root(repo)/"activation.json"
        if not activation_path.exists():
            activation={"schema_version":"btc-direction-activation-v1","started_at_utc":iso(observed),
              "baseline_15m_open_ms":trigger,"repository_commit":revision,"parameter_hash":parameters["parameter_hash"],
              "policy":"only post-start live observations; no retrospective decisions"}
            activation["activation_id"]=digest(activation)
            if record:append_json(repo,"activation.json",activation)
            return {"status":"INITIALIZED" if record else "INITIALIZATION_READY","activation":activation,"decision":None}
        activation=json.loads(activation_path.read_text(encoding="utf-8"))
        if (activation.get("schema_version")!="btc-direction-activation-v1" or activation.get("activation_id")!=digest({k:v for k,v in activation.items() if k!="activation_id"}) or
            activation.get("parameter_hash")!=EXPECTED_PARAMETER_HASH):raise ValueError("activation contract mismatch")
        started=utc_ms(activation["started_at_utc"])
        if trigger<=activation["baseline_15m_open_ms"] or trigger+DURATIONS["15m"]<=started:
            return {"status":"NO_NEW_BOUNDARY","decision":None}
        key=boundary_id(trigger)
        previous=indexed_decision(repo,key)
        if previous is not None:
            if record:persist(repo,previous) # Also recovers interrupted index creation, never recalculates.
            return {"status":"REPLAY_NOOP","decision":previous}
        stage="trigger_gate"
        received=latest.get("received_at_utc")
        if not received or utc_ms(received)<started:
            return operational(repo,"SKIPPED","NON_LIVE_OR_PRESTART_TRIGGER",revision,{"trigger":trigger},record)
        events=load_observations(repo)
        current=observation_event(data,refs,revision,observed,previous=events)
        if current:events=events+[current] # Real current read, kept inline with the decision for permanent audit.
        mapping=evidence_map(events,data)
        registry,units=load_registry(repo)
        stage="snapshot"
        manifest,series=build_dataset(data,observed,files,refs,units,revision,mapping)
        if any(not all(r["input_validity"][k] for k in ("price","volume","boundary")) for records in series.values() for r in records):raise ValueError("invalid raw history")
        generated=now_ms();series=mark_generated(series,generated,"signal-history-generation:"+manifest["manifest_id"]+":"+str(generated))
        decision_time=now_ms();snapshot=synchronize(series,decision_time,{"kind":"production_signal_history","trigger_boundary_id":key})
        for tf in ("4h","1h"):
            selection=snapshot["timeframes"][tf]
            if selection["record"] is None:return operational(repo,"SKIPPED","REQUIRED_EVIDENCE_MISSING",revision,{"timeframe":tf,"trigger":trigger},record)
            if selection["stale"]:return operational(repo,"SKIPPED","REQUIRED_DATA_STALE",revision,{"timeframe":tf,"trigger":trigger},record)
        clock=snapshot["timeframes"]["15m"]
        if not clock.get("record") or clock["record"]["time"]!=trigger or clock["stale"]:
            return operational(repo,"SKIPPED","TRIGGER_NOT_AVAILABLE_OR_STALE",revision,{"trigger":trigger},record)
        stage="direction"
        decision=evaluate(snapshot,parameters)
        if decision["regime"]=="INSUFFICIENT_EVIDENCE":return operational(repo,"SKIPPED","REQUIRED_FEATURES_MISSING",revision,{"trigger":trigger},record)
        direction_generated=now_ms()
        prior_times=[activation["baseline_15m_open_ms"]]
        for path in (root(repo)/"boundaries").glob("*.json"):
            index=json.loads(path.read_text(encoding="utf-8"))
            if index.get("index_id")!=digest({k:v for k,v in index.items() if k!="index_id"}):raise ValueError("prior boundary index corrupt")
            prior_times.append(index["trigger_time"])
        skipped=list(range(max(prior_times)+DURATIONS["15m"],trigger,DURATIONS["15m"]))
        engine_id=decision.pop("decision_id")
        decision.update(signal_history_schema_version="btc-direction-signal-history-v1",engine_decision_id=engine_id,
          trigger_boundary_id=key,trigger_15m={"candle_open_time_ms":trigger,"candle_close_exclusive_ms":trigger+DURATIONS["15m"],
            "received_at_utc":received,"source":latest.get("source") or "legacy_webhook_inferred","raw_ref":refs["15m"][trigger]},
          generated_at_utc=iso(direction_generated),repository_commit=revision,registry_id=registry["registry_id"],
          activation_ref=activation["activation_id"],input_manifest=manifest,input_snapshot=snapshot,
          inline_observation_event=current,
          skipped_prior_15m_boundaries=skipped,skipped_boundary_policy="not reconstructed retrospectively",
          price_references={tf:{"candle_time_utc":selection["record"]["candle_time_utc"],
              "close":next(r["close"] for r in data[tf] if r["time"]==selection["record"]["time"]),
              "source":selection["record"]["source"],"raw_ref":selection["record"]["raw_ref"],
              "role":"observed production reference, not execution price"}
            for tf,selection in snapshot["timeframes"].items() if selection.get("record")})
        # Canonical JSON object shape, including timestamp-keyed manifest mappings, survives replay.
        decision=json.loads(json.dumps(decision,sort_keys=True))
        decision["decision_id"]=digest(decision)
        if revision!=git_revision(repo):raise ValueError("revision changed during computation")
        stage="persist"
        if record:persist(repo,decision)
        return {"status":"STORED" if record else "DRY_RUN_READY","decision":decision}
    except Exception as ex:
        # No headers/tokens/environment/raw payload dumps. Preserve explicit failure stage/type.
        reason={"contract":"CONTRACT_VERSION_MISMATCH","raw_read":"RAW_INPUT_CONTRACT_FAILURE",
                "trigger_gate":"AVAILABILITY_CONTRACT_FAILURE","snapshot":"SNAPSHOT_FAILURE",
                "direction":"DIRECTION_ENGINE_EXCEPTION","persist":"LEDGER_WRITE_CONFLICT"}[stage]
        return operational(repo,"FAILED",reason,revision,{"stage":stage,"exception_name":type(ex).__name__,"exception_message":str(ex)},record)


def run_history(repo,record=False):
    before=protected_hashes(repo)
    protected_code={p.as_posix():p.read_bytes() for directory in ("btc_anytime/features","btc_anytime/direction/parameters") for p in (repo/directory).rglob("*") if p.is_file()}
    if record:
        with writer_lock(repo):result=execute(repo,True)
    else:result=execute(repo,False)
    if before!=protected_hashes(repo) or protected_code!={p.as_posix():p.read_bytes() for directory in ("btc_anytime/features","btc_anytime/direction/parameters") for p in (repo/directory).rglob("*") if p.is_file()}:raise ValueError("protected inputs changed")
    result["protected_inputs_unchanged"]=True
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--repo",type=Path,default=Path.cwd());parser.add_argument("--record",action="store_true")
    args=parser.parse_args();result=run_history(args.repo.resolve(),args.record)
    print(json.dumps(result,sort_keys=True,indent=2))
    raise SystemExit(1 if result["status"]=="FAILED" else 0)


if __name__=="__main__":main()

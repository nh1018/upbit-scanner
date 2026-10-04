"""Prospective production wiring. Entry/Feature/Direction calculations are untouched."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
from copy import deepcopy
from btc_anytime.direction.evaluation.production import inputs as upstream_inputs
from btc_anytime.direction.evaluation.runner import protected
from btc_anytime.features.build import load_raw,build_dataset
from btc_anytime.features.registry import load_registry
from btc_anytime.features.availability import now_ms,git_revision
from btc_anytime.features.engine import SCHEMA_VERSION,ALGORITHM_VERSION,PARAMETERS
from btc_anytime.integrity import iso,utc_ms,DURATIONS
from .engine import evaluate,parameters,VERSION,NAMES,seal_manifest,direction_ref,digest,STEP
from .storage import append

NAMESPACE="output_entry/btc_anytime/v1"
PARAMETER_HASH="da3b45ac1b3aa2ae0e40e75d570bcab1f9da7ba9296dcb7cae088b82cdd2cff8"
DIRECTION_HASH="825a53ad1c2276a1596abe29e3456fa0823fdd426488c79a999e3b7f6b52ca18"
PUBLICATION_SCHEMA="btc-entry-production-v1"


def root(repo):
    path=repo.resolve()/NAMESPACE
    if path.resolve()!=path:raise ValueError("Entry namespace symlink")
    return path


@contextmanager
def writer(repo):
    directory=root(repo);directory.mkdir(parents=True,exist_ok=True);path=directory/".production.lock"
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.close(fd)
    try:yield
    finally:path.unlink()


def checked(path):
    if path.resolve()!=path:raise ValueError("artifact symlink")
    obj=json.loads(path.read_text(encoding="utf-8"))
    if obj.get("integrity_hash")!=digest({k:v for k,v in obj.items() if k!="integrity_hash"}):raise ValueError("Entry artifact integrity failure")
    return obj


def sealed(value):
    value=deepcopy(value);value["integrity_hash"]=digest(value);return value


def key(activation,t,p):
    # Publication identity has NO generation/observation/evaluation timestamp.
    return digest({"schema":PUBLICATION_SCHEMA,"activation_id":activation["activation_id"],
        "symbol":"BTCUSDT.P","timeframe":"15m","open_time":t,"algorithm":VERSION,"parameter_hash":p["parameter_hash"]})


def inventory(repo,activation,p):
    result={};directory=root(repo)
    # Scan immutable records too: interrupted publication can recover its final index.
    for path in sorted((directory/"records").glob("*.json")):
        record=checked(path);t=record["trigger_time"]
        if record["publication_id"]!=path.stem or key(activation,t,p)!=path.stem or t<=activation["baseline_15m_open_ms"]:raise ValueError("Entry publication key failure")
        if t in result:raise ValueError("duplicate Entry boundary")
        if record["activation_ref"]!=activation["activation_id"]:raise ValueError("activation reference failure")
        result[t]=record
    for path in (directory/"boundaries").glob("*.json"):
        index=checked(path);t=index["trigger_time"]
        if t not in result or path.stem!=result[t]["publication_id"] or index["publication_id"]!=path.stem or index["record_integrity_hash"]!=result[t]["integrity_hash"]:raise ValueError("Entry boundary index conflict")
    return result


def persist(repo,record,manifest=None):
    directory=root(repo);ident=record["publication_id"]
    if record.get("integrity_hash")!=digest({k:v for k,v in record.items() if k!="integrity_hash"}):raise ValueError("record hash failure")
    if manifest is not None:
        # Stable reference by publication identity; volatile generation evidence is audit,
        # protected by integrity hash, never a publication/natural-key component.
        input_artifact=sealed({"schema_version":"btc-entry-production-input-v1","publication_id":ident,"manifest":manifest})
        append(directory/"inputs"/(ident+".json"),input_artifact)
    append(directory/"records"/(ident+".json"),record)
    evaluation=record.get("evaluation")
    if evaluation and evaluation["entry_state"]=="ENTRY_CANDIDATE":
        setup=evaluation["state"]["setup"]
        if setup["lifecycle"]!="CONFIRMED":raise ValueError("candidate lifecycle failure")
        candidate=sealed({"schema_version":"btc-entry-candidate-index-v1","setup_id":setup["setup_id"],
            "confirmation_boundary":record["trigger_time"],"publication_id":ident})
        # A terminal setup may never publish another candidate even at another boundary.
        append(directory/"candidates"/(setup["setup_id"]+".json"),candidate)
    index=sealed({"schema_version":"btc-entry-production-boundary-v1","trigger_time":record["trigger_time"],
        "publication_id":ident,"record_integrity_hash":record["integrity_hash"]})
    return "APPENDED" if append(directory/"boundaries"/(ident+".json"),index) else "REPLAY_NOOP"


def verify(repo,record):
    if not record.get("evaluation"):return {"status":"OPERATIONAL_ONLY"}
    directory=root(repo);bundle=checked(directory/"inputs"/(record["publication_id"]+".json"));manifest=bundle["manifest"]
    dpath=repo/manifest["direction_ref"]["path"];d=json.loads(dpath.read_text(encoding="utf-8"))
    prior=None
    if record.get("previous_publication_id"):
        prior_record=checked(directory/"records"/(record["previous_publication_id"]+".json"));prior=prior_record["evaluation"]
    # Replay freezes original actual clocks/evidence; never uses current Direction or raw.
    replay=evaluate(d,manifest,utc_ms(record["evaluation"]["evaluation_time_utc"]),prior)
    if replay!=record["evaluation"]:raise ValueError("independent deterministic replay differs")
    if manifest["manifest_id"]!=replay["input_manifest_id"]:raise ValueError("manifest reference failure")
    return {"status":"EXACT_MATCH","publication_id":record["publication_id"],"engine_evaluation_id":replay["entry_evaluation_id"]}


def protected_all(repo):
    result=protected(repo)
    for folder in ("btc_anytime/direction","btc_anytime/entry","btc_anytime/features","metadata_features"):
        for path in (repo/folder).rglob("*"):
            if path.is_file():result[path.relative_to(repo).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def committed(repo,revision):
    args=["git","--no-optional-locks","-c","safe.directory="+str(repo.resolve())]
    paths=["data_market/btc_anytime","metadata_features","output_direction/btc_anytime/v1"]
    if subprocess.check_output(args+["diff","HEAD","--"]+paths,cwd=repo):raise ValueError("uncommitted upstream input")
    tracked=set(subprocess.check_output(args+["ls-files","--"]+paths,cwd=repo,text=True).splitlines())
    for name in paths:
        for path in (repo/name).rglob("*"):
            if path.is_file() and path.relative_to(repo).as_posix() not in tracked:raise ValueError("untracked upstream input")
    if git_revision(repo)!=revision:raise ValueError("input revision changed")


def activation(repo,t,observed,revision,p,write):
    path=root(repo)/"activation.json"
    if path.exists():
        a=checked(path)
        if a["activation_id"]!=digest({k:v for k,v in a.items() if k not in ("activation_id","integrity_hash")}):raise ValueError("activation identity failure")
        if a["parameter_hash"]!=PARAMETER_HASH or a["algorithm_version"]!=VERSION or a["feature_parameter_hash"]!=digest(PARAMETERS) or a["direction_parameter_hash"]!=DIRECTION_HASH:raise ValueError("activation contract changed")
        return a,False
    a={"schema_version":"btc-entry-activation-v1","started_at_utc":iso(observed),"baseline_15m_open_ms":t,
       "baseline_close_utc":iso(t+STEP),"repository_revision":revision,"algorithm_version":VERSION,
       "parameter_version":p["parameter_version"],"parameter_hash":p["parameter_hash"],
       "feature_schema":SCHEMA_VERSION,"feature_algorithm":ALGORITHM_VERSION,"feature_parameter_hash":digest(PARAMETERS),
       "direction_schema":"btc-direction-signal-history-v1","direction_algorithm":"1.0.0","direction_parameter_hash":DIRECTION_HASH,
       "policy":"prospective live observations only; baseline excluded; historical reconstruction=0"}
    a["activation_id"]=digest(a);a=sealed(a)
    if write:append(path,a)
    return a,True


def materialize(data,refs,evidence,units,revision,observed,d):
    available={tf:[r for r in rows if r["time"]+DURATIONS[tf]<=observed and r["time"] in evidence[tf] and evidence[tf][r["time"]]["observed_at_ms"]<=observed] for tf,rows in data.items()}
    _,series=build_dataset(available,observed,{},refs,units,revision,evidence)
    generated=now_ms();E=now_ms();items=[]
    for tf,count in (("15m",10),("1h",2)):
        raw={r["time"]:r for r in available[tf]}
        for f in [f for f in series[tf] if f["available_at_ms"] is not None and f["available_at_ms"]<=E][-count:]:
            items.append({"timeframe":tf,"time":f["time"],"raw":raw[f["time"]],"raw_ref":refs[tf][f["time"]],
               "available_at_ms":f["available_at_ms"],"generation_time_ms":generated,"availability_evidence":evidence[tf][f["time"]],
               "features":{n:f["features"].get(n) for n in NAMES},"quality":{n:f["feature_quality"].get(n,{}) for n in NAMES},"oi_metadata":f["oi_metadata"]})
    return seal_manifest(items,direction_ref(d,observed),generated),E


def execute(repo,write=False):
    before=protected_all(repo);revision=git_revision(repo);p=parameters()
    if p["parameter_hash"]!=PARAMETER_HASH or VERSION!="1.0.0":raise ValueError("pinned Entry contract changed")
    committed(repo,revision)
    data,refs,_=load_raw(repo);observed=now_ms()
    complete=[r for r in data["15m"] if r["time"]+STEP<=observed and r.get("received_at_utc") and r.get("source") in (None,"legacy_webhook_inferred","tradingview_binance_usdm_htf")]
    if not complete:raise ValueError("no completed live trigger")
    latest=max(complete,key=lambda r:r["time"]);t=latest["time"]
    a,new=activation(repo,t,observed,revision,p,write)
    if new:return {"status":"INITIALIZED" if write else "INITIALIZATION_READY","activation":a,"evaluation":None,"historical_reconstruction":0}
    if t<=a["baseline_15m_open_ms"] or t+STEP<=utc_ms(a["started_at_utc"]):return {"status":"NO_NEW_BOUNDARY","evaluation":None}
    records=inventory(repo,a,p)
    if t in records:
        stored=records[t];comparison=verify(repo,stored)
        if write:persist(repo,stored)
        return {"status":"REPLAY_NOOP","record":stored,"comparison":comparison,"new_artifacts":0}
    decisions,upstream,uprefs,evidence=upstream_inputs(repo)
    if t not in evidence["15m"] or evidence["15m"][t]["observed_at_ms"]>observed:
        return {"status":"AWAITING_TRIGGER_EVIDENCE","trigger_time":t,"evaluation":None}
    if utc_ms(latest["received_at_utc"])<utc_ms(a["started_at_utc"]):raise ValueError("preactivation live observation")
    # The current repository read proves Direction is known NOW, not at its Git clock.
    eligible=[d for d in decisions if utc_ms(d["decision_time_utc"])<=observed and d["trigger_15m"]["candle_open_time_ms"]<=t]
    d=max(eligible,key=lambda x:(utc_ms(x["decision_time_utc"]),x["decision_id"])) if eligible else None
    prior_results=[r for r in records.values() if r.get("evaluation") and r["trigger_time"]<t]
    previous_record=max(prior_results,key=lambda r:r["trigger_time"]) if prior_results else None
    previous=previous_record["evaluation"] if previous_record else None
    latest_consumed=max([a["baseline_15m_open_ms"]]+list(records));missed=list(range(latest_consumed+STEP,t,STEP))
    ident=key(a,t,p);manifest=None;E=observed
    if d is not None:
        if d["parameter_hash"]!=DIRECTION_HASH:raise ValueError("upstream Direction parameter changed")
        _,units=load_registry(repo);manifest,E=materialize(upstream,uprefs,evidence,units,revision,observed,d)
        evaluation=evaluate(d,manifest,E,previous)
        if evaluation.get("trigger_time")!=t:raise ValueError("Entry did not consume exact live trigger")
    else:evaluation=None
    record=sealed({"schema_version":PUBLICATION_SCHEMA,"publication_id":ident,"activation_ref":a["activation_id"],"trigger_time":t,
        "trigger_close_utc":iso(t+STEP),"repository_revision":revision,"repository_observed_at_utc":iso(observed),
        "source_received_at_utc":latest["received_at_utc"],"trigger_evidence":evidence["15m"][t],
        "direction_repository_observed_at_utc":iso(observed) if d else None,"generated_at_utc":iso(now_ms()),
        "direction_availability_basis":"actual current consumer read; exact earlier publication unavailable",
        "upstream_direction_decision_id":d["decision_id"] if d else None,
        "previous_publication_id":previous_record["publication_id"] if previous_record else None,
        "missed_boundaries":missed,"historical_reconstruction":0,"evaluation":evaluation,
        "operational_status":"SKIPPED_NO_DIRECTION" if d is None else evaluation["execution_status"],
        "input_reference":"inputs/"+ident+".json" if manifest else None,
        "identity_basis":"activation+completed boundary+algorithm+parameters; timestamps excluded; engine ID is audit only"})
    if write:persist(repo,record,manifest)
    if before!=protected_all(repo) or revision!=git_revision(repo):raise ValueError("protected contracts modified")
    status="FAILED" if evaluation and evaluation["execution_status"].startswith("FAILED") else "STORED" if write else "DRY_RUN_READY"
    return {"status":status,"record":record,"input_manifest":manifest,"new_artifacts":2+(1 if manifest else 0)+(1 if evaluation and evaluation["entry_state"]=="ENTRY_CANDIDATE" else 0)}


def run(repo,write=False):
    if write:
        with writer(repo):return execute(repo,True)
    return execute(repo,False)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--repo",type=Path,default=Path.cwd());parser.add_argument("--record",action="store_true")
    args=parser.parse_args()
    try:
        result=run(args.repo.resolve(),args.record);record=result.get("record",{});evaluation=record.get("evaluation") or {}
        print(json.dumps({"status":result["status"],"publication_id":record.get("publication_id"),"trigger_time":record.get("trigger_time"),
            "entry_state":evaluation.get("entry_state"),"reason_codes":evaluation.get("reason_codes"),"activation":result.get("activation"),"new_artifacts":result.get("new_artifacts",0)},sort_keys=True,indent=2))
        raise SystemExit(1 if result["status"]=="FAILED" else 0)
    except Exception as ex:
        print(json.dumps({"status":"FAILED","exception_name":type(ex).__name__,"message":str(ex)}));raise SystemExit(1)


if __name__=="__main__":main()

"""Append-only first-observation and generation evidence, no historical backdating."""
from copy import deepcopy
from datetime import datetime,timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from .engine import digest
from btc_anytime.integrity import DURATIONS,iso,utc_ms

EVENT_VERSION="btc-feature-availability-v1"
NAMESPACE="metadata_features/btc_anytime/v1"


def now_ms():return int(datetime.now(timezone.utc).timestamp()*1000)


def event_id(event):return digest({k:v for k,v in event.items() if k!="event_id"})


def append_event(repo,event):
    root=(repo/NAMESPACE).resolve()
    if root!=repo.resolve()/NAMESPACE:raise ValueError("metadata namespace symlink refused")
    folder=root/("observation_events" if event["kind"]=="repository_observation" else "generation_events")
    if event["kind"]=="repository_observation":validate_observation(event)
    elif event["kind"]=="feature_generation":validate_generation(event)
    else:raise ValueError("unsupported evidence kind")
    identity=event_id(event)
    if event.get("event_id")!=identity:raise ValueError("event identity mismatch")
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/(identity+".json")
    text=json.dumps(event,sort_keys=True,indent=2)+"\n"
    if path.exists():
        if path.read_text(encoding="utf-8")!=text:raise ValueError("existing event altered")
        return path
    # Exclusive creation refuses overwrite. A damaged/incomplete event fails closed on replay.
    with path.open("x",encoding="utf-8") as handle:
        handle.write(text);handle.flush();os.fsync(handle.fileno())
    return path


def load_observations(repo):
    events=[]
    for path in sorted((repo/NAMESPACE/"observation_events").glob("*.json")):
        event=json.loads(path.read_text(encoding="utf-8"))
        validate_observation(event)
        if path.stem!=event["event_id"]:raise ValueError("event filename mismatch")
        events.append(event)
    return events


def validate_observation(event):
    if event.get("schema_version")!=EVENT_VERSION or event.get("kind")!="repository_observation":raise ValueError("observation schema")
    if event.get("event_id")!=event_id(event):raise ValueError("observation hash mismatch")
    observed=utc_ms(event["repository_observed_at_utc"])
    if not re.fullmatch(r"[0-9a-f]{40}",event["repository_commit"]):raise ValueError("invalid repository revision")
    seen=set()
    for item in event["observations"]:
        tf=item["timeframe"];t=item["candle_open_time_ms"];duration=DURATIONS[tf]
        if (tf,t) in seen:raise ValueError("duplicate evidence key")
        seen.add((tf,t))
        if t%duration or item["candle_close_exclusive_ms"]!=t+duration or t+duration>observed:raise ValueError("uncompleted observation")
        if not re.fullmatch(r"[0-9a-f]{64}",item["canonical_row_hash"]):raise ValueError("invalid row identity")
        for field in ("source_received_at_utc","backfill_retrieved_at_utc","oi_retrieved_at_utc"):
            if item.get(field) and utc_ms(item[field])>observed:raise ValueError("source clock later than observation")
        if item["historical_availability_status"]!="unavailable":raise ValueError("historical availability must not be invented")


def observation_event(datasets, references, revision, observed_at, committed_at=None, previous=()):
    """Initial inventory is known-by NOW, never as-of candle close. Later events only new keys."""
    previous_keys={}
    for event in previous:
        validate_observation(event)
        for item in event["observations"]:
            key=(item["timeframe"],item["candle_open_time_ms"])
            if key in previous_keys and previous_keys[key]!=item["canonical_row_hash"]:raise ValueError("prior observation conflict")
            previous_keys[key]=item["canonical_row_hash"]
    observations=[]
    for tf,rows in datasets.items():
        for row in sorted(rows,key=lambda x:x["time"]):
            key=(tf,row["time"]);hashed=digest(row)
            if key in previous_keys:
                if previous_keys[key]!=hashed:raise ValueError("observed raw row changed; do not overwrite evidence")
                continue
            observations.append({"timeframe":tf,"candle_open_time_ms":row["time"],
                "candle_close_exclusive_ms":row["time"]+DURATIONS[tf],"canonical_row_hash":hashed,
                "raw_ref":deepcopy(references.get(tf,{}).get(row["time"],{})),
                "source":row.get("source") or "legacy_webhook_inferred",
                "source_received_at_utc":row.get("received_at_utc"),
                "backfill_retrieved_at_utc":row.get("retrieved_at_utc"),"oi_retrieved_at_utc":row.get("oi_retrieved_at_utc"),
                "historical_availability_status":"unavailable",
                "observation_role":"initial_inventory_known_by_now" if not previous else
                                    "new_live_observation" if row.get("received_at_utc") else "late_reference_observation"})
    if not observations:return None
    result={"schema_version":EVENT_VERSION,"kind":"repository_observation","repository_commit":revision,
            "repository_observed_at_utc":iso(observed_at),"repository_committed_at_utc":committed_at,
            "repository_commit_time_basis":"Git committer clock only; NOT publication/availability evidence",
            "observer":"btc_anytime.features.observe/v1","observations":observations,
            "unavailable_fields":["exact_GitHub_publication_time","historical_first_availability"]}
    result["event_id"]=event_id(result)
    validate_observation(result)
    return result


def evidence_map(events,datasets):
    rows={(tf,r["time"]):digest(r) for tf,rs in datasets.items() for r in rs}
    mapping={tf:{} for tf in datasets}
    for event in events:
        validate_observation(event)
        observed=utc_ms(event["repository_observed_at_utc"])
        for item in event["observations"]:
            tf=item["timeframe"];t=item["candle_open_time_ms"]
            if (tf,t) not in rows:continue
            if rows[(tf,t)]!=item["canonical_row_hash"]:raise ValueError("evidence/raw hash conflict")
            candidate={"kind":"consumer_first_observed","observed_at_ms":observed,"ref":"observation:"+event["event_id"],
                       "repository_commit":event["repository_commit"],"canonical_row_hash":item["canonical_row_hash"],
                       "historical_availability_status":"unavailable"}
            previous=mapping[tf].get(t)
            if previous is None or observed<previous["observed_at_ms"]:mapping[tf][t]=candidate
    return mapping


def mark_generated(series, generated_at, generation_ref):
    """Metadata gate only; indicator values/formulas never change."""
    result=deepcopy(series)
    for records in result.values():
        for record in records:
            if record["available_at_ms"] is not None and generated_at<record["available_at_ms"]:raise ValueError("generation precedes observation")
            record["feature_generated_at_ms"]=generated_at
            record["generation_evidence_ref"]=generation_ref
            for q in record["feature_quality"].values():
                if q["available_at_ms"] is not None:q["available_at_ms"]=max(q["available_at_ms"],generated_at)
            record.pop("result_hash",None)
            record["result_hash"]=digest(record)
    return result


def git_revision(repo):
    return subprocess.run(["git","--no-optional-locks","-c","safe.directory="+str(repo.resolve()),"rev-parse","HEAD"],
                          cwd=repo,check=True,capture_output=True,text=True).stdout.strip()


def validate_generation(event):
    if event.get("schema_version")!=EVENT_VERSION or event.get("kind")!="feature_generation":raise ValueError("generation schema")
    if event.get("event_id")!=event_id(event):raise ValueError("generation hash mismatch")
    generated=utc_ms(event["feature_generated_at_utc"])
    decision=utc_ms(event["snapshot_decision_time_utc"])
    if generated>decision:raise ValueError("snapshot before generation")
    for anchor in event["snapshot_reference"]["anchors"].values():
        if anchor is None:continue
        if (anchor["feature_generated_at_ms"]!=generated or anchor["raw_available_at_ms"]>generated or
            utc_ms(anchor["candle_close_exclusive_utc"])>generated):raise ValueError("invalid generation dependency clock")
        dep=anchor["maximum_ready_dependency_available_at_ms"]
        if dep is not None and dep>decision:raise ValueError("future dependency in snapshot")

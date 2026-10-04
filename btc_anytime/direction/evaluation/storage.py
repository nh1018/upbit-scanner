"""Exclusive immutable artifacts, stable replay; PENDING is never persisted."""
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from btc_anytime.features.engine import digest
from btc_anytime.features.availability import now_ms
from btc_anytime.integrity import iso

NAMESPACE="output_direction/btc_anytime/v1/evaluation"


def root(repo):
    expected=repo.resolve()/NAMESPACE
    if expected.resolve()!=expected:raise ValueError("evaluation symlink refused")
    return expected


def append(repo,kind,value):
    if kind not in ("labels","events") or not re.fullmatch(r"[0-9a-f]{64}",value.get("evaluation_key","")):
        raise ValueError("artifact namespace/key contract")
    identity=digest(value)
    directory=root(repo)/kind/value["evaluation_key"]
    path=directory/(identity+".json")
    if path.resolve()!=path:raise ValueError("artifact symlink refused")
    if path.exists():
        stored=json.loads(path.read_text(encoding="utf-8"))
        if stored.get("envelope_hash")!=digest({k:v for k,v in stored.items() if k!="envelope_hash"}) or stored.get("artifact_id")!=identity or stored.get("payload")!=value or digest(stored["payload"])!=identity:
            raise ValueError("existing artifact corrupted")
        return "NOOP",path
    directory.mkdir(parents=True,exist_ok=True)
    envelope={"artifact_id":identity,"recorded_at_utc":iso(now_ms()),"payload":value}
    envelope["envelope_hash"]=digest(envelope)
    with path.open("x",encoding="utf-8") as f:
        json.dump(envelope,f,sort_keys=True,indent=2);f.write("\n");f.flush();os.fsync(f.fileno())
    return "APPENDED",path


@contextmanager
def writer_lock(repo):
    directory=root(repo);directory.mkdir(parents=True,exist_ok=True)
    path=directory/".writer.lock"
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    try:
        os.write(fd,str(os.getpid()).encode());os.close(fd)
        yield
    finally:
        path.unlink()


def persist(repo,result):
    if result.get("schema_version")!="btc-direction-evaluation-v1" or not re.fullmatch(r"[0-9a-f]{64}",result.get("evaluation_key","")) or result["evaluation_key"]!=digest(result.get("contract")):
        raise ValueError("evaluation key/schema contract")
    if result["status"]=="PENDING":return {"status":"PENDING_NOT_PERSISTED"}
    with writer_lock(repo):return _persist(repo,result)


def _persist(repo,result):
    terminal=result.get("return_status")=="MATURED" and not result.get("missing_times") and not result.get("unavailable_evidence_times")
    folder=root(repo)/"labels"/result["evaluation_key"]
    for path in sorted(folder.glob("*.json")):
        stored=json.loads(path.read_text(encoding="utf-8"))
        if stored.get("envelope_hash")!=digest({k:v for k,v in stored.items() if k!="envelope_hash"}) or stored.get("artifact_id")!=path.stem or digest(stored.get("payload"))!=path.stem:
            raise ValueError("finalized label corrupted")
        if stored["payload"]==result:return {"status":"NOOP","path":str(path)}
        conflict={"schema_version":"btc-evaluation-conflict-v1","evaluation_key":result["evaluation_key"],
                  "status":"SOURCE_CONFLICT","original_label_id":path.stem,"candidate_payload_hash":digest(result),
                  "reason":"finalized content changed; never overwrite"}
        state,p=append(repo,"events",conflict)
        return {"status":"SOURCE_CONFLICT","event_storage":state,"path":str(p)}
    state,path=append(repo,"labels" if terminal else "events",result)
    return {"status":state,"path":str(path)}

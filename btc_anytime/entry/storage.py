"""Exclusive append-only contract. No production recorder or activation entry point."""
import json
import os
import re
from .engine import digest


def identity(value,key):
    ident=value.get(key,"")
    if not re.fullmatch(r"[0-9a-f]{64}",ident) or ident!=digest({k:v for k,v in value.items() if k!=key}):
        raise ValueError("artifact identity mismatch")
    return ident


def append(path,value):
    text=json.dumps(value,sort_keys=True,indent=2)+"\n"
    if path.exists():
        if path.read_text(encoding="utf-8")!=text:raise ValueError("append-only content conflict")
        return False
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("x",encoding="utf-8") as stream:
        stream.write(text);stream.flush();os.fsync(stream.fileno())
    return True


def persist(repo,manifest,result):
    """Library contract for a future approved recorder; tests use temporary repositories.

    Boundary marker is written LAST. An interrupted write can replay the same immutable
    artifacts to finish publication; a divergent replay fails closed.
    """
    mid=identity(manifest,"manifest_id");eid=identity(result,"entry_evaluation_id")
    if result["input_manifest_id"]!=mid:raise ValueError("manifest reference mismatch")
    boundary=result.get("trigger_boundary_id")
    if not boundary or not re.fullmatch(r"[0-9a-f]{64}",boundary):raise ValueError("no publishable boundary")
    if result["entry_state"]=="ENTRY_CANDIDATE":
        setup=result["state"]["setup"]
        if setup["lifecycle"]!="CONFIRMED" or not re.fullmatch(r"[0-9a-f]{64}",setup["setup_id"]):raise ValueError("unconfirmed candidate")
    root=repo.resolve()/"output_entry/btc_anytime/v1"
    if root.resolve()!=root:raise ValueError("namespace symlink refused")
    root.mkdir(parents=True,exist_ok=True)
    lock=root/".writer.lock"
    handle=lock.open("x",encoding="utf-8")
    try:
        marker={"boundary_id":boundary,"evaluation_id":eid,"parameter_hash":result["parameter_hash"]}
        target=root/"boundaries"/(boundary+".json")
        if target.exists() and json.loads(target.read_text(encoding="utf-8"))!=marker:raise ValueError("boundary already published")
        append(root/"inputs"/(mid+".json"),manifest)
        append(root/"evaluations"/(eid+".json"),result)
        if result["entry_state"]=="ENTRY_CANDIDATE":
            setup=result["state"]["setup"]
            append(root/"candidates"/(setup["setup_id"]+".json"),{"setup_id":setup["setup_id"],"evaluation_id":eid})
        created=append(target,marker)
        return "APPENDED" if created else "REPLAY_NOOP"
    finally:
        handle.close()
        # Only our own exclusive lock is removed, never an artifact.
        lock.unlink()

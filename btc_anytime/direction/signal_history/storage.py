"""Append-only boundary index and exclusive local writer lock."""
from contextlib import contextmanager
import json
import os
from btc_anytime.features.engine import digest
from btc_anytime.direction.history import NAMESPACE,append_decision


def root(repo):
    r=(repo/NAMESPACE).resolve()
    if r!=repo.resolve()/NAMESPACE:raise ValueError("history namespace symlink")
    return r


def append_json(repo,relative,value):
    directory=root(repo);path=directory/relative
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.resolve()!=directory/relative:raise ValueError("history path symlink")
    body=json.dumps(value,sort_keys=True,indent=2)+"\n"
    if path.exists():
        if path.read_text(encoding="utf-8")!=body:raise ValueError("append-only content conflict")
        return path
    with path.open("x",encoding="utf-8") as f:f.write(body);f.flush();os.fsync(f.fileno())
    return path


def boundary_id(time):return digest({"market":"BINANCE_USDT_M_FUTURES","symbol":"BTCUSDT.P","timeframe":"15m","candle_open_time":time,"ledger":"direction-signal-history-v1"})


def indexed_decision(repo,key):
    directory=root(repo);index=directory/"boundaries"/(key+".json")
    candidates=[]
    for path in (directory/"decisions").glob("*.json"):
        d=json.loads(path.read_text(encoding="utf-8"))
        if d.get("decision_id")!=digest({k:v for k,v in d.items() if k!="decision_id"}):raise ValueError("stored decision hash failure")
        if path.stem!=d["decision_id"]:raise ValueError("stored decision filename mismatch")
        if d.get("trigger_boundary_id")==key:candidates.append(d)
    if len(candidates)>1:raise ValueError("duplicate stored boundary")
    if index.exists():
        link=json.loads(index.read_text(encoding="utf-8"))
        if link.get("boundary_id")!=key or link.get("index_id")!=digest({k:v for k,v in link.items() if k!="index_id"}):raise ValueError("boundary index hash failure")
        if not candidates or candidates[0]["decision_id"]!=link["decision_id"]:raise ValueError("boundary decision conflict")
    return candidates[0] if candidates else None


def persist(repo,decision):
    key=decision["trigger_boundary_id"]
    if boundary_id(decision["trigger_15m"]["candle_open_time_ms"])!=key:raise ValueError("boundary key mismatch")
    previous=indexed_decision(repo,key)
    if previous is not None and previous!=decision:raise ValueError("boundary conflict; never overwrite")
    append_decision(repo,decision)
    index={"schema_version":"btc-direction-boundary-v1","boundary_id":key,"decision_id":decision["decision_id"],"trigger_time":decision["trigger_15m"]["candle_open_time_ms"]}
    index["index_id"]=digest(index)
    append_json(repo,"boundaries/"+key+".json",index)


@contextmanager
def writer_lock(repo):
    directory=root(repo);directory.mkdir(parents=True,exist_ok=True)
    path=directory/".writer.lock"
    try:fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:raise ValueError("concurrent history writer or abandoned lock")
    try:
        os.write(fd,str(os.getpid()).encode());os.close(fd)
        yield
    finally:path.unlink()

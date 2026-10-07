"""Append-only contract for decisions actually delivered by ChatGPT.

This is intentionally separate from automated Direction/Entry/Upbit strategy histories.
A caller supplies the delivered decision plus immutable source references. The module
canonicalizes, validates and writes one record exactly once. It never fabricates a
decision and never reconstructs a past decision from newer market data.
"""
from __future__ import annotations
import argparse, hashlib, json, os
from datetime import datetime, timezone
from pathlib import Path

SCHEMA="chat-analysis-decision-v1"
SYSTEMS={"BTC","UPBIT"}
STANCES={"LONG","SHORT","NEUTRAL","WATCH","NO_TRADE"}
QUALITY={"CURRENT","DEGRADED"}
HORIZONS={"1h":3600_000,"4h":4*3600_000,"12h":12*3600_000,"24h":24*3600_000,"1d":24*3600_000,"3d":3*24*3600_000,"7d":7*24*3600_000}


def canonical(obj):
    return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":"))


def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def iso_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")


def build(system,stance,reference_price,decision_time_utc,summary,source_refs,
          data_quality="CURRENT",strategy=None,instruments=None,horizons=None,metadata=None):
    if system not in SYSTEMS: raise ValueError("unsupported system")
    if stance not in STANCES: raise ValueError("unsupported stance")
    if data_quality not in QUALITY: raise ValueError("decision may only be recorded from CURRENT/DEGRADED evidence")
    if not source_refs: raise ValueError("source_refs required")
    if not summary or not summary.strip(): raise ValueError("summary required")
    if reference_price is not None and float(reference_price)<=0: raise ValueError("reference_price must be positive")
    t=datetime.fromisoformat(decision_time_utc.replace("Z","+00:00"))
    if t.tzinfo is None: raise ValueError("decision_time_utc must be timezone aware")
    horizons=horizons or (["1h","4h","12h","24h"] if system=="BTC" else ["1d","3d","7d"])
    if any(h not in HORIZONS for h in horizons): raise ValueError("unsupported horizon")
    base={
      "schema_version":SCHEMA,"system":system,"stance":stance,"strategy":strategy,
      "instruments":instruments or (["BTCUSDT_PERPETUAL"] if system=="BTC" else []),
      "decision_time_utc":t.astimezone(timezone.utc).isoformat().replace("+00:00","Z"),
      "reference_price":None if reference_price is None else str(reference_price),
      "summary":summary.strip(),"data_quality":data_quality,
      "source_refs":source_refs,"evaluation_horizons":horizons,
      "semantics":{"chatgpt_delivered_decision":True,"automated_engine_signal":False,
                   "reference_price_is_fill":False,"prospective_only":True},
      "metadata":metadata or {}
    }
    base["decision_id"]=digest(base)
    return base


def validate(obj):
    if obj.get("schema_version")!=SCHEMA: raise ValueError("schema")
    rebuilt={k:v for k,v in obj.items() if k!="decision_id"}
    if digest(rebuilt)!=obj.get("decision_id"): raise ValueError("decision hash mismatch")
    if obj["system"] not in SYSTEMS or obj["stance"] not in STANCES: raise ValueError("enum")
    if obj["data_quality"] not in QUALITY: raise ValueError("quality")
    if not obj["source_refs"]: raise ValueError("source refs")
    return obj


def path_for(obj):
    day=obj["decision_time_utc"][:10]
    return Path("output_chat_analysis/v1/decisions")/day/(obj["decision_id"]+".json")


def write_once(repo,obj):
    validate(obj); path=Path(repo)/path_for(obj); path.parent.mkdir(parents=True,exist_ok=True)
    payload=(json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode()
    if path.exists():
        if path.read_bytes()==payload:return "NOOP",path
        raise FileExistsError("immutable decision collision")
    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL
    fd=os.open(path,flags,0o644)
    with os.fdopen(fd,"wb") as f:f.write(payload);f.flush();os.fsync(f.fileno())
    return "WRITTEN",path


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--repo",default=".")
    p.add_argument("--json",required=True,help="decision JSON input file")
    args=p.parse_args(argv)
    raw=json.loads(Path(args.json).read_text(encoding="utf-8"))
    obj=build(**raw)
    status,path=write_once(args.repo,obj)
    print(canonical({"status":status,"path":str(path_for(obj)),"decision_id":obj["decision_id"]}))


if __name__=="__main__":main()

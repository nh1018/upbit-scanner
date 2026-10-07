"""Mature prospective ChatGPT decision outcomes from repository market evidence.

BTC V1 uses immutable completed 15m TradingView rows already stored in data_market.
UPBIT decisions remain PENDING_SOURCE until an immutable post-decision candle source
is wired; this runner never substitutes current ticker/rolling data for finalized bars.
"""
from __future__ import annotations
import csv,hashlib,json
from datetime import datetime,timezone
from pathlib import Path
from chat_analysis.history import validate
from chat_analysis.outcome import evaluate

def sha256(b):return hashlib.sha256(b).hexdigest()

def load_btc_15m(repo):
    root=Path(repo)/"data_market/btc_anytime/15m"
    rows=[]
    for p in sorted(root.glob("btc_15m_20*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():continue
            x=json.loads(line)
            t=int(x["time"])
            rows.append({"open_time_ms":t,"close_time_ms":t+900_000,"open":x["open"],"high":x["high"],"low":x["low"],"close":x["close"],
                         "source_file":str(p.relative_to(repo))})
    # deterministic de-duplication by open time; duplicate conflicts fail closed
    by={}
    for x in rows:
        k=x["open_time_ms"]
        core=(str(x["open"]),str(x["high"]),str(x["low"]),str(x["close"]))
        if k in by and core!=(str(by[k]["open"]),str(by[k]["high"]),str(by[k]["low"]),str(by[k]["close"])):
            raise ValueError(f"conflicting BTC bar {k}")
        by[k]=x
    return [by[k] for k in sorted(by)]

def load_upbit_1d(repo,market):
    path=Path(repo)/"data_market/chat_analysis/upbit_1d.csv"
    if not path.exists():return []
    rows=[]
    for x in csv.DictReader(path.open(encoding="utf-8-sig",newline="")):
        if x["market"]!=market:continue
        start=int(datetime.fromisoformat(x["candle_time_utc"].replace("Z","+00:00")).timestamp()*1000)
        rows.append({"open_time_ms":start,"close_time_ms":start+86400000,"open":x["open"],"high":x["high"],"low":x["low"],"close":x["close"]})
    return sorted(rows,key=lambda x:x["open_time_ms"])

def decision_files(repo):
    root=Path(repo)/"output_chat_analysis/v1/decisions"
    return sorted(root.glob("*/*.json")) if root.exists() else []

def outcome_path(repo,rec):
    return Path(repo)/"output_chat_analysis/v1/outcomes"/rec["decision_time_utc"][:10]/(rec["decision_id"]+".json")

def run(repo=".",now=None):
    repo=Path(repo); now=now or datetime.now(timezone.utc); now_ms=int(now.timestamp()*1000)
    btc=load_btc_15m(repo)
    stats={"decisions":0,"written":0,"unchanged":0,"btc":0,"upbit_pending_source":0}
    for p in decision_files(repo):
        rec=validate(json.loads(p.read_text(encoding="utf-8")));stats["decisions"]+=1
        if rec["system"]=="BTC":
            out=evaluate(rec,btc,now_ms);stats["btc"]+=1
            out["source_policy"]="completed_15m_tradingview_repository_rows"
        else:
            instruments=rec.get("instruments") or []
            if len(instruments)!=1 or not instruments[0].startswith("KRW-"):
                out={"schema_version":"chat-analysis-outcome-v1","decision_id":rec["decision_id"],
                     "status":"UNAVAILABLE","reason":"single_upbit_market_required","horizons":{}}
            else:
                bars=load_upbit_1d(repo,instruments[0])
                if not bars:
                    out={"schema_version":"chat-analysis-outcome-v1","decision_id":rec["decision_id"],
                         "status":"PENDING_SOURCE","reason":"finalized_upbit_daily_source_not_yet_available",
                         "horizons":{},"source_policy":"completed_upbit_1d_repository_rows"}
                    stats["upbit_pending_source"]+=1
                else:
                    out=evaluate(rec,bars,now_ms)
                    out["source_policy"]="completed_upbit_1d_repository_rows"
        out["evaluated_at_utc"]=now.isoformat().replace("+00:00","Z")
        dst=outcome_path(repo,rec);dst.parent.mkdir(parents=True,exist_ok=True)
        # evaluated_at changes every run; compare semantic body before rewriting
        old=json.loads(dst.read_text(encoding="utf-8")) if dst.exists() else None
        semantic=lambda x:{k:v for k,v in x.items() if k!="evaluated_at_utc"} if x else None
        if semantic(old)==semantic(out):stats["unchanged"]+=1;continue
        dst.write_text(json.dumps(out,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8");stats["written"]+=1
    return stats

if __name__=="__main__":
    print(json.dumps(run(),sort_keys=True))

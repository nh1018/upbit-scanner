"""Collect finalized daily Upbit candles only for prospectively recorded ChatGPT decisions."""
from __future__ import annotations
import csv,json,time,urllib.parse,urllib.request
from datetime import datetime,timezone
from pathlib import Path

FIELDS=["market","candle_time_utc","open","high","low","close","base_volume","quote_trade_amount","collected_at_utc"]

def markets_needed(repo="."):
    root=Path(repo)/"output_chat_analysis/v1/decisions";out=set()
    for p in root.glob("*/*.json") if root.exists() else []:
        x=json.loads(p.read_text(encoding="utf-8"))
        if x.get("system")=="UPBIT":
            out.update(i for i in x.get("instruments",[]) if isinstance(i,str) and i.startswith("KRW-"))
    return sorted(out)

def fetch(market,count=10):
    q=urllib.parse.urlencode({"market":market,"count":count})
    req=urllib.request.Request("https://api.upbit.com/v1/candles/days?"+q,headers={"User-Agent":"chat-analysis-upbit-outcome-v1"})
    with urllib.request.urlopen(req,timeout=15) as r:return json.loads(r.read())

def collect(repo=".",now=None):
    now=now or datetime.now(timezone.utc);today=now.date();rows=[]
    for m in markets_needed(repo):
        for x in fetch(m):
            start=datetime.fromisoformat(x["candle_date_time_utc"]).replace(tzinfo=timezone.utc)
            # Upbit daily UTC candle starts at 00:00; only days strictly before current UTC day are final.
            if start.date()>=today:continue
            rows.append({"market":m,"candle_time_utc":start.isoformat().replace("+00:00","Z"),
              "open":x["opening_price"],"high":x["high_price"],"low":x["low_price"],"close":x["trade_price"],
              "base_volume":x["candle_acc_trade_volume"],"quote_trade_amount":x["candle_acc_trade_price"],
              "collected_at_utc":now.isoformat().replace("+00:00","Z")})
        time.sleep(.12)
    path=Path(repo)/"data_market/chat_analysis/upbit_1d.csv";path.parent.mkdir(parents=True,exist_ok=True)
    old={}
    if path.exists():
        for x in csv.DictReader(path.open(encoding="utf-8-sig",newline="")):old[(x["market"],x["candle_time_utc"])]=x
    for x in rows:old[(x["market"],x["candle_time_utc"])]=x
    vals=sorted(old.values(),key=lambda x:(x["candle_time_utc"],x["market"]))
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(vals)
    return {"markets":len(markets_needed(repo)),"rows":len(vals)}

if __name__=="__main__":print(json.dumps(collect(),sort_keys=True))

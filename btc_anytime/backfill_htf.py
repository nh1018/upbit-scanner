"""Official USD-M HTF backfill. Dry-run by default; never writes bootstrap/15m.
Run from repository root: python -B -m btc_anytime.backfill_htf --timeframe 1h
--end-exclusive 2026-10-03T00:00:00Z [--fetch-oi] [--write].
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime,timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import tempfile
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from zipfile import ZipFile
from btc_anytime.integrity import DURATIONS,VALUE_FIELDS,decimal,iso,utc_ms,load_entries,same_values,value_errors,analyze

ARCHIVE="https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT"
OI_API="https://fapi.binance.com/futures/data/openInterestHist"
KLINES_API="https://fapi.binance.com/fapi/v1/klines"

def fetch_bytes(url):
    # Only public endpoints; no token/API key is accepted.
    req=Request(url,headers={"User-Agent":"btc-anytime-htf-backfill"})
    with urlopen(req,timeout=20) as response:return response.read()

def daily_archive(tf,day,fetch=fetch_bytes):
    name=f"BTCUSDT-{tf}-{day}.zip";url=f"{ARCHIVE}/{tf}/{name}"
    content=fetch(url);checksum=fetch(url+".CHECKSUM").decode().split()[0]
    digest=sha256(content).hexdigest()
    if digest!=checksum:raise ValueError("archive checksum mismatch: "+day)
    entry=name[:-4]+".csv";rows=[]
    with ZipFile(io.BytesIO(content)) as archive:
        if entry not in archive.namelist():raise ValueError("expected CSV missing from archive: "+entry)
        with archive.open(entry) as stream:
            reader=csv.reader(io.TextIOWrapper(stream,encoding="utf-8-sig"))
            for line,raw in enumerate(reader,1):
                if raw and raw[0]=="open_time":continue
                if len(raw)!=12:raise ValueError("invalid archive row")
                t=int(raw[0]);duration=DURATIONS[tf]
                row={"symbol":"BTCUSDT","market":"BINANCE_USDT_M_FUTURES","timeframe":tf,"time":t,"candle_time_utc":iso(t),"close_time_ms":int(raw[6]),"close_time_utc":iso(int(raw[6])),"is_closed":True,"open":raw[1],"high":raw[2],"low":raw[3],"close":raw[4],"volume":raw[5],"quote_volume":raw[7],"trade_count":int(raw[8]),"taker_buy_base":raw[9],"taker_buy_quote":raw[10],"source":"binance_usdm_public_data_daily_klines_backfill","source_url":url,"source_sha256":digest,"source_entry":entry,"source_row":line,"oi":None,"oi_status":"unavailable","oi_unavailable_reason":"historical_oi_not_requested"}
                if t%DURATIONS[tf] or row["close_time_ms"]!=t+duration-1 or iso(t)[:10]!=day or value_errors(row):raise ValueError("invalid archive candle")
                rows.append(row)
    if len({r["time"] for r in rows})!=len(rows):raise ValueError("duplicate archive keys")
    if [r["time"] for r in rows]!=sorted(r["time"] for r in rows):raise ValueError("archive not sorted")
    return rows

def rest_day(tf,day,now_ms,fetch=fetch_bytes):
    """Official REST only for absent archives; never derives synthetic bars."""
    start=utc_ms(day+"T00:00:00Z");end=min(start+86400000,now_ms//DURATIONS[tf]*DURATIONS[tf])
    if start>=end:return []
    url=KLINES_API+"?"+urlencode({"symbol":"BTCUSDT","interval":tf,"startTime":start,"endTime":end-1,"limit":1000})
    content=fetch(url);raw_rows=json.loads(content);digest=sha256(content).hexdigest();rows=[]
    if not isinstance(raw_rows,list):raise ValueError("invalid official REST response")
    for n,raw in enumerate(raw_rows,1):
        if not isinstance(raw,list) or len(raw)!=12:raise ValueError("invalid official REST row")
        t=raw[0];close_time=raw[6]
        if not isinstance(t,int) or isinstance(t,bool) or not isinstance(close_time,int):raise ValueError("invalid REST timestamp")
        row={"symbol":"BTCUSDT","market":"BINANCE_USDT_M_FUTURES","timeframe":tf,"time":t,"candle_time_utc":iso(t),"close_time_ms":close_time,"close_time_utc":iso(close_time),"is_closed":True,"open":raw[1],"high":raw[2],"low":raw[3],"close":raw[4],"volume":raw[5],"quote_volume":raw[7],"trade_count":raw[8],"taker_buy_base":raw[9],"taker_buy_quote":raw[10],"source":"binance_usdm_official_rest_klines_backfill","source_url":url,"source_response_sha256":digest,"source_row":n,"source_verification":"official_https_rest_response_hash_no_published_checksum","archive_unavailable_url":f"{ARCHIVE}/{tf}/BTCUSDT-{tf}-{day}.zip","oi":None,"oi_status":"unavailable","oi_unavailable_reason":"historical_oi_not_requested"}
        if not start<=t<end or t%DURATIONS[tf] or close_time!=t+DURATIONS[tf]-1 or value_errors(row):raise ValueError("invalid official REST candle")
        rows.append(row)
    if [r["time"] for r in rows]!=list(range(start,end,DURATIONS[tf])):raise ValueError("incomplete/unsorted/duplicate official REST day")
    return rows

def historical_oi(tf,start,end,fetch=fetch_bytes):
    result={};cursor=start;warnings=[]
    while cursor<end:
        url=OI_API+"?"+urlencode({"symbol":"BTCUSDT","period":tf,"startTime":cursor,"endTime":end-1,"limit":500})
        try:content=fetch(url);batch=json.loads(content)
        except (HTTPError,URLError,TimeoutError) as exc:
            warnings.append("historical OI unavailable: "+type(exc).__name__);break
        if not isinstance(batch,list):raise ValueError("invalid OI response")
        if not batch:break
        times=[]
        for o in batch:
            t=o.get("timestamp")
            if not isinstance(t,int) or isinstance(t,bool) or t% DURATIONS[tf] or o.get("symbol")!="BTCUSDT" or decimal(o.get("sumOpenInterest"))<0:raise ValueError("invalid OI row")
            times.append(t)
            if start<=t<end:
                if t in result:raise ValueError("duplicate OI timestamp")
                result[t]={"oi":o["sumOpenInterest"],"oi_status":"available","oi_source":"binance_usdm_open_interest_hist","oi_source_url":url,"oi_source_response_sha256":sha256(content).hexdigest(),"oi_timestamp_ms":t,"oi_timestamp_utc":iso(t),"oi_alignment":"source_timestamp_equals_candle_open"}
        if times!=sorted(times) or len(times)!=len(set(times)):raise ValueError("unsorted OI response")
        if max(times)<cursor:raise ValueError("OI pagination not advancing")
        cursor=max(times)+DURATIONS[tf]
    return result,warnings

def plan_backfill(root,tf,end_exclusive,now_ms=None,fetch=fetch_bytes,fetch_oi=False,allow_rest_fallback=False):
    if tf not in ("1h","4h","1d"):raise ValueError("15m backfill is forbidden")
    duration=DURATIONS[tf];now_ms=now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp()*1000)
    if end_exclusive%duration or end_exclusive>now_ms:raise ValueError("end must be a completed UTC boundary")
    entries,errors=load_entries(root,tf)
    if errors:raise ValueError("existing JSONL parse errors")
    seed=[e for e in entries if Path(e.file).name==f"btc_{tf}_history.jsonl"]
    if not seed:raise ValueError("missing bootstrap history")
    index={}
    for e in entries:
        d=e.data;t=d.get("time")
        if not isinstance(t,int) or isinstance(t,bool) or t%duration or d.get("timeframe")!=tf or d.get("symbol") not in ("BTCUSDT","BTCUSDT.P") or value_errors(d):raise ValueError("invalid existing candle")
        if t in index:raise ValueError("duplicate existing key")
        index[t]=d
    live_times=[e.data["time"] for e in entries if e.data.get("received_at_utc") or e.data.get("source")=="tradingview_webhook"]
    if live_times and end_exclusive>min(live_times):raise ValueError("end must not pass first live candle")
    start=max(e.data["time"] for e in seed)+duration
    if end_exclusive<start:raise ValueError("end precedes bootstrap continuation")
    official={};day=start//86400000*86400000
    while day<end_exclusive:
        try:day_rows=daily_archive(tf,iso(day)[:10],fetch)
        except HTTPError as exc:
            # Do not mask checksum/parse/network failures. Only archive absence.
            archive_url=f"{ARCHIVE}/{tf}/BTCUSDT-{tf}-{iso(day)[:10]}.zip"
            if not allow_rest_fallback or exc.code!=404 or exc.url!=archive_url:raise
            day_rows=rest_day(tf,iso(day)[:10],now_ms,fetch)
        for row in day_rows:
            if start<=row["time"]<end_exclusive:
                if row["time"] in official:raise ValueError("duplicate official key")
                official[row["time"]]=row
        day+=86400000
    expected=list(range(start,end_exclusive,duration))
    absent=[t for t in expected if t not in official]
    if absent:raise ValueError("official candles missing: "+",".join(iso(t) for t in absent))
    oi,warnings=historical_oi(tf,start,end_exclusive,fetch) if fetch_oi and expected else ({},[])
    rows=[];skipped=[]
    for t in expected:
        row=official[t]
        if t in oi:
            row.update(oi[t]);row.pop("oi_unavailable_reason",None)
        elif fetch_oi:row["oi_unavailable_reason"]="no_verified_exact_timestamp_historical_oi"
        if t in index:
            if not same_values(index[t],row,include_oi=False):raise ValueError("existing OHLCV conflict: "+iso(t))
            if index[t].get("oi_alignment")==row.get("oi_alignment") and index[t].get("oi") is not None and row.get("oi") is not None and decimal(index[t]["oi"])!=decimal(row["oi"]):raise ValueError("existing OI conflict: "+iso(t))
            skipped.append(t);continue
        rows.append(row)
    return {"timeframe":tf,"start":iso(start),"end_exclusive":iso(end_exclusive),"rows":rows,"skipped_existing":skipped,"warnings":warnings}

def write_plan(root,plan):
    tf=plan["timeframe"]
    if tf not in ("1h","4h","1d"):raise ValueError("15m writes forbidden")
    # Re-check all existing keys immediately before writing. Run with collection
    # paused or the repository checkout isolated; this is not a distributed lock.
    existing,errors=load_entries(root,tf)
    if errors:raise ValueError("existing parse errors")
    by_key={e.data["time"]:e.data for e in existing};grouped={}
    if len(by_key)!=len(existing):raise ValueError("duplicate existing key")
    now_ms=int(datetime.now(timezone.utc).timestamp()*1000);seen=set()
    for row in plan["rows"]:
        t=row.get("time")
        if not isinstance(t,int) or isinstance(t,bool) or t<0 or t%DURATIONS[tf] or t+DURATIONS[tf]>now_ms or row.get("timeframe")!=tf or row.get("is_closed") is not True or utc_ms(row.get("candle_time_utc"))!=t or row.get("close_time_ms")!=t+DURATIONS[tf]-1 or value_errors(row) or row.get("source") not in ("binance_usdm_public_data_daily_klines_backfill","binance_usdm_official_rest_klines_backfill"):raise ValueError("invalid planned official candle")
        if t in seen:raise ValueError("duplicate planned key")
        seen.add(t)
    for row in plan["rows"]:
        if row["time"] in by_key:
            if not same_values(by_key[row["time"]],row,include_oi=False):raise ValueError("concurrent key conflict")
            continue
        day=iso(row["time"])[:10].replace("-","")
        file=root/tf/f"btc_{tf}_{day}.jsonl";grouped.setdefault(file,[]).append(row)
    written=[]
    for file,new in grouped.items():
        original=file.read_bytes() if file.exists() else b""
        old_lines=original.decode("utf-8-sig").splitlines()
        lines=[(json.loads(line)["time"],line) for line in old_lines if line.strip()]
        lines.extend((r["time"],json.dumps(r,ensure_ascii=False,separators=(",",":"))) for r in new)
        lines.sort(key=lambda r:r[0])
        if len(set(t for t,_ in lines))!=len(lines):raise ValueError("write duplicate")
        file.parent.mkdir(parents=True,exist_ok=True)
        temp=None
        try:
            with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",newline="\n",dir=file.parent,delete=False) as stream:
                temp=Path(stream.name);stream.write("\n".join(line for _,line in lines)+"\n");stream.flush();os.fsync(stream.fileno())
            if (file.read_bytes() if file.exists() else b"")!=original:raise ValueError("concurrent file change")
            os.replace(temp,file);temp=None;written.append(str(file))
        finally:
            if temp is not None:temp.unlink(missing_ok=True)
    return written

def _validation_args(root,tf):
    entries,errors=load_entries(root,tf)
    return entries,tf,errors

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,default=Path(__file__).resolve().parent.parent/"data_market"/"btc_anytime")
    p.add_argument("--timeframe",choices=("1h","4h","1d"),required=True)
    p.add_argument("--end-exclusive",required=True,help="first live candle OPEN time, explicit UTC; excluded")
    p.add_argument("--fetch-oi",action="store_true")
    p.add_argument("--allow-rest-fallback",action="store_true",help="official REST only if the ZIP itself returns 404; no archive checksum equivalence")
    p.add_argument("--write",action="store_true",help="otherwise plan only; never writes history/15m")
    args=p.parse_args()
    plan=plan_backfill(args.root,args.timeframe,utc_ms(args.end_exclusive),fetch_oi=args.fetch_oi,allow_rest_fallback=args.allow_rest_fallback)
    written=write_plan(args.root,plan) if args.write else []
    print(json.dumps({"dry_run":not args.write,"timeframe":args.timeframe,"start":plan["start"],"end_exclusive":plan["end_exclusive"],"new_count":len(plan["rows"]),"skipped_existing":len(plan["skipped_existing"]),"oi_available":sum(r["oi"] is not None for r in plan["rows"]),"warnings":plan["warnings"],"written_files":written,"rows":plan["rows"],"post_write_validation":analyze(*_validation_args(args.root,args.timeframe)) if args.write else None},ensure_ascii=False,indent=2))
    return 0
if __name__=="__main__":raise SystemExit(main())

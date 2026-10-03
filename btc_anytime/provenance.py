"""Append-only archive verification. Recording is explicit; audit never writes."""
import argparse,csv,io,json,subprocess
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urlencode
from zipfile import ZipFile
from btc_anytime.integrity import DURATIONS,decimal,iso,value_errors
from btc_anytime.backfill_htf import ARCHIVE,KLINES_API,fetch_bytes
VERSION="btc-archive-provenance-v1"
ROOT=Path(__file__).resolve().parent.parent
EVENTS=ROOT/"btc_anytime/provenance_events"
FIELDS=("time","open","high","low","close","volume","close_time_ms","quote_volume","trade_count","taker_buy_base","taker_buy_quote")
HEADER=["open_time","open","high","low","close","volume","close_time","quote_volume","count","taker_buy_volume","taker_buy_quote_volume","ignore"]
CLASSES=("UNCHANGED","MARKET_DATA_CHANGED","ARCHIVE_REPACKAGED","ARCHIVE_REPUBLISHED_WITH_EQUIVALENT_USED_DATA","UNRESOLVED_PROVENANCE_CHANGE")

def canonical(value):return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
def digest(value):return sha256(canonical(value)).hexdigest()
def git(*args):return subprocess.check_output(["git","--no-optional-locks","-c","safe.directory="+ROOT.as_posix(),*args],cwd=ROOT)
def observation(entries,commit):
    refs=[]
    for e in entries:
        path=Path(e.file)
        relative=path.resolve().relative_to(ROOT).as_posix()
        original=json.loads(git("show",commit+":"+relative).decode().splitlines()[e.line-1])
        if original!=e.data:raise ValueError("original observation modified")
        refs.append({"commit":commit,"file":relative,"line":e.line,"row_sha256":digest(original),"time":original["time"]})
    return refs

def evaluate(entries,archive,checksum,rest,refs,headers=None,original_csv_sha=None,now_ms=None):
    if not entries:raise ValueError("empty used rows")
    first=entries[0].data;tf=first["timeframe"];day=iso(first["time"])[:10];name=f"BTCUSDT-{tf}-{day}.csv";url=f"{ARCHIVE}/{tf}/BTCUSDT-{tf}-{day}.zip"
    event={"validator_version":VERSION,"verification_time_utc":datetime.now(timezone.utc).isoformat(),"source_url":url,"original_observation_reference":refs,"original_archive_sha256":first["source_sha256"],"current_archive_sha256":sha256(archive).hexdigest(),"current_checksum":checksum.decode(),"classification":"UNRESOLVED_PROVENANCE_CHANGE","status":"FAIL","unavailable_fields":["original_zip_bytes","original_checksum_text","original_csv_bytes","original_csv_sha256","original_http_metadata"],"evidence":{}}
    if original_csv_sha:event["unavailable_fields"].remove("original_csv_sha256")
    checks=event["evidence"]
    try:
        tokens=checksum.decode().split()
        checks["checksum_pass"]=len(tokens)==2 and tokens[0]==event["current_archive_sha256"] and tokens[1]==name[:-4]+".zip"
        with ZipFile(io.BytesIO(archive)) as z:
            if z.namelist()!=[name]:raise ValueError("unexpected ZIP entries")
            csv_bytes=z.read(name)
        lines=list(csv.reader(io.StringIO(csv_bytes.decode("utf-8-sig"))));header=lines[0];rows=lines[1:]
        event.update(current_csv_sha256=sha256(csv_bytes).hexdigest(),csv_filename=name,csv_size=len(csv_bytes),row_count=len(rows),header=header,http_last_modified=(headers or {}).get("Last-Modified"),http_etag=(headers or {}).get("ETag"))
        if not event["http_last_modified"]:event["unavailable_fields"].append("current_http_last_modified")
        if not event["http_etag"]:event["unavailable_fields"].append("current_http_etag")
        start=first["time"]//86400000*86400000;duration=DURATIONS[tf]
        now_ms=now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp()*1000)
        checks["boundary_pass"]=header==HEADER and all(len(r)==12 for r in rows) and [int(r[0]) for r in rows]==list(range(start,start+86400000,duration)) and all(int(r[6])==int(r[0])+duration-1 and int(r[0])+duration<=now_ms for r in rows)
        comparisons=[]
        for e in entries:
            d=e.data;n=d["source_row"];r=rows[n-2] if isinstance(n,int) and 2<=n<=len(rows)+1 else []
            equal=len(r)==12 and all(k in d and str(d[k])==r[i] for i,k in enumerate(FIELDS)) and d.get("source_url")==url and d.get("source_entry")==name and d.get("source_sha256")==event["original_archive_sha256"] and not value_errors(d)
            comparisons.append({"time":d["time"],"source_row":n,"all_used_fields_equal":equal})
        event["used_row_comparison_result"]=comparisons;checks["used_rows_pass"]=all(c["all_used_fields_equal"] for c in comparisons)
        rest_rows=json.loads(rest)
        checks["rest_pass"]=isinstance(rest_rows,list) and len(rest_rows)==len(rows) and all(len(b)==12 and all(decimal(x)==decimal(y) for x,y in zip(a,b)) for a,b in zip(rows,rest_rows))
        event["rest_cross_check_result"]={"pass":checks["rest_pass"],"rows":len(rest_rows),"response_sha256":sha256(rest).hexdigest(),"compared_fields":12}
        checks["original_preserved_pass"]=len(refs)==len(entries) and all(r.get("row_sha256")==digest(e.data) and r.get("commit") and r.get("file") and r.get("line")==e.line for r,e in zip(refs,entries))
        if all(checks.get(k) is True for k in ("checksum_pass","boundary_pass","used_rows_pass","rest_pass","original_preserved_pass")):
            event["classification"]="UNCHANGED" if event["current_archive_sha256"]==event["original_archive_sha256"] else "ARCHIVE_REPACKAGED" if original_csv_sha==event["current_csv_sha256"] else "ARCHIVE_REPUBLISHED_WITH_EQUIVALENT_USED_DATA"
            event["status"]="PASS"
        elif checks.get("checksum_pass") and checks.get("boundary_pass") and not checks.get("used_rows_pass"):
            event["classification"]="MARKET_DATA_CHANGED"
    except Exception as exc:event["evidence"]["error"]=str(exc)
    return event

def append_event(event,directory=EVENTS):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    event_id=digest(event);path=directory/(event_id+".json")
    with path.open("x",encoding="utf-8",newline="\n") as f:f.write(json.dumps({"event_id":event_id,**event},ensure_ascii=False,indent=2)+"\n")
    return path

def verify_recorded(entries,fetch=fetch_bytes,directory=EVENTS):
    url=entries[0].data["source_url"];original=entries[0].data["source_sha256"]
    for path in sorted(Path(directory).glob("*.json")):
        event=json.loads(path.read_text(encoding="utf-8"));event_id=event.pop("event_id",None)
        if digest(event)!=event_id or path.stem!=event_id:raise ValueError("verification event integrity failure")
        if event.get("source_url")!=url or event.get("original_archive_sha256")!=original:continue
        required=("verification_time_utc","original_observation_reference","current_archive_sha256","current_checksum","current_csv_sha256","csv_filename","csv_size","row_count","header","http_last_modified","http_etag","used_row_comparison_result","rest_cross_check_result","classification","evidence","unavailable_fields")
        if any(k not in event for k in required):continue
        if event.get("validator_version")!=VERSION or event.get("status")!="PASS":continue
        recorded_time=datetime.fromisoformat(event["verification_time_utc"].replace("Z","+00:00"))
        if recorded_time.tzinfo is None or recorded_time.utcoffset().total_seconds()!=0:continue
        recorded_refs=event.get("original_observation_reference",[])
        if not recorded_refs:continue
        refs=observation(entries,recorded_refs[0]["commit"])
        if refs!=recorded_refs:continue
        tf=entries[0].data["timeframe"];start=entries[0].data["time"]//86400000*86400000
        resturl=KLINES_API+"?"+urlencode(dict(symbol="BTCUSDT",interval=tf,startTime=start,endTime=start+86400000-1,limit=1000))
        fresh=evaluate(entries,fetch(url),fetch(url+".CHECKSUM"),fetch(resturl),refs)
        if fresh["status"]!="PASS":raise ValueError("current revalidation failed: "+fresh["classification"])
        keys=("current_archive_sha256","current_checksum","current_csv_sha256","csv_filename","csv_size","row_count","header","used_row_comparison_result","rest_cross_check_result","classification")
        if any(event.get(k)!=fresh.get(k) for k in keys):continue
        mandatory=("checksum_pass","boundary_pass","used_rows_pass","rest_pass","original_preserved_pass")
        if any(event.get("evidence",{}).get(k) is not True for k in mandatory):continue
        return {"event_id":event_id,"classification":event["classification"],"status":"PASS"}
    raise ValueError("no matching complete append-only verification event; record new revalidation")

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--record",action="store_true",required=True);p.add_argument("--commit",required=True);p.add_argument("--day",required=True);args=p.parse_args()
    from btc_anytime.integrity import load_entries
    cache={};headers={}
    def fetch(url):
        if url not in cache:
            with urlopen(Request(url,headers={"User-Agent":VERSION}),timeout=25) as r:cache[url]=r.read();headers[url]=dict(r.headers)
        return cache[url]
    for tf in ("1h","4h","1d"):
        entries,errors=load_entries(ROOT/"data_market/btc_anytime",tf)
        if errors:raise ValueError("parse errors")
        used=[e for e in entries if e.data.get("source")=="binance_usdm_public_data_daily_klines_backfill" and iso(e.data["time"])[:10]==args.day]
        refs=observation(used,args.commit);url=used[0].data["source_url"];start=used[0].data["time"]//86400000*86400000
        resturl=KLINES_API+"?"+urlencode(dict(symbol="BTCUSDT",interval=tf,startTime=start,endTime=start+86400000-1,limit=1000))
        content=fetch(url);event=evaluate(used,content,fetch(url+".CHECKSUM"),fetch(resturl),refs,headers[url]);event["rest_source_url"]=resturl
        print(json.dumps({"file":str(append_event(event)),"status":event["status"],"classification":event["classification"]}))
        if event["status"]!="PASS":raise ValueError("recorded failure; no validation approval")
if __name__=="__main__":main()

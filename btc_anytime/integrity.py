"""Read-only ledger analysis; no synthetic candles are persisted."""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

DURATIONS={"15m":900000,"1h":3600000,"4h":14400000,"1d":86400000}
VALUE_FIELDS=("open","high","low","close","volume")
@dataclass
class Entry:
    file: str
    line: int
    data: dict
    def ref(self):
        return {"file":self.file,"line":self.line,"time":self.data.get("time"),"candle_time_utc":self.data.get("candle_time_utc")}

def decimal(value):
    if value is None or isinstance(value,bool) or not isinstance(value,(str,int,float,Decimal)):
        raise ValueError("not numeric")
    try: result=Decimal(str(value))
    except InvalidOperation as exc: raise ValueError("not numeric") from exc
    if not result.is_finite(): raise ValueError("not finite")
    return result

def utc_ms(value):
    dt=datetime.fromisoformat(value.replace("Z","+00:00"))
    if dt.tzinfo is None or dt.utcoffset().total_seconds()!=0:
        raise ValueError("explicit UTC required")
    return int(dt.timestamp()*1000)

def iso(ms):
    return datetime.fromtimestamp(ms/1000,tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")

def load_entries(root:Path,tf:str):
    entries=[]; errors=[]
    for file in sorted((root/tf).glob(f"btc_{tf}_*.jsonl")):
        lines=file.read_text(encoding="utf-8-sig").splitlines()
        for number,line in enumerate(lines,1):
            if not line.strip():
                errors.append({"file":str(file),"line":number,"error":"blank_line"});continue
            try:
                data=json.loads(line)
                if not isinstance(data,dict): raise ValueError("not object")
                entries.append(Entry(str(file),number,data))
            except (ValueError,TypeError) as exc:
                errors.append({"file":str(file),"line":number,"error":str(exc)})
    return entries,errors

def source(entry):
    d=entry.data
    if d.get("source"): return d["source"]
    if "received_at_utc" in d: return "legacy_webhook_inferred"
    return "unrecorded"

def value_errors(data):
    errors=[]; values={}
    for field in VALUE_FIELDS:
        try: values[field]=decimal(data.get(field))
        except ValueError: errors.append("invalid_"+field)
    if "volume" in values and values["volume"]<0: errors.append("negative_volume")
    if all(k in values for k in ("open","high","low","close")):
        o,h,l,c=(values[k] for k in ("open","high","low","close"))
        for name,ok in [("high>=open",h>=o),("high>=close",h>=c),("low<=open",l<=o),("low<=close",l<=c),("high>=low",h>=l)]:
            if not ok: errors.append(name)
    return errors

def same_values(a,b,include_oi=True):
    fields=VALUE_FIELDS+(("oi",) if include_oi else ())
    for key in fields:
        av,bv=a.get(key),b.get(key)
        if av is None or bv is None:
            if av!=bv:return False
        else:
            try:
                if decimal(av)!=decimal(bv):return False
            except ValueError:return False
    if include_oi:
        for k in ("oi_time","oi_time_basis","oi_timestamp_ms","oi_alignment","oi_period_start_ms","oi_period_end_ms"):
            if a.get(k)!=b.get(k):return False
    return True

def analyze(entries,tf,parse_errors=(),now_ms=None):
    duration=DURATIONS[tf];now_ms=now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp()*1000)
    errors=list(parse_errors);ordered=[];by_file={};oi=Counter();sources=Counter();oi_bases=Counter()
    for e in entries:
        d=e.data;t=d.get("time");sources[source(e)]+=1
        invalid=value_errors(d)
        if d.get("timeframe")!=tf or d.get("symbol") not in ("BTCUSDT","BTCUSDT.P"):invalid.append("wrong_market_or_timeframe")
        if not isinstance(t,int) or isinstance(t,bool) or t<0 or t%duration:invalid.append("timestamp_boundary")
        else:
            ordered.append(e);by_file.setdefault(e.file,[]).append(e)
            if t+duration>now_ms or d.get("is_closed") is False:invalid.append("in_progress")
            if "close_time_ms" in d and d["close_time_ms"]!=t+duration-1:invalid.append("invalid_close_time")
        try:
            if utc_ms(d.get("candle_time_utc"))!=t:invalid.append("timestamp_utc_mismatch")
        except (ValueError,TypeError,AttributeError,OverflowError):invalid.append("invalid_candle_utc")
        if d.get("oi") is None:
            oi["unavailable" if d.get("oi_status")=="unavailable" else "missing_unlabelled"]+=1
        else:
            try:
                if decimal(d["oi"])<0:invalid.append("negative_oi")
                else:oi["available"]+=1
            except ValueError:invalid.append("invalid_oi")
            oi_bases[d.get("oi_time_basis") or d.get("oi_alignment") or "legacy_unspecified"]+=1
            if d.get("oi_status")=="unavailable":invalid.append("contradictory_oi_status")
        if d.get("oi_time_basis")=="confirmed_oi_bar_close_boundary":
            if d.get("oi") is not None and (d.get("oi_time")!=t+duration or d.get("oi_period_start_ms")!=t or d.get("oi_period_end_ms")!=t+duration):invalid.append("oi_period_mismatch")
        if d.get("oi_alignment")=="source_timestamp_equals_candle_open" and d.get("oi_timestamp_ms")!=t:invalid.append("oi_timestamp_mismatch")
        if invalid:errors.append({**e.ref(),"errors":invalid})
    sorting=[]
    for file,rows in by_file.items():
        for a,b in zip(rows,rows[1:]):
            if b.data["time"]<=a.data["time"]:sorting.append({"previous":a.ref(),"next":b.ref()})
    ordered.sort(key=lambda e:e.data["time"])
    groups={};utc_groups={}
    for e in ordered:
        groups.setdefault(e.data["time"],[]).append(e)
        try:utc_groups.setdefault(utc_ms(e.data["candle_time_utc"]),[]).append(e)
        except (ValueError,TypeError,AttributeError):pass
    duplicates=[{"time":t,"rows":[e.ref() for e in rows]} for t,rows in groups.items() if len(rows)>1]
    conflicts=[{"time":t,"rows":[{**e.ref(),"data":e.data} for e in rows]} for t,rows in groups.items() if len(rows)>1 and any(not same_values(rows[0].data,e.data) for e in rows[1:])]
    intervals=[];missing=[];transitions=[];boundaries=[]
    for a,b in zip(ordered,ordered[1:]):
        delta=b.data["time"]-a.data["time"]
        edge={"previous":a.ref(),"next":b.ref(),"delta_ms":delta}
        if delta!=duration:intervals.append(edge)
        if delta>duration:missing.extend({"time":t,"utc":iso(t)} for t in range(a.data["time"]+duration,b.data["time"],duration))
        if source(a)!=source(b):transitions.append({**edge,"from_source":source(a),"to_source":source(b)})
        if a.file!=b.file:boundaries.append(edge)
    warnings=[]
    if oi["unavailable"] or oi["missing_unlabelled"]:warnings.append("incomplete_oi_coverage")
    if len(oi_bases)>1:warnings.append("mixed_oi_time_bases")
    if sources["unrecorded"]:warnings.append("unrecorded_source")
    status="FAIL" if errors or duplicates or intervals or sorting else "WARNING" if warnings else "PASS"
    if not ordered:status="FAIL";errors.append({"error":"empty_timeframe"})
    return {"timeframe":tf,"status":status,"row_count":len(entries),"first":ordered[0].ref() if ordered else None,"last":ordered[-1].ref() if ordered else None,"duplicate_count":sum(len(v)-1 for v in groups.values()),"duplicates":duplicates,"utc_duplicate_count":sum(len(v)-1 for v in utc_groups.values()),"conflicts":conflicts,"missing_slot_count":len(missing),"missing_slots":missing,"abnormal_interval_count":len(intervals),"abnormal_intervals":intervals,"file_order_errors":sorting,"value_timestamp_errors":errors,"oi_coverage":dict(oi),"oi_time_bases":dict(oi_bases),"source_coverage":dict(sources),"source_transitions":transitions,"file_boundaries":boundaries,"warnings":warnings}

def cross_check(lower,upper,tf):
    """Aggregate ONLY in memory for validation; never return writable candles."""
    duration=DURATIONS[tf];buckets={};duplicates=[]
    for e in lower:
        t=e.data.get("time")
        if not isinstance(t,int) or isinstance(t,bool) or t%900000:continue
        bucket=t//duration*duration;buckets.setdefault(bucket,[]).append(e)
    compared=0;missing=[];mismatches=[];invalid=[]
    for e in upper:
        t=e.data.get("time")
        if not isinstance(t,int) or t not in buckets:continue
        rows=sorted(buckets[t],key=lambda r:r.data["time"])
        times=[r.data["time"] for r in rows]
        if len(set(times))!=len(times):duplicates.append(e.ref());continue
        if times!=list(range(t,t+duration,900000)):missing.append(e.ref());continue
        if any(value_errors(r.data) or r.data.get("is_closed") is False for r in rows):invalid.append(e.ref());continue
        values={"open":decimal(rows[0].data["open"]),"high":max(decimal(r.data["high"]) for r in rows),"low":min(decimal(r.data["low"]) for r in rows),"close":decimal(rows[-1].data["close"]),"volume":sum((decimal(r.data["volume"]) for r in rows),Decimal(0))}
        compared+=1
        for key,value in values.items():
            try:actual=decimal(e.data.get(key))
            except ValueError:actual=None
            if actual!=value:mismatches.append({**e.ref(),"field":key,"formal_value":e.data.get(key),"aggregate_value":str(value)})
    return {"timeframe":tf,"compared":compared,"mismatches":mismatches,"incomplete_lower_buckets":missing,"duplicate_lower_buckets":duplicates,"invalid_lower_buckets":invalid,"writes":False}


def source_cross_check(lower,upper,tf,role):
    """Policy: finalized-to-finalized is a gate; raw-to-finalized is audit."""
    if role not in ("official_to_official","raw_to_official"):raise ValueError("unknown source comparison role")
    result=cross_check(lower,upper,tf);result["role"]=role
    bad=result["mismatches"] or result["duplicate_lower_buckets"] or result["invalid_lower_buckets"]
    if role=="official_to_official":
        complete=result["compared"]==len(upper) and not result["incomplete_lower_buckets"]
        result["status"]="PASS" if complete and not bad else "FAIL"
    else:
        result["status"]="SOURCE_MISMATCH" if result["mismatches"] else "MATCH"
        result["structural_status"]="FAIL" if result["duplicate_lower_buckets"] or result["invalid_lower_buckets"] else "PASS"
    return result

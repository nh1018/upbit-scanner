"""Read-only finalized reference audits; public downloads remain in memory."""
from collections import Counter
from datetime import datetime,timezone
from hashlib import sha256
import io,csv,json,re
from urllib.error import HTTPError
from zipfile import ZipFile
from btc_anytime.integrity import Entry,DURATIONS,iso,analyze,source_cross_check,same_values,decimal
from btc_anytime.backfill_htf import ARCHIVE,KLINES_API,OI_API,fetch_bytes,daily_archive,rest_day
OFFICIAL_SOURCES=("binance_usdm_public_data_daily_klines_backfill","binance_usdm_official_rest_klines_backfill")

def audit(entries_by_tf,parse_errors=None,official=False,fetch=fetch_bytes,now_ms=None):
    now_ms=now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp()*1000)
    cache={}
    def cached(url):
        if url not in cache:cache[url]=fetch(url)
        return cache[url]
    reports=[analyze(rows,tf,(parse_errors or {}).get(tf,()),now_ms) for tf,rows in entries_by_tf.items()]
    raw=[e for e in entries_by_tf.get("15m",[]) if "received_at_utc" in e.data]
    raw_checks=[];official_checks=[];provenance=[];failures=[]
    required={tf:[e for e in entries_by_tf.get(tf,[]) if e.data.get("source") in OFFICIAL_SOURCES] for tf in ("1h","4h","1d")}
    for tf in required:raw_checks.append(source_cross_check(raw,entries_by_tf.get(tf,[]),tf,"raw_to_official"))
    if official:
        # Verify recorded HTF provenance against its exact official response.
        for tf,rows in required.items():
            for e in rows:
                d=e.data;url=d.get("source_url","");day=iso(d["time"])[:10]
                try:
                    if d["source"]==OFFICIAL_SOURCES[0]:
                        expected=f"{ARCHIVE}/{tf}/BTCUSDT-{tf}-{day}.zip"
                        if url!=expected:raise ValueError("unexpected archive URL")
                        original=daily_archive(tf,day,cached)
                        ref=next(r for r in original if r["time"]==d["time"])
                        if d.get("source_sha256")!=ref["source_sha256"] or d.get("source_entry")!=ref["source_entry"] or d.get("source_row")!=ref["source_row"]:raise ValueError("archive provenance/checksum mismatch")
                    else:
                        if not url.startswith(KLINES_API+"?"):raise ValueError("unexpected REST URL")
                        content=cached(url);index=d.get("source_row")
                        if not isinstance(index,int) or index<1 or d.get("source_response_sha256")!=sha256(content).hexdigest():raise ValueError("REST response provenance/hash mismatch")
                        row=json.loads(content)[index-1]
                        if row[0]!=d["time"] or row[6]!=d["close_time_ms"]:raise ValueError("REST source timestamp mismatch")
                        ref=dict(zip(("open","high","low","close","volume"),row[1:6]))
                    if not same_values(d,ref,include_oi=False):raise ValueError("recorded OHLCV differs from official source")
                    if d.get("oi") is not None:
                        oi_url=d.get("oi_source_url","")
                        if d.get("oi_source")!="binance_usdm_open_interest_hist" or not oi_url.startswith(OI_API+"?") or d.get("oi_alignment")!="source_timestamp_equals_candle_open" or d.get("oi_timestamp_ms")!=d["time"]:raise ValueError("invalid historical OI provenance")
                        oi_content=cached(oi_url)
                        if sha256(oi_content).hexdigest()!=d.get("oi_source_response_sha256"):raise ValueError("OI response hash mismatch")
                        matched=[o for o in json.loads(oi_content) if o.get("symbol")=="BTCUSDT" and o.get("timestamp")==d["time"]]
                        if len(matched)!=1 or decimal(matched[0]["sumOpenInterest"])!=decimal(d["oi"]):raise ValueError("historical OI exact observation mismatch")
                    elif d.get("oi_status")!="unavailable" or not d.get("oi_unavailable_reason"):raise ValueError("missing explicit OI unavailable provenance")

                except Exception as exc:failures.append({**e.ref(),"error":str(exc)})
            provenance.append({"timeframe":tf,"verified_rows":len(rows),"source_counts":dict(Counter(e.data["source"] for e in rows)),"seed_verification":"existing bootstrap preserved; original archive checksum not recorded"})
        all_required=[e for rows in required.values() for e in rows]
        if all_required:
            start=min(e.data["time"] for e in all_required)//86400000*86400000
            end=max(e.data["time"]+DURATIONS[e.data["timeframe"]] for e in all_required)
            lower=[];day=start
            while day<end:
                date=iso(day)[:10]
                try:rows=daily_archive("15m",date,cached)
                except HTTPError as exc:
                    expected=f"{ARCHIVE}/15m/BTCUSDT-15m-{date}.zip"
                    if exc.code!=404 or exc.url!=expected:raise
                    rows=rest_day("15m",date,now_ms,cached)
                lower.extend(Entry("official-reference:"+date,n,r) for n,r in enumerate(rows,1))
                day+=86400000
            lower_report=analyze(lower,"15m",now_ms=now_ms)
            if lower_report["status"]=="FAIL":failures.append({"official_15m_reference_errors":lower_report})
            for tf,rows in required.items():official_checks.append(source_cross_check(lower,rows,tf,"official_to_official"))
    failed=any(r["status"]=="FAIL" for r in reports) or failures or any(c["status"]=="FAIL" for c in official_checks) or any(c["structural_status"]=="FAIL" for c in raw_checks)
    mandatory_complete=official and all(required[tf] for tf in required) and len(official_checks)==3
    return {"status":"FAIL" if failed else "PASS" if mandatory_complete else "WARNING","mandatory_validation_complete":bool(mandatory_complete),"read_only":True,"policy":"btc-anytime-source-policy-v1","timeframes":reports,"official_to_official":official_checks,"raw_to_official":raw_checks,"provenance":provenance,"source_errors":failures,"warnings":[{"timeframe":r["timeframe"],"warnings":r["warnings"]} for r in reports if r["warnings"]],"raw_observation_immutable":True}

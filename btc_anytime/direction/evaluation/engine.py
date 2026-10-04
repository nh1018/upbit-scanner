"""Pure outcomes from stored decisions and cutoff-bounded observation evidence.

No Direction evaluation, raw writes, tick claims, source substitution or interpolation.
All intervals are UTC [start,end); prices are non-execution references.
"""
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
import json
from pathlib import Path
from btc_anytime.features.engine import digest, encode
from btc_anytime.integrity import utc_ms, iso
from btc_anytime.direction.signal_history.storage import boundary_id

VERSION = "1.0.0"
SCHEMA = "btc-direction-evaluation-v1"
STEP = 900000
ANCHORS = ("TRIGGER_CLOSE_DIAGNOSTIC", "NEXT_15M_OPEN_PROXY")
LIVE_SOURCES = {"legacy_webhook_inferred", "tradingview_binance_usdm_htf"}


def parameters():
    p = json.loads(Path(__file__).with_name("parameters.json").read_text())
    p["parameter_hash"] = digest(p)
    return p


def number(x):
    if isinstance(x, bool) or x is None:
        raise ValueError("missing/non-numeric value")
    n = Decimal(str(x))
    if not n.is_finite():
        raise ValueError("non-finite value")
    return n


def source(row):
    return row.get("source") or ("legacy_webhook_inferred" if row.get("received_at_utc") else "unrecorded")


def bucket(x, edges):
    if x is None:
        return "UNAVAILABLE"
    n = number(x)
    for i in range(len(edges)-1):
        a,b = number(edges[i]),number(edges[i+1])
        if a <= n < b or i == len(edges)-2 and n == b:
            return f"[{edges[i]},{edges[i+1]}{']' if i == len(edges)-2 else ')'}"
    raise ValueError("bucket value outside contract")


def validate_decision(d):
    if d.get("signal_history_schema_version") != "btc-direction-signal-history-v1":
        raise ValueError("not a prospective stored signal")
    if d.get("schema_version") != "btc-direction-v1" or d.get("decision_id") != digest({k:v for k,v in d.items() if k != "decision_id"}):
        raise ValueError("decision hash/schema failure")
    t = utc_ms(d["decision_time_utc"])
    trigger = d["trigger_15m"]["candle_open_time_ms"]
    if not isinstance(trigger,int) or isinstance(trigger,bool) or trigger % STEP or trigger+STEP > t:
        raise ValueError("trigger timestamp contract")
    if d.get("trigger_boundary_id") != boundary_id(trigger) or not d.get("activation_ref"):
        raise ValueError("prospective activation/boundary reference missing")
    if d["direction_class"] not in ("LONG","SHORT","STRONG_LONG","STRONG_SHORT","NEUTRAL"):
        raise ValueError("direction class")
    if d.get("confidence_semantics") != "evidence_quality_not_probability" or not 0 <= number(d["confidence"]) <= 1:
        raise ValueError("confidence contract")
    snapshot=d["input_snapshot"]
    if snapshot.get("snapshot_id") != digest({k:v for k,v in snapshot.items() if k != "snapshot_id"}) or snapshot["decision_time_utc"] != d["decision_time_utc"]:
        raise ValueError("stored snapshot identity")
    r=snapshot["timeframes"]["15m"]["record"]
    if r["time"] != trigger or r.get("available_at_ms") is None or r["available_at_ms"] > t or r.get("feature_generated_at_ms") is None or r["feature_generated_at_ms"] > t:
        raise ValueError("trigger evidence unavailable at decision")
    return t, trigger, r


def metadata(d,p):
    snap=d["input_snapshot"]
    components={tf:{"score":r.get("score"),"components":r.get("components",{}),
                   "oi_status":(r.get("oi_metadata") or {}).get("unit_status", "UNAVAILABLE")}
                for tf,r in d["timeframes"].items()}
    return {"direction":d["direction_class"],"directional_bias":d["directional_bias"],
            "confidence":d["confidence"],"confidence_bucket":bucket(d["confidence"],p["confidence_edges"]),
            "regime":d["regime"],"tf_components":components,
            "oi_status":d.get("oi_agreement_evidence",{}),
            "versions":{"direction_engine":d["direction_engine_version"],"direction_parameter":d["parameter_version"],
             "direction_parameter_hash":d["parameter_hash"],"feature_schema":snap.get("schema_version"),
             "feature_algorithm":snap.get("algorithm_version"),"feature_parameter_hash":snap.get("parameter_hash") or snap["timeframes"]["15m"]["record"].get("parameter_hash"),
             "evaluator":VERSION,"evaluation_parameter":p["parameter_version"],"evaluation_parameter_hash":p["parameter_hash"]}}


def evaluate(d, rows, evidence, refs, cutoff, anchor, hours):
    """Evidence map values: actual observed_at_ms/ref/canonical_row_hash, not candle clocks."""
    p=parameters()
    if anchor not in ANCHORS or hours not in p["horizons_hours"]:
        raise ValueError("unsupported evaluation contract")
    with localcontext() as ctx:
        ctx.prec=p["precision"]
        ctx.rounding=ROUND_HALF_EVEN
        return _evaluate(d,rows,evidence,refs,cutoff,anchor,hours,p)


def _evaluate(d,rows,evidence,refs,cutoff,anchor,hours,p):
    key_data={"decision_id":d.get("decision_id"),"evaluator_version":VERSION,
              "evaluation_parameter_hash":p["parameter_hash"],"anchor":anchor,
              "anchor_contract_version":p["anchor_contract_version"],"source_contract":p["price_source_contract"],"horizon_hours":hours}
    result={"schema_version":SCHEMA,"evaluation_key":digest(key_data),"contract":key_data,
            "decision_ref":{"decision_id":d.get("decision_id"),"content_hash":digest(d),"engine_decision_id":d.get("engine_decision_id")},
            "status":"PENDING","return_status":"PENDING","excursion_status":"PENDING",
            "metrics":None,"source_refs":[],"warnings":[],"reason_codes":[]}
    try:
        t,trigger,record=validate_decision(d)
        if t > cutoff:
            raise ValueError("decision after evaluation cutoff")
        b=((t+STEP-1)//STEP)*STEP
        a=trigger+STEP if anchor == ANCHORS[0] else b
        end=a+hours*3600000
        result.update(cohort=metadata(d,p),decision_time_ms=t,anchor_price_time_ms=a,endpoint_ms=end,
                      anchor_delay_ms=a-t,excursion_start_ms=b,
                      anchor_semantics="stored trigger close diagnostic; not entry price" if anchor == ANCHORS[0] else
                        "retrospectively verified start price of first complete post-decision 15m interval; not first observed tick or execution price")
        if end > cutoff:
            return result
        indexed={}
        for r in rows:
            indexed.setdefault(r.get("time"),[]).append(r)
        needed=set(range(b,end,STEP)) | {end-STEP}
        if anchor == ANCHORS[1]:needed.add(b)
        missing=[];unavailable=[];used={}
        for ts in sorted(needed):
            candidates=indexed.get(ts,[])
            if not candidates:
                missing.append(ts);continue
            ev=evidence.get(ts)
            if not ev or ev.get("kind") != "consumer_first_observed" or not ev.get("ref") or not isinstance(ev.get("observed_at_ms"),int) or ev["observed_at_ms"] > cutoff:
                unavailable.append(ts);continue
            raw_ref=refs.get(ts)
            if not raw_ref or raw_ref.get("time")!=ts or not raw_ref.get("file") or not raw_ref.get("row_sha256"):
                unavailable.append(ts);continue
            if len(candidates)>1:
                raise SourceConflict("duplicate source key: "+iso(ts))
            row=candidates[0]
            if ev.get("canonical_row_hash") != digest(row):
                raise SourceConflict("evidence/raw hash mismatch: "+iso(ts))
            if ts % STEP or ts+STEP > ev["observed_at_ms"] or row.get("timeframe") != "15m" or row.get("symbol") != "BTCUSDT.P":
                raise ValueError("source timestamp/instrument/timeframe contract")
            if row.get("candle_time_utc") and utc_ms(row["candle_time_utc"])!=ts:
                raise ValueError("source candle timestamp disagreement")
            if source(row) not in LIVE_SOURCES or not row.get("received_at_utc"):
                missing.append(ts);continue # Never silently use official/backfill observations.
            if utc_ms(row["received_at_utc"]) < ts+STEP or utc_ms(row["received_at_utc"]) > ev["observed_at_ms"] or row.get("is_closed") is False:
                raise ValueError("source completion/evidence clock contract")
            o,h,l,c=[number(row[x]) for x in ("open","high","low","close")]
            if min(o,h,l,c)<=0 or h<max(o,c,l) or l>min(o,c) or number(row["volume"])<0:
                raise ValueError("source OHLCV invalid")
            used[ts]=row
            result["source_refs"].append({"time":ts,"source":source(row),"symbol":row["symbol"],"market":"BINANCE_USDT_M_FUTURES",
                "canonical_row_hash":digest(row),"raw_ref":refs.get(ts),"observation_evidence":ev,
                "received_at_utc":row["received_at_utc"]})
        if anchor == ANCHORS[0]:
            ref=d["price_references"]["15m"]
            if ref["source"] not in LIVE_SOURCES or utc_ms(ref["candle_time_utc"]) != trigger or ref["raw_ref"] != d["trigger_15m"]["raw_ref"]:
                raise ValueError("diagnostic anchor source/reference contract")
            price=number(ref["close"])
            result["anchor_ref"]={"field":"close","raw_ref":ref["raw_ref"],"source":ref["source"],
                "available_at_ms":record["available_at_ms"],"availability_evidence":record.get("availability_evidence")}
        else:
            price=number(used[b]["open"]) if b in used else None
            result["anchor_ref"]={"field":"open","raw_ref":refs.get(b),"observation_evidence":evidence.get(b)}
        if price is not None and price<=0:raise ValueError("invalid anchor price")
        result.update(anchor_price=encode(price),missing_times=missing,unavailable_evidence_times=unavailable,
                      path_expected_count=len(range(b,end,STEP)),path_observed_count=sum(ts in used for ts in range(b,end,STEP)))
        if price is None or end-STEP not in used:
            result.update(status="SOURCE_MISSING",return_status="SOURCE_MISSING",excursion_status="SOURCE_MISSING")
            result["reason_codes"].append("ANCHOR_OR_ENDPOINT_MISSING")
            return result
        future=number(used[end-STEP]["close"])
        raw=100*(future/price-1)
        sign=0 if d["direction_class"] == "NEUTRAL" else 1 if "LONG" in d["direction_class"] else -1
        dr=sign*raw if sign else None
        quality=record.get("feature_quality",{}).get("atr_14",{})
        atr=record.get("features",{}).get("atr_14")
        atr=number(atr) if quality.get("ready") and quality.get("available_at_ms") is not None and quality["available_at_ms"]<=t and atr is not None else None
        if atr is not None and atr<0:raise ValueError("negative decision ATR")
        threshold=100*atr/price if atr is not None else None
        path=[used[ts] for ts in range(b,end,STEP) if ts in used]
        up=max(Decimal(0),100*(max(number(r["high"]) for r in path)/price-1)) if path else None
        down=max(Decimal(0),100*(1-min(number(r["low"]) for r in path)/price)) if path else None
        complete=not missing and not unavailable
        excursion="MATURED" if complete and anchor == ANCHORS[1] else "PARTIAL"
        result.update(status="MATURED" if excursion == "MATURED" else "PARTIAL",return_status="MATURED",excursion_status=excursion,
            endpoint_price=encode(future),endpoint_price_field="close",
            metrics=encode({"raw_return_pct":raw,"directional_return_pct":dr,
                "sign_hit":None if dr is None else "HIT" if dr>0 else "MISS" if dr<0 else "FLAT",
                "minimum_move_threshold_pct":threshold,"minimum_move_hit":None if dr is None or threshold is None else dr>=threshold,
                "up_excursion_pct":up,"down_excursion_pct":down,
                "mfe_pct":None if not sign else up if sign>0 else down,
                "mae_pct":None if not sign else down if sign>0 else up,
                "neutral_absolute_return_pct":abs(raw) if not sign else None,
                "neutral_max_abs_excursion_pct":max(up,down) if not sign and up is not None else None,
                "neutral_realized_range_pct":100*(max(number(r["high"]) for r in path)-min(number(r["low"]) for r in path))/price if not sign and path else None,
                "neutral_endpoint_within_band":abs(raw)<=threshold if not sign and threshold is not None else None,
                "neutral_path_within_band":max(up,down)<=threshold if not sign and threshold is not None and up is not None and excursion == "MATURED" else None,
                "neutral_up_band_crossed":up>threshold if not sign and threshold is not None and up is not None else None,
                "neutral_down_band_crossed":down>threshold if not sign and threshold is not None and down is not None else None}))
        result["minimum_move_basis"]={"timeframe":"15m","feature":"atr_14","decision_value":encode(atr),"multiplier":"1.0","status":"AVAILABLE" if atr is not None else "UNAVAILABLE"}
        if excursion == "PARTIAL":result["warnings"].append("INCOMPLETE_EXCURSION_OBSERVED_LOWER_BOUND")
        if anchor == ANCHORS[0]:result["warnings"].append("DIAGNOSTIC_PREDECISION_ANCHOR_AND_UNKNOWN_INITIAL_INTRABAR_PATH")
        if atr is None:result["warnings"].append("DECISION_15M_ATR_UNAVAILABLE")
        result["extreme_refs"]={"high":refs.get(max(path,key=lambda r:number(r["high"]))["time"]) if path else None,
                                "low":refs.get(min(path,key=lambda r:number(r["low"]))["time"]) if path else None}
        return result
    except SourceConflict as ex:
        result.update(status="SOURCE_CONFLICT",return_status="SOURCE_CONFLICT",excursion_status="SOURCE_CONFLICT",metrics=None)
        result["reason_codes"].append(str(ex));return result
    except (ValueError,KeyError,TypeError,ArithmeticError) as ex:
        result.update(status="INVALID",return_status="INVALID",excursion_status="INVALID",metrics=None)
        result["reason_codes"].append(type(ex).__name__+": "+str(ex));return result


class SourceConflict(ValueError):
    pass

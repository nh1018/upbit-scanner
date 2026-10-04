"""Feature V1 pure transformation. Availability is evidence, never nominal time."""
from copy import deepcopy
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
import hashlib
import json
from btc_anytime.integrity import DURATIONS, decimal, iso, utc_ms
from .registry import contract_matches

SCHEMA_VERSION = "btc-feature-v1"
ALGORITHM_VERSION = "1.0.0"
MANIFEST_VERSION = "1.0.0"
PARAMETERS = {"precision": 50, "output_places": 18, "rounding": "ROUND_HALF_EVEN",
              "returns": [1,3,6,12,24], "ema": [20,50], "slope_lag": 3,
              "rsi": 14, "atr": 14, "volume": 20, "rolling": [20,50],
              "pivot_left": 2, "pivot_right": 2, "freshness_allowance_ms": 960000}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


def encode(value):
    if isinstance(value, Decimal):
        with localcontext() as ctx:
            ctx.prec = 50
            ctx.rounding = ROUND_HALF_EVEN
            return format(value.quantize(Decimal("1e-18")), "f")
    if isinstance(value, dict):
        return {k: encode(v) for k,v in value.items()}
    if isinstance(value, (tuple,list)):
        return [encode(v) for v in value]
    return value


def source(row):
    return row.get("source") or ("legacy_webhook_inferred" if "received_at_utc" in row else "unrecorded")


def known_availability(row, duration, evidence):
    if not evidence or evidence.get("kind") != "consumer_first_observed" or not evidence.get("ref"):
        return None
    observed = evidence.get("observed_at_ms")
    if isinstance(observed, bool) or not isinstance(observed, int):
        raise ValueError("invalid availability observation")
    clocks = [row["time"]+duration, observed]
    for key in ("received_at_utc", "retrieved_at_utc", "emitted_at_utc"):
        if row.get(key):
            clocks.append(utc_ms(row[key]))
    return max(clocks)


def oi_contract(row, tf, registry):
    """A numeric OI is not sufficient evidence for a validated change segment."""
    if row.get("oi") is None:
        return None, None, "oi_unavailable"
    try:
        value = decimal(row["oi"])
        if value < 0:
            raise ValueError("negative")
    except ValueError:
        return None, None, "oi_invalid"
    if row.get("oi_status")=="unavailable":
        return None, None, "oi_status_conflict"
    start = row["time"]
    end = start+DURATIONS[tf]
    if row.get("oi_time_basis") == "confirmed_oi_bar_close_boundary":
        if (row.get("oi_time"), row.get("oi_period_start_ms"), row.get("oi_period_end_ms")) != (end,start,end):
            return value, None, "oi_alignment_invalid"
        provider, basis = "tradingview", "confirmed_oi_bar_close_boundary"
    elif row.get("oi_alignment") == "source_timestamp_equals_candle_open":
        if row.get("oi_timestamp_ms") != start or row.get("oi_source") != "binance_usdm_open_interest_hist":
            return value, None, "oi_alignment_invalid"
        provider, basis = "binance", "source_timestamp_equals_candle_open"
    else:
        return value, None, "oi_basis_unverified"
    instrument = "BTCUSDT_PERPETUAL"
    if row.get("oi_provider",provider)!=provider or row.get("oi_instrument",instrument)!=instrument or row.get("oi_timeframe",tf)!=tf:
        return value, None, "oi_provider_or_instrument_unverified"
    matches = [r for r in registry if contract_matches(row,tf,r) and all(r.get(k)==v for k,v in
               {"provider":provider,"instrument":instrument,"timeframe":tf,"basis":basis}.items())]
    if len(matches) != 1 or not matches[0].get("unit") or not matches[0].get("evidence"):
        return value, None, "oi_unit_unverified"
    unit = matches[0]["unit"]
    if row.get("oi_unit") and row["oi_unit"] != unit:
        return value, None, "oi_unit_conflict"
    signature = {"provider":provider,"instrument":instrument,"timeframe":tf,"basis":basis,"unit":unit}
    return value, signature, None


def build_timeframe(rows, tf, availability=None, unit_registry=(), raw_refs=None):
    """Sorted immutable input; duplicates fail closed. Invalid fields yield local nulls."""
    if tf not in DURATIONS:
        raise ValueError("unsupported timeframe")
    original = deepcopy(rows)
    ordered = sorted(original, key=lambda r:r["time"])
    times = [r["time"] for r in ordered]
    if any(isinstance(t,bool) or not isinstance(t,int) or t<0 for t in times):
        raise ValueError("invalid natural key")
    if len(times) != len(set(times)):
        raise ValueError("duplicate natural key")
    refs = raw_refs or {}
    evidence = availability or {}
    duration = DURATIONS[tf]
    known = [known_availability(r,duration,evidence.get(r["time"])) for r in ordered]
    out=[]
    prices=[]
    volumes=[]
    ema={20:[],50:[]}
    gains=[];losses=[];trs=[]
    avg_gain=avg_loss=atr=None
    pivots={"high":[],"low":[]}
    previous_oi=None;previous_sig=None;oi_start=None
    with localcontext() as ctx:
        ctx.prec=50;ctx.rounding=ROUND_HALF_EVEN
        for i,row in enumerate(ordered):
            t=row["time"]
            contiguous = i>0 and t-times[i-1]==duration
            boundary = (t%duration==0 and row.get("timeframe")==tf
                        and row.get("symbol") in ("BTCUSDT","BTCUSDT.P")
                        and row.get("market","BINANCE_USDT_M_FUTURES")=="BINANCE_USDT_M_FUTURES"
                        and (evidence.get(t) is None or evidence[t].get("observed_at_ms",t+duration)>=t+duration)
                        and row.get("is_closed") is not False
                        and row.get("close_time_ms",t+duration-1)==t+duration-1)
            try:
                boundary = boundary and utc_ms(row.get("candle_time_utc"))==t
            except (ValueError,TypeError,AttributeError):
                boundary=False
            try:
                o,h,l,c=[decimal(row.get(k)) for k in ("open","high","low","close")]
                pvalid=boundary and min(o,h,l,c)>0 and h>=max(o,c,l) and l<=min(o,c)
            except ValueError:
                pvalid=False
            try:
                v=decimal(row.get("volume"));vvalid=boundary and v>=0
            except ValueError:
                vvalid=False
            if not contiguous or not pvalid:
                prices=[];ema={20:[],50:[]};gains=[];losses=[];trs=[]
                avg_gain=avg_loss=atr=None;pivots={"high":[],"low":[]}
            if not contiguous or not vvalid:
                volumes=[]
            features={};quality={}
            def put(name,value,start=None,reason="warm_up"):
                start=i if start is None else start
                dependencies=known[start:i+1]
                available=max(dependencies) if dependencies and all(x is not None for x in dependencies) else None
                if value is None and reason=="warm_up" and not pvalid and not name.startswith("volume"):
                    reason="invalid_price"
                features[name]=value
                dependency_sources=[source(r) for r in ordered[start:i+1]]
                quality[name]={"ready":value is not None,"null_reason":None if value is not None else reason,
                    "dependency_start_index":start,"dependency_end_index":i,
                    "available_at_ms":available,
                    "sources":sorted(set(dependency_sources)),"mixed_sources":len(set(dependency_sources))>1,
                    "source_transition":any(a!=b for a,b in zip(dependency_sources,dependency_sources[1:]))}
            pstart=i-len(prices)
            if pvalid:
                if prices:
                    delta=c-prices[-1]["close"]
                    gains.append(max(delta,Decimal(0)));losses.append(max(-delta,Decimal(0)))
                    trs.append(max(h-l,abs(h-prices[-1]["close"]),abs(l-prices[-1]["close"])))
                prices.append({"high":h,"low":l,"close":c,"index":i})
            count=len(prices)
            for n in PARAMETERS["returns"]:
                value=100*(c/prices[-n-1]["close"]-1) if pvalid and count>n else None
                put("return_"+str(n),value,i-n if value is not None else pstart, "invalid_price" if not pvalid else "warm_up")
            for n in (20,50):
                current=None
                if pvalid and count>=n:
                    alpha=Decimal(2)/(n+1)
                    current=(sum(x["close"] for x in prices[:n])/n if count==n
                             else alpha*c+(1-alpha)*ema[n][-1])
                ema[n].append(current)
                put(f"ema_{n}",current,pstart)
                slope=100*(current/ema[n][-4]-1)/3 if current is not None and len(ema[n])>=4 and ema[n][-4] is not None else None
                put(f"ema_{n}_slope_3_pct_per_bar",slope,pstart)
                put(f"price_to_ema_{n}_pct",100*(c-current)/current if current is not None else None,pstart)
            if pvalid and len(gains)>=14:
                if len(gains)==14:
                    avg_gain=sum(gains)/14;avg_loss=sum(losses)/14;atr=sum(trs)/14
                else:
                    avg_gain=(13*avg_gain+gains[-1])/14
                    avg_loss=(13*avg_loss+losses[-1])/14
                    atr=(13*atr+trs[-1])/14
            rsi=None
            if avg_gain is not None:
                rsi=(Decimal(50) if avg_gain==avg_loss==0 else Decimal(100) if avg_loss==0
                     else Decimal(0) if avg_gain==0 else 100-100/(1+avg_gain/avg_loss))
            put("rsi_14",rsi,pstart);put("atr_14",atr,pstart)
            put("atr_pct",100*atr/c if atr is not None else None,pstart)
            if vvalid:volumes.append(v)
            ma=sum(volumes[-20:])/20 if len(volumes)>=20 else None
            vstart=i-min(len(volumes),20)+1 if volumes else i
            put("volume_ma_20",ma,vstart,"invalid_volume" if not vvalid else "warm_up")
            put("volume_ratio_20",v/ma if ma is not None and ma>0 else None,vstart,
                "invalid_volume" if not vvalid else "zero_denominator" if ma==0 else "warm_up")
            for n in (20,50):
                rh=max(x["high"] for x in prices[-n-1:-1]) if pvalid and count>n else None
                rl=min(x["low"] for x in prices[-n-1:-1]) if pvalid and count>n else None
                start=i-n if rh is not None else pstart
                put(f"rolling_high_{n}",rh,start);put(f"rolling_low_{n}",rl,start)
                put(f"breakout_{n}",c>rh if rh is not None else None,start)
                put(f"breakdown_{n}",c<rl if rl is not None else None,start)
                put(f"distance_to_rolling_high_{n}_pct",100*(rh-c)/rh if rh is not None else None,start)
                put(f"distance_to_rolling_low_{n}_pct",100*(c-rl)/rl if rl is not None else None,start)
            if pvalid and count>=5:
                window=prices[-5:];center=window[2]
                for kind,compare in (("high",lambda a,b:a>b),("low",lambda a,b:a<b)):
                    if all(compare(center[kind],x[kind]) for j,x in enumerate(window) if j!=2):
                        deps=known[center["index"]-2:i+1]
                        pivots[kind].append({"value":center[kind],"pivot_time":iso(times[center["index"]]),
                            "confirmation_candle_time":iso(t),"confirmation_close_exclusive":iso(t+duration),
                            "confirmed_at":iso(max(deps)) if all(x is not None for x in deps) else None,
                            "center_index":center["index"],"confirmation_index":i})
            for kind in ("high","low"):
                ps=pivots[kind]
                put("pivot_"+kind,deepcopy(ps[-1]) if ps else None,ps[-1]["center_index"]-2 if ps else pstart,"pivot_unconfirmed")
                state=None
                if len(ps)>=2:
                    a,b=ps[-2]["value"],ps[-1]["value"]
                    if a==b:
                        state="EQ"
                    elif kind=="high":
                        state="HH" if b>a else "LH"
                    else:
                        state="HL" if b>a else "LL"
                put("structure_"+kind+"_state",state,ps[-2]["center_index"]-2 if len(ps)>=2 else pstart,"pivot_pair_insufficient")
                quality["structure_"+kind+"_state"]["pivot_pair"]=deepcopy(ps[-2:]) if len(ps)>=2 else []
            oi,sig,oi_reason=oi_contract(row,tf,unit_registry)
            if not boundary:oi=None;sig=None;oi_reason="invalid_candle_boundary"
            same=boundary and contiguous and sig is not None and sig==previous_sig and previous_oi is not None
            if not same:oi_start=i
            segment=digest({"tf":tf,"start":times[oi_start],"signature":sig}) if sig is not None and boundary else None
            put("oi_absolute",oi,i,oi_reason or "oi_unavailable")
            change=oi-previous_oi if same else None
            pct=100*(oi/previous_oi-1) if same and previous_oi>0 else None
            put("oi_change",change,i-1 if same else i,oi_reason or "oi_segment_start")
            put("oi_change_pct",pct,i-1 if same else i,"zero_denominator" if same and previous_oi==0 else oi_reason or "oi_segment_start")
            price_return=features["return_1"]
            state=None
            if change is not None and price_return is not None and source(row)==source(ordered[i-1]):
                sign=lambda x:"UP" if x>0 else "DOWN" if x<0 else "FLAT"
                state=f"PRICE_{sign(price_return)}_OI_{sign(change)}"
            put("price_oi_state",state,i-1 if i else i,oi_reason or ("price_source_transition" if same and source(row)!=source(ordered[i-1]) else "oi_or_price_unavailable"))
            if sig:
                validation_times=[r["validation_available_at_ms"] for r in unit_registry
                                  if r.get("validation_available_at_ms") is not None and contract_matches(row,tf,r)
                                  and all(r.get(k)==v for k,v in sig.items())]
                if validation_times:
                    for name in ("oi_change","oi_change_pct","price_oi_state"):
                        q=quality[name]
                        if q["available_at_ms"] is not None:q["available_at_ms"]=max(q["available_at_ms"],max(validation_times))
            previous_oi=oi if sig is not None and boundary else None
            previous_sig=sig if boundary else None
            ref=deepcopy(refs.get(t,{"row_hash":digest(row),"time":t}))
            out.append(encode({"schema_version":SCHEMA_VERSION,"algorithm_version":ALGORITHM_VERSION,
                "market":"BINANCE_USDT_M_FUTURES","instrument":"BTCUSDT_PERPETUAL",
                "parameter_hash":digest(PARAMETERS),"timeframe":tf,"time":t,"candle_time_utc":iso(t),
                "candle_close_exclusive_utc":iso(t+duration),"available_at_ms":known[i],
                "availability_evidence":deepcopy(evidence.get(t)),"raw_ref":ref,
                "source":source(row),"input_validity":{"boundary":boundary,"price":pvalid,"volume":vvalid,
                    "oi":oi_reason not in ("oi_invalid","oi_status_conflict","oi_alignment_invalid")},
                "oi_metadata":{"signature":sig,"segment_id":segment,"null_reason":oi_reason,
                    "raw_basis":row.get("oi_time_basis") or row.get("oi_alignment") or "legacy_unspecified",
                    "raw_observation_time":row.get("oi_time",row.get("oi_timestamp_ms")),
                    "raw_unit":row.get("oi_unit"),"unit_status":"validated" if sig else "unavailable","registry_hash":digest(list(unit_registry)),
                    "registry_evidence":[r.get("evidence") for r in unit_registry if sig and all(r.get(k)==v for k,v in sig.items())]},
                "features":features,"feature_quality":quality}))
    return out

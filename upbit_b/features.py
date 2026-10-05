"""Pure, finite-window measurements. No scores, strategy labels or persistence."""
import hashlib
import json
import time
from datetime import datetime, timezone
from decimal import Decimal, localcontext, ROUND_HALF_EVEN

from .contracts import DURATIONS, MAPPING_STATES
from .feature_contracts import (ALGORITHM_VERSION, SCHEMA_VERSION, PARAMETER_VERSION,
                                PARAMETER_SHA256, FEATURES, REQUIRED, canonical, digest)

D = Decimal

def _clock_ms(text):
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset().total_seconds() != 0:
        raise ValueError("evidence clock must be UTC")
    delta = dt - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000

def _input_hash(candles):
    payload = [[c.provider, c.instrument, c.timeframe, c.open_ms,
                *[str(v) for v in (c.open,c.high,c.low,c.close,c.base_volume,c.quote_trade_amount)]] for c in candles]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()

def _ema(closes, period):
    result = [None] * len(closes)
    if len(closes) >= period:
        result[period-1] = sum(closes[:period], D(0)) / period
        alpha = D(2) / (period + 1)
        for i in range(period, len(closes)):
            result[i] = alpha * closes[i] + (1-alpha) * result[i-1]
    return result

def _atr(rows):
    result = [None] * len(rows)
    tr = [None] + [max(c.high-c.low, abs(c.high-rows[i-1].close), abs(c.low-rows[i-1].close))
                   for i,c in enumerate(rows) if i]
    if len(rows) >= 15:
        result[14] = sum(tr[1:15], D(0)) / 14
        for i in range(15, len(rows)):
            result[i] = (13*result[i-1]+tr[i])/14
    return result

def _pivots(rows):
    highs, lows = [], []
    for i in range(2, len(rows)-2):
        others = [rows[j] for j in (i-2,i-1,i+1,i+2)]
        for name, items, valid in (
            ("high", highs, all(rows[i].high > r.high for r in others)),
            ("low", lows, all(rows[i].low < r.low for r in others)),
        ):
            if valid:
                items.append({"index": i, "price": getattr(rows[i],name),
                              "pivot_open_ms": rows[i].open_ms,
                              "confirmation_boundary_ms": rows[i+2].close_ms})
    return highs, lows

def snapshot(window, generated_at_ms=None):
    """Latest per-TF snapshot. Evidence clocks do not enter the measurement hash."""
    generated = time.time_ns() // 1000000 if generated_at_ms is None else generated_at_ms
    if isinstance(generated, bool) or not isinstance(generated, int) or generated < 0:
        raise ValueError("integer generated clock required")
    with localcontext() as context:
        context.prec, context.rounding = 34, ROUND_HALF_EVEN
        return canonical(_snapshot(window, generated))

def _snapshot(window, generated):
    values = dict.fromkeys(FEATURES)
    status = dict.fromkeys(FEATURES, "WARMUP")
    metadata = {"source_status": window.status, "source_cutoff_ms": window.cutoff_ms,
                "input_sha256": window.input_sha256, "feature_generated_at_ms": generated,
                "required_lookback": REQUIRED, "evidence": [], "source_reason": window.reason}
    result = {"schema_version": SCHEMA_VERSION, "algorithm_version": ALGORITHM_VERSION,
              "parameter_version": PARAMETER_VERSION, "parameter_sha256": PARAMETER_SHA256,
              "features": values, "readiness": status, "metadata": metadata}

    def finish():
        result["ready_count"] = sum(s == "READY" for s in status.values())
        result["null_count"] = sum(v is None for v in values.values())
        def measurements(value):
            if isinstance(value,dict):
                return {k:measurements(v) for k,v in value.items() if k!="observed_at_ms"}
            return value
        result["measurement_sha256"] = digest({"features": measurements(values), "readiness": status,
            "algorithm": ALGORITHM_VERSION, "parameter": PARAMETER_SHA256,
            "input": window.input_sha256, "cutoff": window.cutoff_ms})
        return result

    def unavailable(reason):
        status.update(dict.fromkeys(FEATURES, reason))
        return finish()

    if window.status in ("API_ERROR", "INVALID_DATA"):
        return unavailable(window.status)
    if isinstance(window.cutoff_ms,bool) or not isinstance(window.cutoff_ms,int) or window.cutoff_ms<0:
        return unavailable("INVALID_INPUT")
    if window.status not in ("AVAILABLE", "INSUFFICIENT_DATA", "INCOMPLETE_COVERAGE"):
        return unavailable("INVALID_INPUT")
    rows = window.candles
    if not rows:
        return unavailable("INSUFFICIENT_DATA")
    provider, instrument, tf = rows[0].provider, rows[0].instrument, rows[0].timeframe
    metadata.update(provider=provider, instrument=instrument, timeframe=tf,
                    source_schema_version=rows[0].schema_version,
                    quote_unit=rows[0].quote_unit, market_type=rows[0].market_type,
                    window_start_ms=rows[0].open_ms, window_end_ms=rows[-1].close_ms)
    duration = DURATIONS.get(tf)
    if duration is None or provider not in ("UPBIT", "BINANCE_SPOT"):
        return unavailable("INVALID_INPUT")
    for i,c in enumerate(rows):
        nums = (c.open,c.high,c.low,c.close,c.base_volume,c.quote_trade_amount)
        if ((c.provider,c.instrument,c.timeframe) != (provider,instrument,tf)
            or not c.completed or isinstance(c.open_ms,bool) or not isinstance(c.open_ms,int)
            or not isinstance(c.close_ms,int) or c.open_ms < 0 or c.open_ms % duration
            or c.close_ms != c.open_ms+duration or c.close_ms > window.cutoff_ms
            or (i and c.open_ms <= rows[i-1].open_ms)
            or any(not isinstance(v,D) or not v.is_finite() for v in nums)):
            return unavailable("INVALID_INPUT")
        if (min(nums[:4]) <= 0 or min(nums[4:]) < 0
            or c.high < max(c.open,c.close,c.low) or c.low > min(c.open,c.close)):
            return unavailable("INVALID_INPUT")
    if _input_hash(rows) != window.input_sha256:
        return unavailable("INPUT_HASH_MISMATCH")
    try:
        if not window.evidence:
            raise ValueError("missing evidence")
        received = max(_clock_ms(ev["received_at_utc"]) for ev in window.evidence)
        for ev in window.evidence:
            if not ev.get("url") or not ev.get("response_sha256"):
                raise ValueError("missing source reference")
            metadata["evidence"].append({k:ev[k] for k in ("url","response_sha256","received_at_utc")})
        if generated < max(received, window.cutoff_ms):
            raise ValueError("generation precedes availability")
        metadata["source_received_at_ms"] = received
    except (KeyError, TypeError, ValueError):
        return unavailable("INVALID_EVIDENCE")
    if rows[-1].close_ms != window.cutoff_ms // duration * duration:
        return unavailable("STALE_INPUT")
    start = 0
    for i in range(1,len(rows)):
        if rows[i].open_ms != rows[i-1].close_ms:
            start = i
    segment = rows[start:]
    rows = segment
    n, t = len(rows), len(rows)-1
    close, quote = [c.close for c in rows], [c.quote_trade_amount for c in rows]
    metadata.update(continuous_segment_start_ms=rows[0].open_ms,
                    available_history=n, gap_reset=bool(start), history_truncated=True,
                    source_candle_open_ms=rows[-1].open_ms, source_candle_close_ms=rows[-1].close_ms)
    if start:
        status.update(dict.fromkeys(FEATURES,"GAP_RESET"))

    def put(name, value, reason="WARMUP"):
        values[name] = value
        status[name] = "READY" if value is not None else ("GAP_RESET" if start and reason=="WARMUP" else reason)

    def ratio(name, numerator, denominator, scale=D(1), offset=D(0)):
        if numerator is None or denominator is None:
            put(name,None)
        elif denominator == 0:
            put(name,None,"ZERO_DENOMINATOR")
        else:
            put(name,scale*(numerator/denominator-offset))

    def ret(i,k):
        return D(100)*(close[i]/close[i-k]-1) if i>=k else None

    for k in (1,3,5,10):
        put(f"return{k}_pct",ret(t,k))
    put("return3_acceleration_pp",ret(t,3)-ret(t-3,3) if n>=7 else None)
    streak = 0
    if n >= 2:
        for i in range(t,0,-1):
            if close[i] <= close[i-1]:
                break
            streak += 1
        put("positive_return_streak",streak)
    metadata["streak_truncated"] = n>=2 and streak==n-1
    ratio("signed_body_ratio",rows[t].close-rows[t].open,rows[t].high-rows[t].low)
    ratio("close_location",rows[t].close-rows[t].low,rows[t].high-rows[t].low)
    emas = {p:_ema(close,p) for p in (20,50)}
    atrs = _atr(rows)
    atr = atrs[t]
    put("atr14",atr)
    ratio("atr_pct",atr,close[t],D(100))
    metadata["seeds"] = {}
    for p in (20,50):
        e = emas[p]
        put(f"ema{p}",e[t])
        ratio(f"ema{p}_slope3_pct",e[t],e[t-3] if t>=3 else None,D(100),D(1))
        ratio(f"close_to_ema{p}_pct",close[t],e[t],D(100),D(1))
        metadata["seeds"][f"ema{p}"] = {"method":"SMA", "time_ms":rows[p-1].close_ms if n>=p else None,
            "updates":max(0,n-p), "residual_weight":(1-D(2)/(p+1))**(n-p) if n>=p else None}
    metadata["seeds"]["atr14"] = {"method":"mean_14_TR_then_Wilder", "time_ms":rows[14].close_ms if n>=15 else None,"updates":max(0,n-15)}
    ratio("ema20_to_ema50_pct",emas[20][t],emas[50][t],D(100),D(1))
    ratio("close_to_ema20_atr",close[t]-emas[20][t] if emas[20][t] is not None else None,atr)
    ratio("price_change3_atr",close[t]-close[t-3] if n>=4 else None,atr)
    put("ema20_upward_recross", close[t-1]<=emas[20][t-1] and close[t]>emas[20][t] if n>=21 else None)

    breakout_events = []
    if n>=21:
        hi,lo = max(c.high for c in rows[t-20:t]), min(c.low for c in rows[t-20:t])
        put("rolling_high20",hi); put("rolling_low20",lo)
        ratio("distance_to_rolling_high20_pct",close[t],hi,D(100),D(1))
        put("breakout20",close[t]>hi); put("breakdown20",close[t]<lo)
        previous = quote[t-20:t]
        mean = sum(previous,D(0))/20
        ordered = sorted(previous)
        median = (ordered[9]+ordered[10])/2
        put("quote_ma20_prior",mean);put("quote_median20_prior",median)
        ratio("quote_ratio_ma20",quote[t],mean);ratio("quote_ratio_median20",quote[t],median)
        for i in range(20,n):
            level = max(c.high for c in rows[i-20:i])
            if close[i]>level:
                breakout_events.append({"candle_open_ms":rows[i].open_ms,"confirmation_boundary_ms":rows[i].close_ms,
                    "level":level,"observed_at_ms":received})
    if n>=23:
        ratio("quote_recent3_vs_prior20",sum(quote[t-2:t+1],D(0))/3,sum(quote[t-22:t-2],D(0))/20)

    highs,lows = _pivots(rows)
    for name,pivots in (("high",highs),("low",lows)):
        latest = dict(pivots[-1]) if pivots else None
        if latest:
            latest.pop("index")
            latest["observed_at_ms"] = received
        put(f"confirmed_pivot_{name}",latest,"MISSING_STRUCTURE")
        label = None
        if len(pivots)>=2:
            a,b = pivots[-2]["price"],pivots[-1]["price"]
            if b==a:
                label="EQ"
            elif name=="high":
                label="HH" if b>a else "LH"
            else:
                label="HL" if b>a else "LL"
        put(f"{name}_structure",label,"MISSING_STRUCTURE")
    for name in ("swing_return_pct","swing_retracement_close","peak_to_close_atr","bars_since_swing_high","post_peak_quote_ratio","distance_to_confirmed_low_atr","distance_to_last_breakout_atr"):
        put(name,None,"MISSING_STRUCTURE")
    if lows:
        ratio("distance_to_confirmed_low_atr",close[t]-lows[-1]["price"],atr)
    if breakout_events:
        metadata["last_breakout"] = breakout_events[-1]
        ratio("distance_to_last_breakout_atr",close[t]-breakout_events[-1]["level"],atr)
    else:
        metadata["last_breakout"] = None
    origin = None
    if highs:
        peak = highs[-1]
        preceding = [p for p in lows if p["index"]<peak["index"]]
        origin = preceding[-1] if preceding else None
        if origin and peak["price"]>origin["price"]:
            a,p = origin["index"],peak["index"]
            metadata["swing_anchor"] = {"low":{k:v for k,v in origin.items() if k!="index"},
                "high":{k:v for k,v in peak.items() if k!="index"},"observed_at_ms":received}
            ratio("swing_return_pct",peak["price"],origin["price"],D(100),D(1))
            ratio("swing_retracement_close",peak["price"]-close[t],peak["price"]-origin["price"])
            ratio("peak_to_close_atr",peak["price"]-close[t],atr)
            put("bars_since_swing_high",t-p)
            ratio("post_peak_quote_ratio",sum(quote[p+1:t+1],D(0))/(t-p),sum(quote[a:p+1],D(0))/(p-a+1))
    metadata.setdefault("swing_anchor",None)
    return finish()

def bundle(upbit_windows, binance_windows=None, mapping=None, generated_at_ms=None):
    """Provider/TF separation; no cross-provider scores or confirmation labels."""
    generated = time.time_ns()//1000000 if generated_at_ms is None else generated_at_ms
    mapping = mapping or {"status":"API_UNAVAILABLE","symbol":None}
    if mapping.get("status") not in MAPPING_STATES:
        raise ValueError("unknown mapping state")
    instruments = set()
    for group,provider in ((upbit_windows,"UPBIT"),(binance_windows or {},"BINANCE_SPOT")):
        for tf,w in group.items():
            if tf not in DURATIONS or any(c.provider!=provider or c.timeframe!=tf for c in w.candles):
                raise ValueError("provider/timeframe envelope mismatch")
            if provider=="UPBIT":
                instruments.update(c.instrument for c in w.candles)
            elif any(c.instrument!=mapping.get("symbol") for c in w.candles):
                raise ValueError("counterpart mapping identity mismatch")
    if len(instruments)>1:
        raise ValueError("bundle must describe one Upbit instrument")
    upbit = {tf:snapshot(w,generated) for tf,w in upbit_windows.items()}
    counterpart = {}
    for tf in upbit_windows:
        w = (binance_windows or {}).get(tf)
        counterpart[tf] = snapshot(w,generated) if w is not None and mapping["status"] in ("VERIFIED","UNVERIFIED") else {
            "features":dict.fromkeys(FEATURES),"readiness":dict.fromkeys(FEATURES,"NO_COUNTERPART"),"ready_count":0,"null_count":len(FEATURES)}
    return {"schema_version":SCHEMA_VERSION,"upbit":upbit,"binance_spot":counterpart,"mapping":canonical(mapping)}

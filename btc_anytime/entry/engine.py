"""Pure prospective Entry V1: immutable Direction, independently available routes."""
from copy import deepcopy
from decimal import Decimal,localcontext,ROUND_HALF_EVEN
import json
from pathlib import Path
from btc_anytime.features.engine import digest,encode,SCHEMA_VERSION,ALGORITHM_VERSION,PARAMETERS
from btc_anytime.integrity import utc_ms,iso,DURATIONS

VERSION="1.0.0"
SCHEMA="btc-entry-v1"
STEP=900000
NAMES=("atr_14","ema_20","ema_50","volume_ratio_20","return_1","return_3",
       "rolling_high_20","rolling_low_20","breakout_20","breakdown_20",
       "pivot_high","pivot_low","structure_high_state","structure_low_state",
       "oi_absolute","oi_change","oi_change_pct","price_oi_state")


def parameters():
    p=json.loads(Path(__file__).with_name("parameters.json").read_text())
    p["parameter_hash"]=digest(p)
    return p


def number(x):
    if isinstance(x,bool) or x is None:raise ValueError("missing numeric input")
    v=Decimal(str(x))
    if not v.is_finite():raise ValueError("non-finite input")
    return v


def feature(item,name,E):
    q=item["quality"].get(name,{})
    if not q.get("ready") or q.get("available_at_ms") is None or q["available_at_ms"]>E:return None
    return item["features"].get(name)


def seal_manifest(items,direction_ref,generation_time):
    """Compact durable raw/feature inputs; no full Direction snapshot or manifest."""
    result={"schema_version":"btc-entry-input-v1","direction_ref":deepcopy(direction_ref),
            "feature_schema":SCHEMA_VERSION,"feature_algorithm":ALGORITHM_VERSION,"feature_parameter_hash":digest(PARAMETERS),
            "feature_generated_at_ms":generation_time,"items":deepcopy(sorted(items,key=lambda x:(x["timeframe"],x["time"])))}
    result["manifest_id"]=digest(result)
    return result


def direction_ref(d,observed):
    return {"decision_id":d["decision_id"],"content_hash":digest(d),"observed_at_ms":observed,
            "path":"output_direction/btc_anytime/v1/decisions/"+d["decision_id"]+".json",
            "decision_time_utc":d["decision_time_utc"],"parameter_version":d["parameter_version"],
            "parameter_hash":d["parameter_hash"],"engine_version":d["direction_engine_version"]}


def validate(manifest,d,E,p):
    if p.get("parameter_hash")!=digest({k:v for k,v in p.items() if k!="parameter_hash"}):raise ValueError("entry parameter hash failure")
    if manifest.get("manifest_id")!=digest({k:v for k,v in manifest.items() if k!="manifest_id"}):raise ValueError("input manifest hash failure")
    if manifest.get("feature_schema")!=SCHEMA_VERSION or manifest.get("feature_algorithm")!=ALGORITHM_VERSION or manifest.get("feature_parameter_hash")!=digest(PARAMETERS):raise ValueError("Feature contract mismatch")
    if manifest["feature_generated_at_ms"]>E:raise ValueError("future feature generation")
    if d.get("signal_history_schema_version")!="btc-direction-signal-history-v1" or d.get("decision_id")!=digest({k:v for k,v in d.items() if k!="decision_id"}):raise ValueError("not immutable prospective Direction")
    ref=manifest["direction_ref"]
    if ref["decision_id"]!=d["decision_id"] or ref["content_hash"]!=digest(d) or ref["observed_at_ms"]>E or utc_ms(d["decision_time_utc"])>E:raise ValueError("Direction unavailable at evaluation")
    items=[];seen=set()
    for item in manifest["items"]:
        tf=item["timeframe"];t=item["time"];raw=item["raw"];duration=DURATIONS[tf]
        # Ignore future suffix observations; they never enter the consumed-input identity.
        if t+duration>E or item["available_at_ms"]>E:continue
        if tf not in ("15m","1h"):raise ValueError("unexpected Entry timeframe")
        if (tf,t) in seen:raise SourceConflict("duplicate input key")
        seen.add((tf,t))
        if t%duration or raw["time"]!=t or raw.get("timeframe")!=tf or raw.get("is_closed") is False:raise ValueError("uncompleted/misaligned input")
        if raw.get("received_at_utc") and not t+duration<=utc_ms(raw["received_at_utc"])<=item["available_at_ms"]:raise ValueError("invalid source received clock")
        if item["available_at_ms"]<t+duration or item["generation_time_ms"]>E or item["generation_time_ms"]<item["available_at_ms"]:raise ValueError("invalid availability/generation")
        ev=item["availability_evidence"]
        if ev.get("kind")!="consumer_first_observed" or not ev.get("ref") or ev.get("canonical_row_hash")!=digest(raw):raise SourceConflict("raw/evidence hash mismatch")
        if ev["observed_at_ms"]>item["available_at_ms"] or not item["raw_ref"].get("file"):raise ValueError("incomplete source reference")
        o,h,l,c=[number(raw[k]) for k in ("open","high","low","close")]
        if min(o,h,l,c)<=0 or h<max(o,c,l) or l>min(o,c) or number(raw["volume"])<0:raise ValueError("invalid OHLCV")
        for name,q in item["quality"].items():
            pivot=item["features"].get(name)
            if name.startswith("pivot_") and q.get("ready") and pivot:
                if not pivot.get("confirmed_at") or utc_ms(pivot["confirmed_at"])>E or utc_ms(pivot["confirmation_close_exclusive"])>E:raise ValueError("unconfirmed pivot")
        items.append(item)
    return sorted(items,key=lambda x:(x["timeframe"],x["time"]))


class SourceConflict(ValueError):pass


def context_conflict(item,previous,s,E,p):
    if item is None or previous is None:return "UNAVAILABLE"
    if item["time"]-previous["time"]!=DURATIONS[item["timeframe"]]:return "UNAVAILABLE"
    high=feature(item,"structure_high_state",E);low=feature(item,"structure_low_state",E)
    flag=feature(item,"breakdown_20" if s==1 else "breakout_20",E);atr=feature(item,"atr_14",E)
    if high is None or low is None or flag is None or atr is None or number(atr)<=0:return "UNAVAILABLE"
    adverse=s*(number(item["raw"]["close"])-number(previous["raw"]["close"]))/number(atr)
    opposing=(high=="LH" and low=="LL") if s==1 else (high=="HH" and low=="HL")
    return "STRONG" if flag and opposing and adverse<=number(p["conflict_movement_max"]) else "NONE"


def chase(C,ema,A,movement,s,p,setup=None):
    ext=s*(C-ema)/A
    risk=ext>number(p["ema20_chase_gt"]) or (movement>number(p["movement3_chase_gt"]) and ext>number(p["movement3_extension_gt"]))
    dist=None
    if setup and setup["type"]=="BREAKOUT":
        dist=s*(C-number(setup["reference"]["value"]))/number(setup["A0"])
        risk=risk or dist>number(p["breakout_chase_gt"])
    return {"veto":risk,"ema20_extension_atr":ext,"movement3_atr":movement,"breakout_extension_atr":dist}


def pullback(history,current,s,E,p):
    result={"status":"UNAVAILABLE","measurements":{},"reference":None,"episode":None}
    atr=feature(current,"atr_14",E);ema20=feature(current,"ema_20",E);ema50=feature(current,"ema_50",E)
    pivot=feature(current,"pivot_low" if s==1 else "pivot_high",E)
    rolling=feature(current,"rolling_low_20" if s==1 else "rolling_high_20",E)
    if atr is None or number(atr)<=0 or ema20 is None or ema50 is None or len(history)<4:return result
    ref=({"type":"CONFIRMED_PIVOT","value":pivot["value"],"pivot_time":pivot["pivot_time"],"confirmed_at":pivot["confirmed_at"]} if pivot else
         {"type":"ROLLING_20","value":rolling,"time":current["time"]} if rolling is not None else None)
    if ref is None:return result
    impulses=[];unavailable=False
    # The current correction candle is excluded from impulse origins.
    lag=p["impulse_lag_bars"]
    for j in range(max(lag,len(history)-1-p["impulse_search_bars"]),len(history)-1):
        A=feature(history[j],"atr_14",E)
        if A is None or number(A)<=0:unavailable=True;continue
        Cj=number(history[j]["raw"]["close"]);start=number(history[j-lag]["raw"]["close"])
        if s*(Cj-start)/number(A)>=number(p["impulse_min_atr"]):impulses.append((j,start))
    if not impulses:
        result["status"]="UNAVAILABLE" if unavailable else "FAIL";return result
    j,start=impulses[-1];extreme=(max if s==1 else min)(number(x["raw"]["high" if s==1 else "low"]) for x in history[j-2:j+1])
    A0=number(atr);C=number(current["raw"]["close"]);amp=s*(extreme-start);depth=s*(extreme-C)/A0
    ratio=s*(extreme-C)/amp if amp>0 else None
    distances=[abs(C-number(ema20))/A0,abs(C-number(ema50))/A0]
    # Prospective/known historical breakout reference is optional location evidence.
    for prior in history[-p["impulse_search_bars"]:]:
        if feature(prior,"breakout_20" if s==1 else "breakdown_20",E):
            level=feature(prior,"rolling_high_20" if s==1 else "rolling_low_20",E)
            if level is not None:distances.append(abs(C-number(level))/A0)
    cooling=any(s*(number(b["raw"]["close"])-number(a["raw"]["close"]))<0 for a,b in zip(history[j:],history[j+1:]))
    intact=s*(C-number(ref["value"]))>=-number(p["failure_buffer"])*A0
    result.update(reference=ref,episode={"impulse_end":history[j]["time"],"extreme":extreme,"start_close":start},
                  measurements={"impulse_amplitude":amp,"depth_atr":depth,"retracement_ratio":ratio,"reference_distance_atr":min(distances),"cooling":cooling})
    result["status"]="PASS" if amp>0 and ratio is not None and number(p["pullback_depth_min"])<=depth<=number(p["pullback_depth_max"]) and ratio<=number(p["retracement_max"]) and min(distances)<=number(p["reference_proximity_max"]) and cooling and intact else "FAIL"
    return result


def breakout(history,current,s,E,p):
    flag=feature(current,"breakout_20" if s==1 else "breakdown_20",E);prev=feature(history[-2],"breakout_20" if s==1 else "breakdown_20",E) if len(history)>1 else None
    level=feature(current,"rolling_high_20" if s==1 else "rolling_low_20",E);vol=feature(current,"volume_ratio_20",E);A=feature(current,"atr_14",E)
    result={"status":"UNAVAILABLE","measurements":{},"reference":None,"episode":None}
    if any(v is None for v in (flag,prev,level,vol,A)) or number(A)<=0:return result
    raw=current["raw"];C,H,L=number(raw["close"]),number(raw["high"]),number(raw["low"])
    location=((C-L) if s==1 else (H-C))/(H-L) if H>L else None
    magnitude=s*(C-number(level))/number(A)
    result.update(reference={"type":"PRIOR_ROLLING_20","value":level,"time":current["time"]},episode={"breakout_origin":current["time"]},measurements={"breakout_magnitude":magnitude,"close_location":location})
    result["status"]="PASS" if flag and prev is False and magnitude>=number(p["breakout_min_atr"]) and number(vol)>=number(p["breakout_volume_min"]) and location is not None and location>=number(p["close_location_min"]) else "FAIL"
    return result


def evaluate(d,manifest,E,previous=None,p=None):
    p=parameters() if p is None else deepcopy(p)
    with localcontext() as ctx:
        ctx.prec=p["decimal_precision"];ctx.rounding=ROUND_HALF_EVEN
        return _evaluate(d,manifest,E,previous,p)


def _evaluate(d,manifest,E,previous,p):
    state=deepcopy(previous["state"]) if previous else {"last_boundary":None,"setup":None,"last_terminal_boundary":None,"last_episode":None}
    out={"schema_version":SCHEMA,"algorithm_version":VERSION,"parameter_version":p["parameter_version"],"parameter_hash":p["parameter_hash"],
         "evaluation_time_utc":iso(E),"upstream_direction_decision_id":d.get("decision_id"),
         "direction":d.get("direction_class"),"direction_score":d.get("direction_score"),"direction_confidence":d.get("confidence"),"regime":d.get("regime"),
         "execution_status":"EVALUATED","entry_state":None,"setup_type":None,"setup_side":None,
         "routes":{},"chase_state":None,"conflict_state":None,"volume_state":None,"oi_state":None,
         "derived_measurements":{},"reference":None,"setup_quality":{},"reason_codes":[],"warnings":[],
         "input_manifest_id":manifest.get("manifest_id"),"previous_evaluation_id":previous.get("entry_evaluation_id") if previous else None,"state":state}
    def finish(status,business,reason):
        out["execution_status"]=status;out["entry_state"]=business
        if reason:out["reason_codes"].append(reason)
        out["state"]=encode(state);out.update({k:encode(v) for k,v in out.items() if k!="state"})
        out["entry_evaluation_id"]=digest(out);return out
    def terminal(lifecycle,reason):
        setup=state["setup"];setup["lifecycle"]=lifecycle;setup["terminal_at"]=E
        state["last_terminal_boundary"]=current["time"];state["last_episode"]=setup["episode_id"]
        out["reason_codes"].append(reason)
    try:
        if previous and previous.get("entry_evaluation_id")!=digest({k:v for k,v in previous.items() if k!="entry_evaluation_id"}):raise ValueError("previous Entry hash failure")
        if previous and previous["parameter_hash"]!=p["parameter_hash"]:raise ValueError("explicit version rollout required")
        items=validate(manifest,d,E,p)
        consumed=seal_manifest(items,manifest["direction_ref"],manifest["feature_generated_at_ms"])
        out["input_manifest_id"]=consumed["manifest_id"]
        history=[x for x in items if x["timeframe"]=="15m"]
        if not history:return finish("SKIPPED_FEATURE_UNAVAILABLE",None,"INPUT.NO_AVAILABLE_15M")
        current=history[-1];t=current["time"];out["trigger_boundary_id"]=digest({"ledger":"btc-entry-v1","time":t,"symbol":"BTCUSDT.P"});out["trigger_time"]=t
        if state["last_boundary"]==t:return deepcopy(previous) # Preserve original timestamp, inputs and authorization.
        if state["last_boundary"] is not None and t<state["last_boundary"]:raise ValueError("historical Entry reconstruction forbidden")
        prior_boundary=state["last_boundary"];state["last_boundary"]=t
        if prior_boundary is not None and t-prior_boundary!=STEP:
            if state["setup"] and state["setup"]["lifecycle"]=="ARMED":terminal("INVALIDATED","ENTRY.BOUNDARY_GAP")
            return finish("SKIPPED_BOUNDARY_MISSED",None,"INPUT.MISSED_BOUNDARY_NOT_RECONSTRUCTED")
        if E-(t+STEP)>STEP+PARAMETERS["freshness_allowance_ms"]:return finish("SKIPPED_FEATURE_UNAVAILABLE",None,"INPUT.STALE_15M")
        if current["raw"].get("source") not in (None,"legacy_webhook_inferred","tradingview_binance_usdm_htf") or not current["raw"].get("received_at_utc"):return finish("SKIPPED_FEATURE_UNAVAILABLE",None,"INPUT.NON_LIVE_TRIGGER")
        out["direction_age_ms"]=E-utc_ms(d["decision_time_utc"])
        lag=t-d["trigger_15m"]["candle_open_time_ms"]
        if lag<0:raise ValueError("Direction trigger after Entry trigger")
        if out["direction_age_ms"]>p["direction_max_age_ms"] or lag>p["upstream_trigger_lag_bars"]*STEP:
            if state["setup"] and state["setup"]["lifecycle"]=="ARMED":terminal("EXPIRED","ENTRY.SETUP_EXPIRED")
            return finish("SKIPPED_STALE_DIRECTION",None,"INPUT.STALE_DIRECTION")
        side="LONG" if d["direction_class"] in ("LONG","STRONG_LONG") else "SHORT" if d["direction_class"] in ("SHORT","STRONG_SHORT") else None
        out["setup_side"]=side
        active=state["setup"] if state["setup"] and state["setup"]["lifecycle"]=="ARMED" else None
        if active and active["side"]!=side:
            terminal("INVALIDATED","ENTRY.DIRECTION_SUPERSEDED")
            return finish("EVALUATED","NO_ENTRY","ENTRY.NO_DIRECTIONAL_ENTRY" if side is None else "ENTRY.SETUP_INVALIDATED")
        if side is None:return finish("EVALUATED","NO_ENTRY","ENTRY.NO_DIRECTIONAL_ENTRY")
        if d["regime"] in ("POSSIBLE_REVERSAL","INSUFFICIENT_EVIDENCE") or number(d["confidence"])<number(p["confidence_min"]):
            if active:terminal("INVALIDATED","ENTRY.AUTHORIZATION_REVOKED")
            return finish("EVALUATED","NO_ENTRY","ENTRY.AUTHORIZATION_VETO")
        s=1 if side=="LONG" else -1
        if active and active["authorizing_direction_decision_id"]!=d["decision_id"]:
            active["authorizing_direction_decision_id"]=d["decision_id"];out["reason_codes"].append("ENTRY.SAME_SIDE_AUTHORIZATION_UPDATED")
        one=[x for x in items if x["timeframe"]=="1h"]
        if not one or E-(one[-1]["time"]+3600000)>3600000+PARAMETERS["freshness_allowance_ms"]:return finish("SKIPPED_FEATURE_UNAVAILABLE",None,"INPUT.NO_FRESH_1H")
        if any(b["time"]-a["time"]!=STEP for a,b in zip(history,history[1:])):
            if active:terminal("INVALIDATED","ENTRY.BOUNDARY_GAP")
            return finish("SKIPPED_FEATURE_UNAVAILABLE",None,"INPUT.PATH_GAP")
        C,H,L=[number(current["raw"][k]) for k in ("close","high","low")];A=feature(current,"atr_14",E);ema=feature(current,"ema_20",E);vol=feature(current,"volume_ratio_20",E)
        if A is None or ema is None or number(A)<=0 or len(history)<4:return finish("SKIPPED_FEATURE_UNAVAILABLE",None,"INPUT.CORE_FEATURE_UNAVAILABLE")
        A=number(A);movement=s*(C-number(history[-4]["raw"]["close"]))/A
        out["derived_measurements"]={"close":C,"atr_14":A,"ema_20":number(ema),"movement3_atr":movement,
            "close_location":((C-L) if s==1 else (H-C))/(H-L) if H>L else None}
        out["volume_state"]={"ratio":vol,"confirmation":"UNAVAILABLE" if vol is None else "PASS" if number(vol)>=number(p["confirmation_volume_min"]) else "FAIL", "contraction_context":None if vol is None else number(vol)<=number(p["contraction_max"])}
        oi=feature(current,"oi_change",E);price_delta=s*(C-number(history[-2]["raw"]["close"]))
        out["oi_state"]="UNAVAILABLE" if oi is None or current["oi_metadata"].get("unit_status")!="validated" else "CONFIRMING" if number(oi)>0 and price_delta>0 else "CONFLICTING" if number(oi)>0 and price_delta<0 else "DECLINING_CONTEXT" if number(oi)<0 else "FLAT_CONTEXT"
        out["reason_codes"].append("ENTRY.OI_"+out["oi_state"])
        c15=context_conflict(current,history[-2],s,E,p);c1=context_conflict(one[-1],one[-2] if len(one)>1 else None,s,E,p)
        out["conflict_state"]={"15m":c15,"1h":c1}
        if "STRONG" in (c15,c1):
            if active:terminal("INVALIDATED","ENTRY.SETUP_INVALIDATED")
            return finish("EVALUATED","NO_ENTRY","ENTRY.SHORT_TERM_CONFLICT")
        if active:
            out["setup_type"]=active["type"]
            if E>=active["expires_at"]:
                terminal("EXPIRED","ENTRY.SETUP_EXPIRED");return finish("EVALUATED","NO_ENTRY",None)
            A0=number(active["A0"]);ref=number(active["reference"]["value"])
            if s*(C-ref)<-number(p["failure_buffer"])*A0:
                terminal("INVALIDATED","ENTRY.REFERENCE_FAILURE");return finish("EVALUATED","NO_ENTRY",None)
            frozen_movement=s*(C-number(history[-4]["raw"]["close"]))/A0
            risk=chase(C,number(ema),A0,frozen_movement,s,p,active);out["chase_state"]=risk
            if risk["veto"]:return finish("EVALUATED","WAIT","ENTRY.CHASE_RISK")
            loc=out["derived_measurements"]["close_location"]
            if vol is None:return finish("SKIPPED_FEATURE_UNAVAILABLE",None,"INPUT.CONFIRMATION_VOLUME_UNAVAILABLE")
            separate=t>active["detected_boundary"]
            reclaim=(s*(C-number(history[-2]["raw"]["high" if s==1 else "low"]))>0 if active["type"]=="PULLBACK" else s*(C-ref)/A0>=number(p["hold_min_atr"]))
            confirmation=separate and price_delta>0 and reclaim and loc is not None and loc>=number(p["close_location_min"]) and number(vol)>=number(p["confirmation_volume_min"])
            if confirmation:
                out["confirmation_type"]="REACCELERATION" if active["type"]=="PULLBACK" else "BREAKOUT_HOLD"
                terminal("CONFIRMED","ENTRY.REACCELERATION_CONFIRMED" if active["type"]=="PULLBACK" else "ENTRY.BREAKOUT_HOLD_CONFIRMED")
                out["reference"]={"type":"CONFIRMATION_CANDLE_CLOSE","price":C,"time":t+STEP,"raw_ref":current["raw_ref"],"role":"observed reference, not fill/first tick/execution price"}
                out["reference_price_type"]="CONFIRMATION_CANDLE_CLOSE";out["reference_price"]=C;out["reference_time"]=iso(t+STEP)
                out["setup_quality"]={"location":"PASS","confirmation":"PASS","participation":"PASS","extension":"PASS","derivatives":out["oi_state"]}
                return finish("EVALUATED","ENTRY_CANDIDATE","ENTRY.CANDIDATE_CREATED")
            return finish("EVALUATED","WAIT","ENTRY.CONFIRMATION_PENDING")
        out["routes"]={"PULLBACK":pullback(history,current,s,E,p),"BREAKOUT":breakout(history,current,s,E,p)}
        risk=chase(C,number(ema),A,movement,s,p);out["chase_state"]=risk
        if all(v["status"]=="UNAVAILABLE" for v in out["routes"].values()):return finish("SKIPPED_FEATURE_UNAVAILABLE",None,"INPUT.ALL_ROUTES_UNAVAILABLE")
        if state["last_terminal_boundary"] is not None and t-state["last_terminal_boundary"]<p["rearm_bars"]*STEP:return finish("EVALUATED","WAIT","ENTRY.REARM_REQUIRED")
        chosen=next((k for k in ("PULLBACK","BREAKOUT") if out["routes"][k]["status"]=="PASS"),None)
        if chosen:
            route=out["routes"][chosen];episode=digest(encode({"side":side,"type":chosen,"episode":route["episode"]}))
            if episode==state["last_episode"]:return finish("EVALUATED","WAIT","ENTRY.REARM_REQUIRED")
            setup={"type":chosen,"side":side,"lifecycle":"ARMED","detected_boundary":t,"first_detected_at":E,
                "A0":A,"reference":deepcopy(route["reference"]),"episode_id":episode,
                "origin_direction_decision_id":d["decision_id"],"authorizing_direction_decision_id":d["decision_id"],
                "expires_at":t+STEP+(p["pullback_expiry_bars"] if chosen=="PULLBACK" else p["breakout_expiry_bars"])*STEP}
            setup["setup_id"]=digest(encode({"algorithm":VERSION,"parameter_hash":p["parameter_hash"],"side":side,"type":chosen,"origin_boundary":t,"direction_id":d["decision_id"],"reference":setup["reference"],"episode_id":episode}))
            state["setup"]=setup;out["setup_type"]=chosen
            risk=chase(C,number(ema),A,movement,s,p,setup);out["chase_state"]=risk
            out["reason_codes"].append("ENTRY."+chosen+"_ARMED")
            return finish("EVALUATED","WAIT","ENTRY.CHASE_RISK" if risk["veto"] else "ENTRY.CONFIRMATION_PENDING")
        return finish("EVALUATED","NO_ENTRY","ENTRY.NO_SETUP")
    except SourceConflict as ex:
        out["warnings"].append(str(ex));return finish("FAILED_SOURCE_CONFLICT",None,"INPUT.SOURCE_CONFLICT")
    except (ValueError,KeyError,TypeError,ArithmeticError) as ex:
        out["warnings"].append(type(ex).__name__+": "+str(ex));return finish("FAILED_CONTRACT",None,"INPUT.CONTRACT_FAILURE")

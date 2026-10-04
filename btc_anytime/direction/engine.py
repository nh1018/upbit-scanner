"""Pure, deterministic Direction V1. Optional OI never changes directional score."""
from copy import deepcopy
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
import json
from pathlib import Path
from btc_anytime.features.engine import digest, encode, PARAMETERS, SCHEMA_VERSION, ALGORITHM_VERSION
from btc_anytime.integrity import DURATIONS, utc_ms

VERSION="1.0.0"
SCHEMA="btc-direction-v1"
D=Decimal


def load_parameters(path=None):
    p=json.loads((path or Path(__file__).parent/"parameters/initial_hypothesis_1.json").read_text(encoding="utf-8"))
    validate_parameters(p)
    return p


def validate_parameters(p):
    if p.get("schema_version")!="btc-direction-parameters-v1" or p.get("parameter_hash")!=digest({k:v for k,v in p.items() if k!="parameter_hash"}):
        raise ValueError("direction parameter contract/hash mismatch")
    for group in ("tf_weights","component_weights","trend_weights","structure_weights","coverage_weights","confidence_weights"):
        values=[D(x) for x in p[group].values()]
        if any(not x.is_finite() or x<0 for x in values) or sum(values)!=1:raise ValueError("invalid weights")
    if set(p["tf_weights"])!=set(DURATIONS) or D(p["tf_weights"]["15m"])!=0:raise ValueError("15m must be context only")
    if set(p["component_weights"])!={"trend","structure","momentum"}:raise ValueError("direction component contract")
    for key in ("slope_saturation_atr_per_bar","momentum_saturation_atr","volume_saturation_ratio"):
        if not D(p[key]).is_finite() or D(p[key])<=0:raise ValueError("invalid normalization parameter")
    for value in p["thresholds"].values():
        if not D(value).is_finite() or not 0<=D(value)<=1:raise ValueError("invalid threshold")
    if not D(p["thresholds"]["direction"])<D(p["thresholds"]["strong"]):raise ValueError("threshold order")
    if p["decimal_precision"]!=50 or p["output_places"]!=18:raise ValueError("unsupported arithmetic contract")


def clip(x):return max(D(-1),min(D(1),x))
def sign(x):return 1 if x>0 else -1 if x<0 else 0


def validate_snapshot(s):
    if s.get("snapshot_id")!=digest({k:v for k,v in s.items() if k!="snapshot_id"}):raise ValueError("snapshot identity mismatch")
    if s.get("schema_version")!=SCHEMA_VERSION or s.get("algorithm_version")!=ALGORITHM_VERSION:raise ValueError("feature schema mismatch")
    t=utc_ms(s["decision_time_utc"])
    if set(s["timeframes"])!=set(DURATIONS):raise ValueError("snapshot timeframe set mismatch")
    for tf,selection in s["timeframes"].items():
        r=selection.get("record")
        if r is None:continue
        if (r.get("timeframe")!=tf or r.get("instrument")!="BTCUSDT_PERPETUAL" or r.get("market")!="BINANCE_USDT_M_FUTURES" or
            r.get("schema_version")!=SCHEMA_VERSION or r.get("algorithm_version")!=ALGORITHM_VERSION or
            r.get("parameter_hash")!=digest(PARAMETERS)):raise ValueError("feature input contract mismatch")
        if (not isinstance(r["time"],int) or isinstance(r["time"],bool) or r["time"]%DURATIONS[tf] or
            utc_ms(r["candle_close_exclusive_utc"])!=r["time"]+DURATIONS[tf] or r["time"]+DURATIONS[tf]>t):raise ValueError("uncompleted/misaligned feature")
        if r.get("available_at_ms") is None or r["available_at_ms"]>t:raise ValueError("future raw availability")
        if r.get("feature_generated_at_ms") is None or r["feature_generated_at_ms"]>t:raise ValueError("feature generation evidence missing/future")
        evidence=r.get("availability_evidence") or {}
        if evidence.get("kind")!="consumer_first_observed" or not evidence.get("ref"):raise ValueError("availability evidence missing")
        if (not r.get("generation_evidence_ref") or not isinstance(evidence.get("observed_at_ms"),int) or
            evidence["observed_at_ms"]>r["available_at_ms"] or r["available_at_ms"]<r["time"]+DURATIONS[tf] or
            r["feature_generated_at_ms"]<r["available_at_ms"]):raise ValueError("invalid availability/generation evidence")
        if set(r["features"])!=set(r["feature_quality"]):raise ValueError("feature quality contract mismatch")
        for name,q in r["feature_quality"].items():
            if q["ready"] and (r["features"][name] is None or q.get("available_at_ms") is None or q["available_at_ms"]>t or q["available_at_ms"]<r["feature_generated_at_ms"]):
                raise ValueError("unavailable dependency marked ready")
    return t


def components(selection,tf,t,p):
    r=selection.get("record");reasons=[];missing=[];stale=[];evidence={}
    values={"trend":None,"structure":None,"momentum":None,"participation":None,"derivatives":None}
    result={"components":values,"score":None,"pivot_structure":None,"range_event":None,
            "missing_inputs":missing,"stale_inputs":stale,"reason_codes":reasons,"feature_evidence":evidence,
            "data_quality":D(0),"candle_time_utc":None,"available_at_ms":None,"oi_metadata":None}
    if r is None:
        reasons.append("INPUT.MISSING_TF");return result
    result.update(candle_time_utc=r["candle_time_utc"],available_at_ms=r["available_at_ms"],oi_metadata=deepcopy(r["oi_metadata"]))
    is_stale=t-(r["time"]+DURATIONS[tf])>DURATIONS[tf]+PARAMETERS["freshness_allowance_ms"]
    if is_stale:
        stale.append(tf);reasons.append("INPUT.STALE_TF");return result
    if not all(r["input_validity"].get(k,False) for k in ("boundary","price","volume")):
        reasons.append("INPUT.INVALID_FEATURE");return result
    result["data_quality"]=D(1)
    def get(name,numeric=True):
        q=r["feature_quality"].get(name,{})
        v=r["features"].get(name)
        evidence[name]={"value":v,"quality":deepcopy(q),"raw_ref":deepcopy(r["raw_ref"]),"input_feature_result_hash":r.get("input_feature_result_hash")}
        if not q.get("ready") or v is None:
            missing.append(name);return None
        if numeric:
            if isinstance(v,bool):raise ValueError("boolean numeric feature")
            try:x=D(str(v))
            except Exception as ex:raise ValueError("non-numeric feature") from ex
            if not x.is_finite():raise ValueError("nonfinite feature")
            return x
        return v
    e20=get("ema_20");e50=get("ema_50");a=get("atr_14")
    sl20=get("ema_20_slope_3_pct_per_bar");sl50=get("ema_50_slope_3_pct_per_bar")
    if all(x is not None for x in (e20,e50,a,sl20,sl50)) and a>0 and e20>0 and e50>0:
        tw=p["trend_weights"]
        values["trend"]=(D(tw["spread"])*clip((e20-e50)/a)+
            D(tw["slope20"])*clip(e20*sl20/100/(D(p["slope_saturation_atr_per_bar"])*a))+
            D(tw["slope50"])*clip(e50*sl50/100/(D(p["slope_saturation_atr_per_bar"])*a)))
    else:reasons.append("INPUT.TREND_UNAVAILABLE")
    hi=get("structure_high_state",False);lo=get("structure_low_state",False)
    bo=get("breakout_20",False);bd=get("breakdown_20",False)
    if bo is not None and not isinstance(bo,bool) or bd is not None and not isinstance(bd,bool):raise ValueError("invalid range flag")
    if bo is True and bd is True:raise ValueError("simultaneous breakout/breakdown")
    if hi is not None and hi not in ("HH","LH","EQ") or lo is not None and lo not in ("HL","LL","EQ"):raise ValueError("invalid pivot state")
    if hi is not None and lo is not None:result["pivot_structure"]=(D({"HH":1,"LH":-1,"EQ":0}[hi])+D({"HL":1,"LL":-1,"EQ":0}[lo]))/2
    if bo is not None and bd is not None:result["range_event"]=D(1 if bo else -1 if bd else 0)
    if result["pivot_structure"] is not None and result["range_event"] is not None:
        values["structure"]=D(p["structure_weights"]["pivot"])*result["pivot_structure"]+D(p["structure_weights"]["range"])*result["range_event"]
    else:reasons.append("INPUT.STRUCTURE_UNAVAILABLE")
    ret=get("return_6");ap=get("atr_pct")
    if ret is not None and ap is not None and ap>0:values["momentum"]=clip(ret/(D(p["momentum_saturation_atr"])*ap))
    else:reasons.append("INPUT.MOMENTUM_UNAVAILABLE")
    vr=get("volume_ratio_20")
    if vr is not None and vr>=0:values["participation"]=min(D(1),vr/D(p["volume_saturation_ratio"]))
    oc=get("oi_change");op=get("oi_change_pct");state=get("price_oi_state",False)
    md=r["oi_metadata"]
    if (oc is not None and op is not None and state is not None and md.get("signature") and md.get("segment_id") and md.get("unit_status")=="validated" and r["input_validity"].get("oi")):
        sig=md["signature"]
        if sig.get("timeframe")!=tf or sig.get("instrument")!="BTCUSDT_PERPETUAL" or not all(sig.get(k) for k in ("provider","basis","unit")):
            raise ValueError("OI signature scope mismatch")
        if state not in {f"PRICE_{ps}_OI_{os}" for ps in ("UP","DOWN","FLAT") for os in ("UP","DOWN","FLAT")}:raise ValueError("invalid OI state")
        oi_part=state.split("_OI_")[1]
        if {"UP":1,"DOWN":-1,"FLAT":0}[oi_part]!=sign(oc):raise ValueError("OI state/change conflict")
        if sign(op)!=sign(oc):raise ValueError("OI percent/change conflict")
        values["derivatives"]={"state":state,"change":oc,"change_pct":op,"segment_id":md["segment_id"],"signature":md["signature"]}
    else:reasons.append("OI."+(md.get("null_reason") or "unavailable").removeprefix("oi_").upper())
    if values["trend"] is not None and values["momentum"] is not None:
        cw=p["component_weights"]
        contributions={k:D(cw[k])*(values[k] if values[k] is not None else D(0)) for k in cw}
        result["score"]=sum(contributions.values());result["score_contributions"]=contributions
    else:result["score_contributions"]={}
    result["missing_inputs"]=sorted(set(missing))
    return result


def confidence(tf_results,bias,p):
    th=p["thresholds"];coverage=alignment=quality=agree_num=oi_weight=oi_num=D(0)
    agree_den=D(1) # Fixed core TF/component weights; missing core evidence is not redistributed.
    oi_evidence={}
    for tf,w_text in p["tf_weights"].items():
        w=D(w_text)
        if not w:continue
        r=tf_results[tf];c=r["components"];score=r["score"]
        coverage+=w*sum(D(v)*(c[k] is not None) for k,v in p["coverage_weights"].items())
        quality+=w*r["data_quality"]
        if score is not None:
            alignment+=w*(D(1) if (sign(score)==bias if bias else abs(score)<D(th["primary"])) else D("0.5") if score==0 and bias else D(0))
        for k,v in p["component_weights"].items():
            if c[k] is None:continue
            cw=w*D(v)
            agrees=sign(c[k])==bias if bias else abs(c[k])<D(th["primary"])
            agree_num+=cw*(D(1) if agrees else D("0.5") if c[k]==0 and bias else D(0))
        oi=c["derivatives"]
        if oi is not None:
            price_part,oi_part=oi["state"].split("_OI_");ps={"PRICE_UP":1,"PRICE_DOWN":-1,"PRICE_FLAT":0}[price_part]
            agreement=D(1) if ps==bias and bias and oi_part=="UP" else D(p["oi_declining_agreement"]) if ps==bias and bias and oi_part=="DOWN" else D(0)
            ow=w*D(p["oi_agreement_weight"]);oi_weight+=ow;oi_num+=ow*agreement
            oi_evidence[tf]={"agreement":agreement,"conflict":bool(bias and ps==-bias),"state":oi["state"]}
    # Missing OI contributes neither numerator nor denominator: coverage penalty only.
    component_agreement=(agree_num+oi_num)/(agree_den+oi_weight) if agree_den+oi_weight else D(0)
    parts={"coverage":coverage,"alignment":alignment,"data_quality":quality,"component_agreement":component_agreement}
    value=sum(D(v)*parts[k] for k,v in p["confidence_weights"].items())
    return value,parts,oi_evidence


def regime(rs,p):
    h4=rs["4h"];h1=rs["1h"];a=h4["score"];b=h1["score"];th=p["thresholds"]
    if a is None or b is None:return "INSUFFICIENT_EVIDENCE",[]
    primary=sign(a) if abs(a)>=D(th["primary"]) else 0
    c4=h4["components"];weak=[]
    if primary:
        if primary*c4["trend"]<D(th["weakening_trend"]):weak.append("REGIME.H4_TREND_WEAKENED")
        if c4["structure"] is not None and primary*c4["structure"]<=D(th["weakening_structure"]):weak.append("REGIME.H4_STRUCTURE_WEAKENED")
        if primary*c4["momentum"]<D(th["weakening_momentum"]):weak.append("REGIME.H4_MOMENTUM_OPPOSED")
    opposed=primary and primary*b<=-D(th["opposition"])
    reversal=(opposed and h1["pivot_structure"] is not None and primary*h1["pivot_structure"]<=-D(th["reversal_pivot"])
              and h1["range_event"]==-primary and bool(weak))
    if reversal:return "POSSIBLE_REVERSAL",weak
    if (opposed and abs(a)>=D(th["pullback_primary"]) and primary*c4["trend"]>0 and
        h4["pivot_structure"] is not None and primary*h4["pivot_structure"]>0):return "PULLBACK_CANDIDATE",[]
    if abs(a)>=D(th["primary"]) and abs(b)>=D(th["primary"]) and sign(a)==sign(b):return "ALIGNED_TREND",[]
    if (abs(a)<D(th["chop_score"]) and abs(b)<D(th["chop_score"]) and
        all(abs(r["components"]["trend"])<D(th["chop_trend"]) and r["pivot_structure"]==0 and r["range_event"]==0 for r in (h4,h1))):return "CHOP_CANDIDATE",[]
    opposed_components=any(r["components"]["structure"] is not None and r["components"]["trend"]*r["components"]["structure"]<0 for r in (h4,h1))
    if primary==0 and abs(b)>=D(th["primary"]) or opposed or opposed_components:return "TRANSITION",weak
    return "MIXED",[]


def evaluate(snapshot,parameters=None):
    p=deepcopy(parameters or load_parameters())
    # Validate supplied parameters too; do not accept a mutated set with an old identity.
    validate_parameters(p)
    t=validate_snapshot(snapshot)
    with localcontext() as ctx:
        ctx.prec=p["decimal_precision"];ctx.rounding=ROUND_HALF_EVEN
        rs={tf:components(snapshot["timeframes"][tf],tf,t,p) for tf in DURATIONS}
        reasons=[];th=p["thresholds"]
        insufficient=any(rs[tf]["score"] is None for tf in ("4h","1h"))
        contributions={tf:D(w)*(rs[tf]["score"] if rs[tf]["score"] is not None else D(0)) for tf,w in p["tf_weights"].items()}
        score=sum(contributions.values()) if not insufficient else None
        bias=0
        if insufficient:reasons.append("DIRECTION.INSUFFICIENT_EVIDENCE")
        else:
            primary=rs["4h"]["score"]
            if score>D(th["direction"]) and primary>=D(th["primary"]):bias=1
            elif score<-D(th["direction"]) and primary<=-D(th["primary"]):bias=-1
            else:reasons.append("DIRECTION.WEAK_OR_CONFLICTING_EVIDENCE")
            if D(th["proximity_min"])<=abs(score)<=D(th["proximity_max"]):reasons.append("DIRECTION.THRESHOLD_PROXIMITY")
        conf,parts,oi=confidence(rs,bias,p)
        if insufficient:conf=min(conf,D(p["insufficient_confidence_cap"]))
        classification="LONG" if bias>0 else "SHORT" if bias<0 else "NEUTRAL"
        strong_checks={}
        if bias:
            macro=rs["1d"]["score"]
            strong_checks={"aggregate":bias*score>=D(th["strong"]),"primary":bias*rs["4h"]["score"]>=D(th["strong_primary"]),
              "confirmation":bias*rs["1h"]["score"]>=D(th["strong_confirmation"]),
              "structure":all(rs[tf]["components"]["structure"] is not None and bias*rs[tf]["components"]["structure"]>=D(th["strong_structure"]) for tf in ("4h","1h")),
              "macro":macro is not None and bias*macro>-D(th["macro_opposition"]),"confidence":conf>=D(th["strong_confidence"])}
            if all(strong_checks.values()):classification="STRONG_"+classification
            reasons.append("DIRECTION.PRIMARY_"+("LONG" if bias>0 else "SHORT"))
        context,weakening=regime(rs,p);reasons.extend(weakening);reasons.append("REGIME."+context)
        conflicts=[];support=[]
        supporting_components=[];conflicting_components=[]
        for tf,r in rs.items():
            s=r["score"]
            if s is not None and bias and abs(s)>=D(th["primary"]):
                (support if sign(s)==bias else conflicts).append(tf)
            for name,value in r["components"].items():
                if name not in p["component_weights"] or value is None or not bias or value==0:continue
                item={"timeframe":tf,"component":name,"value":value,
                      "tf_component_contribution":r["score_contributions"].get(name)}
                (supporting_components if sign(value)==bias else conflicting_components).append(item)
        if rs["4h"]["score"] is not None and rs["1h"]["score"] is not None and rs["4h"]["score"]*rs["1h"]["score"]<0:reasons.append("CONFLICT.H4_H1_OPPOSED")
        for tf,label in (("1d","D1_MACRO_OPPOSED"),("15m","M15_SHORT_TERM_OPPOSED")):
            if tf in conflicts:reasons.append("CONFLICT."+label)
        for tf,e in oi.items():
            rs[tf]["reason_codes"].append("OI.OPPOSING_PRICE_MOVE" if e["conflict"] else
                "OI.AVAILABLE_CONFIRMATION" if e["agreement"]==1 else "OI.PARTIAL_CONFIRMATION" if e["agreement"]>0 else "OI.AVAILABLE_NO_CONFIRMATION")
        all_reasons=sorted(set(reasons+[f"{tf}:{code}" for tf,r in rs.items() for code in r["reason_codes"]]))
        result=encode({"schema_version":SCHEMA,"direction_engine_version":VERSION,"parameter_version":p["parameter_version"],
          "parameter_hash":p["parameter_hash"],"parameter_status":p["status"],"decision_time_utc":snapshot["decision_time_utc"],
          "snapshot_id":snapshot["snapshot_id"],"input_manifest_ids":snapshot["input_manifests"],
          "direction_score":score,"direction_class":classification,"directional_bias":"LONG" if bias>0 else "SHORT" if bias<0 else "NEUTRAL",
          "confidence":conf,"confidence_semantics":"evidence_quality_not_probability","confidence_components":parts,
          "regime":context,"timeframes":rs,"tf_score_contributions":contributions,"strong_gate":strong_checks,
          "oi_agreement_evidence":oi,"supporting_timeframes":support,"conflicting_timeframes":conflicts,
          "supporting_components":supporting_components,"conflicting_components":conflicting_components,
          "primary_reason":"DIRECTION.INSUFFICIENT_EVIDENCE" if insufficient else "DIRECTION.PRIMARY_"+("LONG" if bias>0 else "SHORT") if bias else "DIRECTION.WEAK_OR_CONFLICTING_EVIDENCE",
          "missing_inputs":{tf:r["missing_inputs"] for tf,r in rs.items()},"stale_inputs":{tf:r["stale_inputs"] for tf,r in rs.items()},
          "availability_evidence_refs":sorted({s["record"]["availability_evidence"]["ref"] for s in snapshot["timeframes"].values() if s.get("record")}),
          "feature_generation_times_ms":{tf:s["record"]["feature_generated_at_ms"] if s.get("record") else None for tf,s in snapshot["timeframes"].items()},
          "oi_registry_hashes":sorted({s["record"]["oi_metadata"]["registry_hash"] for s in snapshot["timeframes"].values() if s.get("record")}),
          "reason_codes":all_reasons})
        # Explain independent 18-place serialization without hiding rounding differences.
        for r in result["timeframes"].values():
            r["score_rounding_residual"]=encode(D(r["score"])-sum(D(x) for x in r["score_contributions"].values())) if r["score"] is not None else None
        result["aggregate_rounding_residual"]=encode(D(result["direction_score"])-sum(D(x) for x in result["tf_score_contributions"].values())) if score is not None else None
        result["decision_id"]=digest(result)
        return result

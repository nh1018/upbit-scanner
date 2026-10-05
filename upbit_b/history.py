"""Pure selection/journal projection plus guarded write-once publication."""
import copy
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from decimal import Decimal as D, localcontext, ROUND_HALF_EVEN

from . import feature_contracts as F
from . import history_contracts as C
from .trend_state import feature_hash
from .contracts import DURATIONS

class Conflict(ValueError): pass

def _sha(raw): return hashlib.sha256(raw).hexdigest()

def empty_previous():
    return {"cycle_id":None,"boundary":None,"cohort_id":None,"states":{},"universe":[],"detail_refs":{},"receipt":None}

def candidate(engine):
    if engine is None or not engine["trend"]["data_gate_passed"] or engine["trend"]["score"] is None: return "UNKNOWN"
    return "TRUE" if engine["trend"]["eligible"] and engine["state"]["primary"] in C.CANDIDATE_STATES else "FALSE"

def select_controls(population,cycle):
    ordered=sorted(set(population),key=lambda market:(F.digest([C.POLICY_SHA256,cycle,market]),market))
    return ordered[:C.POLICY["controls_max"]]

def _summary(entry):
    e=entry.get("engine")
    if e is None:
        return {"state":None,"candidate":"UNKNOWN","chase":"UNAVAILABLE","damage":None,
            "mapping":"API_UNAVAILABLE","confirmation":"UNAVAILABLE","data_available":False,"tf_context":{},
            "confidence":"UNAVAILABLE","group_availability":{},"failure":entry["status"]}
    status=candidate(e) if entry["status"]=="OK" else "UNKNOWN"
    return {"state":e["state"]["primary"],"candidate":status,"chase":e["chase"]["category"],
        "damage":e["trend"]["primary_damage"],"mapping":e["binance"]["mapping_status"],
        "confirmation":e["binance"]["confirmation"],"data_available":status!="UNKNOWN",
        "tf_context":{tf:bool(v["eligible"]) for tf,v in e["data"]["tf_readiness"].items()},
        "confidence":e["data"]["category"],"group_availability":e["trend"]["available_masks"],
        "failure":None if entry["status"]=="OK" else entry["status"]}

def _events(old,new,consecutive,cohort,market,cycle,oid):
    specs=[]
    if old is None: specs=[("BASELINE_STATE",False,None,new["state"])]
    else:
        prior=old["summary"]
        both=prior["data_available"] and new["data_available"]
        verified=bool(consecutive and both)
        if prior["data_available"] and not new["data_available"]:
            specs.append(("DATA_LOST",False,True,False))
        if not prior["data_available"] and new["data_available"]:
            specs.append(("DATA_RECOVERED",False,False,True))
        if new["data_available"] and not verified:
            specs.append(("STATE_REOBSERVED_AFTER_GAP",False,prior["state"],new["state"]))
        if verified:
            if prior["state"] != new["state"]:
                name={"TREND_BUILDING":"ENTER_BUILDING","TREND_CONTINUATION":"ENTER_CONTINUATION",
                    "PULLBACK_WATCH":"ENTER_PULLBACK_WATCH","REACCELERATION":"ENTER_REACCELERATION",
                    "TREND_WEAKENING":"ENTER_WEAKENING"}.get(new["state"])
                if name: specs.append((name,True,prior["state"],new["state"]))
            if prior["candidate"]=="TRUE" and new["candidate"]=="FALSE":
                specs.append(("EXIT_B_CANDIDATE",True,"TRUE","FALSE"))
        # These are observed category changes; state continuity verification stays separate.
        for key,name in (("chase","CHASE_RISK_CHANGE"),("damage","PRIMARY_DAMAGE")):
            if prior[key]!=new[key] and (key!="damage" or new[key] is not None):
                typ=("PRIMARY_DAMAGE_CLEARED" if key=="damage" and new[key] is False else name)
                specs.append((typ,verified,prior[key],new[key]))
        if (prior["mapping"],prior["confirmation"])!=(new["mapping"],new["confirmation"]):
            specs.append(("BINANCE_CONTEXT_CHANGE",verified,[prior["mapping"],prior["confirmation"]],[new["mapping"],new["confirmation"]]))
        if both and any(prior[k]!=new[k] for k in ("tf_context","confidence","group_availability")):
            specs.append(("DATA_QUALITY_CHANGE",verified,
                          {k:prior[k] for k in ("tf_context","confidence","group_availability")},
                          {k:new[k] for k in ("tf_context","confidence","group_availability")}))
    previous=old["record_id"] if old else None
    return [{"event_id":C.event_id(cohort,market,cycle,typ,previous,oid),"event_type":typ,
        "previous_state_reference":previous,"previous_effective_cycle_id":old["effective_cycle_id"] if old else None,
        "current_observation_id":oid,"transition_verified":verified,"before":before,"after":after}
        for typ,verified,before,after in specs]

def _source(s):
    m=s.get("metadata",{})
    keys=("provider","instrument","timeframe","source_status","source_reason","source_candle_open_ms",
          "source_candle_close_ms","source_cutoff_ms","source_received_at_ms","feature_generated_at_ms",
          "continuous_segment_start_ms","available_history","gap_reset","input_sha256")
    return {"measurement_sha256":s.get("measurement_sha256"),**{k:m[k] for k in keys if k in m}}

def evidence_subset(bundle,engine):
    result={}
    for provider in ("upbit","binance_spot"):
        result[provider]={}
        for tf,s in bundle.get(provider,{}).items():
            if provider=="binance_spot":
                if tf=="1d": continue
                if tf=="1h": keys=C.BASE[:5]+("return3_pct",)
                else: keys=C.BASE+("distance_to_confirmed_low_atr",)
            else:
                keys=C.BASE
                if tf in ("1h","4h"): keys+=C.CHASE+("distance_to_confirmed_low_atr",)
                if tf=="1h": keys+=C.CONTEXT
            keys=tuple(dict.fromkeys(keys))
            vals={k:s["features"].get(k) for k in keys}
            ready={k:s["readiness"].get(k,"UNAVAILABLE") for k in keys}
            m=s.get("metadata",{})
            refs={}
            if "distance_to_confirmed_low_atr" in keys:
                refs["confirmed_low"]=s["features"].get("confirmed_pivot_low")
            if provider=="upbit" and tf=="1h":refs["swing_anchor"]=m.get("swing_anchor")
            if provider=="upbit" and tf in ("1h","4h"):
                event=m.get("last_breakout")
                age=((m["source_candle_open_ms"]-event["candle_open_ms"])//DURATIONS[tf]
                     if event and "source_candle_open_ms" in m else None)
                if age is not None and 0<=age<=20 and ready.get("distance_to_last_breakout_atr")=="READY":
                    refs["last_breakout"]={**event,"age_bars":age}
                else:
                    vals.pop("distance_to_last_breakout_atr",None);ready.pop("distance_to_last_breakout_atr",None)
            result[provider][tf]={"values":vals,"readiness":ready,"quality":_source(s),"references":refs,
                "response_evidence":m.get("evidence",[])}
    # Spot facts remain unverified if identity has not been explicitly approved.
    result["mapping"]=bundle.get("mapping")
    return result

def _anchors(entry,engine):
    price=engine.get("price_observation")
    close=entry.get("close_1h")
    return {"OBSERVED_TICKER_DIAGNOSTIC":price,
        "COMPLETED_1H_CLOSE_DIAGNOSTIC":close,
        "NEXT_1H_OPEN_PROXY":{"price":None,"target_boundary_ms":(C.clock(engine["observation_time_utc"])//C.HOUR+1)*C.HOUR,
                              "availability":"FUTURE_UNAVAILABLE"}}

def _compact(entry):
    e=entry.get("engine")
    if not e:return {"reason":entry.get("reason"),"availability":"UNKNOWN"}
    return {"score":e["trend"]["score"],"signed_score":e["trend"]["signed_score"],
        "score_before_gate":e["trend"]["score_before_gate"],"eligible":e["trend"]["eligible"],
        "tf_scores":e["trend"]["tf_scores"],"group_scores":e["trend"]["group_scores"],
        "available_masks":e["trend"]["available_masks"],
        "chase_score":e["chase"]["score"],"chase_coverage":e["chase"]["coverage"],
        "coverage":e["data"]["coverage"],"confidence":e["data"]["category"],
        "secondary_tags":e["state"]["secondary_tags"],"reason_codes":e["state"]["reason_codes"],
        "sources":{tf:_source(s) for tf,s in entry["bundle"]["upbit"].items()},
        "price_anchors":_anchors(entry,e),"price_clock_warnings":e["source"].get("price_clock_warnings",[])}

def _verify_entry(market,entry,boundary,started,prepared,contract):
    if entry["status"] not in ("OK","API_ERROR","UNATTEMPTED"): raise ValueError("unknown scan result status")
    e=entry.get("engine")
    if e is None:
        if entry["status"]=="OK":raise ValueError("successful scan requires actual engine result")
        return
    if e["instrument"]!=market or e["source_cutoff"]["time_ms"]!=boundary:raise ValueError("identity/cutoff mismatch")
    if F.digest({k:v for k,v in e.items() if k!="observation_id"})!=e["observation_id"]:raise ValueError("engine envelope hash mismatch")
    if any(e["versions"].get(k)!=contract.get(k) for k in e["versions"]):raise ValueError("engine/feature contract mismatch")
    observed=C.clock(e["observation_time_utc"])
    if not started<=observed<=prepared:raise ValueError("observation unavailable at scan clock")
    b=entry["bundle"]
    for provider in ("upbit","binance_spot"):
        for tf,s in b.get(provider,{}).items():
            if "metadata" not in s:continue
            if feature_hash(s)!=s.get("measurement_sha256"):raise ValueError("feature measurement hash mismatch")
            if e["source"][provider][tf].get("measurement_sha256")!=s["measurement_sha256"]:
                raise ValueError("engine/bundle source mismatch")
            m=s["metadata"]
            if m["source_cutoff_ms"]!=boundary or m["feature_generated_at_ms"]>observed:raise ValueError("future or mixed feature input")
            if m.get("source_received_at_ms",started)>observed:raise ValueError("future receipt")
    close=entry.get("close_1h")
    if close and (close["candle_close_ms"]!=boundary or close["received_at_ms"]>observed
                  or close.get("provider")!="UPBIT" or close.get("instrument")!=market
                  or D(close["price"])<=0 or not close.get("source_reference")):
        raise ValueError("invalid actual completed close anchor")

def build_cycle(universe,entries,boundary,started,completed,prepared,code_revision,
                previous=None,contract=None,publishable=False,scan_evidence=None):
    """Memory-only projection. Production timing enforced when publishable=True."""
    with localcontext() as ctx:
        ctx.prec,ctx.rounding=34,ROUND_HALF_EVEN
        return _build(universe,entries,boundary,started,completed,prepared,code_revision,
                      previous or empty_previous(),contract or C.versions(),publishable,scan_evidence)

def _build(universe,entries,boundary,started,completed,prepared,revision,previous,contract,publishable,scan_evidence):
    C.cycle_id(boundary,"validate")
    if not boundary<=C.integer(started)<=C.integer(completed)<=C.integer(prepared):raise ValueError("invalid scan clocks")
    if publishable:C.check_window(boundary,started,prepared)
    markets=sorted(universe)
    if len(set(markets))!=len(markets) or any(not re.fullmatch(r"KRW-[A-Z0-9]+",m) for m in markets):raise ValueError("invalid universe")
    if set(entries)!=set(markets):raise ValueError("every universe member needs OK/failed/unattempted result")
    if not markets:raise ValueError("universe unavailable; no fabricated cycle")
    cohort=C.cohort_id(contract);cycle=C.cycle_id(boundary,cohort)
    if previous["boundary"] is not None and previous["boundary"]>=boundary:raise ValueError("replay/old cycle must be checked before projection")
    same=previous["cohort_id"]==cohort
    old_states=previous["states"] if same else {}
    for market in markets:_verify_entry(market,entries[market],boundary,started,prepared,contract)
    summaries={m:_summary(entries[m]) for m in markets}
    population=[m for m in markets if entries[m]["status"]=="OK" and summaries[m]["candidate"]=="FALSE"]
    controls=select_controls(population,cycle)
    records=[]
    consecutive=same and previous["boundary"] is not None and boundary-previous["boundary"]==C.HOUR
    for market in markets:
        entry=entries[market];new=summaries[market];old=old_states.get(market)
        continuous=bool(consecutive and old and old["effective_cycle_id"]==previous["cycle_id"])
        change=old is None or old["summary"]!=new
        gap=new["data_available"] and old is not None and not (continuous and old["summary"]["data_available"])
        failure=entry["status"]!="OK"
        heartbeat=new["candidate"]=="TRUE" and (boundary//C.HOUR)%C.POLICY["heartbeat_hours"]==0
        if not (change or gap or failure or heartbeat or market in controls):continue
        kind="BASELINE_STATE" if old is None else "STATE_DELTA" if change or gap else "FAILURE" if failure else "CONTROL" if market in controls else "HEARTBEAT"
        oid=C.observation_id(cycle,market,kind)
        events=_events(old,new,continuous,cohort,market,cycle,oid)
        detail=entry.get("engine") is not None and (change or gap) and (new["candidate"]=="TRUE" or bool(events))
        if old is None:detail=entry.get("engine") is not None and (new["candidate"]=="TRUE" or market in controls)
        record={"record_kind":kind,"observation_id":oid,"instrument":market,"summary":new,
            "engine_observation_id":entry.get("engine",{}).get("observation_id") if entry.get("engine") else None,
            "engine_observation_time":entry.get("engine",{}).get("observation_time_utc") if entry.get("engine") else None,
            "previous_state_reference":old["record_id"] if old else None,
            "previous_effective_cycle_id":old["effective_cycle_id"] if old else None,
            "evidence_level":"DETAIL" if detail else "COMPACT" if heartbeat or market in controls else "STATE_ONLY",
            "selection_reason":"CONTROL" if market in controls else "CANDIDATE_CHANGE" if detail else "HEARTBEAT" if heartbeat else "STATE_OR_FAILURE",
            "events":events}
        if detail or heartbeat or market in controls:
            record["observation"]=_compact(entry)
            record["previous_detail_reference"]=previous["detail_refs"].get(market) if same else None
        if detail:
            record["evidence"]=evidence_subset(entry["bundle"],entry["engine"])
            record["observation"]["recovery_basis"]=entry["engine"]["state"]["recovery_basis"]
            record["observation"]["engine_prior_state_transition_verified"]=entry["engine"]["state"]["prior_state_transition_verified"]
        if failure:record["failure_reason"]=entry.get("reason",entry["status"])
        records.append(F.canonical(record))
    counts={s:sum(e["status"]==s for e in entries.values()) for s in ("OK","API_ERROR","UNATTEMPTED")}
    insufficient=[m for m in markets if entries[m]["status"]=="OK" and summaries[m]["candidate"]=="UNKNOWN"]
    failed={m:entries[m].get("reason","API_ERROR") for m in markets if entries[m]["status"]=="API_ERROR"}
    unattempted=[m for m in markets if entries[m]["status"]=="UNATTEMPTED"]
    def histogram(key):
        result={}
        for m in markets:
            value=summaries[m][key]
            label="UNAVAILABLE" if value is None else str(value)
            result[label]=result.get(label,0)+1
        return result
    def scores():
        result={}
        for entry in entries.values():
            v=entry.get("engine",{}).get("trend",{}).get("score") if entry.get("engine") else None
            v=D(v) if v is not None else None
            label="null" if v is None else "0" if v==0 else ">0~<45" if v<45 else "45~<65" if v<65 else "65~<80" if v<80 else ">=80"
            result[label]=result.get(label,0)+1
        return result
    data_lines=b"".join((F.dumps(r)+"\n").encode("utf-8") for r in records)
    added=sorted(set(markets)-set(previous["universe"]))
    removed=sorted(set(previous["universe"])-set(markets))
    manifest={"record_kind":"MANIFEST","schema_version":C.SCHEMA_VERSION,"cycle_id":cycle,"cohort_id":cohort,
        "source_cutoff":boundary,"started_at":C.iso(started),"completed_at":C.iso(completed),"prepared_at":C.iso(prepared),
        "code_revision":revision,"versions":contract,"history_policy_version":C.POLICY_VERSION,"history_policy_sha256":C.POLICY_SHA256,
        "history_policy":C.POLICY,
        "mode":"PRODUCTION_PREPARED" if publishable else "DRY_RUN_NONPUBLISHABLE", "persisted_at":None,
        "publication_receipt":previous.get("receipt"),"universe_count":len(markets),"universe_hash":F.digest(markets),
        "membership":{"baseline":markets if not same else None,"added":added,"removed":removed},
        "attempted":len(markets)-counts["UNATTEMPTED"],"succeeded":counts["OK"],"insufficient":len(insufficient),
        "failed":counts["API_ERROR"],"unattempted":counts["UNATTEMPTED"],"candidate_count":histogram("candidate").get("TRUE",0),
        "unknown_count":histogram("candidate").get("UNKNOWN",0),"state_histogram":histogram("state"),
        "score_histogram":scores(),"chase_histogram":histogram("chase"),"mapping_histogram":histogram("mapping"),
        "confidence_histogram":{},"selected_controls":controls,"control_population":len(population),
        "sampling":{"version":C.POLICY_VERSION,"policy_sha256":C.POLICY_SHA256,"K":len(controls),
                    "inclusion_fraction":F.canonical(D(len(controls))/len(population)) if population else None},
        "failure_instruments":failed,"insufficient_instruments":insufficient,"unattempted_instruments":unattempted,
        "observation_count":len(records),"event_count":sum(len(r["events"]) for r in records),
        "previous_cycle_id":previous["cycle_id"],"cohort_baseline":not same,
        "missed_cycle_count":max(0,(boundary-previous["boundary"])//C.HOUR-1) if previous["boundary"] is not None else 0,
        "completeness":"PARTIAL" if failed or unattempted else "COMPLETE",
        "records_payload_sha256":_sha(data_lines),"scan_evidence":scan_evidence or {}}
    for entry in entries.values():
        key=entry["engine"]["data"]["category"] if entry.get("engine") else "UNAVAILABLE"
        manifest["confidence_histogram"][key]=manifest["confidence_histogram"].get(key,0)+1
    payload=(F.dumps(manifest)+"\n").encode("utf-8")+data_lines
    validate_cycle(payload)
    return payload

def validate_cycle(payload):
    if not isinstance(payload,bytes) or not payload.endswith(b"\n"):raise ValueError("canonical newline-terminated UTF-8 JSONL required")
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:raise ValueError("duplicate JSON key")
            result[k]=v
        return result
    lines=payload.decode("utf-8").splitlines()
    objects=[json.loads(s,object_pairs_hook=unique) for s in lines]
    if any(F.dumps(obj)!=line for obj,line in zip(objects,lines)):raise ValueError("noncanonical JSONL")
    m=objects[0];records=objects[1:]
    if m["record_kind"]!="MANIFEST" or m["schema_version"]!=C.SCHEMA_VERSION:raise ValueError("invalid manifest")
    if (F.digest(m["history_policy"])!=m["history_policy_sha256"] or m["versions"]["history_policy_sha256"]!=m["history_policy_sha256"]
        or C.cohort_id(m["versions"])!=m["cohort_id"]):raise ValueError("contract hash mismatch")
    if C.cycle_id(m["source_cutoff"],m["cohort_id"],m["history_policy_sha256"])!=m["cycle_id"]:raise ValueError("cycle ID mismatch")
    if len(records)!=m["observation_count"] or _sha(b"".join((line+"\n").encode() for line in lines[1:]))!=m["records_payload_sha256"]:
        raise ValueError("payload count/hash mismatch")
    if sum(len(r["events"]) for r in records)!=m["event_count"]:raise ValueError("event count mismatch")
    if not C.clock(m["started_at"])<=C.clock(m["completed_at"])<=C.clock(m["prepared_at"]):raise ValueError("invalid journal clocks")
    if m["mode"] not in ("PRODUCTION_PREPARED","DRY_RUN_NONPUBLISHABLE"):raise ValueError("invalid publication mode")
    if m["mode"]=="PRODUCTION_PREPARED":C.check_window(m["source_cutoff"],C.clock(m["started_at"]),C.clock(m["prepared_at"]),m["history_policy"])
    if (m["succeeded"]+m["failed"]!=m["attempted"] or m["attempted"]+m["unattempted"]!=m["universe_count"]
        or len(m["failure_instruments"])!=m["failed"] or len(m["insufficient_instruments"])!=m["insufficient"]
        or len(m["unattempted_instruments"])!=m["unattempted"]):raise ValueError("scan count mismatch")
    seen=set();events=set()
    for r in records:
        oid=C.observation_id(m["cycle_id"],r["instrument"],r["record_kind"])
        if r["observation_id"]!=oid or r["instrument"] in seen:raise ValueError("duplicate/invalid observation")
        seen.add(r["instrument"])
        if r["summary"]["candidate"] not in ("TRUE","FALSE","UNKNOWN"):raise ValueError("invalid candidate status")
        if "observation" in r:
            if r["observation"]["price_anchors"]["NEXT_1H_OPEN_PROXY"]["price"] is not None:raise ValueError("future outcome forbidden")
        for event in r["events"]:
            if event["event_type"] not in C.EVENT_TYPES or event["event_id"] in events:raise ValueError("invalid/duplicate event")
            eid=C.event_id(m["cohort_id"],r["instrument"],m["cycle_id"],event["event_type"],event["previous_state_reference"],oid)
            if eid!=event["event_id"] or event["current_observation_id"]!=oid:raise ValueError("event ID mismatch")
            events.add(eid)
    return m,records

def advance(previous,payload):
    m,records=validate_cycle(payload)
    if m["previous_cycle_id"]!=previous["cycle_id"]:raise ValueError("missing/broken previous cycle chain")
    for r in records:
        for event in r["events"]:
            if event["transition_verified"]:
                old=previous["states"].get(r["instrument"])
                if (previous["cohort_id"]!=m["cohort_id"] or previous["boundary"] is None
                    or m["source_cutoff"]-previous["boundary"]!=C.HOUR or old is None
                    or not old["summary"]["data_available"] or not r["summary"]["data_available"]
                    or event["previous_state_reference"]!=old["record_id"]):raise ValueError("unverified transition claimed")
    state=copy.deepcopy(previous) if previous["cohort_id"]==m["cohort_id"] else empty_previous()
    membership=m["membership"]
    universe=membership["baseline"] if membership["baseline"] is not None else sorted((set(state["universe"])|set(membership["added"]))-set(membership["removed"]))
    if F.digest(universe)!=m["universe_hash"]:raise ValueError("membership journal mismatch")
    for market in list(state["states"]):
        if market not in universe:state["states"].pop(market);state["detail_refs"].pop(market,None)
    for r in records:
        state["states"][r["instrument"]]={"summary":r["summary"],"record_id":r["observation_id"],"effective_cycle_id":m["cycle_id"]}
        if r["evidence_level"]=="DETAIL":state["detail_refs"][r["instrument"]]=r["observation_id"]
    for market,item in state["states"].items():
        if market in universe:item["effective_cycle_id"]=m["cycle_id"]
    state.update(cycle_id=m["cycle_id"],boundary=m["source_cutoff"],cohort_id=m["cohort_id"],universe=universe,receipt=None)
    return state

def load_previous(repo,boundary):
    state=empty_previous();root=Path(repo)/"output_upbit_b/v1/history"
    if not root.exists():return state
    for path in sorted(root.glob("*/*.jsonl")):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}/\d{2}\.jsonl",path.relative_to(root).as_posix()):raise ValueError("invalid canonical journal path")
        payload=path.read_bytes();m,_=validate_cycle(payload)
        if m["mode"]!="PRODUCTION_PREPARED":raise ValueError("preview cannot be production history")
        if path.relative_to(Path(repo)).as_posix()!=C.cycle_path(m["source_cutoff"]):raise ValueError("path/cycle mismatch")
        if m["source_cutoff"]>=boundary:continue
        if state["boundary"] is not None and m["source_cutoff"]<=state["boundary"]:raise ValueError("nonmonotonic history")
        state=advance(state,payload)
    return state

def check_existing(repo,boundary,payload=None):
    path=Path(repo)/C.cycle_path(boundary)
    if not path.exists():return "ABSENT"
    existing=path.read_bytes();m,_=validate_cycle(existing)
    if m["mode"]!="PRODUCTION_PREPARED":raise Conflict("nonproduction cycle in canonical namespace")
    if m["source_cutoff"]!=boundary:raise Conflict("path boundary collision")
    if payload is not None and existing!=payload:raise Conflict("same cycle path, different bytes; never overwrite")
    return "NOOP" if payload is not None else "REPLAY_NOOP"

def write_once(repo,payload,now):
    m,_=validate_cycle(payload)
    if m["mode"]!="PRODUCTION_PREPARED":raise ValueError("preview is nonpublishable")
    if check_existing(repo,m["source_cutoff"],payload)=="NOOP":return "NOOP"
    C.check_window(m["source_cutoff"],C.clock(m["started_at"]),now)
    root=Path(repo).resolve();path=root/C.cycle_path(m["source_cutoff"])
    if path.parent.resolve().is_relative_to(root) is False:raise ValueError("journal path escapes repository")
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".prepared-",suffix=".tmp",dir=path.parent)
    try:
        with os.fdopen(fd,"wb") as stream:stream.write(payload);stream.flush();os.fsync(stream.fileno())
        validate_cycle(Path(tmp).read_bytes())
        # Same-directory rename is atomic and no-clobber on Windows. On POSIX link
        # atomically installs the complete inode without replacing a concurrent file.
        try:
            if os.name=="nt":os.rename(tmp,path)
            else:os.link(tmp,path);os.unlink(tmp)
        except FileExistsError:
            if path.read_bytes()!=payload:raise Conflict("concurrent different cycle bytes")
            return "NOOP"
        return "CREATED"
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

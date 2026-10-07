import copy
import io
import json
import os
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from decimal import Decimal as D,localcontext

from upbit_b import feature_contracts as F
from upbit_b import history_contracts as C
from upbit_b.history import (build_cycle,validate_cycle,advance,empty_previous,candidate,
    select_controls,check_existing,write_once,load_previous,Conflict,evidence_subset)
from upbit_b import history_runner as R
from test_trend_state import bundle,geometry,change,fixture,run,CUTOFF
from upbit_b.trend_state import evaluate

H=C.HOUR
START=CUTOFF+7*60000
END=START+1000

def entry(market="KRW-X",state="TREND_CONTINUATION",boundary=CUTOFF,available=True):
    # Re-clock the approved synthetic Feature fixture; sealed Engine envelope is
    # a recording fixture, not a new strategy calculation or historical replay.
    b=bundle(True)
    for provider in ("upbit","binance_spot"):
        for tf,s in b[provider].items():
            m=s["metadata"];duration={"1d":24*H,"4h":4*H,"1h":H}[tf]
            close=boundary//duration*duration
            delta=close-m["source_candle_close_ms"]
            m.update(source_cutoff_ms=boundary,source_candle_open_ms=close-duration,source_candle_close_ms=close,
                     feature_generated_at_ms=boundary+1000,source_received_at_ms=boundary+1000)
            m["evidence"][0]["received_at_utc"]=C.iso(boundary+1000)
            if provider=="upbit":m["instrument"]=market
            for key in ("confirmed_pivot_low","confirmed_pivot_high"):
                p=s["features"][key];p["pivot_open_ms"]+=delta;p["confirmation_boundary_ms"]+=delta;p["observed_at_ms"]=boundary+1000
            from upbit_b.trend_state import feature_hash
            s["measurement_sha256"]=feature_hash(s)
    e=evaluate(b,market,boundary+7*60000+500)
    e["state"]["primary"]=state
    e["trend"]["eligible"]=state in C.CANDIDATE_STATES
    if not available:
        e["state"]["primary"]="INSUFFICIENT_EVIDENCE";e["trend"].update(score=None,signed_score=None,data_gate_passed=False,eligible=False)
    e["observation_id"]=F.digest({k:v for k,v in e.items() if k!="observation_id"})
    return {"engine":e,"bundle":b,"status":"OK","close_1h":{"provider":"UPBIT","instrument":market,
        "price":"100","candle_open_ms":boundary-H,"candle_close_ms":boundary,"received_at_ms":boundary+1000,
        "source_reference":{"input_sha256":"a"*64,"source_row_open_ms":boundary-H}},"reason":None}

def cycle(entries,boundary=CUTOFF,previous=None,contract=None,publishable=True):
    return build_cycle(list(entries),entries,boundary,boundary+7*60000,boundary+7*60000+1000,
        boundary+7*60000+2000,"a"*40,previous,contract,publishable)

def record(payload,market="KRW-X"):
    return next(r for r in validate_cycle(payload)[1] if r["instrument"]==market)

def kinds(payload,market="KRW-X"):return [e["event_type"] for e in record(payload,market)["events"]]

def state_after(payload,previous=None):return advance(previous or empty_previous(),payload)

def reseal_engine(e):e["observation_id"]=F.digest({k:v for k,v in e.items() if k!="observation_id"})

class FixtureTests(unittest.TestCase):
    def prior(self,state="TREND_CONTINUATION",boundary=CUTOFF):
        payload=cycle({"KRW-X":entry(state=state,boundary=boundary)},boundary)
        return state_after(payload)

    def test_01_cold_start_continuation(self):
        p=cycle({"KRW-X":entry()});r=record(p)
        self.assertEqual(r["record_kind"],"BASELINE_STATE")
        self.assertEqual(kinds(p),["BASELINE_STATE"])
        self.assertFalse(r["events"][0]["transition_verified"])
        self.assertEqual(r["evidence_level"],"DETAIL")

    def test_02_continuation_no_hourly_enter(self):
        p=cycle({"KRW-X":entry(boundary=CUTOFF+H)},CUTOFF+H,self.prior())
        self.assertEqual(validate_cycle(p)[1],[])

    def test_03_building_continuation(self):
        p=cycle({"KRW-X":entry(boundary=CUTOFF+H)},CUTOFF+H,self.prior("TREND_BUILDING"))
        self.assertIn("ENTER_CONTINUATION",kinds(p))
        self.assertTrue(record(p)["events"][0]["transition_verified"])

    def test_04_continuation_pullback(self):
        p=cycle({"KRW-X":entry(state="PULLBACK_WATCH",boundary=CUTOFF+H)},CUTOFF+H,self.prior())
        self.assertIn("ENTER_PULLBACK_WATCH",kinds(p))

    def test_05_pullback_recovery(self):
        p=cycle({"KRW-X":entry(state="REACCELERATION",boundary=CUTOFF+H)},CUTOFF+H,self.prior("PULLBACK_WATCH"))
        event=next(e for e in record(p)["events"] if e["event_type"]=="ENTER_REACCELERATION")
        self.assertTrue(event["transition_verified"])
        self.assertFalse(record(p)["observation"]["engine_prior_state_transition_verified"])

    def test_06_candidate_weakening_exit(self):
        p=cycle({"KRW-X":entry(state="TREND_WEAKENING",boundary=CUTOFF+H)},CUTOFF+H,self.prior())
        self.assertIn("ENTER_WEAKENING",kinds(p));self.assertIn("EXIT_B_CANDIDATE",kinds(p))

    def test_07_chase_category_change(self):
        e=entry(boundary=CUTOFF+H);e["engine"]["chase"]["category"]="HIGH";reseal_engine(e["engine"])
        p=cycle({"KRW-X":e},CUTOFF+H,self.prior())
        self.assertIn("CHASE_RISK_CHANGE",kinds(p));self.assertNotIn("ENTER_CONTINUATION",kinds(p))

    def test_08_mapping_context_change(self):
        first=entry();first["engine"]["binance"].update(mapping_status="UNVERIFIED",confirmation="UNVERIFIED");reseal_engine(first["engine"])
        old=state_after(cycle({"KRW-X":first}))
        p=cycle({"KRW-X":entry(boundary=CUTOFF+H)},CUTOFF+H,old)
        self.assertIn("BINANCE_CONTEXT_CHANGE",kinds(p));self.assertNotIn("ENTER_CONTINUATION",kinds(p))

    def test_09_sparse_chase(self):
        e=entry(boundary=CUTOFF+H);e["engine"]["chase"].update(score=None,category="UNAVAILABLE")
        e["engine"]["data"]["tf_readiness"]["1h"]["eligible"]=False;reseal_engine(e["engine"])
        p=cycle({"KRW-X":e},CUTOFF+H,self.prior())
        self.assertEqual(record(p)["summary"]["candidate"],"TRUE")
        self.assertIn("CHASE_RISK_CHANGE",kinds(p))

    def test_10_api_failure_no_exit(self):
        p=cycle({"KRW-X":{"status":"API_ERROR","engine":None,"reason":"HTTP 429"}},CUTOFF+H,self.prior())
        self.assertIn("DATA_LOST",kinds(p));self.assertNotIn("EXIT_B_CANDIDATE",kinds(p))
        self.assertEqual(record(p)["summary"]["candidate"],"UNKNOWN")
        self.assertEqual(validate_cycle(p)[0]["completeness"],"PARTIAL")

    def test_11_parameter_change_baseline(self):
        contract=C.versions();contract["engine_parameter_sha256"]="b"*64
        e=entry(boundary=CUTOFF+H);e["engine"]["versions"]["engine_parameter_sha256"]="b"*64;reseal_engine(e["engine"])
        p=cycle({"KRW-X":e},CUTOFF+H,self.prior(),contract)
        self.assertEqual(kinds(p),["BASELINE_STATE"])
        self.assertTrue(validate_cycle(p)[0]["cohort_baseline"])

    def test_12_identical_retry(self):
        entries={"KRW-X":entry()}
        self.assertEqual(cycle(entries),cycle(entries))
        with tempfile.TemporaryDirectory() as root:
            p=cycle(entries);self.assertEqual(write_once(root,p,END+1000),"CREATED")
            self.assertEqual(write_once(root,p,END+1000),"NOOP")
            self.assertEqual(check_existing(root,CUTOFF),"REPLAY_NOOP")

class PolicyTests(unittest.TestCase):
    def test_true_false_unknown(self):
        self.assertEqual(candidate(entry()["engine"]),"TRUE")
        self.assertEqual(candidate(entry(state="NO_UPTREND")["engine"]),"FALSE")
        self.assertEqual(candidate(entry(available=False)["engine"]),"UNKNOWN")
        self.assertEqual(candidate(None),"UNKNOWN")

    def test_high_chase_does_not_exclude(self):
        e=entry();e["engine"]["chase"]["category"]="HIGH"
        self.assertEqual(candidate(e["engine"]),"TRUE")

    def test_unavailable_spot_does_not_exclude(self):
        e=entry();e["engine"]["binance"]["confirmation"]="UNAVAILABLE"
        self.assertEqual(candidate(e["engine"]),"TRUE")

    def test_control_deterministic_max12(self):
        population=[f"KRW-X{i}" for i in range(30)]
        a=select_controls(population,"cycle")
        self.assertEqual(len(a),12);self.assertEqual(a,select_controls(list(reversed(population)),"cycle"))

    def test_control_population_and_fraction(self):
        entries={f"KRW-X{i}":entry(f"KRW-X{i}",state="NO_UPTREND") for i in range(24)}
        m,_=validate_cycle(cycle(entries))
        self.assertEqual(m["control_population"],24);self.assertEqual(m["sampling"]["K"],12)
        self.assertEqual(m["sampling"]["inclusion_fraction"],"0.5")

    def test_no_controls_unknown(self):
        entries={"KRW-X":entry(available=False)}
        self.assertEqual(validate_cycle(cycle(entries))[0]["selected_controls"],[])

    def test_control_less_than12(self):
        self.assertEqual(set(select_controls(["KRW-X","KRW-Y"],"x")),{"KRW-X","KRW-Y"})

    def test_baseline_full_universe_compact(self):
        entries={f"KRW-X{i}":entry(f"KRW-X{i}",state="NO_UPTREND") for i in range(30)}
        m,rows=validate_cycle(cycle(entries))
        self.assertEqual(len(rows),30);self.assertEqual(sum(r["evidence_level"]=="DETAIL" for r in rows),12)
        self.assertEqual(m["membership"]["baseline"],sorted(entries))

    def test_4h_heartbeat(self):
        first=cycle({"KRW-X":entry()});old=state_after(first)
        for step in range(1,5):
            p=cycle({"KRW-X":entry(boundary=CUTOFF+step*H)},CUTOFF+step*H,old)
            if step==4:
                r=record(p);self.assertEqual(r["record_kind"],"HEARTBEAT");self.assertEqual(r["events"],[])
                self.assertEqual(r["previous_detail_reference"],record(first)["observation_id"])
            else:self.assertEqual(validate_cycle(p)[1],[])
            old=advance(old,p)

    def test_state_change_overrides_heartbeat(self):
        old=state_after(cycle({"KRW-X":entry()}))
        p=cycle({"KRW-X":entry(state="TREND_BUILDING",boundary=CUTOFF+H)},CUTOFF+H,old)
        self.assertEqual(record(p)["evidence_level"],"DETAIL")

    def test_score_drift_no_event(self):
        old=state_after(cycle({"KRW-X":entry()}));e=entry(boundary=CUTOFF+H)
        e["engine"]["trend"]["score"]="70.4";reseal_engine(e["engine"])
        self.assertEqual(validate_cycle(cycle({"KRW-X":e},CUTOFF+H,old))[1],[])

    def test_gap_resets_verification(self):
        old=state_after(cycle({"KRW-X":entry(state="PULLBACK_WATCH")}))
        p=cycle({"KRW-X":entry(state="REACCELERATION",boundary=CUTOFF+2*H)},CUTOFF+2*H,old)
        self.assertIn("STATE_REOBSERVED_AFTER_GAP",kinds(p));self.assertNotIn("ENTER_REACCELERATION",kinds(p))
        self.assertFalse(any(e["transition_verified"] for e in record(p)["events"]))
        self.assertEqual(validate_cycle(p)[0]["missed_cycle_count"],1)

    def test_failure_recovery_rebaseline(self):
        old=state_after(cycle({"KRW-X":entry()}))
        failed=cycle({"KRW-X":{"status":"API_ERROR","engine":None,"reason":"timeout"}},CUTOFF+H,old)
        old=advance(old,failed)
        p=cycle({"KRW-X":entry(state="REACCELERATION",boundary=CUTOFF+2*H)},CUTOFF+2*H,old)
        self.assertIn("DATA_RECOVERED",kinds(p));self.assertNotIn("ENTER_REACCELERATION",kinds(p))

    def test_new_listing_baseline(self):
        old=state_after(cycle({"KRW-X":entry()}))
        p=cycle({"KRW-X":entry(boundary=CUTOFF+H),"KRW-Y":entry("KRW-Y",boundary=CUTOFF+H)},CUTOFF+H,old)
        self.assertEqual(kinds(p,"KRW-Y"),["BASELINE_STATE"])

    def test_removed_not_exit(self):
        old=state_after(cycle({"KRW-X":entry(),"KRW-Y":entry("KRW-Y")}))
        p=cycle({"KRW-X":entry(boundary=CUTOFF+H)},CUTOFF+H,old)
        self.assertEqual(validate_cycle(p)[0]["membership"]["removed"],["KRW-Y"])
        self.assertNotIn("KRW-Y",advance(old,p)["states"])

    def test_damage_on_off(self):
        old=state_after(cycle({"KRW-X":entry()}));e=entry(boundary=CUTOFF+H)
        e["engine"]["trend"]["primary_damage"]=True;reseal_engine(e["engine"])
        p=cycle({"KRW-X":e},CUTOFF+H,old);self.assertIn("PRIMARY_DAMAGE",kinds(p))
        old=advance(old,p)
        p=cycle({"KRW-X":entry(boundary=CUTOFF+2*H)},CUTOFF+2*H,old)
        self.assertIn("PRIMARY_DAMAGE_CLEARED",kinds(p))

    def test_unattempted_distinct(self):
        p=cycle({"KRW-X":{"status":"UNATTEMPTED","engine":None,"reason":"time budget"}})
        m,_=validate_cycle(p);self.assertEqual(m["unattempted"],1);self.assertEqual(m["attempted"],0)

    def test_insufficient_not_api_failure(self):
        p=cycle({"KRW-X":entry(available=False)})
        m,_=validate_cycle(p);self.assertEqual(m["insufficient"],1);self.assertEqual(m["failed"],0)
        self.assertEqual(m["completeness"],"COMPLETE")

    def test_api_partial_engine_not_true(self):
        e=entry();e["status"]="API_ERROR";e["reason"]="optional TF failed"
        self.assertEqual(record(cycle({"KRW-X":e}))["summary"]["candidate"],"UNKNOWN")

class EvidenceTests(unittest.TestCase):
    def test_subset_no_full_features(self):
        p=cycle({"KRW-X":entry()});r=record(p)
        for tf,s in r["evidence"]["upbit"].items():
            self.assertLess(len(s["values"]),41)
        self.assertNotIn("1d",r["evidence"]["binance_spot"])

    def test_no_raw_ohlcv(self):
        p=cycle({"KRW-X":entry()})
        def keys(obj):
            if isinstance(obj,dict):
                for k,v in obj.items():yield k;yield from keys(v)
            elif isinstance(obj,list):
                for v in obj:yield from keys(v)
        allkeys=set(keys(validate_cycle(p)))
        self.assertFalse(allkeys & {"open","high","low","close","volume","base_volume","quote_trade_amount","candles"})

    def test_future_proxy_null(self):
        r=record(cycle({"KRW-X":entry()}))
        a=r["observation"]["price_anchors"]["NEXT_1H_OPEN_PROXY"]
        self.assertIsNone(a["price"]);self.assertEqual(a["target_boundary_ms"],CUTOFF+H)

    def test_price_diagnostic_only(self):
        text=cycle({"KRW-X":entry()}).decode()
        self.assertIn("COMPLETED_1H_CLOSE_DIAGNOSTIC",text);self.assertNotIn('"entry_price"',text)

    def test_engine_false_preserved(self):
        r=record(cycle({"KRW-X":entry(state="REACCELERATION")}))
        self.assertFalse(r["observation"]["engine_prior_state_transition_verified"])

    def test_logical_id_distinct_engine_id(self):
        r=record(cycle({"KRW-X":entry()}))
        self.assertNotEqual(r["observation_id"],r["engine_observation_id"])

    def test_hash_deterministic(self):
        p=cycle({"KRW-X":entry()});m,_=validate_cycle(p)
        raw=p.split(b"\n",1)[1]
        self.assertEqual(m["records_payload_sha256"],hashlib.sha256(raw).hexdigest())

    def test_context_independence(self):
        entries={"KRW-X":entry()}
        with localcontext() as c:c.prec=5;a=cycle(entries)
        with localcontext() as c:c.prec=50;b=cycle(entries)
        self.assertEqual(a,b)

    def test_input_immutability(self):
        entries={"KRW-X":entry()};old=copy.deepcopy(entries)
        cycle(entries);self.assertEqual(entries,old)

    def test_projection_no_io(self):
        entries={"KRW-X":entry()}
        with patch("builtins.open",side_effect=AssertionError("IO forbidden")):cycle(entries)

    def test_policy_and_cohort_hash(self):
        self.assertEqual(C.POLICY_SHA256,F.digest(C.POLICY))
        a=C.versions();b=dict(a,mapping_registry_version="approved-1")
        self.assertNotEqual(C.cohort_id(a),C.cohort_id(b))

    def test_envelope_tamper_rejected(self):
        e=entry();e["engine"]["trend"]["score"]="1"
        with self.assertRaises(ValueError):cycle({"KRW-X":e})

    def test_feature_tamper_rejected(self):
        e=entry();e["bundle"]["upbit"]["4h"]["features"]["return3_pct"]="99"
        with self.assertRaises(ValueError):cycle({"KRW-X":e})

    def test_future_generation_rejected(self):
        e=entry();e["bundle"]["upbit"]["4h"]["metadata"]["feature_generated_at_ms"]=END+100000
        with self.assertRaises(ValueError):cycle({"KRW-X":e})

    def test_future_observation_rejected(self):
        e=entry();e["engine"]["observation_time_utc"]=C.iso(END+99999);reseal_engine(e["engine"])
        with self.assertRaises(ValueError):cycle({"KRW-X":e})

    def test_wrong_close_anchor_rejected(self):
        e=entry();e["close_1h"]["candle_close_ms"]+=H
        with self.assertRaises(ValueError):cycle({"KRW-X":e})

    def test_no_silent_missing_market(self):
        with self.assertRaises(ValueError):build_cycle(["KRW-X","KRW-Y"],{"KRW-X":entry()},CUTOFF,START,END,END+1000,"revision")

    def test_empty_universe_no_fallback(self):
        with self.assertRaises(ValueError):cycle({})

class StorageTests(unittest.TestCase):
    def test_different_bytes_conflict(self):
        p=cycle({"KRW-X":entry()});e=entry();e["engine"]["trend"]["score"]="70.4";reseal_engine(e["engine"])
        q=cycle({"KRW-X":e})
        with tempfile.TemporaryDirectory() as root:
            write_once(root,p,END+1000)
            with self.assertRaises(Conflict):write_once(root,q,END+1000)
            self.assertEqual((Path(root)/C.cycle_path(CUTOFF)).read_bytes(),p)

    def test_preview_not_written(self):
        p=cycle({"KRW-X":entry()},publishable=False)
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):write_once(root,p,END+1000)
            self.assertEqual(list(Path(root).iterdir()),[])

    def test_atomic_temp_removed(self):
        with tempfile.TemporaryDirectory() as root:
            write_once(root,cycle({"KRW-X":entry()}),END+1000)
            self.assertEqual(len(list(Path(root).rglob("*.jsonl"))),1)
            self.assertEqual(list(Path(root).rglob("*.tmp")),[])

    def test_validation_failure_no_file(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):write_once(root,b"bad\n",END+1000)
            self.assertEqual(list(Path(root).iterdir()),[])

    def test_load_append_only_state(self):
        with tempfile.TemporaryDirectory() as root:
            p=cycle({"KRW-X":entry()});write_once(root,p,END+1000)
            old=load_previous(root,CUTOFF+H)
            q=cycle({"KRW-X":entry(state="PULLBACK_WATCH",boundary=CUTOFF+H)},CUTOFF+H,old)
            write_once(root,q,END+H+1000)
            self.assertEqual((Path(root)/C.cycle_path(CUTOFF)).read_bytes(),p)
            self.assertEqual(load_previous(root,CUTOFF+2*H)["states"]["KRW-X"]["summary"]["state"],"PULLBACK_WATCH")

    def test_old_cycle_prohibited(self):
        with self.assertRaises(ValueError):C.check_window(CUTOFF,START+H)

    def test_grace_exact(self):
        C.check_window(CUTOFF,CUTOFF+59*60000)
        with self.assertRaises(ValueError):C.check_window(CUTOFF,CUTOFF+59*60000+1)

    def test_deadline_exact(self):
        C.check_window(CUTOFF,START,CUTOFF+59*60000)
        with self.assertRaises(ValueError):C.check_window(CUTOFF,START,CUTOFF+59*60000+1)

    def test_late_start_has_full_workflow_runtime(self):
        late=CUTOFF+55*60000
        C.check_window(CUTOFF,late,late+24*60000)
        with self.assertRaises(ValueError):
            C.check_window(CUTOFF,late,late+25*60000+1)

    def test_bad_boundary(self):
        with self.assertRaises(ValueError):C.cycle_path(CUTOFF+1)

    def test_missing_newline(self):
        with self.assertRaises(ValueError):validate_cycle(cycle({"KRW-X":entry()})[:-1])

    def test_hash_corruption(self):
        p=cycle({"KRW-X":entry()}).replace(b'TREND_CONTINUATION',b'TREND_BUILDING')
        with self.assertRaises(ValueError):validate_cycle(p)

    def test_noncanonical_rejected(self):
        p=cycle({"KRW-X":entry()}).replace(b'"record_kind":',b'"record_kind": ',1)
        with self.assertRaises(ValueError):validate_cycle(p)

    def test_duplicate_key_rejected(self):
        with self.assertRaises(ValueError):validate_cycle(b'{"a":1,"a":2}\n')

    def test_past_projection_rejected(self):
        old=state_after(cycle({"KRW-X":entry()}))
        with self.assertRaises(ValueError):cycle({"KRW-X":entry()},previous=old)

    def test_preview_late_not_production(self):
        entries={"KRW-X":entry()}
        e=entries["KRW-X"]["engine"];e["observation_time_utc"]=C.iso(CUTOFF+41*60000);reseal_engine(e)
        p=build_cycle(list(entries),entries,CUTOFF,CUTOFF+40*60000,CUTOFF+42*60000,CUTOFF+42*60000,"code",publishable=False)
        self.assertEqual(validate_cycle(p)[0]["mode"],"DRY_RUN_NONPUBLISHABLE")

class RunnerTests(unittest.TestCase):
    def test_activation_off_no_api_no_output(self):
        with patch.dict(os.environ,{},clear=True),patch.object(R,"collect",side_effect=AssertionError("API forbidden")),io.StringIO() as out,patch("sys.stdout",out):
            R.main(["--repo",".","--record"])
            self.assertIn("ACTIVATION_OFF",out.getvalue())

    def test_publish_requires_activation(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ValueError):R.publish(".",cycle({"KRW-X":entry()}),lambda:END)

    def test_workflow_gate_off(self):
        root=Path(__file__).resolve().parents[2]
        text=(root/".github/workflows/upbit-b-history.yml").read_text()
        self.assertIn("active=true",text);self.assertNotIn("active=false",text)
        self.assertIn("needs.activation.outputs.active == 'true'",text)
        self.assertIn("cancel-in-progress: false",text);self.assertIn("timeout-minutes: 25",text)
        self.assertIn("cron: '7 * * * *'",text);self.assertNotIn("workflow_run:",text)

    def test_replay_before_api(self):
        with tempfile.TemporaryDirectory() as root:
            write_once(root,cycle({"KRW-X":entry()}),END+1000)
            with patch.dict(os.environ,{"UPBIT_B_HISTORY_ACTIVATED":"true"}),patch.object(R.time,"time_ns",return_value=START*1000000),patch.object(R,"collect",side_effect=AssertionError("API forbidden")),patch.object(R,"publish",return_value={"status":"REMOTE_IDENTICAL_NOOP"}),patch("sys.stdout",io.StringIO()):
                self.assertIsNone(R.main(["--repo",root,"--record"]))

    def test_preview_main_no_production_file(self):
        e=entry();times=iter([START,END,END+1000,END+2000])
        with tempfile.TemporaryDirectory() as root,patch.object(R.time,"time_ns",side_effect=lambda:next(times)*1000000),patch.object(R,"collect",return_value=(["KRW-X"],{"KRW-X":e},{})),patch.object(R,"_git",return_value=SimpleNamespace(stdout=b"a"*40,returncode=0)),patch("sys.stdout",io.StringIO()):
            result=R.main(["--repo",root,"--dry-run"])
            self.assertEqual(result["production_files_created"],0);self.assertEqual(list(Path(root).rglob("*.jsonl")),[])

    def test_record_main_passes_clock_callback_to_publish(self):
        e=entry();times=iter([START,END,END+1000,END+2000,END+3000,END+4000,END+5000])
        seen={}
        def fake_publish(repo,payload,clock):
            seen["callable"]=callable(clock)
            seen["now"]=clock()
            return {"status":"PUSHED","path":"fixture"}
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{"UPBIT_B_HISTORY_ACTIVATED":"true"}),patch.object(R.time,"time_ns",side_effect=lambda:next(times)*1000000),patch.object(R,"collect",return_value=(["KRW-X"],{"KRW-X":e},{})),patch.object(R,"_git",return_value=SimpleNamespace(stdout=b"a"*40,returncode=0)),patch.object(R,"publish",side_effect=fake_publish),patch("sys.stdout",io.StringIO()):
            result=R.main(["--repo",root,"--record"])
            self.assertTrue(seen["callable"]);self.assertEqual(result["publication"]["status"],"PUSHED")

    def test_storage_projection_no_outcomes(self):
        e={"KRW-X":entry(),"KRW-Y":entry("KRW-Y",state="NO_UPTREND")}
        p=cycle(e);report=R.storage_report(p,e)
        self.assertEqual(report["cold_start_payload_bytes"],len(p))
        self.assertFalse(report["future_event_rate_measured"])

    def test_no_force_push_in_source(self):
        text=Path(R.__file__).read_text()
        self.assertNotIn('"--force"',text);self.assertNotIn('"--force-with-lease"',text)

    def test_existing_parameters_unchanged(self):
        contract=C.versions()
        self.assertEqual(contract["engine_parameter_sha256"],"17515b811ab3c68a2ba9590c7ce9a3c43c587106570f2bfc0616cc5857a95d53")
        self.assertEqual(contract["feature_parameter_sha256"],"7dc9a941df84e364f66412ce326ef8d917335d71c3c28edf2c6dd2e033001227")

class PublicationTests(unittest.TestCase):
    def setup_payload(self,root):
        p=cycle({"KRW-X":entry()});write_once(root,p,END+1000)
        return p,C.cycle_path(CUTOFF)

    def transport(self,path,push_ok=True,status=True,remote=None):
        calls=[]
        def git(repo,*args,check=True):
            calls.append(args)
            data=b"";code=0
            if args[0]=="status":data=("?? "+path+"\n").encode() if status else b""
            elif args[:3]==("diff","--cached","--name-status"):data=("A\t"+path+"\n").encode()
            elif args[0]=="push":code=0 if push_ok else 1
            elif args[0]=="rev-parse":data=b"a"*40+b"\n"
            elif args[0]=="cat-file":code=0 if remote is not None else 1
            elif args[0]=="show":data=remote
            elif args[0]=="merge-base":data=b"b"*40+b"\n"
            elif args[:2]==("diff","--name-status"):data=("A\t"+path+"\n").encode()
            return SimpleNamespace(stdout=data,returncode=code)
        return git,calls

    def test_success_one_cycle_commit(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{"UPBIT_B_HISTORY_ACTIVATED":"true"}):
            p,path=self.setup_payload(root);git,calls=self.transport(path)
            with patch.object(R,"_git",side_effect=git):result=R.publish(root,p,lambda:END+1000)
            self.assertEqual(result["status"],"PUSHED")
            self.assertEqual(sum(a[0]=="commit" for a in calls),1)
            self.assertEqual(sum(a[0]=="push" for a in calls),1)

    def test_retries_reuse_bytes_no_api(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{"UPBIT_B_HISTORY_ACTIVATED":"true"}):
            p,path=self.setup_payload(root);git,calls=self.transport(path,push_ok=False)
            with patch.object(R,"_git",side_effect=git),patch.object(R,"collect",side_effect=AssertionError("no recollection")):
                with self.assertRaises(Conflict):R.publish(root,p,lambda:END+1000)
            self.assertEqual(sum(a[0]=="push" for a in calls),3)
            self.assertEqual((Path(root)/path).read_bytes(),p)

    def test_remote_same_bytes_noop(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{"UPBIT_B_HISTORY_ACTIVATED":"true"}):
            p,path=self.setup_payload(root);git,calls=self.transport(path,push_ok=False,remote=p)
            with patch.object(R,"_git",side_effect=git):result=R.publish(root,p,lambda:END+1000)
            self.assertEqual(result["status"],"REMOTE_IDENTICAL_NOOP")
            self.assertFalse(any(a[0]=="rebase" for a in calls))

    def test_remote_different_conflict(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{"UPBIT_B_HISTORY_ACTIVATED":"true"}):
            p,path=self.setup_payload(root);git,calls=self.transport(path,push_ok=False,remote=p+b"other")
            with patch.object(R,"_git",side_effect=git):
                with self.assertRaises(Conflict):R.publish(root,p,lambda:END+1000)
            self.assertFalse(any(a[0]=="rebase" for a in calls))

    def test_locally_committed_retry_not_lost(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{"UPBIT_B_HISTORY_ACTIVATED":"true"}):
            p,path=self.setup_payload(root);git,calls=self.transport(path,status=False)
            with patch.object(R,"_git",side_effect=git):result=R.publish(root,p,lambda:END+1000)
            self.assertEqual(result["status"],"PUSHED")
            self.assertFalse(any(a[0]=="commit" for a in calls));self.assertTrue(any(a[0]=="rebase" for a in calls))

    def test_unrelated_local_change_stops(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{"UPBIT_B_HISTORY_ACTIVATED":"true"}):
            p,path=self.setup_payload(root)
            with patch.object(R,"_git",return_value=SimpleNamespace(stdout=b" M upbit_b/features.py\n",returncode=0)):
                with self.assertRaises(ValueError):R.publish(root,p,lambda:END+1000)

    def test_missing_chain_rejected(self):
        previous=state_after(cycle({"KRW-X":entry()}))
        p=cycle({"KRW-X":entry(boundary=CUTOFF+H)},CUTOFF+H,previous)
        with self.assertRaises(ValueError):advance(empty_previous(),p)

    def test_matching_engine_bundle_required(self):
        e=entry();e["engine"]["source"]["upbit"]["1h"]["measurement_sha256"]="b"*64;reseal_engine(e["engine"])
        with self.assertRaises(ValueError):cycle({"KRW-X":e})

if __name__=="__main__":unittest.main()

import unittest
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from btc_anytime.entry.engine import *
from btc_anytime.entry.storage import persist

BASE=1791072000000


def item(t,C="99",tf="15m",**features):
    c=D(C);duration=DURATIONS[tf];observed=t+duration+100
    raw={"time":t,"timeframe":tf,"open":str(c-D('.1')),"high":str(c+D('.1')),"low":str(c-D('1')),
         "close":str(c),"volume":"100","is_closed":True,"received_at_utc":iso(observed),"source":"tradingview_binance_usdm_htf"}
    f={n:None for n in NAMES};f.update(atr_14="2",ema_20="99",ema_50="99",volume_ratio_20="1.2",
        rolling_high_20="100",rolling_low_20="95",breakout_20=False,breakdown_20=False,
        structure_high_state="EQ",structure_low_state="EQ");f.update(features)
    return {"time":t,"timeframe":tf,"raw":raw,"raw_ref":{"file":"fixture.jsonl","line":1},
        "available_at_ms":observed,"generation_time_ms":observed,"features":f,
        "quality":{n:{"ready":v is not None,"available_at_ms":observed} for n,v in f.items()},
        "availability_evidence":{"kind":"consumer_first_observed","ref":"fixture","observed_at_ms":observed,"canonical_row_hash":digest(raw)},
        "oi_metadata":{"unit_status":"unavailable"}}


def reseal(x):
    x["availability_evidence"]["canonical_row_hash"]=digest(x["raw"])
    return x


def direction(t,side="LONG",confidence=".50",regime="ALIGNED_TREND"):
    d={"signal_history_schema_version":"btc-direction-signal-history-v1","decision_time_utc":iso(t+STEP+200),
       "direction_class":side,"confidence":confidence,"regime":regime,"direction_score":".5",
       "parameter_version":"initial-hypothesis-1","parameter_hash":"direction-fixture","direction_engine_version":"1.0.0",
       "trigger_15m":{"candle_open_time_ms":t}}
    d["decision_id"]=digest(d);return d


def fixture(pb=False):
    values=["96","96","96","96","97","98","100","99.4"] if pb else ["99"]*7+["100.4"]
    xs=[item(BASE+i*STEP,c) for i,c in enumerate(values)]
    if not pb:xs[-1]["features"]["breakout_20"]=True
    xs.extend([item(BASE,"99","1h"),item(BASE+3600000,"99","1h")])
    return xs


def run(xs,previous=None,side="LONG",confidence=".50",regime="ALIGNED_TREND",E=None):
    t=max(x["time"] for x in xs if x["timeframe"]=="15m");E=t+STEP+300 if E is None else E
    d=direction(t,side,confidence,regime);m=seal_manifest(xs,direction_ref(d,t+STEP+200),t+STEP+200)
    return evaluate(d,m,E,previous),m


def follow(xs,c="100.6",**f):
    t=max(x["time"] for x in xs if x["timeframe"]=="15m")+STEP
    return xs+[item(t,c,**f)]


def mirror(xs):
    xs=deepcopy(xs)
    for x in xs:
        a=x["raw"];h,l=a["high"],a["low"]
        for k in ("open","close"):a[k]=str(D(200)-D(a[k]))
        a["high"],a["low"]=str(D(200)-D(l)),str(D(200)-D(h))
        f=x["features"]
        for k in ("ema_20","ema_50"):
            if f[k] is not None:f[k]=str(D(200)-D(f[k]))
        f["rolling_high_20"],f["rolling_low_20"]=str(D(200)-D(f["rolling_low_20"])),str(D(200)-D(f["rolling_high_20"]))
        f["breakout_20"],f["breakdown_20"]=f["breakdown_20"],f["breakout_20"];reseal(x)
    return xs


class EntryTests(unittest.TestCase):
    def test_breakout_arm(self):
        r,_=run(fixture());self.assertEqual(r["state"]["setup"]["type"],"BREAKOUT")
    def test_pullback_arm(self):
        r,_=run(fixture(True));self.assertEqual(r["state"]["setup"]["type"],"PULLBACK")
    def test_candidate(self):
        xs=fixture();r,_=run(xs);q,_=run(follow(xs),r)
        self.assertEqual(q["entry_state"],"ENTRY_CANDIDATE");self.assertEqual(q["reference"]["price"],"100.600000000000000000")
    def test_pullback_confirmation(self):
        xs=fixture(True);r,_=run(xs);q,_=run(follow(xs,"100.4"),r)
        self.assertEqual(q["entry_state"],"ENTRY_CANDIDATE")
    def test_same_side_origin(self):
        xs=fixture();r,_=run(xs);q,_=run(follow(xs),r,side="STRONG_LONG")
        self.assertEqual(r["state"]["setup"]["origin_direction_decision_id"],q["state"]["setup"]["origin_direction_decision_id"])
        self.assertNotEqual(r["state"]["setup"]["authorizing_direction_decision_id"],q["state"]["setup"]["authorizing_direction_decision_id"])
    def test_opposite_invalidates(self):
        xs=fixture();r,_=run(xs);q,_=run(follow(xs),r,side="SHORT");self.assertEqual(q["state"]["setup"]["lifecycle"],"INVALIDATED")
    def test_neutral_invalidates(self):
        xs=fixture();r,_=run(xs);q,_=run(follow(xs),r,side="NEUTRAL");self.assertEqual(q["state"]["setup"]["lifecycle"],"INVALIDATED")
    def test_neutral_no_entry(self):
        r,_=run(fixture(),side="NEUTRAL");self.assertIn("ENTRY.NO_DIRECTIONAL_ENTRY",r["reason_codes"])
    def test_confidence_boundary(self):
        self.assertEqual(run(fixture(),confidence=".50")[0]["entry_state"],"WAIT")
        self.assertEqual(run(fixture(),confidence=".499999")[0]["entry_state"],"NO_ENTRY")
    def test_reversal_veto(self):
        self.assertEqual(run(fixture(),regime="POSSIBLE_REVERSAL")[0]["entry_state"],"NO_ENTRY")
    def test_frozen(self):
        xs=fixture();r,_=run(xs);ys=follow(xs,atr_14="3",rolling_high_20="100.5");q,_=run(ys,r)
        for k in ("A0","reference","setup_id"):self.assertEqual(r["state"]["setup"][k],q["state"]["setup"][k])
    def test_expiry_precedence(self):
        xs=fixture();r,_=run(xs)
        for _ in range(3):xs=follow(xs,"100.4",volume_ratio_20=".9");r,_=run(xs,r)
        xs=follow(xs);q,_=run(xs,r);self.assertEqual(q["state"]["setup"]["lifecycle"],"EXPIRED")
    def test_reference_failure(self):
        xs=fixture();r,_=run(xs);q,_=run(follow(xs,"99.7"),r);self.assertEqual(q["state"]["setup"]["lifecycle"],"INVALIDATED")
    def test_failure_exact_buffer(self):
        xs=fixture();r,_=run(xs);q,_=run(follow(xs,"99.8"),r);self.assertEqual(q["state"]["setup"]["lifecycle"],"ARMED")
    def test_candidate_once(self):
        xs=fixture();r,_=run(xs);ys=follow(xs);q,_=run(ys,r);z,_=run(follow(ys,"100.7"),q);self.assertNotEqual(z["entry_state"],"ENTRY_CANDIDATE")
    def test_replay(self):
        xs=fixture();r,_=run(xs);q,_=run(xs,r,E=max(x["time"] for x in xs)+STEP+400);self.assertEqual(q,r)
    def test_deterministic(self):self.assertEqual(run(fixture())[0],run(fixture())[0])
    def test_future_suffix(self):
        xs=fixture();r,m=run(xs);d=direction(xs[7]["time"]);ys=follow(xs)
        mm=seal_manifest(ys,m["direction_ref"],m["feature_generated_at_ms"])
        self.assertEqual(r,evaluate(d,mm,xs[7]["time"]+STEP+300))
    def test_late_backfill(self):
        xs=fixture();E=xs[7]["time"]+STEP+300;xs[7]["available_at_ms"]=E+1
        r,_=run(xs,E=E);self.assertNotEqual(r.get("trigger_time"),xs[7]["time"])
    def test_missed_boundary(self):
        xs=fixture();r,_=run(xs);ys=follow(follow(xs));q,_=run(ys,r);self.assertEqual(q["execution_status"],"SKIPPED_BOUNDARY_MISSED")
    def test_route_independence(self):
        xs=fixture();xs[7]["features"]["ema_50"]=None;xs[7]["quality"]["ema_50"]["ready"]=False
        r,_=run(xs);self.assertEqual(r["routes"]["PULLBACK"]["status"],"UNAVAILABLE");self.assertEqual(r["routes"]["BREAKOUT"]["status"],"PASS")
    def test_both_unavailable(self):
        xs=fixture();xs[7]["features"]["ema_50"]=None;xs[7]["quality"]["ema_50"]["ready"]=False;xs[7]["quality"]["breakout_20"]["ready"]=False
        r,_=run(xs);self.assertEqual(r["execution_status"],"SKIPPED_FEATURE_UNAVAILABLE");self.assertIsNone(r["entry_state"])
    def test_future_pivot(self):
        xs=fixture(True);x=xs[7];x["features"]["pivot_low"]={"value":"95","pivot_time":iso(BASE),"confirmed_at":iso(x["time"]+2*STEP),"confirmation_close_exclusive":iso(x["time"]+2*STEP)};x["quality"]["pivot_low"]["ready"]=True
        self.assertEqual(run(xs)[0]["execution_status"],"FAILED_CONTRACT")
    def test_pivot_priority(self):
        xs=fixture(True);x=xs[7];x["features"]["pivot_low"]={"value":"97","pivot_time":iso(BASE),"confirmed_at":iso(x["time"]),"confirmation_close_exclusive":iso(x["time"])};x["quality"]["pivot_low"]["ready"]=True
        self.assertEqual(run(xs)[0]["state"]["setup"]["reference"]["type"],"CONFIRMED_PIVOT")
    def test_chase_exact(self):
        p=parameters();self.assertFalse(chase(D(103),D(100),D(2),D(1),1,p)["veto"]);self.assertTrue(chase(D('103.01'),D(100),D(2),D(1),1,p)["veto"])
    def test_movement_chase_exact(self):
        p=parameters();self.assertFalse(chase(D('102.1'),D(100),D(2),D(2),1,p)["veto"]);self.assertTrue(chase(D('102.1'),D(100),D(2),D('2.01'),1,p)["veto"])
    def test_breakout_chase_exact(self):
        p=parameters();s={"type":"BREAKOUT","reference":{"value":"100"},"A0":"2"}
        self.assertFalse(chase(D('101.5'),D(101),D(2),D(0),1,p,s)["veto"]);self.assertTrue(chase(D('101.51'),D(101),D(2),D(0),1,p,s)["veto"])
    def test_breakout_threshold(self):
        xs=fixture();x=xs[7];x["raw"]["close"]="100.2";x["raw"]["open"]="100.1";x["raw"]["high"]="100.21";reseal(x)
        self.assertEqual(run(xs)[0]["routes"]["BREAKOUT"]["status"],"PASS")
    def test_conflict(self):
        xs=fixture();x=xs[7];x["features"].update(structure_high_state="LH",structure_low_state="LL",breakdown_20=True);x["raw"]["close"]="98";x["raw"]["open"]="98";x["raw"]["low"]="97";reseal(x)
        self.assertIn("ENTRY.SHORT_TERM_CONFLICT",run(xs)[0]["reason_codes"])
    def test_unknown_oi_optional(self):self.assertEqual(run(fixture())[0]["oi_state"],"UNAVAILABLE")
    def test_oi_confirmation(self):
        xs=fixture();x=xs[7];x["features"]["oi_change"]="10";x["quality"]["oi_change"]["ready"]=True;x["oi_metadata"]["unit_status"]="validated"
        self.assertEqual(run(xs)[0]["oi_state"],"CONFIRMING")
    def test_source_hash(self):
        xs=fixture();xs[7]["raw"]["volume"]="123";self.assertEqual(run(xs)[0]["execution_status"],"FAILED_SOURCE_CONFLICT")
    def test_immutable_inputs(self):
        xs=fixture();before=deepcopy(xs);run(xs);self.assertEqual(xs,before)
    def test_parameter_isolation(self):
        xs=fixture();r,m=run(xs);p=parameters();p["parameter_version"]="test-2";p["parameter_hash"]=digest({k:v for k,v in p.items() if k!="parameter_hash"})
        q=evaluate(direction(xs[7]["time"]),m,xs[7]["time"]+STEP+300,p=p);self.assertNotEqual(q["entry_evaluation_id"],r["entry_evaluation_id"])
    def test_append_only(self):
        r,m=run(fixture())
        with TemporaryDirectory() as tmp:
            repo=Path(tmp);self.assertEqual(persist(repo,m,r),"APPENDED");self.assertEqual(persist(repo,m,r),"REPLAY_NOOP")
            q=deepcopy(r);q.pop("entry_evaluation_id");q["warnings"].append("different");q["entry_evaluation_id"]=digest(q)
            with self.assertRaises(ValueError):persist(repo,m,q)
    def test_compact(self):
        r,m=run(fixture());self.assertNotIn("input_snapshot",m);self.assertNotIn("input_snapshot",r);self.assertLess(len(json.dumps(m)),50000)
    def test_mirror(self):
        xs=fixture();r,_=run(xs);ys=deepcopy(xs)
        for x in ys:
            a=x["raw"];h,l=a["high"],a["low"]
            for k in ("open","close"):a[k]=str(D(200)-D(a[k]))
            a["high"]=str(D(200)-D(l));a["low"]=str(D(200)-D(h))
            f=x["features"]
            for k in ("ema_20","ema_50"):f[k]=str(D(200)-D(f[k]))
            f["rolling_high_20"],f["rolling_low_20"]=str(D(200)-D(f["rolling_low_20"])),str(D(200)-D(f["rolling_high_20"]))
            f["breakout_20"],f["breakdown_20"]=f["breakdown_20"],f["breakout_20"];reseal(x)
        q,_=run(ys,side="SHORT");self.assertEqual(r["entry_state"],q["entry_state"]);self.assertEqual(r["routes"]["BREAKOUT"]["measurements"],q["routes"]["BREAKOUT"]["measurements"])

    def test_duplicate_input(self):
        xs=fixture();xs.append(deepcopy(xs[7]));self.assertEqual(run(xs)[0]["execution_status"],"FAILED_SOURCE_CONFLICT")
    def test_gap_input(self):
        xs=fixture();del xs[2];self.assertIn("INPUT.PATH_GAP",run(xs)[0]["reason_codes"])
    def test_bad_received_clock(self):
        xs=fixture();xs[7]["raw"]["received_at_utc"]=iso(xs[7]["time"]);reseal(xs[7]);self.assertEqual(run(xs)[0]["execution_status"],"FAILED_CONTRACT")
    def test_future_generation(self):
        xs=fixture();xs[7]["generation_time_ms"]+=1000;self.assertEqual(run(xs)[0]["execution_status"],"FAILED_CONTRACT")
    def test_future_feature_quality(self):
        xs=fixture();xs[7]["quality"]["atr_14"]["available_at_ms"]+=1000;self.assertEqual(run(xs)[0]["execution_status"],"SKIPPED_FEATURE_UNAVAILABLE")
    def test_parameter_rollout_previous_rejected(self):
        xs=fixture();r,_=run(xs);ys=follow(xs);_,m=run(ys);p=parameters();p["parameter_version"]="new";p["parameter_hash"]=digest({k:v for k,v in p.items() if k!="parameter_hash"})
        q=evaluate(direction(ys[-1]["time"]),m,ys[-1]["time"]+STEP+300,r,p);self.assertEqual(q["execution_status"],"FAILED_CONTRACT")
    def test_no_next_open(self):
        xs=fixture();r,m=run(xs);ys=follow(xs,"199");mm=seal_manifest(ys,m["direction_ref"],m["feature_generated_at_ms"])
        self.assertEqual(evaluate(direction(xs[7]["time"]),mm,xs[7]["time"]+STEP+300),r)
    def test_stale_direction(self):
        xs=fixture();r,m=run(xs);d=direction(xs[7]["time"]-3*STEP);m=seal_manifest(xs,direction_ref(d,xs[7]["time"]+STEP+200),xs[7]["time"]+STEP+200)
        self.assertEqual(evaluate(d,m,xs[7]["time"]+STEP+300)["execution_status"],"SKIPPED_STALE_DIRECTION")
    def test_non_live_trigger(self):
        xs=fixture();xs[7]["raw"]["source"]="binance_usdm_official_rest_klines_backfill";reseal(xs[7]);self.assertIn("INPUT.NON_LIVE_TRIGGER",run(xs)[0]["reason_codes"])
    def test_breakout_volume_below(self):
        xs=fixture();xs[7]["features"]["volume_ratio_20"]="1.199999";self.assertEqual(run(xs)[0]["routes"]["BREAKOUT"]["status"],"FAIL")
    def test_confirmation_volume_exact(self):
        xs=fixture();r,_=run(xs);q,_=run(follow(xs,volume_ratio_20="1"),r);self.assertEqual(q["entry_state"],"ENTRY_CANDIDATE")
    def test_confirmation_volume_below(self):
        xs=fixture();r,_=run(xs);q,_=run(follow(xs,volume_ratio_20=".999999"),r);self.assertEqual(q["entry_state"],"WAIT")
    def test_hold_exact(self):
        xs=fixture();r,_=run(xs);ys=follow(xs,"100.1");q,_=run(ys,r);self.assertNotEqual(q["state"]["setup"]["lifecycle"],"INVALIDATED")
        p=parameters();self.assertEqual(D('100.1')-D(100),D(p["hold_min_atr"])*D(2))
    def test_close_location_boundary(self):
        xs=fixture();r,_=run(xs);ys=follow(xs,"100.6");x=ys[-1];x["raw"]["low"]="99.3";x["raw"]["high"]="101.3";reseal(x)
        self.assertEqual(run(ys,r)[0]["entry_state"],"ENTRY_CANDIDATE")
        x["raw"]["high"]="101.300001";reseal(x);self.assertEqual(run(ys,r)[0]["entry_state"],"WAIT")
    def test_oi_conflict_not_veto(self):
        xs=fixture();r,_=run(xs);ys=follow(xs,"100.3",oi_change="10");ys[-1]["oi_metadata"]["unit_status"]="validated";q,_=run(ys,r)
        self.assertEqual(q["oi_state"],"CONFLICTING");self.assertEqual(q["entry_state"],"WAIT")
    def test_same_boundary_new_authorization_no_rewrite(self):
        xs=fixture();r,_=run(xs);q,_=run(xs,r,side="STRONG_LONG");self.assertEqual(q,r)
    def test_candidate_storage_once(self):
        xs=fixture();r,_=run(xs);q,m=run(follow(xs),r)
        with TemporaryDirectory() as tmp:
            repo=Path(tmp);persist(repo,m,q);persist(repo,m,q);self.assertEqual(len(list((repo/"output_entry/btc_anytime/v1/candidates").glob("*.json"))),1)
    def test_storage_tamper(self):
        r,m=run(fixture())
        with TemporaryDirectory() as tmp:
            repo=Path(tmp);persist(repo,m,r);f=repo/"output_entry/btc_anytime/v1/inputs"/(m["manifest_id"]+".json");f.write_text("{}")
            with self.assertRaises(ValueError):persist(repo,m,r)
    def test_reference_is_not_execution(self):
        xs=fixture();r,_=run(xs);q,_=run(follow(xs),r);self.assertEqual(q["reference_price_type"],"CONFIRMATION_CANDLE_CLOSE");self.assertNotIn("fill_price",q)
    def test_pullback_depth_exact(self):
        p=parameters()
        for c,expected in (("99.5","PASS"),("99.500001","FAIL"),("97.7","PASS"),("97.699999","FAIL")):
            xs=fixture(True);x=xs[7];x["raw"].update(open=c,close=c,high=str(D(c)+D('.1')),low=str(D(c)-1));reseal(x)
            x["features"].update(ema_20=c,ema_50=c)
            self.assertEqual(pullback(xs[:8],x,1,x["time"]+STEP+300,p)["status"],expected)
    def test_pullback_retracement_exact(self):
        xs=fixture(True);x=xs[7];c="97.64";x["raw"].update(open=c,close=c,high="97.7",low="97");reseal(x);x["features"].update(ema_20=c,ema_50=c,atr_14="3")
        route=pullback(xs[:8],x,1,x["time"]+STEP+300,parameters());self.assertEqual(route["measurements"]["retracement_ratio"],D('.6'));self.assertEqual(route["status"],"PASS")
        x["raw"]["close"]="97.639999";self.assertEqual(pullback(xs[:8],x,1,x["time"]+STEP+300,parameters())["status"],"FAIL")
    def test_pullback_proximity_exact(self):
        xs=fixture(True);x=xs[7];x["features"].update(ema_20="100.4",ema_50="100.4")
        self.assertEqual(pullback(xs[:8],x,1,x["time"]+STEP+300,parameters())["status"],"PASS")
        x["features"].update(ema_20="100.400001",ema_50="100.400001")
        self.assertEqual(pullback(xs[:8],x,1,x["time"]+STEP+300,parameters())["status"],"FAIL")
    def test_manifest_order_invariance(self):
        xs=fixture();r,_=run(xs);self.assertEqual(r,run(list(reversed(xs)))[0])
    def test_frozen_atr_chase(self):
        xs=fixture();r,_=run(xs);ys=follow(xs,"102",atr_14="100",ema_20="99")
        q,_=run(ys,r);self.assertTrue(q["chase_state"]["veto"]);self.assertEqual(q["state"]["setup"]["A0"],"2.000000000000000000")
    def test_expiry_exact_equality(self):
        xs=fixture();r,_=run(xs)
        for _ in range(3):xs=follow(xs,"100.4",volume_ratio_20=".9");r,_=run(xs,r)
        xs=follow(xs);E=r["state"]["setup"]["expires_at"];x=xs[-1]
        x["raw"]["received_at_utc"]=iso(E);reseal(x);x["available_at_ms"]=E;x["generation_time_ms"]=E;x["availability_evidence"]["observed_at_ms"]=E
        for quality in x["quality"].values():quality["available_at_ms"]=E
        d=direction(x["time"]);d.pop("decision_id");d["decision_time_utc"]=iso(E);d["decision_id"]=digest(d)
        q=evaluate(d,seal_manifest(xs,direction_ref(d,E),E),E,r)
        self.assertEqual(q["state"]["setup"]["lifecycle"],"EXPIRED")
    def test_short_breakout_candidate(self):
        xs=fixture();r,_=run(mirror(xs),side="SHORT");q,_=run(mirror(follow(xs)),r,side="SHORT")
        self.assertEqual(q["entry_state"],"ENTRY_CANDIDATE");self.assertEqual(q["reference_price"],"99.400000000000000000")
    def test_short_pullback_candidate(self):
        xs=fixture(True);r,_=run(mirror(xs),side="SHORT");q,_=run(mirror(follow(xs,"100.4")),r,side="SHORT")
        self.assertEqual(q["entry_state"],"ENTRY_CANDIDATE");self.assertEqual(q["confirmation_type"],"REACCELERATION")
    def test_hold_confirmation_boundary(self):
        xs=fixture();r,_=run(xs);xs=follow(xs,"99.8",volume_ratio_20=".9");r,_=run(xs,r)
        self.assertEqual(run(follow(xs,"100.1"),r)[0]["entry_state"],"ENTRY_CANDIDATE")
        self.assertEqual(run(follow(xs,"100.099999"),r)[0]["entry_state"],"WAIT")
    def test_rearm_two_bars(self):
        xs=fixture();r,_=run(xs);xs=follow(xs);r,_=run(xs,r);xs=follow(xs,"100.3");r,_=run(xs,r)
        self.assertIn("ENTRY.REARM_REQUIRED",r["reason_codes"])
        xs=follow(xs,"100.6",rolling_high_20="100.4",breakout_20=True);q,_=run(xs,r)
        self.assertEqual(q["state"]["setup"]["lifecycle"],"ARMED");self.assertNotEqual(q["state"]["setup"]["setup_id"],r["state"]["setup"]["setup_id"])
    def test_writer_lock(self):
        r,m=run(fixture())
        with TemporaryDirectory() as tmp:
            root=Path(tmp)/"output_entry/btc_anytime/v1";root.mkdir(parents=True);(root/".writer.lock").write_text("occupied")
            with self.assertRaises(FileExistsError):persist(Path(tmp),m,r)
            self.assertEqual((root/".writer.lock").read_text(),"occupied")
    def test_interrupted_publication_recovery(self):
        r,m=run(fixture())
        with TemporaryDirectory() as tmp:
            root=Path(tmp)/"output_entry/btc_anytime/v1/inputs";root.mkdir(parents=True)
            (root/(m["manifest_id"]+".json")).write_text(json.dumps(m,sort_keys=True,indent=2)+"\n",encoding="utf-8")
            self.assertEqual(persist(Path(tmp),m,r),"APPENDED")
    def test_1h_gap_conflict_unavailable(self):
        xs=fixture();a,b=xs[-2:];a["time"]-=3600000
        self.assertEqual(context_conflict(b,a,1,b["time"]+3600300,parameters()),"UNAVAILABLE")


if __name__=="__main__":unittest.main()

import ast
import copy
from pathlib import Path
import unittest
from decimal import Decimal as D, localcontext
from unittest.mock import patch

from upbit_b import feature_contracts as F
from upbit_b.contracts import DURATIONS
from upbit_b.trend_contracts import PARAMETERS, PARAMETER_SHA256
from upbit_b.trend_state import evaluate, feature_hash, groups, data_gate
from upbit_b.market_data import iso

CUTOFF = 1791158400000  # 2026-10-05 00:00 UTC, common source cutoff
NOW = CUTOFF+3000

def fixture(tf, a="1", momentum="1", high="HH", low="HL", q="1.5", provider="UPBIT"):
    duration = DURATIONS[tf]
    v = dict.fromkeys(F.FEATURES)
    r = dict.fromkeys(F.FEATURES,"WARMUP")
    values = {"atr_pct":"1", "ema20_to_ema50_pct":a,"close_to_ema50_pct":a,
              "ema20_slope3_pct":F.canonical(D(a)*D(".5")), "ema50_slope3_pct":F.canonical(D(a)*D(".25")),
              "high_structure":high,"low_structure":low,"breakout20":False,"breakdown20":False,
              "return3_pct":F.canonical(D(momentum)*2),"quote_recent3_vs_prior20":q,
              "close_to_ema20_atr":"1","price_change3_atr":"1","distance_to_confirmed_low_atr":"1"}
    pivot = {"price":"100","pivot_open_ms":CUTOFF-6*duration,
             "confirmation_boundary_ms":CUTOFF-3*duration,"observed_at_ms":CUTOFF+1000}
    values["confirmed_pivot_low"] = dict(pivot)
    values["confirmed_pivot_high"] = dict(pivot,pivot_open_ms=CUTOFF-5*duration,
                                          confirmation_boundary_ms=CUTOFF-2*duration,price="110")
    v.update(values);r.update(dict.fromkeys(values,"READY"))
    m = {"provider":provider,"instrument":"KRW-X" if provider=="UPBIT" else "XUSDT","timeframe":tf,
         "source_status":"AVAILABLE","source_cutoff_ms":CUTOFF,"input_sha256":"a"*64,
         "source_candle_open_ms":CUTOFF-duration,"source_candle_close_ms":CUTOFF,
         "source_received_at_ms":CUTOFF+1000,"feature_generated_at_ms":CUTOFF+2000,
         "evidence":[{"url":"https://fixture/public","response_sha256":"b"*64,"received_at_utc":iso(CUTOFF+1000)}],
         "swing_anchor":None,"last_breakout":None}
    s = {"features":v,"readiness":r,"metadata":m,"schema_version":F.SCHEMA_VERSION,
         "algorithm_version":F.ALGORITHM_VERSION,"parameter_version":F.PARAMETER_VERSION,"parameter_sha256":F.PARAMETER_SHA256}
    return seal(s)

def seal(s):
    s["measurement_sha256"] = feature_hash(s)
    return s

def change(s, **values):
    for k,v in values.items():
        s["features"][k] = v
        s["readiness"][k] = "READY" if v is not None else "MISSING_STRUCTURE"
    return seal(s)

def geometry(s):
    p = s["features"]
    s["metadata"]["swing_anchor"] = {"low":{k:v for k,v in p["confirmed_pivot_low"].items() if k!="observed_at_ms"},
        "high":{k:v for k,v in p["confirmed_pivot_high"].items() if k!="observed_at_ms"},"observed_at_ms":CUTOFF+1000}
    return change(s,swing_retracement_close=".3",peak_to_close_atr="1",bars_since_swing_high=4,post_peak_quote_ratio=".8")

def bundle(spot=None, mapping="VERIFIED"):
    return {"schema_version":F.SCHEMA_VERSION,"upbit":{tf:fixture(tf) for tf in DURATIONS},
            "binance_spot":{tf:fixture(tf,provider="BINANCE_SPOT") for tf in DURATIONS} if spot else {},
            "mapping":{"status":mapping if spot else "NO_SYMBOL","symbol":"XUSDT" if spot else None,
                       "identity_evidence":{"symbol":"XUSDT","registry_version":"fixture-1",
                                            "evidence_reference":"fixture-approved-identity"}}}

def run(b): return evaluate(b,"KRW-X",NOW)

class HypothesisTests(unittest.TestCase):
    def test_01_strong_alignment(self):
        o=run(bundle(True))
        self.assertEqual(o["trend"]["score"],"100")
        self.assertEqual(o["state"]["primary"],"TREND_CONTINUATION")
        self.assertEqual(o["binance"]["confirmation"],"CONFIRMED")
        self.assertEqual(o["data"]["category"],"HIGH")

    def test_02_primary_with_pullback(self):
        b=bundle();b["upbit"]["1h"]=geometry(fixture("1h",a="-.3",momentum="-.5",high="HH",low="LL",q=".8"))
        o=run(b)
        self.assertEqual(o["state"]["primary"],"PULLBACK_WATCH")
        self.assertIn("PARTICIPATION_CONTRACTION",o["state"]["secondary_tags"])
        self.assertGreater(D(o["trend"]["score"]),65)
        self.assertEqual(o["trend"]["group_scores"]["4h"],run(bundle())["trend"]["group_scores"]["4h"])

    def test_03_recovery(self):
        b=bundle();s=geometry(b["upbit"]["1h"])
        change(s,return1_pct="1",return3_acceleration_pp=".1",ema20_upward_recross=True,quote_ratio_median20="1.2")
        o=run(b)
        self.assertEqual(o["state"]["primary"],"REACCELERATION")
        self.assertEqual(o["state"]["recovery_basis"],"CURRENT_GEOMETRY_AND_RECOVERY_EVIDENCE")
        self.assertFalse(o["state"]["prior_state_transition_verified"])

    def test_04_extreme_extension_independent(self):
        b=bundle();before=run(b)
        for s in b["upbit"].values():change(s,close_to_ema20_atr="10",price_change3_atr="10")
        after=run(b)
        self.assertEqual(after["chase"]["category"],"HIGH")
        self.assertEqual(before["trend"],after["trend"])
        self.assertEqual(before["state"],after["state"])

    def test_05_macro_cannot_hide_damage(self):
        b=bundle();change(b["upbit"]["4h"],low_structure="LL",breakdown20=True)
        o=run(b)
        self.assertTrue(o["trend"]["primary_damage"])
        self.assertEqual(o["trend"]["score"],"49")
        self.assertGreater(D(o["trend"]["score_before_gate"]),49)
        self.assertFalse(o["trend"]["eligible"])
        self.assertEqual(o["state"]["primary"],"TREND_WEAKENING")

    def test_06_weak_participation(self):
        b=bundle()
        for s in b["upbit"].values():change(s,quote_recent3_vs_prior20="0")
        o=run(b)
        self.assertEqual(o["trend"]["score"],"80")
        self.assertEqual(o["state"]["primary"],"TREND_BUILDING")
        self.assertIn("PARTICIPATION_WEAK",o["state"]["secondary_tags"])

    def test_07_spot_conflict_independent(self):
        b=bundle(True);before=run(b)
        b["binance_spot"]["4h"]=fixture("4h",a="-1",momentum="-1",high="LH",low="LL",provider="BINANCE_SPOT")
        after=run(b)
        self.assertEqual(after["binance"]["confirmation"],"CONFLICTING")
        self.assertEqual(after["trend"],before["trend"])

    def test_08_spot_unavailable(self):
        self.assertEqual(run(bundle())["trend"],run(bundle(True))["trend"])
        self.assertEqual(run(bundle())["binance"]["confirmation"],"UNAVAILABLE")

    def test_09_sparse_hour(self):
        b=bundle();b["upbit"].pop("1h");o=run(b)
        self.assertEqual(o["trend"]["available_tf_weight"],"0.8")
        self.assertEqual(o["trend"]["score"],"100")
        self.assertEqual(o["data"]["category"],"MEDIUM")
        self.assertEqual(o["chase"]["category"],"UNAVAILABLE")
        self.assertIn("SHORT_CONTEXT_UNAVAILABLE",o["state"]["secondary_tags"])

    def test_10_sparse_primary(self):
        b=bundle();change(b["upbit"]["4h"],high_structure=None);o=run(b)
        self.assertIsNone(o["trend"]["score"])
        self.assertEqual(o["state"]["primary"],"INSUFFICIENT_EVIDENCE")
        self.assertEqual(o["data"]["category"],"LOW")

    def test_11_bearish_signed(self):
        b=bundle();b["upbit"]={tf:fixture(tf,a="-1",momentum="-1",high="LH",low="EQ") for tf in DURATIONS}
        o=run(b)
        self.assertLess(D(o["trend"]["signed_score"]),0)
        self.assertEqual(o["trend"]["score"],"0")
        self.assertEqual(o["state"]["primary"],"NO_UPTREND")
        self.assertEqual(o["data"]["category"],"HIGH")

    def test_12_short_spike_not_primary(self):
        b=bundle();b["upbit"]={tf:fixture(tf,a="0",momentum="0",high="EQ",low="EQ") for tf in DURATIONS}
        b["upbit"]["1h"]=fixture("1h")
        change(b["upbit"]["1h"],close_to_ema20_atr="4",price_change3_atr="4")
        o=run(b)
        self.assertEqual(o["state"]["primary"],"NO_UPTREND")
        self.assertIn("SHORT_TERM_SPIKE",o["state"]["secondary_tags"])
        self.assertEqual(o["chase"]["category"],"HIGH")

class FormulaTests(unittest.TestCase):
    def test_alignment_hand(self):
        v={"atr_pct":D(2),"ema20_to_ema50_pct":D(1),"close_to_ema50_pct":D(-1),
           "ema20_slope3_pct":D(2),"ema50_slope3_pct":D(-2)}
        self.assertEqual(groups(v)["A"],D(".175"))

    def test_momentum_hand_cap(self):
        self.assertEqual(groups({"atr_pct":D(2),"return3_pct":D(1)})["M"],D(".25"))
        self.assertEqual(groups({"atr_pct":D(1),"return3_pct":D(-20)})["M"],-1)

    def test_zero_atr_unavailable(self):
        b=bundle();change(b["upbit"]["4h"],atr_pct="0")
        self.assertIsNone(run(b)["trend"]["score"])

    def test_structure_floor_not_bonus(self):
        b=bundle();change(b["upbit"]["4h"],breakout20=True)
        self.assertEqual(run(b)["trend"]["group_scores"]["4h"]["S"],"1")

    def test_structure_neutral_not_null(self):
        b=bundle();change(b["upbit"]["4h"],high_structure="EQ",low_structure="EQ")
        self.assertEqual(run(b)["trend"]["group_scores"]["4h"]["S"],"0")
        change(b["upbit"]["4h"],high_structure=None)
        self.assertIsNone(run(b)["trend"]["group_scores"]["4h"]["S"])

    def test_participation_down_drying_no_bonus(self):
        self.assertEqual(groups({"return3_pct":D(-1),"quote_recent3_vs_prior20":D(".5")})["P"],0)

    def test_participation_down_expansion(self):
        self.assertEqual(groups({"return3_pct":D(-1),"quote_recent3_vs_prior20":D("1.25")})["P"],D("-.5"))

    def test_participation_flat(self):
        self.assertEqual(groups({"return3_pct":D(0),"quote_recent3_vs_prior20":D(2)})["P"],0)

    def test_unavailable_p_denominator(self):
        b=bundle()
        for s in b["upbit"].values():change(s,quote_recent3_vs_prior20=None)
        o=run(b)
        self.assertEqual(o["trend"]["score"],"100")
        self.assertEqual(o["data"]["coverage"],"0.9")
        self.assertFalse(o["trend"]["available_masks"]["4h"]["P"])

    def test_coverage_weight_not_feature_count(self):
        b=bundle();o=run(b)
        self.assertEqual(o["data"]["coverage"],"1")
        self.assertLess(o["data"]["tf_readiness"]["4h"]["ready_feature_count"],41)

    def test_weight_085(self):
        b=bundle();b["upbit"].pop("1d");o=run(b)
        self.assertEqual(o["trend"]["available_tf_weight"],"0.85")
        self.assertIsNotNone(o["trend"]["score"])

    def test_weight_065_fail(self):
        b=bundle();b["upbit"]={"4h":b["upbit"]["4h"]}
        self.assertIsNone(run(b)["trend"]["score"])

    def test_no_primary(self):
        b=bundle();b["upbit"].pop("4h")
        self.assertIsNone(run(b)["trend"]["score"])

    def test_coverage_exact_gate(self):
        self.assertTrue(data_gate(True,D(".80"),D(".65")))
        self.assertFalse(data_gate(True,D(".80"),D(".649999999999999999")))
        self.assertFalse(data_gate(True,D(".799999999"),D(1)))
        self.assertFalse(data_gate(False,D(1),D(1)))

    def test_damage_second_rule(self):
        b=bundle();b["upbit"]["4h"]=fixture("4h",a="-.1",high="LH",low="LL")
        self.assertTrue(run(b)["trend"]["primary_damage"])

    def test_damage_broken_low_rule(self):
        b=bundle();change(b["upbit"]["4h"],breakdown20=True,distance_to_confirmed_low_atr="-.1")
        self.assertTrue(run(b)["trend"]["primary_damage"])

    def test_low_chase_not_eligible(self):
        b=bundle();b["upbit"]={tf:fixture(tf,a="0",momentum="0",high="EQ",low="EQ") for tf in DURATIONS}
        o=run(b)
        self.assertEqual(o["chase"]["category"],"LOW")
        self.assertFalse(o["trend"]["eligible"])

    def test_1h_correction_without_anchor(self):
        b=bundle();b["upbit"]["1h"]=fixture("1h",a="-.1",momentum="-.1")
        o=run(b)
        self.assertNotEqual(o["state"]["primary"],"PULLBACK_WATCH")
        self.assertIn("CORRECTION_CONTEXT_UNAVAILABLE",o["state"]["secondary_tags"])

    def test_recovery_without_evidence(self):
        b=bundle();geometry(b["upbit"]["1h"])
        self.assertNotEqual(run(b)["state"]["primary"],"REACCELERATION")

    def test_pullback_exact_bounds(self):
        b=bundle();s=geometry(fixture("1h",momentum="-.1"));b["upbit"]["1h"]=s
        for retrace,peak,age in ((".1",".5",3),(".6","3",20)):
            change(s,swing_retracement_close=retrace,peak_to_close_atr=peak,bars_since_swing_high=age)
            self.assertEqual(run(b)["state"]["primary"],"PULLBACK_WATCH")
        change(s,swing_retracement_close=".60001")
        self.assertNotEqual(run(b)["state"]["primary"],"PULLBACK_WATCH")

    def test_recovery_priority_over_pullback(self):
        b=bundle();s=geometry(fixture("1h",momentum="-.1"));b["upbit"]["1h"]=s
        change(s,return1_pct="1",return3_acceleration_pp="1",breakout20=True,quote_ratio_median20="1.2")
        o=run(b)
        self.assertTrue(o["state"]["predicate_results"]["PULLBACK_WATCH"])
        self.assertEqual(o["state"]["primary"],"REACCELERATION")

    def test_damage_priority(self):
        b=bundle();s=geometry(b["upbit"]["1h"])
        change(s,return1_pct="1",return3_acceleration_pp="1",breakout20=True,quote_ratio_median20="2")
        change(b["upbit"]["4h"],low_structure="LL",breakdown20=True)
        self.assertEqual(run(b)["state"]["primary"],"TREND_WEAKENING")

    def test_weakening_background(self):
        b=bundle();b["upbit"]["4h"]=fixture("4h",a="-.1",momentum="-.1",high="EQ",low="EQ")
        self.assertEqual(run(b)["state"]["primary"],"TREND_WEAKENING")

    def test_chase_exact_boundaries(self):
        b=bundle()
        for score,expected in (("34.999","LOW"),("35","MEDIUM"),("69.999","MEDIUM"),("70","HIGH")):
            for tf in ("1h","4h"):change(b["upbit"][tf],close_to_ema20_atr=F.canonical(D(score)/25),price_change3_atr="0")
            self.assertEqual(run(b)["chase"]["category"],expected)

    def test_chase_downward_clamps_zero(self):
        b=bundle()
        for tf in ("1h","4h"):change(b["upbit"][tf],close_to_ema20_atr="-10",price_change3_atr="-10")
        self.assertEqual(run(b)["chase"]["score"],"0")

    def test_chase_breakout_age(self):
        b=bundle();s=b["upbit"]["1h"];d=DURATIONS["1h"]
        for age,score in ((20,"85"),(21,"25")):
            start=CUTOFF-d-age*d
            s["metadata"]["last_breakout"]={"candle_open_ms":start,"confirmation_boundary_ms":start+d,"observed_at_ms":CUTOFF+1000,"level":"100"}
            change(s,distance_to_last_breakout_atr="4")
            self.assertEqual(run(b)["chase"]["score"],score)

    def test_chase_partial_coverage(self):
        b=bundle();change(b["upbit"]["4h"],price_change3_atr=None)
        o=run(b)
        self.assertEqual(o["chase"]["score"],"25")
        self.assertEqual(o["chase"]["coverage"],"0.8")

    def test_binance_unverified_no_promotion(self):
        o=run(bundle(True,"UNVERIFIED"))
        self.assertEqual(o["binance"]["confirmation"],"UNVERIFIED")
        self.assertEqual(o["binance"]["directional_diagnostic"],"CONFIRMED")

    def test_verified_registry_evidence_required(self):
        b=bundle(True);b["mapping"]["identity_evidence"].pop("evidence_reference")
        self.assertEqual(run(b)["binance"]["confirmation"],"UNAVAILABLE")

    def test_binance_supportive(self):
        b=bundle(True);b["binance_spot"]["4h"]=fixture("4h",a=".4",momentum="0",high="EQ",low="EQ",provider="BINANCE_SPOT")
        self.assertEqual(run(b)["binance"]["confirmation"],"SUPPORTIVE")

    def test_binance_neutral(self):
        b=bundle(True);b["binance_spot"]["4h"]=fixture("4h",a="0",momentum="0",high="EQ",low="EQ",provider="BINANCE_SPOT")
        self.assertEqual(run(b)["binance"]["confirmation"],"NEUTRAL")

    def test_reason_matches_masks(self):
        b=bundle();change(b["upbit"]["4h"],high_structure=None)
        o=run(b)
        self.assertIn("PRIMARY_CORE_UNAVAILABLE",o["state"]["reason_codes"])
        self.assertFalse(o["trend"]["available_masks"]["4h"]["S"])

    def test_building_score_exact_boundary(self):
        b=bundle();b["upbit"]={tf:fixture(tf,a=".5",momentum=".5",high="HH",low="EQ",q="1") for tf in DURATIONS}
        o=run(b)
        self.assertEqual(o["trend"]["score"],"45")
        self.assertEqual(o["state"]["primary"],"TREND_BUILDING")
        b["upbit"]={tf:fixture(tf,a=".49999",momentum=".5",high="HH",low="EQ",q="1") for tf in DURATIONS}
        self.assertEqual(run(b)["state"]["primary"],"MIXED")

    def test_continuation_score_exact_boundary(self):
        b=bundle();b["upbit"]={tf:fixture(tf,a=".5625",high="HH",low="EQ") for tf in DURATIONS}
        o=run(b)
        self.assertEqual(o["trend"]["score"],"65")
        self.assertEqual(o["state"]["primary"],"TREND_CONTINUATION")
        b["upbit"]={tf:fixture(tf,a=".56249",high="HH",low="EQ") for tf in DURATIONS}
        self.assertEqual(run(b)["state"]["primary"],"TREND_BUILDING")

    def test_damage_null_condition_not_true(self):
        b=bundle();change(b["upbit"]["4h"],breakdown20=True,low_structure="EQ",distance_to_confirmed_low_atr=None)
        self.assertFalse(run(b)["trend"]["primary_damage"])

    def test_unverified_conflict_still_unverified(self):
        b=bundle(True,"UNVERIFIED")
        b["binance_spot"]["4h"]=fixture("4h",a="-1",momentum="-1",high="LH",low="LL",provider="BINANCE_SPOT")
        o=run(b)
        self.assertEqual(o["binance"]["confirmation"],"UNVERIFIED")
        self.assertEqual(o["binance"]["directional_diagnostic"],"CONFLICTING")

    def test_no_hour_confirmation_not_confirmed(self):
        b=bundle(True);b["binance_spot"].pop("1h")
        self.assertEqual(run(b)["binance"]["confirmation"],"SUPPORTIVE")

    def test_invalid_primary_unavailable_not_bearish(self):
        b=bundle();s=b["upbit"]["4h"]
        s["metadata"]["source_status"]="API_ERROR"
        o=run(b)
        self.assertIsNone(o["trend"]["score"])
        self.assertFalse(o["trend"]["primary_damage"])
        self.assertEqual(o["state"]["primary"],"INSUFFICIENT_EVIDENCE")

class ContractTests(unittest.TestCase):
    def test_parameter_hash_real_object(self):
        self.assertEqual(PARAMETER_SHA256,F.digest(PARAMETERS))
        self.assertEqual(F.PARAMETER_SHA256,"7dc9a941df84e364f66412ce326ef8d917335d71c3c28edf2c6dd2e033001227")

    def test_replay_hash(self):
        b=bundle();a=run(b)
        self.assertEqual(a,run(b))
        self.assertEqual(a["observation_id"],F.digest({k:v for k,v in a.items() if k!="observation_id"}))

    def test_decimal_context_independence(self):
        b=bundle();b["upbit"]["4h"]=fixture("4h",a=".333333333333333333",momentum=".3")
        with localcontext() as c:c.prec=6;a=run(b)
        with localcontext() as c:c.prec=50;other=run(b)
        self.assertEqual(a,other)

    def test_feature_hash_tamper(self):
        b=bundle();b["upbit"]["4h"]["features"]["return3_pct"]="999"
        self.assertIsNone(run(b)["trend"]["score"])

    def test_feature_version_mismatch(self):
        for key in ("schema_version","algorithm_version","parameter_version","parameter_sha256"):
            b=bundle();b["upbit"]["1d"][key]="other"
            self.assertIsNone(run(b)["trend"]["score"])

    def test_stale_optional_excluded(self):
        b=bundle();b["upbit"]["1h"]["metadata"]["source_candle_close_ms"]-=3600000
        o=run(b)
        self.assertEqual(o["trend"]["available_tf_weight"],"0.8")
        self.assertIsNotNone(o["trend"]["score"])
        self.assertEqual(o["chase"]["category"],"UNAVAILABLE")

    def test_future_generation_excluded(self):
        b=bundle();b["upbit"]["4h"]["metadata"]["feature_generated_at_ms"]=NOW+1
        self.assertIsNone(run(b)["trend"]["score"])

    def test_missing_source_evidence(self):
        b=bundle();b["upbit"]["4h"]["metadata"]["evidence"]=[]
        self.assertIsNone(run(b)["trend"]["score"])

    def test_provider_mismatch(self):
        b=bundle();b["upbit"]["4h"]["metadata"]["provider"]="BINANCE_SPOT"
        self.assertIsNone(run(b)["trend"]["score"])

    def test_spot_wrong_identity_independent(self):
        b=bundle(True);before=run(b)
        b["binance_spot"]["4h"]["metadata"]["instrument"]="WRONGUSDT"
        after=run(b)
        self.assertEqual(after["trend"],before["trend"])
        self.assertEqual(after["binance"]["confirmation"],"UNAVAILABLE")

    def test_future_pivot_rejected(self):
        b=bundle();p=b["upbit"]["4h"]["features"]["confirmed_pivot_low"]
        p["confirmation_boundary_ms"]=CUTOFF+1;seal(b["upbit"]["4h"])
        self.assertIsNone(run(b)["trend"]["score"])

    def test_swing_without_actual_anchor_unavailable(self):
        b=bundle();s=geometry(fixture("1h",momentum="-.1"));b["upbit"]["1h"]=s
        s["metadata"]["swing_anchor"]=None
        self.assertNotEqual(run(b)["state"]["primary"],"PULLBACK_WATCH")

    def test_negative_quote_rejected(self):
        b=bundle();change(b["upbit"]["4h"],quote_recent3_vs_prior20="-1")
        self.assertIsNone(run(b)["trend"]["score"])

    def test_contradictory_breakout_rejected(self):
        b=bundle();change(b["upbit"]["4h"],breakout20=True,breakdown20=True)
        self.assertIsNone(run(b)["trend"]["score"])

    def test_ready_null_rejected(self):
        b=bundle();s=b["upbit"]["4h"];s["features"]["return3_pct"]=None;seal(s)
        self.assertIsNone(run(b)["trend"]["score"])

    def test_float_rejected(self):
        b=bundle();b["upbit"]["4h"]["features"]["return3_pct"]=1.0
        self.assertIsNone(run(b)["trend"]["score"])

    def test_fixed_cutoff_required(self):
        b=bundle();b["upbit"]["1h"]["metadata"]["source_cutoff_ms"]+=1
        with self.assertRaises(ValueError):run(b)

    def test_bool_clock_rejected(self):
        with self.assertRaises(ValueError):evaluate(bundle(),"KRW-X",True)

    def test_price_actual_only(self):
        self.assertIsNone(run(bundle())["price_observation"])
        price={"instrument":"KRW-X","provider":"UPBIT","price":"123.4","source_time_ms":CUTOFF,
               "received_at_ms":CUTOFF+1000,"source_reference":{"url":"fixture","response_sha256":"a"*64}}
        o=evaluate(bundle(),"KRW-X",NOW,price)
        self.assertEqual(o["price_observation"],price)
        price["received_at_ms"]=NOW+1
        with self.assertRaises(ValueError):evaluate(bundle(),"KRW-X",NOW,price)

    def test_actual_ticker_clock_skew_preserved(self):
        price={"instrument":"KRW-X","provider":"UPBIT","price":"123.4","source_time_ms":NOW+2000,
               "received_at_ms":CUTOFF+1000,"source_reference":{"url":"fixture","response_sha256":"a"*64}}
        o=evaluate(bundle(),"KRW-X",NOW,price)
        self.assertEqual(o["price_observation"],price)
        self.assertIn("TICKER_SOURCE_CLOCK_AFTER_LOCAL_RECEIPT",o["source"]["price_clock_warnings"])
        self.assertEqual(o["trend"],run(bundle())["trend"])

    def test_input_immutability(self):
        b=bundle(True);before=copy.deepcopy(b)
        run(b);self.assertEqual(b,before)

    def test_no_io(self):
        with patch("builtins.open",side_effect=AssertionError("IO forbidden")):
            run(bundle())

    def test_no_protected_imports(self):
        text=(Path(__file__).resolve().parents[2]/"upbit_b/trend_state.py").read_text(encoding="utf-8")
        tree=ast.parse(text)
        modules={n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)}
        self.assertEqual(modules,{"datetime","decimal","contracts",None,"trend_contracts"})
        self.assertNotIn("open(",text)

    def test_sparse_old_gap_not_penalty(self):
        b=bundle();before=run(b)
        for s in b["upbit"].values():s["metadata"].update(source_status="INCOMPLETE_COVERAGE",gap_reset=True)
        self.assertEqual(run(b)["trend"],before["trend"])

    def test_future_suffix_independent(self):
        b=bundle();old=run(b);future=copy.deepcopy(b)
        for s in future["upbit"].values():change(s,return3_pct="-999")
        self.assertEqual(run(b),old)

    def test_late_backfill_not_available_in_past(self):
        b=bundle();s=b["upbit"]["4h"]
        s["metadata"]["feature_generated_at_ms"]=NOW+10000
        self.assertIsNone(run(b)["trend"]["score"])

    def test_feature_actual_integration(self):
        from test_features import rows, window
        from upbit_b.features import bundle as feature_bundle
        windows={}
        for tf in DURATIONS:
            data=rows(100,tf=tf)
            # Deliberate flat series has no structure: must not manufacture trend.
            cutoff=max(c.close_ms for c in data)
            windows[tf]=window(data,cutoff=CUTOFF+100*DURATIONS["1d"])
        latest=max(w.cutoff_ms for w in windows.values())
        b=feature_bundle(windows,generated_at_ms=latest+3000)
        self.assertIsNone(evaluate(b,"KRW-X",latest+4000)["trend"]["score"])

    def test_actual_mapping_contract(self):
        from upbit_b.market_data import mapping
        info={"symbols":[{"baseAsset":"X","quoteAsset":"USDT","symbol":"XUSDT","status":"TRADING","isSpotTradingAllowed":True}]}
        b=bundle(True)
        b["mapping"]=mapping("KRW-X",info,{"KRW-X":{"symbol":"XUSDT","registry_version":"test","evidence_reference":"actual"}})
        self.assertEqual(run(b)["binance"]["confirmation"],"CONFIRMED")

    def test_actual_stale_optional_snapshot(self):
        from test_features import rows, window
        from upbit_b.features import snapshot
        data=rows(100)
        b=bundle()
        b["upbit"]["1h"]=snapshot(window(data,cutoff=CUTOFF),CUTOFF+2000)
        o=run(b)
        self.assertEqual(o["trend"]["available_tf_weight"],"0.8")
        self.assertIsNotNone(o["trend"]["score"])

if __name__ == "__main__": unittest.main()

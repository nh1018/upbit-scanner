"""Direction arithmetic, gates, causal evidence and immutable decisions."""
from copy import deepcopy
from decimal import Decimal,localcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from btc_anytime.features.engine import digest,encode
from btc_anytime.features.build import build_dataset,protected_hashes
from btc_anytime.features.availability import mark_generated
from btc_anytime.features.snapshot import synchronize
from btc_anytime.integrity import DURATIONS,iso
from btc_anytime.direction.engine import evaluate,load_parameters,components,confidence
from btc_anytime.direction.history import append_decision
from test_features import rows,START
D=Decimal
T=START+100*86400000


def rehash(s):
    s.pop("snapshot_id",None);s["snapshot_id"]=digest(s);return s


def fixture():
    data={}
    for tf,duration in DURATIONS.items():
        rs=rows(65,tf)
        for i,r in enumerate(rs):r["time"]=T-(65-i)*duration;r["candle_time_utc"]=iso(r["time"])
        data[tf]=rs
    _,series=build_dataset(data,T)
    series=mark_generated(series,T,"actual_fixture_generation")
    s=synchronize(series,T)
    for tf in DURATIONS:set_tf(s,tf)
    return rehash(s)


def feature(s,tf,k,v):
    r=s["timeframes"][tf]["record"]
    r["features"][k]=str(v) if isinstance(v,(int,D)) and not isinstance(v,bool) else v
    r["feature_quality"][k]={"ready":v is not None,"available_at_ms":T,"null_reason":None if v is not None else "fixture_unavailable"}


def set_tf(s,tf,trend=1,structure=1,momentum=1,oi=None):
    with localcontext() as ctx:
        ctx.prec=50
        trend=D(str(trend));momentum=D(str(momentum))
        for k,v in {"ema_20":100,"ema_50":100-trend,"atr_14":1,
          "ema_20_slope_3_pct_per_bar":trend*D("0.10"),
          "ema_50_slope_3_pct_per_bar":trend*10/(100-trend),
          "return_6":2*momentum,"atr_pct":1,"volume_ratio_20":1}.items():feature(s,tf,k,v)
    hi,lo=("HH","HL") if structure==1 else ("LH","LL") if structure==-1 else ("EQ","EQ") if structure==0 else (None,None)
    feature(s,tf,"structure_high_state",hi);feature(s,tf,"structure_low_state",lo)
    feature(s,tf,"breakout_20",structure==1);feature(s,tf,"breakdown_20",structure==-1)
    for k in ("oi_change","oi_change_pct","price_oi_state"):feature(s,tf,k,None)
    if oi:
        ps,os=oi
        change={"UP":1,"DOWN":-1,"FLAT":0}[os]
        feature(s,tf,"oi_change",change);feature(s,tf,"oi_change_pct",change);feature(s,tf,"price_oi_state",f"PRICE_{ps}_OI_{os}")
        s["timeframes"][tf]["record"]["oi_metadata"].update(signature={"provider":"fixture","instrument":"BTCUSDT_PERPETUAL","timeframe":tf,"basis":"validated_fixture","unit":"BTC"},segment_id="fixture",unit_status="validated",null_reason=None)
    rehash(s)


class DirectionTests(unittest.TestCase):
    def test_manual_components_and_score(self):
        r=evaluate(fixture());self.assertEqual(D(r["direction_score"]),1)
        self.assertEqual(r["direction_class"],"STRONG_LONG")
        for tf in DURATIONS:
            self.assertEqual(D(r["timeframes"][tf]["components"]["trend"]),1)
            self.assertEqual(D(r["timeframes"][tf]["score"]),1)
    def test_full_sign_symmetry(self):
        for strength in ("1","0.6","0.1"):
            a=fixture();b=fixture()
            for tf in DURATIONS:
                set_tf(a,tf,trend=strength,structure=1,momentum=strength,oi=("UP","UP"))
                set_tf(b,tf,trend=-D(strength),structure=-1,momentum=-D(strength),oi=("DOWN","UP"))
            x=evaluate(a);y=evaluate(b)
            self.assertEqual(D(x["direction_score"]),-D(y["direction_score"]))
            self.assertEqual(x["direction_class"].replace("LONG","SHORT"),y["direction_class"])
            self.assertEqual(x["confidence"],y["confidence"]);self.assertEqual(x["regime"],y["regime"])
    def test_exact_direction_boundary_and_epsilon_both_signs(self):
        # Isolate classification with exact Decimal component scores.
        from btc_anytime.direction.engine import components as original
        for boundary in (D("0.35"),D("-0.35")):
            s=fixture()
            def exact(sel,tf,t,p):
                r=original(sel,tf,t,p);r["score"]=boundary;return r
            with patch("btc_anytime.direction.engine.components",side_effect=exact):self.assertEqual(evaluate(s)["direction_class"],"NEUTRAL")
            def outside(sel,tf,t,p):
                r=exact(sel,tf,t,p);r["score"]+=D("0.000001")*(1 if boundary>0 else -1);return r
            with patch("btc_anytime.direction.engine.components",side_effect=outside):
                self.assertEqual(evaluate(s)["direction_class"],"LONG" if boundary>0 else "SHORT")
    def test_exact_strong_boundary(self):
        from btc_anytime.direction.engine import components as original
        for direction in (1,-1):
            s=fixture()
            for tf in DURATIONS:set_tf(s,tf,direction,direction,direction)
            def exact(sel,tf,t,p):r=original(sel,tf,t,p);r["score"]=D("0.65")*direction;return r
            with patch("btc_anytime.direction.engine.components",side_effect=exact):
                self.assertEqual(evaluate(s)["direction_class"],"STRONG_LONG" if direction>0 else "STRONG_SHORT")
    def test_pullback_keeps_long_or_short(self):
        for direction in (1,-1):
            s=fixture()
            for tf in DURATIONS:set_tf(s,tf,direction,direction,direction)
            set_tf(s,"1h",-D("0.3")*direction,-direction,-D("0.3")*direction)
            r=evaluate(s)
            self.assertEqual(r["direction_class"],"LONG" if direction>0 else "SHORT")
            self.assertEqual(r["regime"],"PULLBACK_CANDIDATE")
            self.assertIn("CONFLICT.H4_H1_OPPOSED",r["reason_codes"])
    def test_h1_reversal_evidence_alone_not_reversal(self):
        s=fixture();set_tf(s,"1h",-1,-1,-1)
        self.assertNotEqual(evaluate(s)["regime"],"POSSIBLE_REVERSAL")
    def test_reversal_needs_h4_weakening(self):
        for direction in (1,-1):
            s=fixture();set_tf(s,"4h",D("0.4")*direction,direction,direction)
            set_tf(s,"1h",-direction,-direction,-direction)
            r=evaluate(s);self.assertEqual(r["regime"],"POSSIBLE_REVERSAL")
            self.assertIn("REGIME.H4_TREND_WEAKENED",r["reason_codes"])
    def test_reversal_requires_opposite_range_event(self):
        s=fixture();set_tf(s,"4h",D("0.4"),1,1);set_tf(s,"1h",-1,-1,-1)
        feature(s,"1h","breakdown_20",False);rehash(s)
        self.assertNotEqual(evaluate(s)["regime"],"POSSIBLE_REVERSAL")
    def test_reversal_requires_opposite_pivot(self):
        s=fixture();set_tf(s,"4h",D("0.4"),1,1);set_tf(s,"1h",-1,0,-1)
        feature(s,"1h","breakdown_20",True);rehash(s)
        self.assertNotEqual(evaluate(s)["regime"],"POSSIBLE_REVERSAL")
    def test_m15_changes_only_context(self):
        s=fixture();a=evaluate(s);set_tf(s,"15m",-1,-1,-1,oi=("DOWN","UP"));b=evaluate(s)
        for k in ("direction_class","direction_score","confidence","regime"):self.assertEqual(a[k],b[k])
        self.assertIn("CONFLICT.M15_SHORT_TERM_OPPOSED",b["reason_codes"])
    def test_missing_oi_has_no_agreement_penalty(self):
        s=fixture();a=evaluate(s)
        for tf in DURATIONS:set_tf(s,tf,oi=("UP","UP"))
        b=evaluate(s)
        self.assertEqual(a["confidence_components"]["component_agreement"],b["confidence_components"]["component_agreement"])
        self.assertEqual(D(b["confidence"])-D(a["confidence"]),D("0.35")*D("0.05"))
        self.assertEqual(a["direction_score"],b["direction_score"])
    def test_oi_agreement_conflict_optional(self):
        s=fixture();set_tf(s,"1h",oi=("DOWN","UP"));r=evaluate(s)
        self.assertTrue(r["oi_agreement_evidence"]["1h"]["conflict"])
        self.assertLess(D(r["confidence_components"]["component_agreement"]),1)
        self.assertEqual(D(r["direction_score"]),1)
    def test_declining_oi_not_direction_vote(self):
        s=fixture();set_tf(s,"4h",oi=("UP","DOWN"));r=evaluate(s)
        self.assertEqual(D(r["direction_score"]),1)
        self.assertEqual(D(r["oi_agreement_evidence"]["4h"]["agreement"]),D("0.5"))
    def test_structure_null_vs_neutral(self):
        s=fixture();set_tf(s,"1h",1,0,1);neutral=evaluate(s)
        set_tf(s,"1h",1,None,1);missing=evaluate(s)
        self.assertEqual(neutral["timeframes"]["1h"]["score"],missing["timeframes"]["1h"]["score"])
        self.assertEqual(D(neutral["timeframes"]["1h"]["components"]["structure"]),0)
        self.assertIsNone(missing["timeframes"]["1h"]["components"]["structure"])
        self.assertGreater(D(neutral["confidence_components"]["coverage"]),D(missing["confidence_components"]["coverage"]))
    def test_missing_daily_fixed_weights_no_strong(self):
        s=fixture();s["timeframes"]["1d"]={"record":None};rehash(s);r=evaluate(s)
        self.assertEqual(D(r["direction_score"]),D("0.9"));self.assertEqual(r["direction_class"],"LONG")
        self.assertFalse(r["strong_gate"]["macro"])
    def test_confidence_not_score_magnitude(self):
        r=evaluate(fixture());self.assertNotEqual(D(r["confidence"]),abs(D(r["direction_score"])))
    def test_deterministic_and_snapshot_immutability(self):
        s=fixture();before=deepcopy(s);a=evaluate(s);b=evaluate(s)
        self.assertEqual(a,b);self.assertEqual(s,before)
    def test_score_explanation_exact_contributions(self):
        r=evaluate(fixture());self.assertEqual(sum(D(x) for x in r["tf_score_contributions"].values()),D(r["direction_score"]))
        for tf in DURATIONS:self.assertEqual(sum(D(x) for x in r["timeframes"][tf]["score_contributions"].values()),D(r["timeframes"][tf]["score"]))
    def test_missing_primary_neutral_insufficient(self):
        s=fixture();s["timeframes"]["4h"]={"record":None};rehash(s);r=evaluate(s)
        self.assertEqual(r["direction_class"],"NEUTRAL");self.assertEqual(r["regime"],"INSUFFICIENT_EVIDENCE")
        self.assertIsNone(r["direction_score"]);self.assertLessEqual(D(r["confidence"]),D("0.25"))
    def test_stale_tf_excluded(self):
        s=fixture();s["decision_time_utc"]=iso(T+2*DURATIONS["4h"]+960001);rehash(s)
        r=evaluate(s);self.assertEqual(r["regime"],"INSUFFICIENT_EVIDENCE")
        self.assertIn("4h:INPUT.STALE_TF",r["reason_codes"])
    def test_future_availability_fails(self):
        s=fixture();s["timeframes"]["4h"]["record"]["available_at_ms"]=T+1;rehash(s)
        with self.assertRaisesRegex(ValueError,"future raw"):evaluate(s)
    def test_future_generation_fails(self):
        s=fixture();s["timeframes"]["4h"]["record"]["feature_generated_at_ms"]=T+1;rehash(s)
        with self.assertRaises(ValueError):evaluate(s)
    def test_missing_generation_fails(self):
        s=fixture();s["timeframes"]["4h"]["record"].pop("feature_generated_at_ms");rehash(s)
        with self.assertRaises(ValueError):evaluate(s)
    def test_future_feature_dependency_fails(self):
        s=fixture();s["timeframes"]["4h"]["record"]["feature_quality"]["return_6"]["available_at_ms"]=T+1;rehash(s)
        with self.assertRaises(ValueError):evaluate(s)
    def test_snapshot_tamper_fails(self):
        s=fixture();s["decision_time_utc"]=iso(T+1)
        with self.assertRaises(ValueError):evaluate(s)
    def test_parameter_tamper_fails(self):
        p=load_parameters();p["tf_weights"]["15m"]="1"
        with self.assertRaises(ValueError):evaluate(fixture(),p)
    def test_rehashed_invalid_weight_set_fails(self):
        p=load_parameters();p["tf_weights"]["4h"]="0.99";p["parameter_hash"]=digest({k:v for k,v in p.items() if k!="parameter_hash"})
        with self.assertRaises(ValueError):evaluate(fixture(),p)
    def test_stale_optional_oi_not_reused(self):
        s=fixture();set_tf(s,"4h",oi=("UP","UP"))
        feature(s,"4h","oi_change_pct",None);rehash(s);r=evaluate(s)
        self.assertNotIn("4h",r["oi_agreement_evidence"])
        self.assertEqual(D(r["direction_score"]),1)
    def test_missing_generation_reference_fails(self):
        s=fixture();s["timeframes"]["1h"]["record"].pop("generation_evidence_ref");rehash(s)
        with self.assertRaises(ValueError):evaluate(s)
    def test_neutral_structure_weakening_can_confirm_reversal(self):
        s=fixture();set_tf(s,"4h",1,0,1);set_tf(s,"1h",-1,-1,-1)
        r=evaluate(s);self.assertEqual(r["regime"],"POSSIBLE_REVERSAL")
        self.assertIn("REGIME.H4_STRUCTURE_WEAKENED",r["reason_codes"])
    def test_momentum_weakening_can_confirm_reversal(self):
        s=fixture();set_tf(s,"4h",1,1,-1);set_tf(s,"1h",-1,-1,-1)
        r=evaluate(s);self.assertEqual(r["regime"],"POSSIBLE_REVERSAL")
        self.assertIn("REGIME.H4_MOMENTUM_OPPOSED",r["reason_codes"])
    def test_atr_zero_is_insufficient(self):
        s=fixture();feature(s,"4h","atr_14",0);rehash(s)
        self.assertEqual(evaluate(s)["regime"],"INSUFFICIENT_EVIDENCE")
    def test_chop_vs_structure_unavailable(self):
        s=fixture()
        for tf in DURATIONS:set_tf(s,tf,0,0,0)
        self.assertEqual(evaluate(s)["regime"],"CHOP_CANDIDATE")
        set_tf(s,"4h",0,None,0);self.assertNotEqual(evaluate(s)["regime"],"CHOP_CANDIDATE")
    def test_invalid_range_flags_fail(self):
        s=fixture();feature(s,"4h","breakdown_20",True);rehash(s)
        with self.assertRaises(ValueError):evaluate(s)
    def test_oi_state_change_conflict_fails(self):
        s=fixture();set_tf(s,"4h",oi=("UP","UP"));feature(s,"4h","oi_change",-1);rehash(s)
        with self.assertRaises(ValueError):evaluate(s)
    def test_oi_timeframe_scope_mismatch_fails(self):
        s=fixture();set_tf(s,"4h",oi=("UP","UP"))
        s["timeframes"]["4h"]["record"]["oi_metadata"]["signature"]["timeframe"]="1h";rehash(s)
        with self.assertRaises(ValueError):evaluate(s)
    def test_oi_change_percent_sign_conflict_fails(self):
        s=fixture();set_tf(s,"4h",oi=("UP","UP"));feature(s,"4h","oi_change_pct",-1);rehash(s)
        with self.assertRaises(ValueError):evaluate(s)
    def test_invalid_price_does_not_vote(self):
        s=fixture();s["timeframes"]["4h"]["record"]["input_validity"]["price"]=False;rehash(s)
        self.assertEqual(evaluate(s)["regime"],"INSUFFICIENT_EVIDENCE")
    def test_append_only_decisions_and_raw_immutability(self):
        with tempfile.TemporaryDirectory() as folder:
            repo=Path(folder);raw=repo/"data_market/raw";raw.parent.mkdir();raw.write_bytes(b"immutable raw")
            before=protected_hashes(repo);decision=evaluate(fixture());p=append_decision(repo,decision);body=p.read_bytes()
            self.assertEqual(append_decision(repo,decision),p);self.assertEqual(p.read_bytes(),body)
            self.assertEqual(protected_hashes(repo),before)
            bad=deepcopy(decision);bad["direction_class"]="SHORT"
            with self.assertRaises(ValueError):append_decision(repo,bad)
    def test_existing_decision_corruption_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            repo=Path(folder);decision=evaluate(fixture());p=append_decision(repo,decision);p.write_text("corrupt",encoding="utf-8")
            with self.assertRaises(ValueError):append_decision(repo,decision)
            self.assertEqual(p.read_text(),"corrupt")
    def test_incomplete_decision_schema_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            d={"schema_version":"btc-direction-v1"};d["decision_id"]=digest(d)
            with self.assertRaises(ValueError):append_decision(Path(folder),d)
            self.assertFalse((Path(folder)/"output_direction").exists())
    def test_production_dry_run_has_no_writes(self):
        from test_evidence import ObserverIntegrationTests
        from btc_anytime.direction.run import dry_run
        with tempfile.TemporaryDirectory() as folder:
            repo=Path(folder);ObserverIntegrationTests().repo_fixture(repo)
            before={p:p.read_bytes() for p in (repo/"data_market").rglob("*.jsonl")}
            with patch("btc_anytime.direction.run.load_registry",return_value=({"registry_id":"fixture"},[])):
                r=dry_run(repo)
            self.assertEqual(r["status"],"PASS");self.assertEqual(r["written_files"],[])
            self.assertEqual(before,{p:p.read_bytes() for p in before})
            self.assertFalse((repo/"output_direction").exists());self.assertFalse((repo/"metadata_features").exists())
    def causal_series(self,suffix=False,late=False):
        datasets={}
        for tf,duration in DURATIONS.items():
            rs=rows(66 if suffix else 65,tf)
            for i,r in enumerate(rs):r["time"]=T-(65-i)*duration;r["candle_time_utc"]=iso(r["time"])
            datasets[tf]=rs
        mapping={tf:{r["time"]:{"kind":"consumer_first_observed","ref":"actual","observed_at_ms":T if not late else T+1} for r in rs} for tf,rs in datasets.items()}
        _,series=build_dataset(datasets,T,availability_evidence=mapping)
        return {tf:[mark_generated({tf:[r]},max(T if not late else T+1,r["available_at_ms"]),"actual_generation")[tf][0]
                    for r in rs] for tf,rs in series.items()}
    def test_future_suffix_invariance(self):
        a=synchronize(self.causal_series(),T);b=synchronize(self.causal_series(True),T)
        x=evaluate(a);y=evaluate(b)
        # Whole-dataset manifest identity changes when a suffix is appended; past judgments must not.
        for k in ("direction_score","direction_class","confidence","confidence_components","regime","reason_codes","strong_gate"):
            self.assertEqual(x[k],y[k])
        for tf in DURATIONS:
            self.assertEqual(x["timeframes"][tf]["score"],y["timeframes"][tf]["score"])
            self.assertEqual(x["timeframes"][tf]["components"],y["timeframes"][tf]["components"])
    def test_late_backfill_non_retroactive(self):
        s=synchronize(self.causal_series(late=True),T)
        r=evaluate(s);self.assertEqual(r["regime"],"INSUFFICIENT_EVIDENCE")
        self.assertTrue(all(x["score"] is None for x in r["timeframes"].values()))


if __name__=="__main__":unittest.main()

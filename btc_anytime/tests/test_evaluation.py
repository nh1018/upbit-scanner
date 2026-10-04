"""Outcome fixtures, no executions, immutable decisions and cutoff-safe labels."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from btc_anytime.features.engine import digest
from btc_anytime.integrity import utc_ms,iso
from btc_anytime.direction.signal_history.storage import boundary_id
from btc_anytime.direction.evaluation.engine import evaluate,parameters,ANCHORS,STEP,bucket
from btc_anytime.direction.evaluation.storage import persist,writer_lock
from btc_anytime.direction.evaluation.aggregate import aggregate,non_overlapping

START=utc_ms("2026-10-04T00:00:00Z")


def sign_decision(d):
    snapshot=d["input_snapshot"]
    snapshot["snapshot_id"]=digest({k:v for k,v in snapshot.items() if k!="snapshot_id"})
    d["decision_id"]=digest({k:v for k,v in d.items() if k!="decision_id"})
    return d


def fixture():
    record={"time":START-STEP,"available_at_ms":START+1,"feature_generated_at_ms":START+2,
            "parameter_hash":"feature-fixture","features":{"atr_14":"2"},
            "availability_evidence":{"ref":"observation:fixture","observed_at_ms":START+1},
            "feature_quality":{"atr_14":{"ready":True,"available_at_ms":START+2}}}
    ref={"file":"raw.jsonl","line":1,"time":START-STEP,"row_sha256":"fixture"}
    d={"schema_version":"btc-direction-v1","signal_history_schema_version":"btc-direction-signal-history-v1",
       "engine_decision_id":"fixture","activation_ref":"fixture","trigger_boundary_id":boundary_id(START-STEP),
       "trigger_15m":{"candle_open_time_ms":START-STEP,"raw_ref":ref},
       "decision_time_utc":iso(START+1000),"direction_class":"LONG","directional_bias":"LONG","confidence":"0.65",
       "confidence_semantics":"evidence_quality_not_probability","regime":"ALIGNED_TREND",
       "direction_engine_version":"1.0.0","parameter_version":"initial-hypothesis-1","parameter_hash":"direction-fixture",
       "input_snapshot":{"schema_version":"btc-feature-v1","algorithm_version":"1.0.0","decision_time_utc":iso(START+1000),
                         "timeframes":{"15m":{"record":record}}},
       "price_references":{"15m":{"close":"100","candle_time_utc":iso(START-STEP),"raw_ref":ref,"source":"legacy_webhook_inferred"}},
       "timeframes":{tf:{"score":"0.5","components":{"trend":"0.5","structure":None,"momentum":"0","participation":"0.6","derivatives":None},
                        "oi_metadata":{"unit_status":"validated" if tf=="1h" else "unavailable"}} for tf in ("15m","1h","4h","1d")}}
    sign_decision(d)
    rows=[];evidence={};refs={}
    for i in range(6):
        t=START+i*STEP
        r={"time":t,"symbol":"BTCUSDT.P","timeframe":"15m","open":"100","high":"110","low":"90","close":"102","volume":"1","received_at_utc":iso(t+STEP+1)}
        rows.append(r);evidence[t]={"kind":"consumer_first_observed","ref":"observation:"+str(t),"observed_at_ms":t+STEP+2,"canonical_row_hash":digest(r)}
        refs[t]={"file":"raw.jsonl","line":i+2,"time":t,"row_sha256":digest(r)}
    return d,rows,evidence,refs


class EvaluationTests(unittest.TestCase):
    def setUp(self):self.d,self.rows,self.ev,self.refs=fixture();self.cutoff=START+6*STEP+100
    def run_label(self,anchor=ANCHORS[1],hours=1):return evaluate(self.d,self.rows,self.ev,self.refs,self.cutoff,anchor,hours)
    def resign(self):sign_decision(self.d)
    def change_row(self,i,**values):
        self.rows[i].update(values);self.ev[self.rows[i]["time"]]["canonical_row_hash"]=digest(self.rows[i])
    def test_proxy_exact_boundary(self):
        r=self.run_label();self.assertEqual(r["anchor_price_time_ms"],START+STEP);self.assertEqual(r["endpoint_ms"],START+5*STEP);self.assertEqual(r["status"],"MATURED")
    def test_exact_decision_boundary(self):
        self.d["decision_time_utc"]=iso(START+STEP);self.d["input_snapshot"]["decision_time_utc"]=iso(START+STEP);self.resign()
        self.assertEqual(self.run_label()["anchor_price_time_ms"],START+STEP)
    def test_diagnostic_return_fixture(self):
        r=self.run_label(ANCHORS[0]);self.assertEqual(r["metrics"]["raw_return_pct"],"2.000000000000000000");self.assertEqual(r["endpoint_ms"],START+4*STEP)
    def test_diagnostic_excursion_partial(self):self.assertEqual(self.run_label(ANCHORS[0])["excursion_status"],"PARTIAL")
    def test_unknown_initial_intrabar_excluded(self):
        self.change_row(0,high="999",low="1")
        r=self.run_label(ANCHORS[0]);self.assertEqual(r["metrics"]["mfe_pct"],"10.000000000000000000");self.assertEqual(r["path_expected_count"],3)
    def test_mfe_mae_hand_fixture(self):
        r=self.run_label();self.assertEqual(r["metrics"]["mfe_pct"],"10.000000000000000000");self.assertEqual(r["metrics"]["mae_pct"],"10.000000000000000000")
    def test_proxy_schema_no_execution_claim(self):
        r=self.run_label();self.assertIn("not first observed tick or execution price",r["anchor_semantics"]);self.assertNotIn("entry_price",r)
    def test_long_short_symmetry(self):
        a=self.run_label();self.d.update(direction_class="SHORT",directional_bias="SHORT");self.resign();b=self.run_label()
        self.assertEqual(float(a["metrics"]["directional_return_pct"]),-float(b["metrics"]["directional_return_pct"]));self.assertEqual(a["metrics"]["mfe_pct"],b["metrics"]["mae_pct"])
    def test_strong_same_return(self):
        a=self.run_label();self.d["direction_class"]="STRONG_LONG";self.resign();self.assertEqual(a["metrics"],self.run_label()["metrics"])
    def test_neutral_no_directional_return(self):
        self.d.update(direction_class="NEUTRAL",directional_bias="NEUTRAL");self.resign();r=self.run_label()
        self.assertIsNone(r["metrics"]["directional_return_pct"]);self.assertIsNone(r["metrics"]["mfe_pct"]);self.assertEqual(r["metrics"]["neutral_realized_range_pct"],"20.000000000000000000")
    def test_only_decision_15m_atr(self):self.assertEqual(self.run_label()["metrics"]["minimum_move_threshold_pct"],"2.000000000000000000")
    def test_other_tf_atr_irrelevant(self):
        before=self.run_label()["metrics"]
        self.d["input_snapshot"]["timeframes"]["4h"]={"record":{"features":{"atr_14":"9999"}}};self.resign()
        self.assertEqual(before,self.run_label()["metrics"])
    def test_atr_unavailable(self):
        self.d["input_snapshot"]["timeframes"]["15m"]["record"]["feature_quality"]["atr_14"]["ready"]=False;self.resign()
        r=self.run_label();self.assertIsNone(r["metrics"]["minimum_move_hit"]);self.assertEqual(r["return_status"],"MATURED")
    def test_future_atr_unavailable(self):
        self.d["input_snapshot"]["timeframes"]["15m"]["record"]["feature_quality"]["atr_14"]["available_at_ms"]=self.cutoff;self.resign()
        self.assertIsNone(self.run_label()["metrics"]["minimum_move_threshold_pct"])
    def test_exact_minimum_move_boundary(self):self.assertTrue(self.run_label()["metrics"]["minimum_move_hit"])
    def test_pending_no_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            for _ in range(3):self.assertEqual(persist(Path(tmp),self.run_label(hours=24))["status"],"PENDING_NOT_PERSISTED")
            self.assertEqual(list(Path(tmp).rglob("*")),[])
    def test_first_append_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);r=self.run_label();self.assertEqual(persist(p,r)["status"],"APPENDED")
            before={x:x.read_bytes() for x in p.rglob("*.json")};self.assertEqual(persist(p,r)["status"],"NOOP");self.assertEqual(before,{x:x.read_bytes() for x in p.rglob("*.json")})
    def test_finalized_conflict_never_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);r=self.run_label();first=persist(p,r);body=Path(first["path"]).read_bytes()
            changed=deepcopy(r);changed["metrics"]["raw_return_pct"]="99"
            self.assertEqual(persist(p,changed)["status"],"SOURCE_CONFLICT");self.assertEqual(body,Path(first["path"]).read_bytes())
    def test_missing_event_replay(self):
        self.rows.pop(4)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);r=self.run_label();self.assertEqual(persist(p,r)["status"],"APPENDED");self.assertEqual(persist(p,r)["status"],"NOOP")
    def test_source_missing_endpoint(self):self.rows.pop(4);self.assertEqual(self.run_label()["status"],"SOURCE_MISSING")
    def test_no_nearest_endpoint(self):
        self.change_row(5,close="109");self.rows.pop(4);self.assertIsNone(self.run_label()["metrics"])
    def test_source_conflict(self):self.rows.append(deepcopy(self.rows[2]));self.assertEqual(self.run_label()["status"],"SOURCE_CONFLICT")
    def test_evidence_hash_conflict(self):self.rows[2]["high"]="111";self.assertEqual(self.run_label()["status"],"SOURCE_CONFLICT")
    def test_gap_partial(self):self.rows.pop(2);r=self.run_label();self.assertEqual(r["return_status"],"MATURED");self.assertEqual(r["excursion_status"],"PARTIAL")
    def test_no_official_substitution(self):
        self.change_row(2,source="binance_usdm_official_rest_klines_backfill");r=self.run_label();self.assertIn(START+2*STEP,r["missing_times"]);self.assertEqual(r["excursion_status"],"PARTIAL")
    def test_missing_anchor_no_shift(self):self.rows.pop(1);self.assertEqual(self.run_label()["status"],"SOURCE_MISSING")
    def test_future_evidence_cutoff(self):self.ev[START+4*STEP]["observed_at_ms"]=self.cutoff+1;self.assertEqual(self.run_label()["status"],"SOURCE_MISSING")
    def test_late_evidence_nonretroactive(self):
        before=self.run_label();self.ev[START+4*STEP]["observed_at_ms"]=self.cutoff+STEP;old=self.run_label()
        self.assertEqual(old["status"],"SOURCE_MISSING");self.cutoff+=STEP;self.assertEqual(self.run_label()["status"],"MATURED");self.assertEqual(before["status"],"MATURED")
    def test_future_suffix_invariance(self):
        before=self.run_label();self.rows.append({"time":self.cutoff+STEP,"open":"bad"});self.assertEqual(before,self.run_label())
    def test_decision_raw_immutability(self):
        before=deepcopy((self.d,self.rows,self.ev,self.refs));self.run_label();self.assertEqual(before,(self.d,self.rows,self.ev,self.refs))
    def test_no_snapshot_copy(self):
        text=json.dumps(self.run_label());self.assertNotIn('"input_snapshot"',text);self.assertNotIn('"input_manifest"',text);self.assertLess(len(text),15000)
    def test_invalid_decision_hash(self):self.d["confidence"]="0.9";self.assertEqual(self.run_label()["status"],"INVALID")
    def test_no_historical_reconstruction_input(self):self.d.pop("signal_history_schema_version");self.assertEqual(self.run_label()["status"],"INVALID")
    def test_invalid_ohlc(self):self.change_row(2,high="50");self.assertEqual(self.run_label()["status"],"INVALID")
    def test_progress_bar_rejected(self):self.change_row(2,is_closed=False);self.assertEqual(self.run_label()["status"],"INVALID")
    def test_confidence_boundary(self):self.assertEqual(bucket("0.65",parameters()["confidence_edges"]),"[0.65,0.80)");self.assertEqual(bucket("1",parameters()["confidence_edges"]),"[0.90,1]")
    def test_nonoverlap_deterministic(self):
        a=self.run_label();b=deepcopy(a);b["anchor_price_time_ms"]+=STEP;b["endpoint_ms"]+=STEP;b["decision_ref"]["decision_id"]="second"
        self.assertEqual(non_overlapping([b,a]),[a])
    def test_nonoverlap_no_result_selection(self):
        a=self.run_label();b=deepcopy(a);a["status"]="INVALID";a["metrics"]=None;b["anchor_price_time_ms"]+=STEP
        self.assertEqual(non_overlapping([b,a]),[a])
    def test_aggregate_cohorts_and_quality(self):
        r=aggregate([self.run_label(),self.run_label(ANCHORS[0])]);partitions=" ".join(x["partition"] for x in r["cohorts"])
        for name in ("direction_class","directional_bias","confidence_bucket","regime","1h_oi_status","4h_trend","daily_cohort"):self.assertIn(name,partitions)
        self.assertTrue(all("SPARSE_SAMPLE" in x["warnings"] for x in r["cohorts"]))
    def test_horizons(self):
        for h in (4,12,24):self.assertEqual(self.run_label(hours=h)["status"],"PENDING")
    def test_deterministic_replay(self):self.assertEqual(self.run_label(),self.run_label())
    def test_no_cutoff_timestamp_churn(self):
        before=self.run_label();self.cutoff+=1;self.assertEqual(before,self.run_label())
    def test_exclusive_writer_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            with writer_lock(p):
                with self.assertRaises(FileExistsError):persist(p,self.run_label())
    def test_late_gap_event_then_final_label(self):
        full=deepcopy(self.rows);self.rows.pop(2)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);first=persist(p,self.run_label());body=Path(first["path"]).read_bytes()
            self.rows=full;last=persist(p,self.run_label())
            self.assertEqual(last["status"],"APPENDED");self.assertIn("labels",last["path"]);self.assertEqual(body,Path(first["path"]).read_bytes())
    def test_partial_not_pooled_with_complete_excursion(self):
        report=aggregate([self.run_label(ANCHORS[0])]);allgroup=next(x for x in report["cohorts"] if '"dimension":"all"' in x["partition"])
        self.assertEqual(allgroup["all_observations"]["complete_mfe_pct"]["count"],0)
        self.assertEqual(allgroup["all_observations"]["partial_mfe_lower_bound_pct"]["count"],1)
    def test_production_derivatives_object_cohort(self):
        self.d["timeframes"]["1h"]["components"]["derivatives"]={"change":"2","change_pct":"1","state":"PRICE_UP_OI_UP","segment_id":"fixture","signature":{"basis":"confirmed_oi_bar_close_boundary"}}
        self.resign();report=aggregate([self.run_label()])
        self.assertTrue(any('PRICE_UP_OI_UP' in x["partition"] for x in report["cohorts"]))
    def test_missing_raw_reference_not_matured(self):
        self.refs.pop(START+4*STEP);self.assertEqual(self.run_label()["status"],"SOURCE_MISSING")
    def test_full_readonly_runner_no_labels(self):
        from btc_anytime.direction.evaluation.runner import dry_run
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);directory=p/"output_direction/btc_anytime/v1";directory.mkdir(parents=True)
            activation={"started_at_utc":iso(START-STEP),"baseline_15m_open_ms":START-2*STEP}
            activation["activation_id"]=digest(activation);self.d["activation_ref"]=activation["activation_id"];self.resign()
            (directory/"activation.json").write_text(json.dumps(activation));(directory/"decisions").mkdir()
            (directory/"decisions"/(self.d["decision_id"]+".json")).write_text(json.dumps(self.d))
            before={x:x.read_bytes() for x in p.rglob("*") if x.is_file()}
            with patch("btc_anytime.direction.evaluation.runner.load_raw",return_value=({"15m":self.rows},{"15m":self.refs},{})),patch("btc_anytime.direction.evaluation.runner.load_observations",return_value=[]),patch("btc_anytime.direction.evaluation.runner.evidence_map",return_value={"15m":self.ev}):
                r=dry_run(p,self.cutoff)
            self.assertEqual(r["states"],{"PARTIAL":1,"PENDING":6,"MATURED":1});self.assertTrue(r["protected_inputs_unchanged"])
            self.assertEqual(before,{x:x.read_bytes() for x in p.rglob("*") if x.is_file()});self.assertFalse((directory/"evaluation").exists())
    def test_storage_namespace_escape_rejected(self):
        r=self.run_label();r["evaluation_key"]="../../raw"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):persist(Path(tmp),r)
            self.assertEqual(list(Path(tmp).rglob("*")),[])
    def test_recording_envelope_tamper_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);r=self.run_label();first=persist(p,r);file=Path(first["path"])
            envelope=json.loads(file.read_text());envelope["recorded_at_utc"]=iso(START);file.write_text(json.dumps(envelope))
            with self.assertRaises(ValueError):persist(p,r)
    def test_ambient_decimal_context_independent(self):
        from decimal import localcontext,ROUND_FLOOR
        before=self.run_label()
        with localcontext() as ctx:
            ctx.prec=9;ctx.rounding=ROUND_FLOOR
            self.assertEqual(before,self.run_label())
    def test_all_completed_horizons_exact_path(self):
        self.rows=[];self.ev={};self.refs={}
        for i in range(97):
            t=START+i*STEP
            row={"time":t,"symbol":"BTCUSDT.P","timeframe":"15m","open":"100","high":"110","low":"90","close":"102","volume":"1","received_at_utc":iso(t+STEP+1)}
            self.rows.append(row);self.ev[t]={"kind":"consumer_first_observed","ref":"observation:"+str(t),"observed_at_ms":t+STEP+2,"canonical_row_hash":digest(row)}
            self.refs[t]={"file":"raw.jsonl","line":i+1,"time":t,"row_sha256":digest(row)}
        self.cutoff=START+98*STEP
        for h in (1,4,12,24):
            r=self.run_label(hours=h);self.assertEqual(r["status"],"MATURED");self.assertEqual(r["path_observed_count"],h*4);self.assertEqual(r["metrics"]["directional_return_pct"],"2.000000000000000000")


if __name__=="__main__":unittest.main()

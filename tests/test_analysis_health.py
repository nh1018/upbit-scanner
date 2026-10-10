import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import analysis_health as H


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / "output").mkdir()
        (self.repo / "output_btc_anytime").mkdir()
        from unittest.mock import patch
        self.active_patch = patch('analysis_health._btc_active_evidence', return_value={"valid":True,"blocking_reasons":[]})
        self.active_mock = self.active_patch.start()
        self.addCleanup(self.active_patch.stop)
        self.now = datetime(2026, 10, 7, 12, 30, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_upbit_a_stale_fails_closed(self):
        (self.repo / "output/latest_scan.json").write_text(json.dumps({
            "scanner_version":"1.3-cloud","generated_at_kst":"2026-10-07T14:53:04+09:00",
            "scanned_count":282,"candidate_count":30,"binance_status":"OK"
        }))
        result = H.upbit_a_health(self.repo, H._utc_ms(self.now), 120)
        self.assertEqual(result["status"], "STALE")
        self.assertFalse(result["usable_for_current_analysis"])

    def test_upbit_a_current_allows_binance_warning_without_inventing_context(self):
        (self.repo / "output/latest_scan.json").write_text(json.dumps({
            "scanner_version":"1.3-cloud","generated_at_kst":"2026-10-07T21:00:00+09:00",
            "scanned_count":282,"candidate_count":30,"binance_status":"PARTIAL"
        }))
        result = H.upbit_a_health(self.repo, H._utc_ms(self.now), 120)
        self.assertTrue(result["usable_for_current_analysis"])
        self.assertEqual(result["warnings"], ["BINANCE_CONTEXT_NOT_OK"])

    def test_btc_consumer_time_staleness_overrides_generation_flags(self):
        obj = self.btc_fixture()
        obj["generated_at_utc"] = "2026-10-07T10:00:00Z"
        self.write_btc(obj)
        result = H.btc_health(self.repo, H._utc_ms(self.now), 45)
        self.assertEqual(result["status"], "STALE")
        self.assertFalse(result["usable_for_current_analysis"])

    def test_missing_inputs_are_explicit(self):
        obj = H.build(self.repo, self.now)
        self.assertEqual(obj["overall_status"], "BLOCKED")
        self.assertEqual(set(obj["blocked_systems"]), {"btc","upbit_a","upbit_b"})

    def btc_fixture(self):
        durations = {"15m":900000,"1h":3600000,"4h":14400000,"1d":86400000}
        now = H._utc_ms(self.now)
        return {"generated_at_utc":H._iso_ms(now),
                "direction":{"available":True,"stale":False,"decision_time_utc":H._iso_ms(now)},
                "entry":{"available":True,"stale":False,"generated_at_utc":H._iso_ms(now),
                         "matches_snapshot_direction":True,"matches_latest_15m":True},
                "provenance":{"snapshot_policy":{"decision_max_age_ms":1800000,"market_publication_allowance_ms":1200000}},
                "data_freshness":{tf:{"market_stale":False,"feature_matches_latest_market":True,
                                      "feature_candle_open_utc":H._iso_ms(now//d*d-d)} for tf,d in durations.items()},
                "market_data":{tf:{"integrity":{"duplicate":0,"missing_slots":0,"abnormal_intervals":0,"ohlcv_invalid":0}} for tf in durations}}

    def write_btc(self,obj):
        (self.repo/"output_btc_anytime/latest_analysis.json").write_text(json.dumps(obj))

    def test_btc_normal_contract_remains_current(self):
        self.write_btc(self.btc_fixture())
        self.assertTrue(H.btc_health(self.repo,H._utc_ms(self.now),45)["usable_for_current_analysis"])

    def test_regenerated_snapshot_does_not_refresh_old_decision(self):
        obj=self.btc_fixture();obj["direction"]["decision_time_utc"]="2026-10-07T11:00:00Z";self.write_btc(obj)
        self.assertEqual(H.btc_health(self.repo,H._utc_ms(self.now),45)["status"],"STALE")

    def test_btc_mismatch_unavailable_stale_and_continuity_fail_closed(self):
        import copy
        for section,key,value in [("entry","matches_snapshot_direction",False),("entry","matches_latest_15m",False),
                                  ("entry","available",False),("direction","stale",True)]:
            with self.subTest(key=key):
                obj=copy.deepcopy(self.btc_fixture());obj[section][key]=value;self.write_btc(obj)
                self.assertFalse(H.btc_health(self.repo,H._utc_ms(self.now),45)["usable_for_current_analysis"])
        obj=self.btc_fixture();obj["market_data"]["15m"]["integrity"]["missing_slots"]=1;self.write_btc(obj)
        self.assertTrue(H.btc_health(self.repo,H._utc_ms(self.now),45)["usable_for_current_analysis"])
        self.active_mock.return_value={"valid":False,"blocking_reasons":["ENTRY:ACTIVE_PATH_GAP"]}
        self.assertFalse(H.btc_health(self.repo,H._utc_ms(self.now),45)["usable_for_current_analysis"])

    def test_generation_freshness_does_not_mix_consumer_time(self):
        from datetime import timedelta
        self.write_btc(self.btc_fixture())
        r=H.btc_health(self.repo,H._utc_ms(self.now+timedelta(minutes=36)),45)
        self.assertTrue(r["all_timeframes_fresh_at_generation"])
        self.assertFalse(r["all_timeframes_fresh_at_consumer_time"])
        self.assertFalse(r["usable_for_current_analysis"])

    def test_missing_active_evidence_fails_closed(self):
        self.write_btc(self.btc_fixture())
        self.active_mock.side_effect=ValueError("missing evidence")
        self.assertEqual(H.btc_health(self.repo,H._utc_ms(self.now),45)["status"],"INVALID")

    def test_missing_timeframe_and_future_timestamp_invalid(self):
        for mutation in ("missing_tf","future","naive"):
            obj=self.btc_fixture()
            if mutation=="missing_tf":del obj["data_freshness"]["4h"]
            elif mutation=="future":obj["generated_at_utc"]="2026-10-08T00:00:00Z"
            else:obj["generated_at_utc"]="2026-10-07T12:30:00"
            self.write_btc(obj)
            self.assertEqual(H.btc_health(self.repo,H._utc_ms(self.now),45)["status"],"INVALID")

    def test_b_partial_degraded_complete_current(self):
        from unittest.mock import patch
        path=self.repo/"output_upbit_b/v1/history/2026-10-07/12.jsonl";path.parent.mkdir(parents=True);path.write_bytes(b"fixture")
        for completeness in ("PARTIAL","COMPLETE"):
            manifest={"source_cutoff":H._utc_ms(self.now)-1800000,"completeness":completeness,"failed":1 if completeness=="PARTIAL" else 0,"unattempted":0,"insufficient":1}
            with patch('upbit_b.history.validate_cycle',return_value=(manifest,[{}])):
                result=H.upbit_b_health(self.repo,H._utc_ms(self.now),120)
            self.assertEqual(result["complete"],completeness=="COMPLETE")
            self.assertEqual(result["usable_for_current_analysis"],completeness=="COMPLETE")
            self.assertEqual(result["status"],"CURRENT" if completeness=="COMPLETE" else "DEGRADED")

    def test_cached_health_expires_without_mutating_snapshot(self):
        from datetime import timedelta
        self.write_btc(self.btc_fixture());snapshot=H.build(self.repo,self.now)
        self.assertTrue(snapshot["systems"]["btc"]["usable_for_current_analysis"])
        consumed=H.at_consumer_time(snapshot,self.now+timedelta(minutes=31))
        self.assertFalse(consumed["systems"]["btc"]["usable_for_current_analysis"])
        self.assertTrue(snapshot["systems"]["btc"]["usable_for_current_analysis"])
        expired=H.at_consumer_time(snapshot,self.now+timedelta(minutes=36))
        self.assertTrue(expired["systems"]["btc"]["all_timeframes_fresh_at_generation"])
        self.assertFalse(expired["systems"]["btc"]["all_timeframes_fresh_at_consumer_time"])
        del snapshot["systems"]["btc"]["valid_until_utc"]
        self.assertFalse(H.at_consumer_time(snapshot,self.now)["systems"]["btc"]["usable_for_current_analysis"])

    def test_invalid_json_fails_closed(self):
        (self.repo/"output/latest_scan.json").write_text('{bad')
        self.assertEqual(H.upbit_a_health(self.repo,H._utc_ms(self.now),120)["status"],"INVALID")

    def test_health_workflow_separates_read_only_pr_from_production(self):
        text=(Path(__file__).parents[1]/".github/workflows/analysis-health.yml").read_text()
        self.assertIn("workflows: [BTC Analysis Snapshot V1, BTC Entry Timing Engine V1, Upbit Scanner, Upbit B Prospective History V1]",text)
        self.assertIn("cron: '10,25,40,55 * * * *'",text)
        self.assertIn("github.event_name != 'pull_request' &&",text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'",text)
        validation=text.split('  validate:',1)[1].split('  health:',1)[0]
        self.assertIn('contents: read',validation)
        self.assertNotIn('git push',validation)


if __name__ == "__main__":
    unittest.main()


class StoredBtcEvidenceTests(unittest.TestCase):
    def setUp(self):
        import gzip
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo=Path(self.tmp.name)
        fixture=Path(__file__).parent/"fixtures/btc_health_historical_gap.json.gz"
        for name,raw in json.loads(gzip.decompress(fixture.read_bytes())).items():
            path=self.repo/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(raw.encode('utf-8'))

    def test_actual_historical_gaps_and_stored_valid_inputs(self):
        repo=self.repo
        obj=json.loads((repo/"output_btc_anytime/latest_analysis.json").read_text())
        result=H._btc_active_evidence(repo,obj)
        self.assertTrue(result["valid"],result)
        self.assertEqual(result["entry_path_gap_intervals"],0)
        self.assertGreater(sum(x["integrity"]["missing_slots"] for x in obj["market_data"].values()),0)
        at=H._parse_iso_ms(obj["generated_at_utc"])
        health=H.btc_health(repo,at,45)
        self.assertTrue(health["usable_for_current_analysis"],health)
        self.assertEqual(health["historical_integrity"]["15m"],obj["market_data"]["15m"]["integrity"])

    def test_active_direction_missing_component_and_path_gap(self):
        from unittest.mock import patch
        from btc_anytime.analysis_snapshot import artifact
        repo=self.repo
        obj=json.loads((repo/"output_btc_anytime/latest_analysis.json").read_text())
        def changed(path,key):
            value=artifact(path,key)
            if key=="decision_id":
                value["direction_class"]="LONG"
                value["timeframes"]["4h"]["components"]["trend"]=None
            return value
        items=[{"timeframe":"15m","time":0},{"timeframe":"15m","time":1800000}]
        with patch('btc_anytime.analysis_snapshot.artifact',side_effect=changed), patch('btc_anytime.entry.engine.validate',return_value=items):
            r=H._btc_active_evidence(repo,obj)
        self.assertFalse(r["valid"])
        self.assertIn("ENTRY:ACTIVE_PATH_GAP",r["blocking_reasons"])
        self.assertIn("4h:CURRENT_DIRECTION_COMPONENT_UNAVAILABLE",r["blocking_reasons"])

    def test_snapshot_tamper_and_missing_source_rejected(self):
        from btc_anytime.analysis_snapshot import sealed
        repo=self.repo
        obj=json.loads((repo/"output_btc_anytime/latest_analysis.json").read_text())
        obj["feature"]["timeframes"]["4h"]["values"]["atr_14"]="0"
        with self.assertRaises(ValueError):H._btc_active_evidence(repo,obj)
        obj=sealed({k:v for k,v in obj.items() if k!="payload_sha256"})
        with self.assertRaises(ValueError):H._btc_active_evidence(repo,obj)

    def test_missing_or_modified_original_file_fails_closed(self):
        obj=json.loads((self.repo/"output_btc_anytime/latest_analysis.json").read_text())
        path=self.repo/obj["direction"]["source_reference"]["file"]
        path.write_bytes(path.read_bytes()+b" ")
        r=H.btc_health(self.repo,H._parse_iso_ms(obj["generated_at_utc"]),45)
        self.assertEqual(r["status"],"INVALID")
        path.unlink()
        self.assertEqual(H.btc_health(self.repo,H._parse_iso_ms(obj["generated_at_utc"]),45)["status"],"INVALID")

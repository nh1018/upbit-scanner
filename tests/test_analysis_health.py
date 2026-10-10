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
        self.assertFalse(H.btc_health(self.repo,H._utc_ms(self.now),45)["usable_for_current_analysis"])

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

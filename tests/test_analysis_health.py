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
        (self.repo / "output_btc_anytime/latest_analysis.json").write_text(json.dumps({
            "generated_at_utc":"2026-10-07T10:00:00Z",
            "data_freshness":{"15m":{"market_stale":False,"feature_matches_latest_market":True}},
            "direction":{"available":True,"direction_class":"LONG","regime":"ALIGNED_TREND"},
            "entry":{"available":True}
        }))
        result = H.btc_health(self.repo, H._utc_ms(self.now), 45)
        self.assertEqual(result["status"], "STALE")
        self.assertFalse(result["usable_for_current_analysis"])

    def test_missing_inputs_are_explicit(self):
        obj = H.build(self.repo, self.now)
        self.assertEqual(obj["overall_status"], "BLOCKED")
        self.assertEqual(set(obj["blocked_systems"]), {"btc","upbit_a","upbit_b"})


if __name__ == "__main__":
    unittest.main()

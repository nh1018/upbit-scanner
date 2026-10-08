"""Research-only C evidence contracts."""
import unittest
from upbit_c.evidence import observe, FIELDS


class EvidenceTests(unittest.TestCase):
    def snapshot(self):
        return {
            "metadata": {"provider": "UPBIT", "instrument": "KRW-BTC", "timeframe": "1h",
                         "source_cutoff_ms": 3700000, "source_candle_close_ms": 3600000,
                         "source_status": "AVAILABLE", "input_sha256": "raw",
                         "evidence": [{"url": "source"}]},
            "features": {"return3_pct": "-12.5", "breakdown20": True},
            "readiness": {"return3_pct": "READY", "breakdown20": "READY"},
            "measurement_sha256": "measurement",
        }

    def test_research_only_and_no_fabricated_score(self):
        result = observe(self.snapshot())
        self.assertEqual(result["values"]["return3_pct"], "-12.5")
        self.assertIs(result["values"]["breakdown20"], True)
        self.assertIsNone(result["score"])
        self.assertEqual(result["candidate"], "NOT_EVALUATED")
        self.assertEqual(result["activation"], "RESEARCH_ONLY")
        self.assertEqual(set(result["values"]), set(FIELDS))

    def test_completed_candle_before_collection_time_accepted(self):
        result = observe(self.snapshot())
        self.assertEqual(result["source_cutoff_ms"], 3700000)

    def test_stale_rejected(self):
        data = self.snapshot()
        data["metadata"]["source_candle_close_ms"] = 0
        with self.assertRaises(ValueError):
            observe(data)

    def test_unverified_provider_rejected(self):
        data = self.snapshot()
        data["metadata"]["provider"] = "BINANCE_SPOT"
        with self.assertRaises(ValueError):
            observe(data)

    def test_invalid_boolean_rejected(self):
        data = self.snapshot()
        data["features"]["breakdown20"] = "true"
        with self.assertRaises(ValueError):
            observe(data)


if __name__ == "__main__":
    unittest.main()

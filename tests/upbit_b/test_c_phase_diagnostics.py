import unittest
from upbit_c.phase_diagnostics import diagnose


class CPhaseTests(unittest.TestCase):
    def sample(self):
        return {"schema_version": "upbit-c-rebound-evidence-0",
                "activation": "RESEARCH_ONLY", "market": "KRW-BTC",
                "timeframe": "1h", "source_cutoff_ms": 123,
                "values": {"return3_pct": "-9", "return5_pct": "-15",
                           "quote_ratio_ma20": "2", "return3_acceleration_pp": "1",
                           "distance_to_confirmed_low_atr": "0.5",
                           "breakdown20": False, "signed_body_ratio": "0.2",
                           "ema20_upward_recross": False}}

    def test_recovery_evidence_is_not_candidate(self):
        x = diagnose(self.sample())
        self.assertEqual(x["state"], "DECLINING_WITH_RECOVERY_EVIDENCE")
        self.assertIsNone(x["score"])
        self.assertEqual(x["candidate"], "NOT_EVALUATED")

    def test_breakdown_prevents_recovery_label(self):
        x = self.sample()
        x["values"]["breakdown20"] = True
        self.assertEqual(diagnose(x)["state"], "DECLINING_UNCONFIRMED")

    def test_missing_is_not_negative_or_zero(self):
        x = self.sample()
        x["values"]["return3_acceleration_pp"] = None
        y = diagnose(x)
        self.assertEqual(y["state"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("deceleration", y["missing_required"])

    def test_no_recent_decline(self):
        x = self.sample()
        x["values"]["return3_pct"] = "3"
        self.assertEqual(diagnose(x)["state"], "NO_RECENT_MULTIWINDOW_SELLOFF")

    def test_wrong_evidence_rejected(self):
        x = self.sample()
        x["activation"] = "LIVE"
        with self.assertRaises(ValueError):
            diagnose(x)


if __name__ == "__main__":
    unittest.main()

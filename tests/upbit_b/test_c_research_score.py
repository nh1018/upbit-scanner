"""Offline C score V0 contract tests; no network and no production writes."""
import unittest
from copy import deepcopy
from upbit_c.research_score import evaluate, PARAMETER_SHA256


def observation(tf):
    values = {
        "atr_pct": "2", "return1_pct": "0.5", "return3_pct": "-4",
        "return5_pct": "-5", "return10_pct": "-8",
        "return3_acceleration_pp": "1.5", "quote_ratio_ma20": "3",
        "quote_ratio_median20": "2.5", "close_location": "0.7",
        "signed_body_ratio": "0.5", "distance_to_confirmed_low_atr": "0.8",
        "quote_recent3_vs_prior20": "1.5", "low_structure": "HL",
        "breakdown20": False, "ema20_upward_recross": True,
    }
    from upbit_c.evidence import RESEARCH_GROUPS
    return {
        "schema_version": "upbit-c-rebound-evidence-0", "timeframe": tf,
        "activation": "RESEARCH_ONLY", "score": None, "candidate": "NOT_EVALUATED",
        "source_input_sha256": "a" * 64, "source_measurement_sha256": "b" * 64,
        "source_cutoff_ms": 3600000,
        "values": values, "missing": {},
        "groups": {k: {"available": len(v), "total": len(v)} for k, v in RESEARCH_GROUPS.items()},
    }


class CScoreTests(unittest.TestCase):
    def setUp(self):
        self.rows = {tf: observation(tf) for tf in ("1h", "4h", "1d")}

    def test_rebound_research_only_and_deterministic(self):
        a = evaluate(self.rows)
        self.assertEqual(a, evaluate(deepcopy(self.rows)))
        self.assertEqual(a["research_setup"], "PASS")
        self.assertEqual(a["activation"], "RESEARCH_ONLY")
        self.assertEqual(a["candidate"], "NOT_EVALUATED")
        self.assertIsNone(a["entry_signal"])
        self.assertEqual(a["parameter_sha256"], PARAMETER_SHA256)
        self.assertGreater(float(a["score"]), 0)
        self.assertLessEqual(float(a["score"]), 100)

    def test_ongoing_breakdown_blocks_even_with_recovery(self):
        self.rows["4h"]["values"]["breakdown20"] = True
        self.assertEqual(evaluate(self.rows)["research_setup"], "BLOCKED")

    def test_weak_bounce_no_selloff(self):
        for row in self.rows.values():
            for k in ("return3_pct", "return5_pct", "return10_pct"):
                row["values"][k] = "-0.1"
        self.assertFalse(evaluate(self.rows)["gates"]["selloff"])

    def test_no_recovery(self):
        self.rows["1h"]["values"].update(
            signed_body_ratio="-0.8", ema20_upward_recross=False,
            quote_recent3_vs_prior20="0.7")
        self.assertFalse(evaluate(self.rows)["gates"]["recovery"])

    def test_missing_and_bad_evidence_rejected(self):
        self.rows["4h"]["values"]["atr_pct"] = None
        with self.assertRaises(ValueError):
            evaluate(self.rows)
        self.rows["4h"]["values"]["atr_pct"] = "NaN"
        with self.assertRaises(ValueError):
            evaluate(self.rows)

    def test_missing_timeframe_rejected(self):
        del self.rows["1d"]
        with self.assertRaises(ValueError):
            evaluate(self.rows)


if __name__ == "__main__":
    unittest.main()

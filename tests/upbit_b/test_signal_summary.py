"""Read-only summary regression checks; no external APIs."""
import unittest
from unittest.mock import patch

from upbit_b.signal_summary import summarize


class SummaryTests(unittest.TestCase):
    def test_rank_and_missing_score(self):
        manifest = {"cycle_id": "cycle", "source_cutoff": "2026-10-08T13:00:00Z",
                    "universe_count": 3, "candidate_count": 3}
        def row(market, score):
            return {"instrument": market, "observation_id": market+"-id",
                    "evidence_level": "COMPACT",
                    "summary": {"candidate": "TRUE", "state": "TREND_CONTINUATION",
                                "chase": "LOW", "confidence": "HIGH",
                                "confirmation": "UNAVAILABLE"},
                    "observation": {"score": score} if score is not None else None}
        with patch("upbit_b.signal_summary.validate_cycle", return_value=(manifest, [
            row("KRW-Z", "71"), row("KRW-A", "71"), row("KRW-M", None)
        ])):
            import json
            payload = (json.dumps({"schema_version": "upbit-b-history-journal-1"})+"\n").encode()
            result = summarize(payload)
        self.assertEqual([r["market"] for r in result["candidates"]], ["KRW-A", "KRW-Z"])
        self.assertEqual(result["unranked_candidate_count"], 1)
        self.assertEqual(result["advice"], "OBSERVATION_ONLY_NOT_ENTRY_SIGNAL")

    def test_reject_incomplete(self):
        with self.assertRaises(ValueError):
            summarize(b"{}")

    def test_reject_non_bytes(self):
        with self.assertRaises(TypeError):
            summarize("{}")


if __name__ == "__main__":
    unittest.main()

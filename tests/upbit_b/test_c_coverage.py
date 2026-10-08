"""C coverage summaries must distinguish missing windows and feature gaps."""
import unittest
from upbit_c.coverage import coverage
from upbit_c.evidence import RESEARCH_GROUPS


class CoverageTests(unittest.TestCase):
    def test_evaluated_and_missing_denominators(self):
        groups = {g: {"available": len(keys)-1, "total": len(keys)}
                  for g, keys in RESEARCH_GROUPS.items()}
        report = {"schema_version": "upbit-c-evidence-smoke-0", "activation": "RESEARCH_ONLY",
                  "markets": 1, "source_cutoff_ms": 123,
                  "results": [{"market": "KRW-BTC", "timeframes": {
                      "1h": {"status": "EVALUATED", "groups": groups},
                      "4h": {"status": "API_ERROR", "reason": "timeout"},
                      "1d": {"status": "NOT_EVALUATED", "reason": "stale"},
                  }}]}
        result = coverage(report)
        self.assertEqual(result["expected_windows"], 3)
        self.assertEqual(result["evaluated_windows"], 1)
        self.assertEqual(result["group_coverage_by_timeframe"]["4h"]["selloff"]["total"], 0)
        self.assertEqual(result["group_coverage_by_timeframe"]["1h"]["selloff"]["available"], 3)
        self.assertEqual(len(result["unevaluated_reasons"]), 2)
        self.assertEqual(result["candidates_created"], 0)

    def test_incomplete_report_rejected(self):
        with self.assertRaises(ValueError):
            coverage({"schema_version": "upbit-c-evidence-smoke-0", "activation": "RESEARCH_ONLY",
                      "markets": 1, "source_cutoff_ms": 0, "results": []})


if __name__ == "__main__":
    unittest.main()

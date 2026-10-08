"""C live audit is isolated from candidate scoring and publication."""
import unittest
from unittest.mock import patch
from upbit_c import research_smoke


class ResearchSmokeTests(unittest.TestCase):
    def test_reject_unknown_or_duplicate_markets(self):
        with patch.object(research_smoke, "HTTPClient"), patch.object(
            research_smoke, "universe", return_value=([{"market": "KRW-BTC"}], [])
        ), patch.object(research_smoke, "MarketData") as md:
            for market in ("KRW-UNKNOWN", "KRW-BTC,KRW-BTC"):
                with self.subTest(market=market), self.assertRaises(ValueError):
                    research_smoke.main(["--markets", market])
            md.assert_not_called()

    def test_research_output_has_no_candidates(self):
        class Window:
            status = "AVAILABLE"
            reason = None
        class MD:
            cutoff_ms = 3600000
            cache = {}
            def __init__(self, client): pass
            def window(self, provider, market, tf): return Window()
        with patch.object(research_smoke, "HTTPClient"), patch.object(
            research_smoke, "universe", return_value=([{"market": "KRW-BTC"}], [])
        ), patch.object(research_smoke, "MarketData", MD), patch.object(
            research_smoke, "snapshot", return_value={"snapshot": "mock"}
        ), patch.object(research_smoke, "observe", return_value={
            "groups": {"selloff": {"available": 2, "total": 4}},
            "missing": {"return1_pct": "WARMUP"}, "source_cutoff_ms": 3600000
        }):
            report = research_smoke.main(["--markets", "KRW-BTC"])
        self.assertEqual(report["candidates_created"], 0)
        self.assertEqual(report["scores_created"], 0)
        self.assertEqual(report["timeframe_status_counts"], {"EVALUATED": 3})


if __name__ == "__main__":
    unittest.main()

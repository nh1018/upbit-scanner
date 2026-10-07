import json
import tempfile
import unittest
from pathlib import Path

from btc_anytime.direction.evaluation.performance_context import _latest_decision, build
from btc_anytime.features.engine import digest


class PerformanceContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_decision(self, decision_id, clock, **extra):
        folder = self.repo / "output_direction/btc_anytime/v1/decisions"
        folder.mkdir(parents=True, exist_ok=True)
        value = {"decision_id": decision_id, "decision_time_utc": clock, **extra}
        (folder / f"{decision_id}.json").write_text(json.dumps(value), encoding="utf-8")
        return value

    def write_dashboard(self, rows):
        performance = self.repo / "output_direction/btc_anytime/v1/performance"
        performance.mkdir(parents=True, exist_ok=True)
        dashboard = {"schema_version": "btc-direction-performance-dashboard-v1", "rows": rows}
        dashboard["dashboard_id"] = digest(dashboard)
        (performance / "dashboard_latest.json").write_text(json.dumps(dashboard), encoding="utf-8")

    def test_latest_decision_orders_by_utc_not_iso_text(self):
        self.write_decision("zzzz-older", "2026-10-07T05:30:00Z")
        self.write_decision("aaaa-newer", "2026-10-07T06:00:00Z")
        self.assertEqual(_latest_decision(self.repo)["decision_id"], "aaaa-newer")

    def test_latest_decision_tie_breaks_deterministically(self):
        self.write_decision("a", "2026-10-07T06:00:00Z")
        self.write_decision("b", "2026-10-07T06:00:00Z")
        self.assertEqual(_latest_decision(self.repo)["decision_id"], "b")

    def test_build_includes_current_confidence_cohort_and_semantics(self):
        d = self.write_decision(
            "current", "2026-10-07T06:00:00Z",
            direction_class="NEUTRAL", regime="TRANSITION",
            confidence="0.841428571428571429",
            confidence_semantics="evidence_quality_not_probability",
        )
        rows = [
            {"horizon": h, "dimension": dim, "value": value}
            for h in (1, 4, 12, 24)
            for dim, value in (
                ("all", "ALL"),
                ("direction_class", "NEUTRAL"),
                ("direction_regime", "NEUTRAL:TRANSITION"),
                ("regime", "TRANSITION"),
                ("confidence_bucket", "[0.80,0.90)"),
            )
        ]
        self.write_dashboard(rows)
        out = build(self.repo)
        self.assertEqual(out["decision_time_utc"], d["decision_time_utc"])
        self.assertEqual(out["confidence_semantics"], "evidence_quality_not_probability")
        self.assertEqual(len(out["rows"]), 20)
        self.assertEqual(sum(r["dimension"] == "confidence_bucket" for r in out["rows"]), 4)

    def test_build_rejects_tampered_dashboard(self):
        self.write_decision("current", "2026-10-07T06:00:00Z")
        performance = self.repo / "output_direction/btc_anytime/v1/performance"
        performance.mkdir(parents=True, exist_ok=True)
        dashboard = {"schema_version": "btc-direction-performance-dashboard-v1", "rows": [], "dashboard_id": "tampered"}
        (performance / "dashboard_latest.json").write_text(json.dumps(dashboard), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "dashboard hash mismatch"):
            build(self.repo)


if __name__ == "__main__":
    unittest.main()

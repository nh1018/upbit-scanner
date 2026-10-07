import json
from pathlib import Path

from btc_anytime.direction.evaluation.performance_context import _latest_decision, build


def _write(root: Path, decision_id: str, clock: str):
    folder = root / "output_direction/btc_anytime/v1/decisions"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{decision_id}.json").write_text(
        json.dumps({"decision_id": decision_id, "decision_time_utc": clock}),
        encoding="utf-8",
    )


def test_latest_decision_orders_by_utc_not_iso_text(tmp_path):
    # Lexical ISO ordering is unsafe when equivalent timestamps use offsets.
    _write(tmp_path, "older", "2026-10-07T14:30:00+09:00")
    _write(tmp_path, "newer", "2026-10-07T06:00:00Z")
    assert _latest_decision(tmp_path)["decision_id"] == "newer"


def test_latest_decision_tie_breaks_deterministically(tmp_path):
    _write(tmp_path, "a", "2026-10-07T06:00:00Z")
    _write(tmp_path, "b", "2026-10-07T06:00:00+00:00")
    assert _latest_decision(tmp_path)["decision_id"] == "b"


def test_build_includes_current_confidence_cohort_and_semantics(tmp_path):
    folder = tmp_path / "output_direction/btc_anytime/v1/decisions"
    folder.mkdir(parents=True, exist_ok=True)
    decision = {
        "decision_id": "current",
        "decision_time_utc": "2026-10-07T06:00:00Z",
        "direction_class": "NEUTRAL",
        "regime": "TRANSITION",
        "confidence": "0.841428571428571429",
        "confidence_semantics": "evidence_quality_not_probability",
    }
    (folder / "current.json").write_text(json.dumps(decision), encoding="utf-8")
    performance = tmp_path / "output_direction/btc_anytime/v1/performance"
    performance.mkdir(parents=True, exist_ok=True)
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
    (performance / "dashboard_latest.json").write_text(
        json.dumps({"dashboard_id": "dashboard", "rows": rows}), encoding="utf-8"
    )
    out = build(tmp_path)
    assert out["decision_time_utc"] == decision["decision_time_utc"]
    assert out["confidence_semantics"] == "evidence_quality_not_probability"
    assert len(out["rows"]) == 20
    assert sum(r["dimension"] == "confidence_bucket" for r in out["rows"]) == 4

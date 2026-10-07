import json
from pathlib import Path

from btc_anytime.direction.evaluation.performance_context import _latest_decision


def _write(root: Path, decision_id: str, clock: str):
    folder = root / "output_direction/btc_anytime/v1/decisions"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{decision_id}.json").write_text(
        json.dumps({"decision_id": decision_id, "decision_time_utc": clock}),
        encoding="utf-8",
    )


def test_latest_decision_orders_by_utc_not_iso_text(tmp_path):
    # Lexical ISO ordering is unsafe when equivalent timestamps use offsets.
    _write(tmp_path, "older", "2026-10-07T12:30:00Z")
    _write(tmp_path, "newer", "2026-10-07T22:00:00+09:00")
    assert _latest_decision(tmp_path)["decision_id"] == "newer"

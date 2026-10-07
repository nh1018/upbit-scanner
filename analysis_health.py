"""Consumer-time health gate for BTC and Upbit analysis artifacts.

This module does not calculate strategy signals. It only validates that the existing
production artifacts are present, parseable, internally usable, and fresh enough to
be treated as current analysis inputs.
"""
from __future__ import annotations
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "coin-analysis-health-v1"
DEFAULT_LIMITS_MIN = {"btc": 45, "upbit_a": 120, "upbit_b": 120}


def _utc_ms(now=None):
    now = now or datetime.now(timezone.utc)
    return int(now.timestamp() * 1000)


def _iso_ms(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_ms(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def _age(now_ms, observed_ms):
    return max(0, now_ms - observed_ms)


def _status(age_ms, limit_min):
    return "CURRENT" if age_ms <= limit_min * 60_000 else "STALE"


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def btc_health(repo, now_ms, limit_min):
    path = repo / "output_btc_anytime/latest_analysis.json"
    if not path.exists():
        return {"status": "MISSING", "usable_for_current_analysis": False, "path": str(path.relative_to(repo))}
    try:
        obj = _load_json(path)
        generated = _parse_iso_ms(obj["generated_at_utc"])
        direction = obj.get("direction") or {}
        entry = obj.get("entry") or {}
        freshness = obj.get("data_freshness") or {}
        tf_ok = bool(freshness) and all(
            isinstance(v, dict) and not v.get("market_stale") and v.get("feature_matches_latest_market")
            for v in freshness.values()
        )
        age = _age(now_ms, generated)
        current = _status(age, limit_min) == "CURRENT"
        usable = current and tf_ok and bool(direction.get("available"))
        return {
            "status": "CURRENT" if usable else ("STALE" if not current else "DEGRADED"),
            "usable_for_current_analysis": usable,
            "path": str(path.relative_to(repo)),
            "generated_at_utc": _iso_ms(generated),
            "age_minutes": round(age / 60_000, 3),
            "limit_minutes": limit_min,
            "direction_available": bool(direction.get("available")),
            "entry_available": bool(entry.get("available")),
            "all_timeframes_fresh_at_generation": tf_ok,
            "direction_class": direction.get("direction_class"),
            "regime": direction.get("regime"),
        }
    except Exception as exc:
        return {"status": "INVALID", "usable_for_current_analysis": False,
                "path": str(path.relative_to(repo)), "error": type(exc).__name__}


def upbit_a_health(repo, now_ms, limit_min):
    path = repo / "output/latest_scan.json"
    if not path.exists():
        return {"status": "MISSING", "usable_for_current_analysis": False, "path": str(path.relative_to(repo))}
    try:
        obj = _load_json(path)
        generated = _parse_iso_ms(obj["generated_at_kst"])
        age = _age(now_ms, generated)
        current = _status(age, limit_min) == "CURRENT"
        counts_ok = isinstance(obj.get("scanned_count"), int) and obj["scanned_count"] > 0
        binance = obj.get("binance_status")
        usable = current and counts_ok
        return {
            "status": "CURRENT" if usable else ("STALE" if not current else "DEGRADED"),
            "usable_for_current_analysis": usable,
            "path": str(path.relative_to(repo)),
            "generated_at_utc": _iso_ms(generated),
            "age_minutes": round(age / 60_000, 3),
            "limit_minutes": limit_min,
            "scanner_version": obj.get("scanner_version"),
            "scanned_count": obj.get("scanned_count"),
            "candidate_count": obj.get("candidate_count"),
            "binance_status": binance,
            "warnings": [] if binance == "OK" else ["BINANCE_CONTEXT_NOT_OK"],
        }
    except Exception as exc:
        return {"status": "INVALID", "usable_for_current_analysis": False,
                "path": str(path.relative_to(repo)), "error": type(exc).__name__}


def _latest_b_path(repo):
    root = repo / "output_upbit_b/v1/history"
    paths = sorted(root.glob("*/*.jsonl")) if root.exists() else []
    return paths[-1] if paths else None


def upbit_b_health(repo, now_ms, limit_min):
    path = _latest_b_path(repo)
    if path is None:
        return {"status": "MISSING", "usable_for_current_analysis": False,
                "path": "output_upbit_b/v1/history"}
    try:
        from upbit_b.history import validate_cycle
        manifest, records = validate_cycle(path.read_bytes())
        cutoff = int(manifest["source_cutoff"])
        age = _age(now_ms, cutoff)
        current = _status(age, limit_min) == "CURRENT"
        complete = bool(manifest.get("complete", False))
        # Partial cycles are still valid prospective evidence; expose completeness
        # separately and do not silently convert missing markets into FALSE.
        usable = current and len(records) > 0
        return {
            "status": "CURRENT" if usable else ("STALE" if not current else "DEGRADED"),
            "usable_for_current_analysis": usable,
            "path": str(path.relative_to(repo)),
            "source_cutoff_utc": _iso_ms(cutoff),
            "age_minutes": round(age / 60_000, 3),
            "limit_minutes": limit_min,
            "cycle_id": manifest.get("cycle_id"),
            "complete": complete,
            "candidate_count": manifest.get("candidate_count"),
            "record_count": len(records),
            "failure_count": manifest.get("failure_count"),
        }
    except Exception as exc:
        return {"status": "INVALID", "usable_for_current_analysis": False,
                "path": str(path.relative_to(repo)), "error": type(exc).__name__}


def build(repo=".", now=None, limits=None):
    repo = Path(repo)
    now_ms = _utc_ms(now)
    limits = {**DEFAULT_LIMITS_MIN, **(limits or {})}
    systems = {
        "btc": btc_health(repo, now_ms, limits["btc"]),
        "upbit_a": upbit_a_health(repo, now_ms, limits["upbit_a"]),
        "upbit_b": upbit_b_health(repo, now_ms, limits["upbit_b"]),
    }
    usable = [k for k, v in systems.items() if v["usable_for_current_analysis"]]
    blocked = [k for k, v in systems.items() if not v["usable_for_current_analysis"]]
    return {
        "schema_version": SCHEMA,
        "generated_at_utc": _iso_ms(now_ms),
        "semantics": "consumer_time_freshness_gate_not_strategy_signal",
        "systems": systems,
        "usable_systems": usable,
        "blocked_systems": blocked,
        "overall_status": "READY" if not blocked else ("PARTIAL" if usable else "BLOCKED"),
        "rules": {
            "stale_data_must_not_be_presented_as_current": True,
            "missing_or_invalid_data_must_not_be_invented": True,
            "strategy_parameters_modified": False,
        },
    }


def validate(obj):
    assert obj["schema_version"] == SCHEMA
    assert obj["overall_status"] in {"READY", "PARTIAL", "BLOCKED"}
    assert set(obj["systems"]) == {"btc", "upbit_a", "upbit_b"}
    for value in obj["systems"].values():
        assert value["status"] in {"CURRENT", "STALE", "DEGRADED", "MISSING", "INVALID"}
        assert isinstance(value["usable_for_current_analysis"], bool)
    return obj


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--repo", default=".")
    p.add_argument("--write", action="store_true")
    args = p.parse_args(argv)
    obj = validate(build(args.repo))
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.write:
        path = Path(args.repo) / "output_system_health/latest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()

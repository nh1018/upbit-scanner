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
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timezone required")
    return int(parsed.timestamp() * 1000)


def _age(now_ms, observed_ms):
    if observed_ms > now_ms:
        raise ValueError("future source timestamp")
    return now_ms - observed_ms


def _status(age_ms, limit_min):
    return "CURRENT" if age_ms <= limit_min * 60_000 else "STALE"


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _btc_active_evidence(repo, obj):
    """Validate stored inputs, never replay a strategy or invent a gap lookback.

    Feature V1 intentionally retains price indicator state across absent slots.
    Entry's consumed 15m path (not the all-history raw ledger) is its continuity
    domain. NEUTRAL legitimately terminates before directional path evaluation.
    """
    from btc_anytime.analysis_snapshot import validate, artifact
    from btc_anytime.features.engine import digest
    from btc_anytime.direction.engine import validate_snapshot
    from btc_anytime.entry.engine import validate as validate_entry, parameters
    validate(obj)
    def referenced(ref, namespace, key):
        path = (repo / ref["file"]).resolve()
        if not path.is_relative_to((repo / namespace).resolve()):
            raise ValueError("unexpected evidence namespace")
        import hashlib
        if hashlib.sha256(path.read_bytes()).hexdigest() != ref["file_sha256"]:
            raise ValueError("source evidence hash mismatch")
        return artifact(path, key)
    d = referenced(obj["direction"]["source_reference"], "output_direction/btc_anytime/v1/decisions", "decision_id")
    e = referenced(obj["entry"]["source_reference"], "output_entry/btc_anytime/v1/records", "integrity_hash")
    inp = referenced(obj["provenance"]["source_versions"]["entry"]["input_reference"],
                     "output_entry/btc_anytime/v1/inputs", "integrity_hash")
    validate_snapshot(d["input_snapshot"])
    ev = e["evaluation"]
    if (d["decision_id"] != obj["direction"]["decision_id"] or ev != obj["entry"]["evaluation"]
            or ev["entry_evaluation_id"] != digest({k:v for k,v in ev.items() if k != "entry_evaluation_id"})
            or inp["manifest"]["manifest_id"] != ev["input_manifest_id"]):
        raise ValueError("projected engine evidence mismatch")
    # Verify Entry against its own immutable upstream even during a legitimate
    # projection transition. Do not confuse a different latest Direction with
    # damaged Entry evidence, and do not skip integrity checks on either record.
    upstream = d
    if inp["manifest"]["direction_ref"]["decision_id"] != d["decision_id"]:
        ref = inp["manifest"]["direction_ref"]
        path = (repo / ref["path"]).resolve()
        if not path.is_relative_to((repo / "output_direction/btc_anytime/v1/decisions").resolve()):
            raise ValueError("unexpected upstream namespace")
        upstream = artifact(path, "decision_id")
        validate_snapshot(upstream["input_snapshot"])
    if (e["upstream_direction_decision_id"] != upstream["decision_id"]
            or ev["upstream_direction_decision_id"] != upstream["decision_id"]):
        raise ValueError("Entry upstream evidence mismatch")
    items = validate_entry(inp["manifest"], upstream, _parse_iso_ms(ev["evaluation_time_utc"]), parameters())
    reasons = []
    if upstream["decision_id"] != d["decision_id"]:
        reasons.append("BTC.DIRECTION_ENTRY_TRANSITION")
    if obj["entry"].get("matches_latest_15m") is not True:
        reasons.append("BTC.ENTRY_LAGS_MARKET")
    for tf, selected in d["input_snapshot"]["timeframes"].items():
        r = selected.get("record")
        f = obj["feature"]["timeframes"].get(tf)
        if not r or not f:
            reasons.append(tf + ":CURRENT_FEATURE_EVIDENCE_UNAVAILABLE")
            continue
        if (f["values"] != r["features"] or f["quality"] != r["feature_quality"]
                or f["candle_open_ms"] != r["time"]):
            raise ValueError("projected Feature evidence mismatch")
        if not all(r["input_validity"].get(k) is True for k in ("boundary", "price", "volume")):
            reasons.append(tf + ":CURRENT_FEATURE_INVALID")
        component = d["timeframes"][tf]
        if component["stale_inputs"] or any(component["components"].get(k) is None for k in ("trend", "structure", "momentum")):
            reasons.append(tf + ":CURRENT_DIRECTION_COMPONENT_UNAVAILABLE")
    history = [x for x in items if x["timeframe"] == "15m"]
    gaps = sum(b["time"] - a["time"] != 900000 for a,b in zip(history, history[1:]))
    neutral = upstream["direction_class"] == "NEUTRAL" and ev["entry_state"] == "NO_ENTRY"
    path_veto = ev["entry_state"] == "NO_ENTRY" and any(
        reason in ev["reason_codes"] for reason in ("ENTRY.AUTHORIZATION_VETO", "ENTRY.SETUP_INVALIDATED"))
    if gaps and not (neutral or path_veto):
        reasons.append("ENTRY:ACTIVE_PATH_GAP")
    if ev["execution_status"] != "EVALUATED":
        reasons.append("ENTRY:CURRENT_EVALUATION_UNAVAILABLE")
    return {"valid": not reasons, "blocking_reasons": reasons,
            "entry_path_gap_intervals": gaps, "entry_path_rows": len(history),
            "continuity_basis": "stored_feature_contract_and_consumed_entry_manifest",
            "neutral_no_entry_path_not_required": neutral}


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
        generation_tf_ok = set(freshness) == {"15m", "1h", "4h", "1d"} and all(
            isinstance(v, dict) and v.get("market_stale") is False and v.get("feature_matches_latest_market") is True
            for v in freshness.values()
        )
        age = _age(now_ms, generated)
        current = _status(age, limit_min) == "CURRENT"
        # Projection regeneration must not rejuvenate an old engine decision.
        decision_ms = _parse_iso_ms(direction["decision_time_utc"])
        entry_ms = _parse_iso_ms(entry["generated_at_utc"])
        decision_limit = obj["provenance"]["snapshot_policy"]["decision_max_age_ms"]
        if type(decision_limit) is not int or decision_limit <= 0:
            raise ValueError("invalid snapshot freshness policy")
        allowance = obj["provenance"]["snapshot_policy"]["market_publication_allowance_ms"]
        if type(allowance) is not int or allowance < 0:
            raise ValueError("invalid publication allowance")
        durations = {"15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000}
        market_deadlines = []
        integrity_warnings = {}
        integrity_ok = True
        for tf, duration in durations.items():
            candle_open = _parse_iso_ms(freshness[tf]["feature_candle_open_utc"])
            if candle_open % duration or candle_open + duration > now_ms:
                raise ValueError("invalid completed candle boundary")
            market_deadlines.append(candle_open + 2 * duration + allowance)
            integrity = obj["market_data"][tf]["integrity"]
            integrity_warnings[tf] = dict(integrity)
            if any(type(integrity[k]) is not int or integrity[k] < 0 for k in
                   ("duplicate", "missing_slots", "abnormal_intervals", "ohlcv_invalid")):
                raise ValueError("invalid integrity counters")
            integrity_ok = integrity_ok and integrity["duplicate"] == 0 and integrity["ohlcv_invalid"] == 0
        active = _btc_active_evidence(repo, obj)
        consumer_tf_ok = generation_tf_ok and now_ms <= min(market_deadlines)
        valid_until = min(generated + limit_min * 60000, decision_ms + decision_limit,
                          entry_ms + decision_limit, *market_deadlines)
        engine_current = (_age(now_ms, decision_ms) <= decision_limit
                          and _age(now_ms, entry_ms) <= decision_limit)
        aligned = (direction.get("stale") is False and entry.get("stale") is False
                   and entry.get("matches_snapshot_direction") is True
                   and entry.get("matches_latest_15m") is True)
        usable = (current and engine_current and consumer_tf_ok and integrity_ok and active["valid"] and aligned
                  and direction.get("available") is True and entry.get("available") is True)
        return {
            "status": "CURRENT" if usable else ("STALE" if not current or not engine_current else "DEGRADED"),
            "usable_for_current_analysis": usable,
            "path": str(path.relative_to(repo)),
            "generated_at_utc": _iso_ms(generated),
            "age_minutes": round(age / 60_000, 3),
            "limit_minutes": limit_min,
            "valid_until_utc": _iso_ms(valid_until),
            "direction_decision_at_utc": _iso_ms(decision_ms),
            "entry_generated_at_utc": _iso_ms(entry_ms),
            "direction_available": bool(direction.get("available")),
            "entry_available": bool(entry.get("available")),
            "all_timeframes_fresh_at_generation": generation_tf_ok,
            "all_timeframes_fresh_at_consumer_time": consumer_tf_ok,
            "timeframes_valid_until_utc": _iso_ms(min(market_deadlines)),
            "active_evidence": active,
            "reason_codes": active["blocking_reasons"],
            "historical_integrity": integrity_warnings,
            "snapshot_warnings": obj.get("warnings", []),
            "snapshot_provenance": obj["provenance"],
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
        counts_ok = type(obj.get("scanned_count")) is int and obj["scanned_count"] > 0
        binance = obj.get("binance_status")
        usable = current and counts_ok
        return {
            "status": "CURRENT" if usable else ("STALE" if not current else "DEGRADED"),
            "usable_for_current_analysis": usable,
            "path": str(path.relative_to(repo)),
            "generated_at_utc": _iso_ms(generated),
            "age_minutes": round(age / 60_000, 3),
            "limit_minutes": limit_min,
            "valid_until_utc": _iso_ms(generated + limit_min * 60000),
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
        complete = manifest["completeness"] == "COMPLETE"
        # Partial cycles are still valid prospective evidence; expose completeness
        # separately and do not silently convert missing markets into FALSE.
        usable = current and complete and len(records) > 0
        return {
            "status": "CURRENT" if usable else ("STALE" if not current else "DEGRADED"),
            "usable_for_current_analysis": usable,
            "path": str(path.relative_to(repo)),
            "source_cutoff_utc": _iso_ms(cutoff),
            "valid_until_utc": _iso_ms(cutoff + limit_min * 60000),
            "age_minutes": round(age / 60_000, 3),
            "limit_minutes": limit_min,
            "cycle_id": manifest.get("cycle_id"),
            "complete": complete,
            "candidate_count": manifest.get("candidate_count"),
            "record_count": len(records),
            "failure_count": manifest["failed"],
            "unattempted_count": manifest["unattempted"],
            "insufficient_count": manifest["insufficient"],
            "completeness": manifest["completeness"],
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
            "current_status_is_as_of_generated_at_only": True,
            "consumer_must_rebuild_at_request_time": True,
        },
    }


def at_consumer_time(obj, now=None):
    """Conservatively expire a cached health snapshot; never upgrade old blocked inputs.

    Rebuild from current source artifacts to detect later upstream changes.
    Older snapshots without explicit expiry cannot authorize current analysis.
    """
    from copy import deepcopy
    result = deepcopy(validate(obj))
    now_ms = _utc_ms(now)
    _age(now_ms, _parse_iso_ms(result["generated_at_utc"]))
    for value in result["systems"].values():
        if "all_timeframes_fresh_at_generation" in value:
            market_deadline = value.get("timeframes_valid_until_utc")
            value["all_timeframes_fresh_at_consumer_time"] = bool(
                value["all_timeframes_fresh_at_generation"] and market_deadline
                and now_ms <= _parse_iso_ms(market_deadline))
        if value["usable_for_current_analysis"]:
            deadline = value.get("valid_until_utc")
            if deadline is None:
                value.update(status="DEGRADED", usable_for_current_analysis=False)
            elif now_ms > _parse_iso_ms(deadline):
                value.update(status="STALE", usable_for_current_analysis=False)
    result["evaluated_at_utc"] = _iso_ms(now_ms)
    result["usable_systems"] = [k for k,v in result["systems"].items() if v["usable_for_current_analysis"]]
    result["blocked_systems"] = [k for k,v in result["systems"].items() if not v["usable_for_current_analysis"]]
    result["overall_status"] = "READY" if not result["blocked_systems"] else "PARTIAL" if result["usable_systems"] else "BLOCKED"
    return result


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

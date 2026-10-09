"""Append-only research observations; not entry or execution signals."""
import json
import re
import os
import tempfile
from pathlib import Path
from upbit_b.feature_contracts import digest, dumps
from upbit_b.contracts import DURATIONS
from .research_score import VERSION, PARAMETER_SHA256

SCHEMA = "upbit-c-prospective-research-1"
HOUR = DURATIONS["1h"]


def validate_signal(signal):
    if not isinstance(signal, dict) or signal.get("schema_version") != SCHEMA or signal.get("activation") != "RESEARCH_ONLY":
        raise ValueError("unrecognized research observation")
    for field in ("observed_at_ms", "trigger_close_ms", "proxy_anchor_open_ms"):
        if type(signal.get(field)) is not int:
            raise ValueError("invalid research clock")
    if (signal["observed_at_ms"] < signal["trigger_close_ms"]
            or signal["proxy_anchor_open_ms"] != (signal["observed_at_ms"] // HOUR + 1) * HOUR):
        raise ValueError("invalid prospective anchor")
    if signal.get("score", {}).get("research_setup") != "PASS":
        raise ValueError("unqualified research observation")
    if signal["score"].get("parameter_sha256") != PARAMETER_SHA256:
        raise ValueError("unknown parameter")
    if (signal.get("score_engine_version") != VERSION or signal.get("parameter_sha256") != PARAMETER_SHA256
            or signal.get("market") != signal.get("reference_candle", {}).get("instrument")
            or signal.get("trigger_close_ms") != signal.get("reference_candle", {}).get("close_ms")
            or signal.get("reference_price") != signal.get("reference_candle", {}).get("close")
            or not signal.get("reference_candle", {}).get("completed")):
        raise ValueError("invalid reference/engine contract")
    identity = {k: signal[k] for k in ("market", "score_engine_version", "parameter_sha256", "source_inputs")}
    if signal.get("signal_id") != digest(identity):
        raise ValueError("research identity mismatch")
    return signal


def signals(report):
    """Only new actual PASS observations; no historical signal reconstruction."""
    for row in report["results"]:
        score = row.get("score")
        if not score or score["research_setup"] != "PASS":
            continue
        observed = max(report["finished_at_ms"], row["collected_at_ms"])
        identity = {"market": row["market"], "score_engine_version": VERSION,
                    "parameter_sha256": PARAMETER_SHA256,
                    "source_inputs": {tf: row["timeframes"][tf]["source_input_sha256"] for tf in ("1h", "4h", "1d")}}
        latest = row["timeframes"]["1h"]["candles"][-1]
        signal = {**identity, "schema_version": SCHEMA, "activation": "RESEARCH_ONLY",
                  "signal_id": digest(identity), "scan_id": report["scan_id"],
                  "observed_at_ms": observed, "trigger_close_ms": latest["close_ms"],
                  "reference_price": latest["close"], "reference_type": "COMPLETED_1H_CLOSE_DIAGNOSTIC",
                  "reference_candle": latest, "score": score,
                  "proxy_anchor_open_ms": (observed // HOUR + 1) * HOUR,
                  "outcome_contract": "NEXT_1H_OPEN_PROXY_GROSS_LONG_RESEARCH_1",
                  "source_evidence": {tf: row["timeframes"][tf]["source_evidence"] for tf in ("1h", "4h", "1d")}}
        yield validate_signal(signal)


def write_once(root, category, identifier, payload, replay=False):
    if category not in ("scans", "signals", "evaluations") or not re.fullmatch("[0-9a-f]{64}", identifier):
        raise ValueError("unsafe research record path")
    path = Path(root) / category / (identifier + ".json")
    envelope = {"record_sha256": digest(payload), "record": payload}
    data = dumps(envelope) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
            temp = f.name
            f.write(data.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.link(temp, path)  # atomic publication; cannot replace existing records
        return "CREATED"
    except FileExistsError:
        old = read_record(path)
        if dumps(old) == dumps(payload):
            return "REPLAY_NOOP"
        if replay and category == "signals":
            validate_signal(old)
            comparable = lambda s: {k:v for k,v in s["score"].items() if k != "sources"}
            if old["signal_id"] == payload["signal_id"] and comparable(old) == comparable(payload):
                # Preserve first availability; repeat retrieval must not move its anchor.
                return "REPLAY_NOOP"
        raise ValueError("append-only research record conflict")
    finally:
        if temp is not None:
            os.unlink(temp)


def read_record(path):
    envelope = json.loads(Path(path).read_text(encoding="utf-8"))
    if digest(envelope["record"]) != envelope["record_sha256"]:
        raise ValueError("research record hash mismatch")
    return envelope["record"]


def record_scan(root, report):
    counts = {"scan": write_once(root, "scans", report["scan_id"], report), "signals_created": 0, "signals_replayed": 0}
    for signal in signals(report):
        status = write_once(root, "signals", signal["signal_id"], signal, replay=True)
        counts["signals_created" if status == "CREATED" else "signals_replayed"] += 1
    return counts

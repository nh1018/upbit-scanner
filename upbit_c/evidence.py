"""Upbit C crash/rebound research evidence V0 (NOT an activated strategy).

Uses only completed-candle Upbit feature snapshots already calculated by the B
feature library. Deliberately no C score, thresholds, ranking or buy signals.
"""
from decimal import Decimal, InvalidOperation

from upbit_b.feature_contracts import digest

SCHEMA_VERSION = "upbit-c-rebound-evidence-0"
FIELDS = (
    "return1_pct", "return3_pct", "return5_pct", "return10_pct",
    "return3_acceleration_pp", "close_location", "signed_body_ratio",
    "quote_ratio_ma20", "quote_ratio_median20", "quote_recent3_vs_prior20",
    "distance_to_confirmed_low_atr", "low_structure", "breakdown20",
    "ema20_upward_recross", "atr_pct",
)
RESEARCH_GROUPS = {
    "selloff": ("return3_pct", "return5_pct", "return10_pct", "atr_pct"),
    "capitulation": ("quote_ratio_ma20", "quote_ratio_median20", "close_location"),
    "deceleration": ("return3_acceleration_pp", "return1_pct"),
    "defense": ("distance_to_confirmed_low_atr", "low_structure", "breakdown20"),
    "recovery": ("signed_body_ratio", "ema20_upward_recross", "quote_recent3_vs_prior20"),
}


def observe(snapshot):
    if not isinstance(snapshot, dict):
        raise TypeError("feature snapshot object required")
    meta = snapshot.get("metadata") or {}
    if meta.get("provider") != "UPBIT" or meta.get("timeframe") not in ("1h", "4h", "1d"):
        raise ValueError("Upbit completed-candle feature snapshot required")
    cutoff = meta.get("source_cutoff_ms")
    candle_close = meta.get("source_candle_close_ms")
    if not isinstance(cutoff, int) or isinstance(cutoff, bool) or candle_close != cutoff:
        raise ValueError("stale or unverified completed candle")
    if meta.get("source_status") != "AVAILABLE" or not meta.get("input_sha256") or not meta.get("evidence"):
        raise ValueError("verified raw-source evidence required")
    features = snapshot.get("features") or {}
    readiness = snapshot.get("readiness") or {}
    values = {}
    missing = {}
    for field in FIELDS:
        if readiness.get(field) != "READY" or features.get(field) is None:
            values[field] = None
            missing[field] = readiness.get(field, "MISSING")
            continue
        raw = features[field]
        if field in ("low_structure", "breakdown20", "ema20_upward_recross"):
            if field == "low_structure":
                if raw not in ("HL", "LL", "EQ"):
                    raise ValueError("unexpected structure category")
            elif not isinstance(raw, bool):
                raise ValueError("boolean feature required")
            values[field] = raw
        else:
            try:
                value = Decimal(raw)
            except (InvalidOperation, TypeError):
                raise ValueError("invalid feature number") from None
            if not value.is_finite():
                raise ValueError("non-finite feature")
            values[field] = str(value)
    groups = {name: {"available": sum(values[k] is not None for k in keys), "total": len(keys)}
              for name, keys in RESEARCH_GROUPS.items()}
    return {
        "schema_version": SCHEMA_VERSION,
        "market": meta.get("instrument"),
        "timeframe": meta["timeframe"],
        "source_cutoff_ms": cutoff,
        "source_input_sha256": meta["input_sha256"],
        "source_measurement_sha256": snapshot.get("measurement_sha256"),
        "groups": groups,
        "values": values,
        "missing": missing,
        "score": None,
        "candidate": "NOT_EVALUATED",
        "activation": "RESEARCH_ONLY",
        "research_contract_sha256": digest({"fields": FIELDS, "groups": RESEARCH_GROUPS}),
    }

"""Exclusive append-only decisions; never overwrite raw or production outputs."""
import json
import os
from pathlib import Path
from btc_anytime.features.engine import digest
from .engine import SCHEMA
from decimal import Decimal
from btc_anytime.integrity import DURATIONS,utc_ms

NAMESPACE="output_direction/btc_anytime/v1"


def append_decision(repo,decision):
    required={"direction_engine_version","parameter_version","parameter_hash","snapshot_id","decision_time_utc",
              "direction_score","direction_class","directional_bias","confidence","confidence_semantics","regime",
              "timeframes","feature_generation_times_ms","reason_codes","input_manifest_ids","availability_evidence_refs"}
    if not required.issubset(decision):raise ValueError("incomplete decision schema")
    if decision.get("schema_version")!=SCHEMA or decision.get("decision_id")!=digest({k:v for k,v in decision.items() if k!="decision_id"}):
        raise ValueError("decision contract/hash mismatch")
    utc_ms(decision["decision_time_utc"])
    if decision["direction_class"] not in ("LONG","SHORT","STRONG_LONG","STRONG_SHORT","NEUTRAL"):raise ValueError("invalid direction class")
    if decision["regime"] not in ("ALIGNED_TREND","PULLBACK_CANDIDATE","POSSIBLE_REVERSAL","TRANSITION","CHOP_CANDIDATE","INSUFFICIENT_EVIDENCE","MIXED"):raise ValueError("invalid regime")
    if set(decision["timeframes"])!=set(DURATIONS):raise ValueError("decision timeframe schema")
    conf=Decimal(decision["confidence"])
    score=Decimal(decision["direction_score"]) if decision["direction_score"] is not None else None
    if not conf.is_finite() or not 0<=conf<=1 or score is not None and (not score.is_finite() or not -1<=score<=1):raise ValueError("decision numeric schema")
    root=(repo/NAMESPACE).resolve()
    if root!=repo.resolve()/NAMESPACE:raise ValueError("decision namespace symlink refused")
    folder=root/"decisions"
    folder.mkdir(parents=True,exist_ok=True)
    if folder.resolve()!=root/"decisions":raise ValueError("decision directory symlink refused")
    path=folder/(decision["decision_id"]+".json")
    if path.is_symlink():raise ValueError("decision file symlink refused")
    body=json.dumps(decision,sort_keys=True,indent=2)+"\n"
    if path.exists():
        if path.read_text(encoding="utf-8")!=body:raise ValueError("existing decision modified")
        return path
    with path.open("x",encoding="utf-8") as handle:
        handle.write(body);handle.flush();os.fsync(handle.fileno())
    return path

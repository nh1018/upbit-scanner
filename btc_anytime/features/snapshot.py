"""Strict as-of snapshot: no late backfill or unconfirmed pivot leakage."""
from copy import deepcopy
from btc_anytime.integrity import DURATIONS, iso
from .engine import digest, PARAMETERS, SCHEMA_VERSION, ALGORITHM_VERSION


def synchronize(series, decision_time_ms, decision_event_ref=None):
    if isinstance(decision_time_ms,bool) or not isinstance(decision_time_ms,int) or decision_time_ms<0:
        raise ValueError("decision time must be UTC epoch milliseconds")
    selected={}
    for tf,records in series.items():
        if tf not in DURATIONS or any(r["timeframe"]!=tf or r["schema_version"]!=SCHEMA_VERSION or
                                     r["algorithm_version"]!=ALGORITHM_VERSION or r["parameter_hash"]!=digest(PARAMETERS)
                                     for r in records):
            raise ValueError("incompatible feature contract")
    for tf,duration in DURATIONS.items():
        eligible=[r for r in series.get(tf,[]) if r["available_at_ms"] is not None
                  and r["available_at_ms"]<=decision_time_ms and r["time"]+duration<=decision_time_ms
                  and r["input_validity"]["boundary"]]
        if not eligible:
            selected[tf]={"record":None,"reason":"no_available_completed_candle"}
            continue
        record=deepcopy(max(eligible,key=lambda r:(r["time"],r["available_at_ms"],r.get("dataset_manifest_id",""))))
        if "result_hash" in record:record["input_feature_result_hash"]=record.pop("result_hash")
        for name,q in record["feature_quality"].items():
            if q["available_at_ms"] is None or q["available_at_ms"]>decision_time_ms:
                record["features"][name]=None
                q["ready"]=False;q["null_reason"]="dependency_not_available"
        age=decision_time_ms-(record["time"]+duration)
        stale=age>duration+PARAMETERS["freshness_allowance_ms"]
        selected[tf]={"record":record,"age_ms":age,"stale":stale,
                      "ready":not stale and any(q["ready"] for q in record["feature_quality"].values())}
    result={"schema_version":SCHEMA_VERSION,"algorithm_version":ALGORITHM_VERSION,
            "decision_time_utc":iso(decision_time_ms),"decision_event_ref":deepcopy(decision_event_ref),
            "input_manifests":sorted({v["record"]["dataset_manifest_id"] for v in selected.values()
                                      if v["record"] and v["record"].get("dataset_manifest_id")}),"timeframes":selected}
    result["snapshot_id"]=digest(result)
    return result

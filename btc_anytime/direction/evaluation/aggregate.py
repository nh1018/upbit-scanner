"""Descriptive cohorts only. Non-overlapping does not imply independence."""
from collections import defaultdict
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from statistics import median
from btc_anytime.features.engine import digest,encode
from .engine import bucket,parameters


def non_overlapping(labels):
    selected=[];endpoint=None
    for label in sorted(labels,key=lambda x:(x["anchor_price_time_ms"],x["decision_ref"]["decision_id"])):
        if endpoint is None or label["anchor_price_time_ms"]>=endpoint:
            selected.append(label);endpoint=label["endpoint_ms"]
    return selected


def stats(labels):
    counts={s:sum(x["status"]==s for x in labels) for s in ("PENDING","MATURED","PARTIAL","SOURCE_MISSING","SOURCE_CONFLICT","INVALID")}
    valid=[x for x in labels if x["return_status"]=="MATURED" and x.get("metrics")]
    directional=[x for x in valid if x["metrics"]["directional_return_pct"] is not None]
    def summary(field,items):
        values=sorted(Decimal(x["metrics"][field]) for x in items if x["metrics"].get(field) is not None)
        if not values:return {"count":0,"mean":None,"median":None}
        def quantile(q):
            index=Decimal(len(values)-1)*Decimal(q);lo=int(index);hi=min(lo+1,len(values)-1)
            return values[lo]+(values[hi]-values[lo])*(index-lo)
        return encode({"count":len(values),"mean":sum(values)/len(values),"median":median(values),
                       "p05":quantile("0.05"),"p95":quantile("0.95"),
                       "positive_count":sum(v>0 for v in values),"negative_count":sum(v<0 for v in values)})
    complete=[x for x in valid if x["excursion_status"]=="MATURED"]
    partial=[x for x in valid if x["excursion_status"]=="PARTIAL"]
    hit=[x for x in directional if x["metrics"]["minimum_move_hit"] is not None]
    neutral=[x for x in valid if x["cohort"]["direction"]=="NEUTRAL"]
    def rate(field):
        available=[x["metrics"][field] for x in neutral if x["metrics"].get(field) is not None]
        return {"count":len(available),"rate":encode(Decimal(sum(available))/len(available)) if available else None}
    ratios=[Decimal(x["metrics"]["mfe_pct"])/Decimal(x["metrics"]["mae_pct"]) for x in complete if x["metrics"].get("mae_pct") is not None and Decimal(x["metrics"]["mae_pct"])>0]
    return {"count":len(labels),"states":counts,"return_matured_count":len(valid),"directional_count":len(directional),
            "directional_return_pct":summary("directional_return_pct",directional),"raw_return_pct":summary("raw_return_pct",valid),
            "neutral_absolute_return_pct":summary("neutral_absolute_return_pct",valid),
            "neutral_realized_range_pct":summary("neutral_realized_range_pct",complete),
            "neutral_max_abs_excursion_pct":summary("neutral_max_abs_excursion_pct",complete),
            "up_excursion_pct":summary("up_excursion_pct",complete),"down_excursion_pct":summary("down_excursion_pct",complete),
            "neutral_endpoint_within_band":rate("neutral_endpoint_within_band"),"neutral_path_within_band":rate("neutral_path_within_band"),
            "neutral_up_band_crossed":rate("neutral_up_band_crossed"),"neutral_down_band_crossed":rate("neutral_down_band_crossed"),
            "sign_hit_rate":encode(Decimal(sum(x["metrics"]["sign_hit"]=="HIT" for x in directional))/len(directional)) if directional else None,
            "flat_count":sum(x["metrics"]["sign_hit"]=="FLAT" for x in directional),
            "minimum_move_hit_rate":encode(Decimal(sum(x["metrics"]["minimum_move_hit"] for x in hit))/len(hit)) if hit else None,
            "minimum_move_count":len(hit),"complete_mfe_pct":summary("mfe_pct",complete),"complete_mae_pct":summary("mae_pct",complete),
            "partial_mfe_lower_bound_pct":summary("mfe_pct",partial),"partial_mae_lower_bound_pct":summary("mae_pct",partial),
            "mfe_mae_ratio_mean":encode(sum(ratios)/len(ratios)) if ratios else None,
            "zero_mae_count":sum(x["metrics"].get("mae_pct") is not None and Decimal(x["metrics"]["mae_pct"])==0 for x in complete)}


def aggregate(labels):
    p=parameters();groups=defaultdict(list)
    for x in labels:
        if "cohort" not in x or "anchor_price_time_ms" not in x:continue
        c=x["cohort"];contract=x["contract"]
        base={"versions":c["versions"],"anchor":contract["anchor"],"horizon":contract["horizon_hours"],"source_contract":contract["source_contract"]}
        dimensions={"all":"ALL","direction_class":c["direction"],"directional_bias":c["directional_bias"],
                    "confidence_bucket":c["confidence_bucket"],"regime":c["regime"],
                    "direction_regime":c["direction"]+":"+c["regime"],
                    "daily_cohort":str(x["anchor_price_time_ms"]//86400000),"4h_cohort":str(x["anchor_price_time_ms"]//14400000)}
        for tf,r in c["tf_components"].items():
            dimensions[tf+"_score"]=bucket(r["score"],p["component_edges"])
            dimensions[tf+"_oi_status"]=r["oi_status"]
            dimensions[tf+"_oi_change_available"]="AVAILABLE" if r["components"].get("derivatives") is not None else "UNAVAILABLE"
            for k,v in r["components"].items():
                dimensions[tf+"_"+k]=(v.get("state","UNAVAILABLE") if isinstance(v,dict) else
                    "NULL" if v is None else "ZERO" if Decimal(v)==0 else "POSITIVE" if Decimal(v)>0 else "NEGATIVE")
        for dimension,value in dimensions.items():
            key=json_key({**base,"dimension":dimension,"value":value});groups[key].append(x)
    report={"schema_version":"btc-direction-evaluation-report-v1","evaluator_version":"1.0.0",
            "input_payload_hashes":sorted(digest(x) for x in labels),
            "total_count":len(labels),"total_states":{s:sum(x["status"]==s for x in labels) for s in ("PENDING","MATURED","PARTIAL","SOURCE_MISSING","SOURCE_CONFLICT","INVALID")},"cohorts":[]}
    with localcontext() as ctx:
        ctx.prec=50
        ctx.rounding=ROUND_HALF_EVEN
        for key,items in sorted(groups.items()):
            selected=non_overlapping(items)
            warnings=["NONOVERLAPPING_NOT_PROOF_OF_INDEPENDENCE"]
            if sum(x["return_status"]=="MATURED" for x in items)<p["sparse_warning_count"]:warnings.append("SPARSE_SAMPLE")
            if sum(x["return_status"]=="MATURED" for x in selected)<p["sparse_warning_count"]:warnings.append("SPARSE_NONOVERLAPPING_SAMPLE")
            if len(selected)<len(items):warnings.append("OVERLAPPING_OBSERVATIONS")
            report["cohorts"].append({"partition":key,"all_observations":stats(items),"non_overlapping":stats(selected),
                                      "selected_evaluation_keys":[x["evaluation_key"] for x in selected],"warnings":warnings})
    report["report_id"]=digest(report)
    return report


def json_key(value):
    import json
    return json.dumps(value,sort_keys=True,separators=(",",":"))

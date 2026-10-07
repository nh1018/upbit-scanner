"""Read-only historical performance context for the current BTC Direction decision.

The current Direction decision is never changed by this module. Context is
descriptive and comes only from the already-published performance dashboard.
"""
import argparse,json
from pathlib import Path
from btc_anytime.features.engine import digest
from btc_anytime.integrity import utc_ms
from decimal import Decimal

SCHEMA_VERSION="btc-direction-performance-context-v1"
ANCHOR="NEXT_15M_OPEN_PROXY"
HORIZONS=(1,4,12,24)

def _latest_decision(repo):
    root=repo/"output_direction/btc_anytime/v1/decisions"
    files=list(root.glob("*.json"))
    if not files: raise FileNotFoundError("no Direction decisions")
    decisions=[]
    for path in files:
        x=json.loads(path.read_text(encoding="utf-8"))
        d=x.get("payload",x)
        clock=_pick(d,"decision_time_utc","generated_at_utc")
        if clock is None: raise ValueError(f"Direction decision missing time: {path.name}")
        decisions.append((utc_ms(clock),str(_pick(d,"decision_id","id") or path.stem),d))
    return max(decisions,key=lambda item:(item[0],item[1]))[2]

def _pick(obj,*keys):
    for k in keys:
        if k in obj and obj[k] is not None:return obj[k]
    return None

def build(repo:Path):
    d=_latest_decision(repo)
    direction=_pick(d,"direction","direction_class","decision")
    confidence=_pick(d,"confidence","direction_confidence")
    regime=_pick(d,"regime","market_regime")
    confidence_semantics=_pick(d,"confidence_semantics")
    dash=json.loads((repo/"output_direction/btc_anytime/v1/performance/dashboard_latest.json").read_text(encoding="utf-8"))
    if dash.get("schema_version")!="btc-direction-performance-dashboard-v1": raise ValueError("performance dashboard schema mismatch")
    if dash.get("dashboard_id")!=digest({k:v for k,v in dash.items() if k!="dashboard_id"}): raise ValueError("performance dashboard hash mismatch")
    rows=[]
    targets=[("all","ALL")]
    if confidence is not None:
        value=Decimal(str(confidence))
        bucket=None
        for row in dash.get("rows",[]):
            if row.get("dimension")!="confidence_bucket": continue
            label=str(row.get("value",""))
            try:
                lo_s,hi_s=label.strip("[]()").split(",")
                lo,hi=Decimal(lo_s),Decimal(hi_s)
                if value>=lo and (value<hi or label.endswith("]") and value<=hi):
                    bucket=label; break
            except (ValueError,ArithmeticError):
                continue
        if bucket: targets.append(("confidence_bucket",bucket))
    if direction: targets.append(("direction_class",str(direction)))
    if direction and regime: targets.append(("direction_regime",f"{direction}:{regime}"))
    if regime: targets.append(("regime",str(regime)))
    for h in HORIZONS:
        for dim,val in targets:
            hit=next((r for r in dash.get("rows",[]) if r.get("horizon")==h and r.get("dimension")==dim and r.get("value")==val),None)
            if hit: rows.append(hit)
    out={"schema_version":SCHEMA_VERSION,"dashboard_id":dash["dashboard_id"],
         "decision_id":_pick(d,"decision_id","id"),"direction":direction,
         "confidence":confidence,"confidence_semantics":confidence_semantics,"regime":regime,
         "decision_time_utc":_pick(d,"decision_time_utc","generated_at_utc"),"anchor":ANCHOR,
         "interpretation":"DESCRIPTIVE_ONLY_DO_NOT_OVERRIDE_DIRECTION","rows":rows}
    out["context_id"]=digest(out)
    return out

def write(repo:Path):
    x=build(repo); base=repo/"output_direction/btc_anytime/v1/performance"; base.mkdir(parents=True,exist_ok=True)
    p=base/"context_latest.json"; s=json.dumps(x,sort_keys=True,separators=(",",":"))+"\n"
    if not p.exists() or p.read_text(encoding="utf-8")!=s:p.write_text(s,encoding="utf-8")
    return {"status":"READY","schema_version":SCHEMA_VERSION,"context_id":x["context_id"],
            "direction":x["direction"],"regime":x["regime"],"row_count":len(x["rows"]),
            "latest":str(p.relative_to(repo))}
def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument("--repo",type=Path,default=Path.cwd());ap.add_argument("--write",action="store_true")
    a=ap.parse_args();x=write(a.repo.resolve()) if a.write else build(a.repo.resolve());print(json.dumps(x,sort_keys=True,indent=2))
if __name__=="__main__":main()

"""Small operational dashboard derived only from Performance Summary V1.

No Direction signal, label, return, MFE or MAE is recomputed here.
"""
import argparse, json
from pathlib import Path
from btc_anytime.features.engine import digest

SCHEMA_VERSION="btc-direction-performance-dashboard-v1"
ANCHOR="NEXT_15M_OPEN_PROXY"
DIMENSIONS={"all","direction_class","confidence_bucket","regime","direction_regime"}

def slim_stats(s):
    d={"count":s.get("count")}
    for k in ("sign_hit_rate","minimum_move_hit_rate","mfe_mae_ratio_mean"):
        if k in s: d[k]=s[k]
    for k in ("directional_return_pct","raw_return_pct","neutral_absolute_return_pct"):
        v=s.get(k)
        if isinstance(v,dict):
            d[k]={x:v.get(x) for x in ("count","mean","median","p05","p95") if x in v}
    return d

def build(repo:Path):
    src=repo/"output_direction/btc_anytime/v1/performance/summary_latest.json"
    s=json.loads(src.read_text(encoding="utf-8"))
    rows=[]
    for r in s.get("cohorts",[]):
        p=r.get("partition",{})
        if p.get("anchor")!=ANCHOR or p.get("dimension") not in DIMENSIONS: continue
        rows.append({
            "horizon":p.get("horizon"),"dimension":p.get("dimension"),"value":p.get("value"),
            "all":slim_stats(r.get("all_observations",{})),
            "non_overlapping":slim_stats(r.get("non_overlapping",{})),
            "warnings":r.get("warnings",[]),
        })
    rows.sort(key=lambda x:(x["horizon"],x["dimension"],str(x["value"])))
    out={"schema_version":SCHEMA_VERSION,"source_summary_id":s["summary_id"],
         "input_label_count":s["input_label_count"],"anchor":ANCHOR,"rows":rows}
    out["dashboard_id"]=digest(out)
    return out

def write(repo:Path):
    d=build(repo); base=repo/"output_direction/btc_anytime/v1/performance"
    text=json.dumps(d,sort_keys=True,separators=(",",":"))+"\n"
    latest=base/"dashboard_latest.json"
    if not latest.exists() or latest.read_text(encoding="utf-8")!=text: latest.write_text(text,encoding="utf-8")
    return {"status":"READY","schema_version":SCHEMA_VERSION,"dashboard_id":d["dashboard_id"],
            "input_label_count":d["input_label_count"],"row_count":len(d["rows"]),
            "latest":str(latest.relative_to(repo))}

def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument("--repo",type=Path,default=Path.cwd()); ap.add_argument("--write",action="store_true")
    a=ap.parse_args(); x=write(a.repo.resolve()) if a.write else build(a.repo.resolve())
    print(json.dumps(x,sort_keys=True,indent=2))
if __name__=="__main__": main()

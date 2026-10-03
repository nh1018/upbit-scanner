"""Read-only CLI: python -B -m btc_anytime.validate_history --cross-check."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from btc_anytime.integrity import DURATIONS,load_entries
from btc_anytime.source_audit import audit

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path(__file__).resolve().parent.parent/"data_market"/"btc_anytime")
    parser.add_argument("--timeframe",choices=list(DURATIONS),action="append")
    parser.add_argument("--cross-check",action="store_true")
    parser.add_argument("--summary",action="store_true",help="omit row-level detail from stdout")
    parser.add_argument("--official-cross-check",action="store_true",help="verify backfill provenance and require official 15m-to-HTF equality; public network reads")
    args=parser.parse_args();loaded={};errors={}
    for tf in args.timeframe or DURATIONS:loaded[tf],errors[tf]=load_entries(args.root,tf)
    if args.cross_check or args.official_cross_check:
        for tf in DURATIONS:
            if tf not in loaded:loaded[tf],errors[tf]=load_entries(args.root,tf)
    result=audit(loaded,errors,official=args.official_cross_check);status=result["status"];reports=result["timeframes"]
    if args.summary:
        reports=[{k:v for k,v in r.items() if k in ("timeframe","status","row_count","first","last","duplicate_count","utc_duplicate_count","missing_slot_count","abnormal_interval_count","oi_coverage","oi_time_bases","source_coverage","warnings")} for r in reports]
    result["timeframes"]=reports
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return {"PASS":0,"WARNING":1,"FAIL":2}[status]
if __name__=="__main__":raise SystemExit(main())

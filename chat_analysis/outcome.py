"""Outcome evaluator for immutable ChatGPT decision records.

Only finalized observed bars may be supplied by a future runner. This module contains
no network fetch and cannot backfill a decision that was never prospectively recorded.
"""
from __future__ import annotations
from decimal import Decimal as D
from .history import HORIZONS, validate


def evaluate(record,bars,now_ms):
    validate(record)
    t=int(__import__("datetime").datetime.fromisoformat(record["decision_time_utc"].replace("Z","+00:00")).timestamp()*1000)
    ref=D(record["reference_price"]) if record["reference_price"] is not None else None
    out={"decision_id":record["decision_id"],"schema_version":"chat-analysis-outcome-v1","horizons":{}}
    for h in record["evaluation_horizons"]:
        target=t+HORIZONS[h]
        eligible=[b for b in bars if int(b["close_time_ms"])<=target and int(b["close_time_ms"])>t]
        mature=now_ms>=target
        if not mature or not eligible or ref is None:
            out["horizons"][h]={"status":"PENDING" if not mature else "UNAVAILABLE","target_time_ms":target}
            continue
        closes=[D(str(b["close"])) for b in eligible]
        highs=[D(str(b["high"])) for b in eligible]; lows=[D(str(b["low"])) for b in eligible]
        last=closes[-1]; raw=(last/ref-D(1))*D(100)
        side=D(1) if record["stance"]=="LONG" else D(-1) if record["stance"]=="SHORT" else D(0)
        directional=raw*side if side else abs(raw)
        if side>0:mfe=(max(highs)/ref-D(1))*100;mae=(min(lows)/ref-D(1))*100
        elif side<0:mfe=(D(1)-min(lows)/ref)*100;mae=(D(1)-max(highs)/ref)*100
        else:mfe=max(abs(max(highs)/ref-D(1)),abs(min(lows)/ref-D(1)))*100;mae=None
        out["horizons"][h]={"status":"MATURED","target_time_ms":target,"raw_return_pct":str(raw),
          "directional_return_pct":str(directional),"mfe_pct":str(mfe),"mae_pct":None if mae is None else str(mae),
          "sample_count":len(eligible),"semantics":"signal_outcome_not_realized_pnl"}
    return out

"""Explicit public-API, memory-only audit. Never scheduled and never persisted."""
import argparse
import time
import urllib.request
import urllib.error
from collections import Counter
from decimal import Decimal as D

from .contracts import DURATIONS
from .feature_contracts import dumps
from .features import bundle
from .market_data import HTTPClient, MarketData, universe, tickers, exchange_info, mapping, DataError
from .trend_state import evaluate

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--markets", help="Comma-separated real KRW markets")
    choice.add_argument("--full-market", action="store_true")
    args = parser.parse_args(argv)
    started = time.time_ns()
    requests, failures = Counter(), Counter()

    def opener(request, **kw):
        host = request.full_url.split("/")[2]
        requests[host] += 1
        try:
            return urllib.request.urlopen(request, **kw)
        except urllib.error.HTTPError as exc:
            failures[str(exc.code)] += 1
            raise
        except (OSError, TimeoutError):
            failures["NETWORK"] += 1
            raise

    client = HTTPClient(opener=opener)
    md = MarketData(client)
    all_markets, universe_evidence = universe(client)
    markets = [r["market"] for r in all_markets]
    if args.markets:
        selected = args.markets.split(",")
        if len(set(selected)) != len(selected) or set(selected)-set(markets):
            raise ValueError("unknown/duplicate actual KRW market")
        markets = selected
    prices, price_evidence = tickers(client, markets)
    info, exchange_evidence = exchange_info(client)
    records = []
    for i, market in enumerate(markets):
        counterpart = mapping(market, info)
        up = {tf:md.window("UPBIT",market,tf) for tf in DURATIONS}
        spot = ({tf:md.window("BINANCE_SPOT",counterpart["symbol"],tf) for tf in DURATIONS}
                if counterpart["status"] in ("VERIFIED","UNVERIFIED") else {})
        if any(w.reason and "418" in w.reason for w in (*up.values(),*spot.values())):
            raise DataError("API blocked; audit stopped without another request")
        generated = time.time_ns()//1000000
        features = bundle(up,spot,counterpart,generated)
        ticker = prices[market]
        ev = next(e for e in price_evidence if market in e["url"].replace("%2C",",").split("markets=")[-1].split(","))
        # Public ticker response clock, not inferred from a feature/EMA price.
        from .features import _clock_ms
        actual = {"instrument":market,"provider":"UPBIT","price":ticker["trade_price"],
                  "source_time_ms":ticker["timestamp"],"received_at_ms":_clock_ms(ev["received_at_utc"]),
                  "source_reference":ev}
        o = evaluate(features,market,time.time_ns()//1000000,actual)
        t, state, chase, confidence = o["trend"],o["state"],o["chase"],o["data"]
        # Technical contract checks, never distribution-based parameter tuning.
        assert (t["score"] is None) == (state["primary"] == "INSUFFICIENT_EVIDENCE")
        if t["score"] is not None:
            assert D(0) <= D(t["score"]) <= 100 and D(t["available_tf_weight"]) >= D(".80")
            assert all(t["available_masks"]["4h"][k] for k in ("A","S","M"))
        if t["primary_damage"]: assert not t["eligible"]
        record = {"market":market,"score":t["score"],"signed_score":t["signed_score"],
                  "state":state["primary"],"tags":state["secondary_tags"],"chase_score":chase["score"],
                  "chase":chase["category"],"mapping":counterpart["status"],"confirmation":o["binance"]["confirmation"],
                  "confidence":confidence["category"],"coverage":confidence["coverage"],"damage":t["primary_damage"],
                  "tf_scores":t["tf_scores"],"groups":t["group_scores"],"reason_codes":state["reason_codes"],
                  "windows":{tf:{"status":w.status,"rows":len(w.candles),"missing_slots":len(w.missing_slots),
                                 "reason":w.reason,"latest_segment":features["upbit"][tf]["metadata"].get("available_history",0)}
                             for tf,w in up.items()}}
        records.append(record)
        # One-market cache lifetime: raw is not kept as a cross-run history store.
        md.cache.clear()
        if args.full_market and ((i+1)%20 == 0 or i+1 == len(markets)):
            print(dumps({"progress":i+1,"total":len(markets),"elapsed_seconds":D(time.time_ns()-started)/1000000000}),flush=True)

    def count(key): return dict(sorted(Counter(r[key] for r in records).items()))
    buckets = Counter()
    for r in records:
        v = None if r["score"] is None else D(r["score"])
        key = ("null" if v is None else "0" if v == 0 else ">0~<45" if v < 45 else
               "45~<65" if v < 65 else "65~<80" if v < 80 else ">=80")
        buckets[key] += 1
    top = sorted((r for r in records if r["score"] is not None),key=lambda r:(-D(r["score"]),r["market"]))[:10]
    sparse = [r for r in records if any(w["status"] != "AVAILABLE" for w in r["windows"].values())]
    report = {
        "mode":"FULL_MARKET" if args.full_market else "SMOKE", "source_cutoff_ms":md.cutoff_ms,
        "markets":len(markets),"market_data_nonempty":sum(any(w["rows"] for w in r["windows"].values()) for r in records),
        "market_data_all_windows_available":sum(all(w["status"] == "AVAILABLE" for w in r["windows"].values()) for r in records),
        "score_available":sum(r["score"] is not None for r in records),"states":count("state"),"score_buckets":dict(buckets),
        "chase":count("chase"),"mapping":count("mapping"),"confirmation":count("confirmation"),"confidence":count("confidence"),
        "primary_damage":sum(r["damage"] for r in records),
        "high_score_high_chase":sum(r["score"] is not None and D(r["score"]) >= 80 and r["chase"]=="HIGH" for r in records),
        "sparse_markets":len(sparse),"sparse_examples":sparse[:8],"top_score_examples":top,
        "requests":dict(requests),"transport_failures":dict(failures),"elapsed_seconds":D(time.time_ns()-started)/1000000000,
        "universe_evidence":universe_evidence,"exchange_info_evidence":exchange_evidence,
        "production_files_created":0,"parameters_tuned":False,"implementation_contract_checks":"PASS",
    }
    if not args.full_market: report["smoke_records"] = records
    print(dumps(report),flush=True)
    return report

if __name__ == "__main__": main()

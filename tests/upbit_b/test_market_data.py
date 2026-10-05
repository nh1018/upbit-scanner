import io
import json
import unittest
import urllib.error
from decimal import Decimal
from unittest.mock import patch

from upbit_b.contracts import DURATIONS, WINDOWS
from upbit_b.market_data import (BINANCE, UPBIT, DataError, HTTPClient, MarketData,
                                exchange_info, iso, mapping, normalize, number, tickers, universe)

CUTOFF = 1791158400000  # exact UTC midnight

def fixture(provider, tf, start):
    d = DURATIONS[tf]
    if provider == "UPBIT":
        from datetime import datetime, timezone
        return {"market": "KRW-BTC", "unit": d//60000,
                "candle_date_time_utc": iso(start),
                "candle_date_time_kst": datetime.fromtimestamp((start+32400000)/1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                "opening_price": "100.000000000000000001", "high_price": "110", "low_price": "90", "trade_price": "105",
                "candle_acc_trade_volume": "0.000000000000000001", "candle_acc_trade_price": "2"}
    return [start, "100.000000000000000001", "110", "90", "105", "0.000000000000000001", start+d-1, "2", 1, "0", "0", "0"]

class Fake:
    def __init__(self, rows):
        self.rows, self.calls = rows, []
    def get(self, *args):
        self.calls.append(args)
        rows=self.rows
        if isinstance(rows,list) and args[1].startswith("/v1/candles/"):
            from upbit_b.market_data import upbit_time
            rows=[r for r in rows if not isinstance(r,dict) or "candle_date_time_utc" not in r or upbit_time(r["candle_date_time_utc"]) < upbit_time(args[2]["to"])]
        return rows, {"url": "fixture", "response_sha256": "fixture", "received_at_utc": "fixture"}

class WindowsTest(unittest.TestCase):
    def full(self, provider, tf):
        return [fixture(provider, tf, CUTOFF-i*DURATIONS[tf]) for i in range(1, WINDOWS[tf]+1)]

    def test_all_six_full_windows_and_cache(self):
        for provider in ("UPBIT", "BINANCE_SPOT"):
            for tf in WINDOWS:
                with self.subTest(provider=provider, tf=tf):
                    client = Fake(self.full(provider, tf))
                    fetch = MarketData(client, CUTOFF)
                    instrument = "KRW-BTC" if provider == "UPBIT" else "BTCUSDT"
                    w = fetch.window(provider, instrument, tf)
                    self.assertEqual(w.status, "AVAILABLE")
                    self.assertEqual(len(w.candles), WINDOWS[tf])
                    self.assertEqual(w.candles[-1].close_ms, CUTOFF)
                    self.assertTrue(all(c.completed for c in w.candles))
                    self.assertIs(fetch.window(provider, instrument, tf), w)
                    self.assertEqual(len(client.calls), 1)
                    self.assertEqual(w.candles[0].open, Decimal("100.000000000000000001"))

    def test_forming_excluded(self):
        rows = self.full("UPBIT", "1h") + [fixture("UPBIT", "1h", CUTOFF)]
        client=Fake(rows)
        client.get=lambda *a:(rows,{"url":"forming-fixture"})
        w = MarketData(client, CUTOFF+1000).window("UPBIT", "KRW-BTC", "1h")
        self.assertEqual(w.status, "AVAILABLE")
        self.assertTrue(all(c.close_ms <= CUTOFF for c in w.candles))

    def test_insufficient(self):
        w = MarketData(Fake(self.full("UPBIT", "1h")[:3]), CUTOFF).window("UPBIT", "KRW-BTC", "1h")
        self.assertEqual(w.status, "INSUFFICIENT_DATA")

    def test_sparse_not_filled(self):
        rows = self.full("UPBIT", "1h")
        del rows[2]
        w = MarketData(Fake(rows), CUTOFF).window("UPBIT", "KRW-BTC", "1h")
        self.assertEqual(w.status, "INCOMPLETE_COVERAGE")
        self.assertEqual(w.missing_slots, (CUTOFF-3*DURATIONS["1h"],))
        self.assertEqual(len(w.candles), 199)

    def test_stale(self):
        w = MarketData(Fake(self.full("UPBIT", "1h")[1:]), CUTOFF).window("UPBIT", "KRW-BTC", "1h")
        self.assertEqual(w.status, "INCOMPLETE_COVERAGE")

    def test_duplicate_rejected(self):
        row = fixture("UPBIT", "1h", CUTOFF-DURATIONS["1h"])
        w = MarketData(Fake([row,row]), CUTOFF).window("UPBIT", "KRW-BTC", "1h")
        self.assertEqual(w.status, "INVALID_DATA")
        self.assertFalse(w.candles)

    def test_replay_hash_sorted(self):
        rows = self.full("UPBIT", "1h")
        a = MarketData(Fake(rows), CUTOFF).window("UPBIT", "KRW-BTC", "1h")
        b = MarketData(Fake(rows[::-1]), CUTOFF).window("UPBIT", "KRW-BTC", "1h")
        self.assertEqual(a.input_sha256, b.input_sha256)
        self.assertEqual(a.candles, b.candles)

    def test_bad_shape_returns_no_input(self):
        for rows in ({}, [None], [{"market": "wrong"}]):
            with self.subTest(rows=rows):
                w = MarketData(Fake(rows), CUTOFF).window("UPBIT", "KRW-BTC", "1h")
                self.assertEqual(w.status, "INVALID_DATA")
                self.assertFalse(w.candles)

    def test_api_error(self):
        client = Fake([])
        client.get = lambda *args: (_ for _ in ()).throw(DataError("API retries exhausted"))
        self.assertEqual(MarketData(client,CUTOFF).window("UPBIT","KRW-BTC","1h").status,"API_ERROR")

    def test_tf_independence(self):
        class Routed:
            def get(inner,base,path,params):
                tf="1d" if path.endswith("days") else "4h" if path.endswith("240") else "1h"
                return self.full("UPBIT",tf),{}
        f=MarketData(Routed(),CUTOFF)
        for tf in WINDOWS:
            self.assertEqual(len(f.window("UPBIT","KRW-BTC",tf).candles),WINDOWS[tf])
        self.assertEqual(len(f.cache),3)

    def test_pagination_recovers_excluded_forming_row(self):
        rows=self.full("UPBIT","1h")
        class Pages:
            def __init__(inner): inner.n=0
            def get(inner,*a):
                inner.n+=1
                return ([fixture("UPBIT","1h",CUTOFF)]+rows[:-1] if inner.n==1 else rows[-1:]),{}
        client=Pages()
        w=MarketData(client,CUTOFF).window("UPBIT","KRW-BTC","1h")
        self.assertEqual(w.status,"AVAILABLE")
        self.assertEqual(client.n,2)

    def test_transient_input_no_file_writes(self):
        with patch("builtins.open",side_effect=AssertionError("file write/read unexpected")):
            w=MarketData(Fake(self.full("UPBIT","1h")),CUTOFF).window("UPBIT","KRW-BTC","1h")
            self.assertEqual(w.status,"AVAILABLE")

class ValidationTest(unittest.TestCase):
    def test_boundary_all_tf(self):
        for provider in ("UPBIT", "BINANCE_SPOT"):
            for tf in WINDOWS:
                instrument = "KRW-BTC" if provider == "UPBIT" else "BTCUSDT"
                normalize(provider,instrument,tf,fixture(provider,tf,CUTOFF-DURATIONS[tf]))
                with self.assertRaises(DataError):
                    normalize(provider,instrument,tf,fixture(provider,tf,CUTOFF-DURATIONS[tf]+1000))

    def test_kst_day_0900(self):
        row = fixture("UPBIT","1d",CUTOFF-DURATIONS["1d"])
        self.assertTrue(row["candle_date_time_kst"].endswith("09:00:00"))
        row["candle_date_time_kst"] = row["candle_date_time_kst"].replace("09:00:00", "00:00:00")
        with self.assertRaises(DataError): normalize("UPBIT","KRW-BTC","1d",row)

    def test_bad_ohlcv(self):
        for key,value in [("opening_price","0"),("high_price","99"),("low_price","106"),("candle_acc_trade_volume","-1"),("candle_acc_trade_price",None),("trade_price","NaN"),("trade_price",True)]:
            with self.subTest(key=key,value=value):
                row=fixture("UPBIT","1h",CUTOFF-DURATIONS["1h"]);row[key]=value
                with self.assertRaises(DataError): normalize("UPBIT","KRW-BTC","1h",row)

    def test_unit_close_and_float(self):
        row=fixture("BINANCE_SPOT","4h",CUTOFF-DURATIONS["4h"]);row[6]+=1
        with self.assertRaises(DataError): normalize("BINANCE_SPOT","BTCUSDT","4h",row)
        row=fixture("UPBIT","4h",CUTOFF-DURATIONS["4h"]);row["unit"]=60
        with self.assertRaises(DataError): normalize("UPBIT","KRW-BTC","4h",row)
        with self.assertRaises(DataError): number(0.1)

class MappingTest(unittest.TestCase):
    def info(self, **kw):
        row={"symbol":"BTCUSDT","baseAsset":"BTC","quoteAsset":"USDT","status":"TRADING","isSpotTradingAllowed":True};row.update(kw)
        return {"symbols":[row]}
    def test_all_states(self):
        self.assertEqual(mapping("KRW-BTC",None)["status"],"API_UNAVAILABLE")
        self.assertEqual(mapping("KRW-OTHER",self.info())["status"],"NO_SYMBOL")
        self.assertEqual(mapping("KRW-BTC",self.info(status="BREAK"))["status"],"NO_ELIGIBLE_SYMBOL")
        self.assertEqual(mapping("KRW-BTC",self.info())["status"],"UNVERIFIED")
        info=self.info();info["symbols"]*=2
        self.assertEqual(mapping("KRW-BTC",info)["status"],"AMBIGUOUS")
        registry={"KRW-BTC":{"symbol":"BTCUSDT","registry_version":"approved-1","evidence_reference":"approved-evidence"}}
        self.assertEqual(mapping("KRW-BTC",self.info(),registry)["status"],"VERIFIED")
    def test_no_alias_or_unsubstantiated_approval(self):
        self.assertEqual(mapping("KRW-XBT",self.info())["status"],"NO_SYMBOL")
        self.assertEqual(mapping("KRW-BTC",self.info(),{"KRW-BTC":{"symbol":"BTCUSDT"}})["status"],"UNVERIFIED")
    def test_universe_and_ticker_no_momentum_filter(self):
        rows=[{"market":"KRW-X"},{"market":"BTC-Y"},{"market":"KRW-BTC"}]
        markets,_=universe(Fake(rows))
        self.assertEqual([r["market"] for r in markets],["KRW-BTC","KRW-X"])
        result,_=tickers(Fake([{"market":"KRW-X","trade_price":"1"}]),["KRW-X"])
        self.assertIn("KRW-X",result)
    def test_malformed_universe_and_mapping(self):
        for rows in ([],[{"market":"KRW-X"}]*2,[{}]):
            with self.assertRaises(DataError): universe(Fake(rows))
        with self.assertRaises(DataError): mapping("KRW-BTC",{"symbols":[{}]})

    def test_confirmation_failure_does_not_remove_upbit(self):
        class Failure:
            def get(self,*a): raise DataError("API timeout")
        info,ev=exchange_info(Failure())
        self.assertEqual(mapping("KRW-BTC",info)["status"],"API_UNAVAILABLE")
        self.assertEqual(ev["status"],"API_UNAVAILABLE")
        markets,_=universe(Fake([{"market":"KRW-BTC"}]))
        self.assertEqual(len(markets),1)

class Response(io.BytesIO):
    def __init__(self,raw,headers=None):
        super().__init__(raw);self.headers=headers or {}

class HTTPTest(unittest.TestCase):
    def test_decimal_json_evidence_pacing(self):
        sleeps=[]
        client=HTTPClient(opener=lambda *a,**k:Response(b'{"x":0.123456789012345678901}',{"Remaining-Req":"group=candles; min=600; sec=0"}),sleep=sleeps.append,clock=lambda:0)
        row,ev=client.get(UPBIT,"/fixture",{})
        self.assertEqual(row["x"],Decimal("0.123456789012345678901"))
        self.assertEqual(len(ev["response_sha256"]),64)
        client.get(UPBIT,"/fixture",{})
        self.assertIn(1,sleeps)
    def test_timeout_retry_bound(self):
        calls=[]
        def opener(*a,**kw): calls.append(1);raise TimeoutError()
        with self.assertRaises(DataError): HTTPClient(opener=opener,sleep=lambda x:None).get(UPBIT,"/fixture",{})
        self.assertEqual(len(calls),3)
    def test_429_retry_and_418_stop(self):
        for code,n in ((429,3),(418,1),(400,1)):
            calls=[]
            def opener(*a,**kw):
                calls.append(1);raise urllib.error.HTTPError("fixture",code,"failure",{},None)
            with self.assertRaises(DataError): HTTPClient(opener=opener,sleep=lambda x:None).get(UPBIT,"/fixture",{})
            self.assertEqual(len(calls),n)
    def test_bad_json_no_retry(self):
        for raw in (b'not json',b'{"x":NaN}'):
            with self.assertRaises(DataError): HTTPClient(opener=lambda *a,**k:Response(raw),sleep=lambda x:None).get(UPBIT,"/fixture",{})

if __name__ == "__main__": unittest.main()

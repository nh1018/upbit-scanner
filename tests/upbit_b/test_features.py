import copy
import ast
from pathlib import Path
import unittest
from dataclasses import replace
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from unittest.mock import patch

from upbit_b.contracts import Candle, Window, DURATIONS, WINDOWS
from upbit_b.features import snapshot, bundle, _input_hash, _ema, _atr, _pivots
from upbit_b.feature_contracts import FEATURES, PARAMETERS, PARAMETER_SHA256, canonical, dumps, digest
from upbit_b.market_data import iso

D=Decimal
START=1790899200000

def rows(n=100, tf="1h", provider="UPBIT", closes=None, quotes=None):
    instrument="KRW-X" if provider=="UPBIT" else "XUSDT"
    result=[]
    for i in range(n):
        c=D(str(closes[i])) if closes else D(100)
        result.append(Candle(provider,instrument,tf,START+i*DURATIONS[tf],START+(i+1)*DURATIONS[tf],c,c+10,c-10,c,D(1),D(str(quotes[i])) if quotes else D(20),True))
    return result

def window(data, status=None, cutoff=None, received=None):
    cutoff=data[-1].close_ms if cutoff is None else cutoff
    status=status or ("AVAILABLE" if len(data)>=WINDOWS[data[0].timeframe] else "INSUFFICIENT_DATA")
    ev={"url":"https://fixture/candles","response_sha256":"a"*64,"received_at_utc":iso(cutoff+1000 if received is None else received)}
    return Window(status,tuple(data),cutoff,(ev,),_input_hash(data))

def calc(data, **kw):
    w=window(data,**kw)
    return snapshot(w,w.cutoff_ms+3000)

class IndicatorTests(unittest.TestCase):
    def test_feature_count_and_version(self):
        result=calc(rows())
        self.assertEqual(len(FEATURES),41)
        self.assertEqual(set(result["features"]),set(FEATURES))
        self.assertEqual(result["parameter_sha256"],digest(PARAMETERS))
        self.assertEqual(PARAMETER_SHA256,result["parameter_sha256"])

    def test_hand_return_all_periods(self):
        f=calc(rows(11,closes=list(range(100,111))))["features"]
        with localcontext() as ctx:
            ctx.prec=34
            for k in (1,3,5,10):
                self.assertEqual(f[f"return{k}_pct"],canonical(100*(D(110)/D(110-k)-1)))

    def test_nonoverlap_acceleration(self):
        f=calc(rows(7,closes=[100,100,100,110,110,110,121]))["features"]
        self.assertEqual(f["return3_acceleration_pp"],"0")

    def test_ema_seed20_50(self):
        with localcontext() as ctx:
            ctx.prec=34
            for n in (20,50):
                closes=list(map(D,range(100,100+n)))
                values=_ema(closes,n)
                self.assertTrue(all(v is None for v in values[:-1]))
                self.assertEqual(values[-1],sum(closes)/n)

    def test_ema_update_hand(self):
        with localcontext() as ctx:
            ctx.prec=34
            result=calc(rows(21,closes=[100]*20+[121]))
            self.assertEqual(result["features"]["ema20"],"102")
            self.assertEqual(result["metadata"]["seeds"]["ema20"]["updates"],1)

    def test_ema_slope_hand_and_warmup(self):
        f=calc(rows(23,closes=[100]*20+[121]*3))["features"]
        with localcontext() as ctx:
            ctx.prec=34
            e=D(100);a=D(2)/21
            for _ in range(3): e=a*121+(1-a)*e
            self.assertEqual(f["ema20_slope3_pct"],canonical(100*(e/100-1)))
        self.assertIsNone(calc(rows(22))["features"]["ema20_slope3_pct"])

    def test_atr_seed_requires15(self):
        self.assertIsNone(calc(rows(14))["features"]["atr14"])
        self.assertEqual(calc(rows(15))["features"]["atr14"],"20")

    def test_atr_wilder_update_and_previous_close(self):
        data=rows(16)
        data[-1]=replace(data[-1],open=D(140),high=D(145),low=D(135),close=D(140))
        with localcontext() as ctx:
            ctx.prec=34
            self.assertEqual(calc(data)["features"]["atr14"],canonical((D(20)*13+45)/14))

    def test_atr_percent_distances(self):
        f=calc(rows(53))["features"]
        self.assertEqual(f["atr_pct"],"20")
        self.assertEqual(f["close_to_ema20_pct"],"0")
        self.assertEqual(f["close_to_ema50_pct"],"0")
        self.assertEqual(f["ema20_to_ema50_pct"],"0")
        self.assertEqual(f["close_to_ema20_atr"],"0")
        self.assertEqual(f["price_change3_atr"],"0")

    def test_body_location_and_flat_range(self):
        data=rows(1);data[0]=replace(data[0],open=D(95))
        f=calc(data)["features"]
        self.assertEqual(f["signed_body_ratio"],"0.25")
        self.assertEqual(f["close_location"],"0.5")
        data[0]=replace(data[0],open=D(100),high=D(100),low=D(100))
        f=calc(data)
        self.assertIsNone(f["features"]["close_location"])
        self.assertEqual(f["readiness"]["signed_body_ratio"],"ZERO_DENOMINATOR")

    def test_streak_and_truncated(self):
        r=calc(rows(5,closes=[100,101,102,103,104]))
        self.assertEqual(r["features"]["positive_return_streak"],4)
        self.assertTrue(r["metadata"]["streak_truncated"])
        r=calc(rows(5,closes=[100,101,100,101,102]))
        self.assertEqual(r["features"]["positive_return_streak"],2)
        self.assertFalse(r["metadata"]["streak_truncated"])

    def test_recross(self):
        f=calc(rows(21,closes=[100]*20+[101]))["features"]
        self.assertIs(f["ema20_upward_recross"],True)
        self.assertIs(calc(rows(21))["features"]["ema20_upward_recross"],False)

class RollingParticipationTests(unittest.TestCase):
    def test_current_high_excluded(self):
        data=rows(21);data[-1]=replace(data[-1],high=D(500),close=D(111))
        f=calc(data)["features"]
        self.assertEqual(f["rolling_high20"],"110")
        self.assertIs(f["breakout20"],True)

    def test_equal_not_breakout_or_breakdown(self):
        for close in (110,90):
            data=rows(21);data[-1]=replace(data[-1],close=D(close))
            f=calc(data)["features"]
            self.assertIs(f["breakout20"],False)
            self.assertIs(f["breakdown20"],False)

    def test_wick_only_not_breakout(self):
        data=rows(21);data[-1]=replace(data[-1],high=D(200))
        self.assertIs(calc(data)["features"]["breakout20"],False)

    def test_close_breakdown(self):
        data=rows(21);data[-1]=replace(data[-1],close=D(89),low=D(80))
        self.assertIs(calc(data)["features"]["breakdown20"],True)

    def test_prior_mean_median_hand(self):
        f=calc(rows(21,quotes=[10]*19+[210,100]))["features"]
        self.assertEqual(f["quote_ma20_prior"],"20")
        self.assertEqual(f["quote_median20_prior"],"10")
        self.assertEqual(f["quote_ratio_ma20"],"5")
        self.assertEqual(f["quote_ratio_median20"],"10")

    def test_recent3_preceding20_nonoverlap(self):
        f=calc(rows(23,quotes=[10]*20+[30]*3))["features"]
        self.assertEqual(f["quote_recent3_vs_prior20"],"3")

    def test_zero_quote_denominator(self):
        f=calc(rows(23,quotes=[0]*23))
        for key in ("quote_ratio_ma20","quote_ratio_median20","quote_recent3_vs_prior20"):
            self.assertIsNone(f["features"][key])
            self.assertEqual(f["readiness"][key],"ZERO_DENOMINATOR")

    def test_zero_atr_denominator(self):
        data=[replace(c,high=D(100),low=D(100)) for c in rows(53)]
        f=calc(data)
        self.assertEqual(f["features"]["atr14"],"0")
        self.assertIsNone(f["features"]["close_to_ema20_atr"])
        self.assertEqual(f["readiness"]["close_to_ema20_atr"],"ZERO_DENOMINATOR")

class StructureTests(unittest.TestCase):
    def pattern(self,n=18,hs=(110,115,112),ls=(90,88,91)):
        data=[replace(c,high=D(105),low=D(95)) for c in rows(n)]
        for i,v in zip((3,8,13),hs):
            if i<n:data[i]=replace(data[i],high=D(v))
        for i,v in zip((2,7,12),ls):
            if i<n:data[i]=replace(data[i],low=D(v))
        return data

    def test_pivot_right2_timing(self):
        data=self.pattern(6)
        a=calc(data[:5]);b=calc(data[:6])
        self.assertIsNone(a["features"]["confirmed_pivot_high"])
        p=b["features"]["confirmed_pivot_high"]
        self.assertEqual(p["pivot_open_ms"],data[3].open_ms)
        self.assertEqual(p["confirmation_boundary_ms"],data[5].close_ms)
        self.assertGreater(p["observed_at_ms"],p["confirmation_boundary_ms"])

    def test_tie_exclusion(self):
        data=rows(6);data[2]=replace(data[2],high=D(120));data[3]=replace(data[3],high=D(120))
        self.assertIsNone(calc(data)["features"]["confirmed_pivot_high"])

    def test_all_structure_labels(self):
        for hs,ls,expected in [((110,115,120),(90,92,93),("HH","HL")),
                              ((120,115,110),(93,92,90),("LH","LL")),
                              ((110,110,110),(90,90,90),("EQ","EQ"))]:
            f=calc(self.pattern(hs=hs,ls=ls))["features"]
            self.assertEqual((f["high_structure"],f["low_structure"]),expected)

    def test_two_same_kind_required(self):
        f=calc(self.pattern(6))["features"]
        self.assertIsNotNone(f["confirmed_pivot_high"])
        self.assertIsNone(f["high_structure"])

    def test_swing_order_and_retracement_hand(self):
        data=self.pattern()
        r=calc(data);f=r["features"];a=r["metadata"]["swing_anchor"]
        self.assertLess(a["low"]["pivot_open_ms"],a["high"]["pivot_open_ms"])
        with localcontext() as ctx:
            ctx.prec=34
            self.assertEqual(f["swing_return_pct"],canonical(100*(D(112)/91-1)))
            self.assertEqual(f["swing_retracement_close"],canonical(D(12)/21))
        self.assertEqual(f["bars_since_swing_high"],4)
        self.assertEqual(f["post_peak_quote_ratio"],"1")
        self.assertIsNotNone(f["distance_to_confirmed_low_atr"])
        self.assertIsNotNone(f["peak_to_close_atr"])

    def test_retracement_not_clipped(self):
        data=self.pattern();data[-1]=replace(data[-1],close=D(120),high=D(125))
        self.assertLess(D(calc(data)["features"]["swing_retracement_close"]),0)

    def test_no_low_before_high(self):
        data=rows(8);data[2]=replace(data[2],high=D(120));data[5]=replace(data[5],low=D(80))
        self.assertIsNone(calc(data)["features"]["swing_return_pct"])

    def test_breakout_event_level_frozen(self):
        data=rows(30);data[24]=replace(data[24],high=D(125),close=D(120))
        r=calc(data)
        self.assertEqual(r["metadata"]["last_breakout"]["level"],"110")
        self.assertEqual(r["metadata"]["last_breakout"]["candle_open_ms"],data[24].open_ms)
        with localcontext() as ctx:
            ctx.prec=34
            atr=D(r["features"]["atr14"])
            self.assertEqual(r["features"]["distance_to_last_breakout_atr"],canonical(D(-10)/atr))

class SafetyTests(unittest.TestCase):
    def test_gap_restart_ema_atr(self):
        data=rows(80);del data[54]
        r=calc(data,status="INCOMPLETE_COVERAGE")
        self.assertEqual(r["metadata"]["available_history"],25)
        self.assertEqual(r["features"]["ema20"],"100")
        self.assertIsNone(r["features"]["ema50"])
        self.assertEqual(r["readiness"]["ema50"],"GAP_RESET")
        self.assertEqual(r["features"]["atr14"],"20")

    def test_gap_return_rolling_pivot_reset(self):
        data=StructureTests().pattern(18);del data[14]
        r=calc(data,status="INCOMPLETE_COVERAGE")
        for key in ("return3_pct","ema20","rolling_high20","confirmed_pivot_high","high_structure","swing_return_pct"):
            self.assertIsNone(r["features"][key])
        self.assertIsNotNone(r["features"]["return1_pct"])

    def test_stale_is_all_null(self):
        data=rows()
        r=calc(data,cutoff=data[-1].close_ms+DURATIONS["1h"])
        self.assertEqual(r["null_count"],41)
        self.assertEqual(set(r["readiness"].values()),{"STALE_INPUT"})

    def test_insufficient_partial_ready(self):
        r=calc(rows(10))
        self.assertIsNotNone(r["features"]["return5_pct"])
        self.assertIsNone(r["features"]["ema20"])
        self.assertGreater(r["ready_count"],0)

    def test_zero_not_missing_false_not_missing(self):
        f=calc(rows(100))["features"]
        self.assertEqual(f["return1_pct"],"0")
        self.assertIs(f["breakout20"],False)
        self.assertIsNone(f["high_structure"])

    def test_invalid_and_api_window(self):
        for state in ("API_ERROR","INVALID_DATA"):
            w=Window(state,(),START,(),"",reason="fixture")
            r=snapshot(w,START+1000)
            self.assertEqual(r["null_count"],41)
            self.assertEqual(set(r["readiness"].values()),{state})

    def test_incomplete_or_corrupt_candle_rejected(self):
        for mutation in (dict(completed=False),dict(high=D(90)),dict(close=D("NaN")),dict(timeframe="4h")):
            data=rows();data[-1]=replace(data[-1],**mutation)
            r=calc(data)
            self.assertEqual(set(r["readiness"].values()),{"INVALID_INPUT"})

    def test_hash_mismatch(self):
        w=replace(window(rows()),input_sha256="corrupt")
        self.assertEqual(snapshot(w,w.cutoff_ms+3000)["readiness"]["ema20"],"INPUT_HASH_MISMATCH")

    def test_missing_evidence_and_future_availability(self):
        w=window(rows())
        for other in (replace(w,evidence=()),replace(w,evidence=({"url":"x"},))):
            self.assertEqual(snapshot(other,w.cutoff_ms+3000)["readiness"]["ema20"],"INVALID_EVIDENCE")
        self.assertEqual(snapshot(w,w.cutoff_ms)["readiness"]["ema20"],"INVALID_EVIDENCE")

    def test_canonical_and_context_independence(self):
        self.assertEqual(dumps({"a":D("-0"),"b":D("1.2300"),"c":False,"d":None}),'{"a":"0","b":"1.23","c":false,"d":null}')
        for bad in (1.2,D("NaN"),D("Infinity")):
            with self.assertRaises((ValueError,TypeError)): canonical(bad)
        w=window(rows())
        with localcontext() as ctx:
            ctx.prec=5;a=snapshot(w,w.cutoff_ms+3000)
        with localcontext() as ctx:
            ctx.prec=50;b=snapshot(w,w.cutoff_ms+3000)
        self.assertEqual(a,b)

    def test_deterministic_replay_and_immutability(self):
        w=window(rows());before=copy.deepcopy(w)
        a=snapshot(w,w.cutoff_ms+3000);b=snapshot(w,w.cutoff_ms+3000)
        self.assertEqual(a,b);self.assertEqual(w,before)

    def test_received_clock_not_measurement_hash(self):
        data=StructureTests().pattern();w=window(data)
        a=snapshot(w,w.cutoff_ms+3000)
        b=snapshot(window(data,received=w.cutoff_ms+2000),w.cutoff_ms+3000)
        self.assertEqual(a["measurement_sha256"],b["measurement_sha256"])

    def test_future_suffix_invariance_fixed_seed(self):
        data=StructureTests().pattern(40)
        prefix=copy.deepcopy(data[:18])
        before=calc(prefix)
        data[-1]=replace(data[-1],high=D(1000))
        self.assertEqual(calc(data[:18]),before)
        self.assertEqual(_ema([c.close for c in data],20)[:30],_ema([c.close for c in data[:30]],20))
        old_highs,_=_pivots(data[:18]);new_highs,_=_pivots(data)
        self.assertEqual(old_highs,[p for p in new_highs if p["confirmation_boundary_ms"]<=data[17].close_ms])

    def test_seed_shift_recorded(self):
        data=rows(110,closes=list(range(100,210)))
        a=calc(data);b=calc(data[10:])
        self.assertNotEqual(a["metadata"]["seeds"]["ema50"]["time_ms"],b["metadata"]["seeds"]["ema50"]["time_ms"])
        self.assertNotEqual(a["measurement_sha256"],b["measurement_sha256"])

    def test_no_file_writes(self):
        with patch("builtins.open",side_effect=AssertionError("file IO forbidden")):
            self.assertGreater(calc(rows())["ready_count"],0)

    def test_a_btc_market_data_protection(self):
        root=Path(__file__).resolve().parents[2]
        tree=ast.parse((root/"upbit_b/features.py").read_text(encoding="utf-8"))
        imported=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Import): imported.update(alias.name for alias in node.names)
            if isinstance(node,ast.ImportFrom): imported.add(node.module)
        self.assertEqual(imported,{"hashlib","json","time","datetime","decimal","contracts","feature_contracts"})
        calls={n.func.id for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
        self.assertNotIn("open",calls)

class BundleTests(unittest.TestCase):
    def test_unverified_and_unavailable_independent(self):
        up={"1h":window(rows())};spot={"1h":window(rows(provider="BINANCE_SPOT"))}
        gen=up["1h"].cutoff_ms+3000
        a=bundle(up,spot,{"status":"UNVERIFIED","symbol":"XUSDT"},gen)
        b=bundle(up,None,{"status":"API_UNAVAILABLE","symbol":None},gen)
        self.assertEqual(a["upbit"],b["upbit"])
        self.assertEqual(a["mapping"]["status"],"UNVERIFIED")
        self.assertGreater(a["binance_spot"]["1h"]["ready_count"],0)
        self.assertEqual(b["binance_spot"]["1h"]["null_count"],41)
        self.assertNotIn("confirmation",a)

    def test_tf_independence(self):
        up={tf:window(rows(tf=tf)) for tf in DURATIONS}
        gen=max(w.cutoff_ms for w in up.values())+3000
        a=bundle(up,generated_at_ms=gen)
        up["1h"]=window(rows(closes=[101]*100))
        b=bundle(up,generated_at_ms=gen)
        self.assertEqual(a["upbit"]["4h"],b["upbit"]["4h"])
        self.assertEqual(a["upbit"]["1d"],b["upbit"]["1d"])

    def test_mapping_and_provider_identity_guard(self):
        with self.assertRaises(ValueError): bundle({"1h":window(rows(provider="BINANCE_SPOT"))})
        with self.assertRaises(ValueError): bundle({"1h":window(rows())},mapping={"status":"invented"})
        with self.assertRaises(ValueError): bundle({"1h":window(rows())},{"1h":window(rows(provider="BINANCE_SPOT"))},{"status":"UNVERIFIED","symbol":"OTHER"})

if __name__=="__main__": unittest.main()

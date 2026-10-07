"""Independent arithmetic fixtures, causality and protected-input regressions."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from btc_anytime.integrity import DURATIONS, iso
from btc_anytime.features.engine import build_timeframe, digest, encode, PARAMETERS
from btc_anytime.features.snapshot import synchronize
from btc_anytime.features.build import build_dataset, write_derived, load_raw, protected_hashes

D=Decimal
START=1790812800000  # UTC midnight, every supported boundary.


def rows(n=60,tf="15m",closes=None):
    values=closes or [100+i for i in range(n)]
    duration=DURATIONS[tf]
    return [{"symbol":"BTCUSDT.P","timeframe":tf,"time":START+i*duration,
             "candle_time_utc":iso(START+i*duration),"is_closed":True,
             "open":str(c),"high":str(D(str(c))+2),"low":str(max(D("0.5"),D(str(c))-2)),
             "close":str(c),"volume":str(i+1),"source":"fixture"} for i,c in enumerate(values)]


def availability(rs, overrides=None):
    return {r["time"]:{"kind":"consumer_first_observed","ref":"fixture_poll",
                      "observed_at_ms":(overrides or {}).get(r["time"],r["time"]+DURATIONS[r["timeframe"]])} for r in rs}


def build(rs, registry=(), evidence=True):
    return build_timeframe(rs,rs[0]["timeframe"],availability(rs) if evidence else {},registry)


def value(record,name):
    x=record["features"][name]
    return D(x) if isinstance(x,str) and not name.endswith("state") else x


def add_oi(rs, provider="binance", values=None, unit="BTC"):
    for i,r in enumerate(rs):
        r["oi"]=str((values or [100+j for j in range(len(rs))])[i])
        r["oi_status"]="available"
        if provider=="binance":
            r.update(oi_source="binance_usdm_open_interest_hist",oi_alignment="source_timestamp_equals_candle_open",oi_timestamp_ms=r["time"])
        else:
            end=r["time"]+DURATIONS[r["timeframe"]]
            r.update(oi_time_basis="confirmed_oi_bar_close_boundary",oi_time=end,oi_period_start_ms=r["time"],oi_period_end_ms=end)
    basis="source_timestamp_equals_candle_open" if provider=="binance" else "confirmed_oi_bar_close_boundary"
    return [{"provider":provider,"instrument":"BTCUSDT_PERPETUAL","timeframe":rs[0]["timeframe"],"basis":basis,"unit":unit,"evidence":"fixture validated contract"}]


class IndicatorTests(unittest.TestCase):
    def test_returns_manual_each_lag(self):
        r=build(rows())[-1]
        for lag in (1,3,6,12,24):
            with self.subTest(lag=lag):
                expected=100*(D(159)/D(159-lag)-1)
                self.assertAlmostEqual(value(r,"return_"+str(lag)),expected,places=17)
    def test_ema_seed_and_recursion(self):
        rs=build(rows(54,closes=list(range(1,55))))
        self.assertIsNone(value(rs[18],"ema_20"))
        self.assertEqual(value(rs[19],"ema_20"),D("10.5"))
        self.assertEqual(value(rs[20],"ema_20"),D("11.5"))
        self.assertEqual(value(rs[49],"ema_50"),D("25.5"))
        self.assertEqual(value(rs[50],"ema_50"),D("26.5"))
    def test_ema_slope_lag_and_distance(self):
        rs=build(rows(24,closes=list(range(1,25))))
        self.assertIsNone(value(rs[21],"ema_20_slope_3_pct_per_bar"))
        self.assertAlmostEqual(value(rs[22],"ema_20_slope_3_pct_per_bar"),100*(D("13.5")/D("10.5")-1)/3,places=17)
        self.assertAlmostEqual(value(rs[19],"price_to_ema_20_pct"),100*(D(20)-D("10.5"))/D("10.5"),places=17)
    def test_rsi_zero_cases(self):
        for values,expected in ((list(range(100,116)),100),(list(range(116,100,-1)),0),([100]*16,50)):
            with self.subTest(expected=expected):
                rs=build(rows(closes=values))
                self.assertIsNone(value(rs[13],"rsi_14"))
                self.assertEqual(value(rs[14],"rsi_14"),D(expected))
                self.assertEqual(value(rs[15],"rsi_14"),D(expected))
    def test_rsi_wilder_manual(self):
        closes=[100]+[102 if i%2 else 100 for i in range(1,15)]+[104]
        rs=build(rows(closes=closes))
        self.assertEqual(value(rs[14],"rsi_14"),D(50))
        # Seed gain=loss=1; next gain=4: G=17/14, L=13/14, RSI=170/3.
        self.assertAlmostEqual(value(rs[15],"rsi_14"),D(170)/3,places=17)
    def test_atr_seed_and_wilder_manual(self):
        rs=rows(16);rs[15].update(open="124",high="126",low="122",close="124")
        out=build(rs)
        self.assertIsNone(value(out[13],"atr_14"))
        self.assertEqual(value(out[14],"atr_14"),D(4))
        # Previous close 114: TR=12; Wilder (13*4+12)/14.
        self.assertAlmostEqual(value(out[15],"atr_14"),D(64)/14,places=17)
        self.assertAlmostEqual(value(out[15],"atr_pct"),100*(D(64)/14)/124,places=17)
    def test_volume_ma_ratio_manual(self):
        rs=build(rows(21))
        self.assertIsNone(value(rs[18],"volume_ma_20"))
        self.assertEqual(value(rs[19],"volume_ma_20"),D("10.5"))
        self.assertAlmostEqual(value(rs[19],"volume_ratio_20"),D(20)/D("10.5"),places=17)
    def test_volume_zero_denominator(self):
        rs=rows(21)
        for r in rs:r["volume"]="0"
        out=build(rs)
        self.assertEqual(value(out[-1],"volume_ma_20"),D(0))
        self.assertIsNone(value(out[-1],"volume_ratio_20"))
        rs[0]["volume"]="2"
        self.assertEqual(value(build(rs)[19],"volume_ratio_20"),D(0))
    def test_gap_preserves_indicator_readiness_but_remains_auditable(self):
        rs=rows(100);del rs[55]
        out=build(rs)
        # Missing provider slots must not erase 20/50-bar state for hours.
        for key in ("return_1","ema_20","ema_50","rsi_14","atr_14","volume_ma_20"):
            self.assertIsNotNone(value(out[55],key),key)
        # OI continuity still fails closed across the gap.
        reg=add_oi(rs)
        oi=build(rs,reg)
        self.assertIsNone(value(oi[55],"oi_change"))
    def test_invalid_price_restarts_price_not_volume(self):
        rs=rows(80);rs[55]["low"]="999"
        out=build(rs)
        self.assertIsNone(value(out[55],"ema_20"))
        self.assertIsNotNone(value(out[55],"volume_ma_20"))
        self.assertIsNone(value(out[56],"return_1"))
        self.assertEqual(value(out[75],"ema_20"),D("165.5"))
    def test_invalid_volume_preserves_price(self):
        rs=rows(80);rs[55]["volume"]="-1"
        out=build(rs)
        self.assertIsNotNone(value(out[55],"ema_20"))
        self.assertIsNone(value(out[55],"volume_ma_20"))
        self.assertIsNone(value(out[74],"volume_ma_20"))
        self.assertIsNotNone(value(out[75],"volume_ma_20"))
    def test_invalid_numeric_no_fill(self):
        for bad in (None,"NaN","Infinity",True,"bad","0"):
            with self.subTest(bad=bad):
                rs=rows(55);rs[-1]["close"]=bad
                self.assertIsNone(value(build(rs)[-1],"ema_20"))
    def test_decimal_half_even(self):
        self.assertEqual(encode(D("1.0000000000000000005")),"1.000000000000000000")
        self.assertEqual(encode(D("1.0000000000000000015")),"1.000000000000000002")
    def test_duplicate_rejected(self):
        rs=rows(2);rs.append(deepcopy(rs[-1]))
        with self.assertRaisesRegex(ValueError,"duplicate"):build(rs)
    def test_current_bar_excluded_rolling_manual(self):
        rs=rows(51);rs[-1].update(open="1000",high="1002",low="1",close="1000")
        result=build(rs)[-1]
        for n in (20,50):
            self.assertEqual(value(result,f"rolling_high_{n}"),D(151))
            self.assertEqual(value(result,f"rolling_low_{n}"),D(148-n))
            self.assertTrue(value(result,f"breakout_{n}"))
            self.assertAlmostEqual(value(result,f"distance_to_rolling_high_{n}_pct"),100*(D(151)-1000)/151,places=17)
    def test_breakout_equality_and_distance_signed(self):
        rs=rows(21);rs[-1].update(open="121",high="123",low="119",close="121")
        r=build(rs)[-1]
        self.assertFalse(value(r,"breakout_20"));self.assertEqual(value(r,"distance_to_rolling_high_20_pct"),D(0))
        rs[-1].update(open="97",high="99",low="95",close="97")
        r=build(rs)[-1]
        self.assertTrue(value(r,"breakdown_20"));self.assertLess(value(r,"distance_to_rolling_low_20_pct"),0)
    def test_rolling_warmup(self):
        rs=build(rows(51))
        for n in (20,50):
            self.assertIsNone(value(rs[n-1],f"rolling_high_{n}"))
            self.assertIsNotNone(value(rs[n],f"rolling_high_{n}"))


class StructureTests(unittest.TestCase):
    def fixture(self, highs):
        rs=rows(closes=[100]*len(highs))
        for r,h in zip(rs,highs):r["high"]=str(h)
        return rs
    def test_pivot_confirmation_not_retroactive(self):
        rs=self.fixture([101,102,105,102,101,102,106,102,101])
        out=build(rs)
        self.assertIsNone(value(out[2],"pivot_high"));self.assertIsNone(value(out[3],"pivot_high"))
        p=value(out[4],"pivot_high")
        self.assertEqual(p["pivot_time"],iso(rs[2]["time"]))
        self.assertEqual(p["confirmed_at"],iso(rs[4]["time"]+DURATIONS["15m"]))
        self.assertEqual(value(out[8],"structure_high_state"),"HH")
    def test_strict_ties_and_pair_insufficient(self):
        rs=self.fixture([101,102,105,105,101,102,103])
        self.assertIsNone(value(build(rs)[-1],"pivot_high"))
        rs=self.fixture([101,102,105,102,101])
        self.assertIsNone(value(build(rs)[-1],"structure_high_state"))
    def test_high_lh_eq_and_low_hl_ll_eq(self):
        for second,expected in ((104,"LH"),(105,"EQ")):
            self.assertEqual(value(build(self.fixture([101,102,105,102,101,102,second,102,101]))[-1],"structure_high_state"),expected)
        for second,expected in ((96,"HL"),(94,"LL"),(95,"EQ")):
            rs=rows(closes=[100]*9)
            for r,l in zip(rs,[99,98,95,98,99,98,second,98,99]):r["low"]=str(l)
            self.assertEqual(value(build(rs)[-1],"structure_low_state"),expected)
    def test_gap_preserves_confirmed_pivot_context(self):
        rs=self.fixture([101,102,105,102,101,102,106,102,101]);rs[5:]=[{**r,"time":r["time"]+900000,"candle_time_utc":iso(r["time"]+900000)} for r in rs[5:]]
        self.assertEqual(value(build(rs)[-1],"structure_high_state"),"HH")


class OITests(unittest.TestCase):
    def test_manual_same_segment(self):
        rs=rows(3);registry=add_oi(rs,values=[100,110,121]);out=build(rs,registry)
        self.assertIsNone(value(out[0],"oi_change"))
        self.assertEqual(value(out[1],"oi_change"),D(10));self.assertEqual(value(out[1],"oi_change_pct"),D(10))
        self.assertEqual(out[1]["oi_metadata"]["segment_id"],out[2]["oi_metadata"]["segment_id"])
    def test_all_nine_exact_raw_states(self):
        for p,ps in ((-1,"DOWN"),(0,"FLAT"),(1,"UP")):
            for o,os in ((-1,"DOWN"),(0,"FLAT"),(1,"UP")):
                rs=rows(closes=[100,D(100)+D(p)/1000000000]);reg=add_oi(rs,values=[100,D(100)+D(o)/1000000000])
                self.assertEqual(value(build(rs,reg)[-1],"price_oi_state"),f"PRICE_{ps}_OI_{os}")
    def test_basis_provider_boundary(self):
        rs=rows(4);reg=add_oi(rs[:2]);reg+=add_oi(rs[2:],provider="tradingview")
        out=build(rs,reg)
        self.assertIsNone(value(out[2],"oi_change"));self.assertIsNotNone(value(out[3],"oi_change"))
        self.assertNotEqual(out[1]["oi_metadata"]["segment_id"],out[2]["oi_metadata"]["segment_id"])
    def test_oi_missing_gap_unit_alignment_boundaries(self):
        for mutation in ("missing","gap","unit","alignment"):
            rs=rows(4);reg=add_oi(rs)
            if mutation=="missing":rs[1]["oi"]=None
            if mutation=="unit":rs[1]["oi_unit"]="contracts"
            if mutation=="alignment":rs[1]["oi_timestamp_ms"]+=1
            if mutation=="gap":
                for r in rs[2:]:r["time"]+=900000;r["candle_time_utc"]=iso(r["time"]);r["oi_timestamp_ms"]=r["time"]
            out=build(rs,reg)
            self.assertIsNone(value(out[2],"oi_change"),mutation)
            self.assertIsNotNone(value(out[3],"oi_change"),mutation)
    def test_unit_unknown_and_legacy_basis(self):
        rs=rows(2);add_oi(rs)
        out=build(rs)
        self.assertEqual(value(out[1],"oi_absolute"),D(101));self.assertIsNone(value(out[1],"oi_change"))
        self.assertEqual(out[1]["oi_metadata"]["null_reason"],"oi_unit_unverified")
        rs=rows(2)
        for r in rs:r["oi"]=10
        self.assertEqual(build(rs)[1]["oi_metadata"]["null_reason"],"oi_basis_unverified")
    def test_zero_previous_oi(self):
        rs=rows(2);reg=add_oi(rs,values=[0,2]);r=build(rs,reg)[1]
        self.assertEqual(value(r,"oi_change"),D(2));self.assertIsNone(value(r,"oi_change_pct"))
    def test_price_source_transition_blocks_state_only(self):
        rs=rows(3);reg=add_oi(rs);rs[1]["source"]="different";rs[2]["source"]="different"
        out=build(rs,reg)
        self.assertIsNotNone(value(out[1],"oi_change"));self.assertIsNone(value(out[1],"price_oi_state"))
        self.assertIsNotNone(value(out[2],"price_oi_state"))
    def test_timeframe_registry_independent(self):
        rs=rows(2,"1h");reg=add_oi(rs);reg[0]["timeframe"]="15m"
        self.assertIsNone(value(build(rs,reg)[1],"oi_change"))


class LeakageTests(unittest.TestCase):
    def test_future_suffix_invariance(self):
        rs=rows(90);reg=add_oi(rs)
        prefix=build(rs[:65],reg)
        entire=build(rs,reg)
        self.assertEqual(prefix,entire[:65])
    def test_timeframe_independence(self):
        datasets={tf:rows(55,tf) for tf in DURATIONS}
        _,a=build_dataset(datasets,START+100*86400000)
        datasets["1d"][-1]["volume"]="999"
        _,b=build_dataset(datasets,START+100*86400000)
        for tf in ("15m","1h","4h"):
            self.assertEqual([x["features"] for x in a[tf]],[x["features"] for x in b[tf]])
    def test_snapshot_availability_exact_boundary(self):
        rs=rows(2);second=rs[1]["time"]+900000+5000
        series={"15m":build_timeframe(rs,"15m",availability(rs,{rs[1]["time"]:second}))}
        self.assertEqual(synchronize(series,second-1)["timeframes"]["15m"]["record"]["time"],rs[0]["time"])
        self.assertEqual(synchronize(series,second)["timeframes"]["15m"]["record"]["time"],rs[1]["time"])
    def test_unobserved_not_historical_replay(self):
        rs=rows(55)
        for r in rs:r["received_at_utc"]=iso(r["time"]+900000)
        snap=synchronize({"15m":build(rs,evidence=False)},rs[-1]["time"]+900000)
        self.assertIsNone(snap["timeframes"]["15m"]["record"])
    def test_late_backfill_dependency_not_used_in_past(self):
        rs=rows(55);late=rs[-1]["time"]+900000+100000
        evidence=availability(rs,{rs[5]["time"]:late})
        out=build_timeframe(rs,"15m",evidence)
        now=rs[-1]["time"]+900000
        before=synchronize({"15m":out},now)["timeframes"]["15m"]["record"]
        self.assertIsNone(before["features"]["ema_20"])
        self.assertIsNotNone(before["features"]["return_1"])
        after=synchronize({"15m":out},late)["timeframes"]["15m"]["record"]
        self.assertIsNotNone(after["features"]["ema_20"])
    def test_latest_anchor_not_old_ready_fallback(self):
        rs=rows(60);rs[-1]["close"]=None
        r=synchronize({"15m":build(rs)},rs[-1]["time"]+900000)["timeframes"]["15m"]["record"]
        self.assertEqual(r["time"],rs[-1]["time"]);self.assertIsNone(r["features"]["ema_20"])
    def test_in_progress_and_boundary_excluded(self):
        rs=rows(2);rs[1]["is_closed"]=False
        r=synchronize({"15m":build(rs)},rs[1]["time"]+900000)["timeframes"]["15m"]["record"]
        self.assertEqual(r["time"],rs[0]["time"])
    def test_received_later_than_observation(self):
        rs=rows(1);end=rs[0]["time"]+900000;rs[0]["received_at_utc"]=iso(end+20000)
        out=build(rs);self.assertEqual(out[0]["available_at_ms"],end+20000)
    def test_pivot_delayed_dependency_confirmation(self):
        rs=rows(closes=[100]*5)
        for r,h in zip(rs,[101,102,105,102,101]):r["high"]=h
        late=rs[-1]["time"]+900000+10000
        out=build_timeframe(rs,"15m",availability(rs,{rs[1]["time"]:late}))
        self.assertEqual(out[-1]["features"]["pivot_high"]["confirmed_at"],iso(late))
        self.assertIsNone(synchronize({"15m":out},late-1)["timeframes"]["15m"]["record"]["features"]["pivot_high"])
    def test_raw_immutability_and_determinism(self):
        rs=rows(60);original=deepcopy(rs)
        a=build(rs);b=build(rs)
        self.assertEqual(rs,original);self.assertEqual(a,b)
    def test_stale_flags(self):
        rs=rows(55);now=rs[-1]["time"]+900000+900000+960001
        self.assertTrue(synchronize({"15m":build(rs)},now)["timeframes"]["15m"]["stale"])
    def test_synchronized_tf_close_and_availability(self):
        datasets={tf:rows(2,tf) for tf in DURATIONS}
        series={tf:build(rs) for tf,rs in datasets.items()}
        snap=synchronize(series,START+2*900000)
        self.assertIsNotNone(snap["timeframes"]["15m"]["record"])
        for tf in ("1h","4h","1d"):self.assertIsNone(snap["timeframes"][tf]["record"])
    def test_versioned_exclusive_output_and_raw_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            repo=Path(folder);raw=repo/"data_market/raw";raw.parent.mkdir();raw.write_bytes(b"immutable")
            manifest,series=build_dataset({"15m":rows(2)},START+86400000)
            before=protected_hashes(repo)
            path=write_derived(repo,manifest,series,synchronize(series,START+86400000))
            self.assertIn("data_features",str(path));self.assertEqual(protected_hashes(repo),before)
            with self.assertRaises(FileExistsError):write_derived(repo,manifest,series,{})
    def test_loader_preserves_decimal_text(self):
        with tempfile.TemporaryDirectory() as folder:
            repo=Path(folder)
            for tf in DURATIONS:
                path=repo/"data_market/btc_anytime"/tf;path.mkdir(parents=True)
                r=rows(1,tf)[0]
                text=json.dumps(r).replace('"volume": "1"','"volume": 1.1234567890123456789')
                (path/f"btc_{tf}_history.jsonl").write_text(text+"\n",encoding="utf-8")
            loaded,_,_=load_raw(repo)
            self.assertEqual(loaded["15m"][0]["volume"],"1.1234567890123456789")


class ContractTests(unittest.TestCase):
    def test_observation_before_close_is_not_completed(self):
        rs=rows(1);ev=availability(rs);ev[rs[0]["time"]]["observed_at_ms"]=rs[0]["time"]+1000
        result=build_timeframe(rs,"15m",ev)
        self.assertFalse(result[0]["input_validity"]["boundary"])
        self.assertIsNone(synchronize({"15m":result},rs[0]["time"]+900000)["timeframes"]["15m"]["record"])
    def test_incompatible_snapshot_contract_rejected(self):
        out=build(rows(2));out[-1]["timeframe"]="1h"
        with self.assertRaises(ValueError):synchronize({"15m":out},START+86400000)
        for bad in (True,-1,"2026-10-01"):
            with self.assertRaises(ValueError):synchronize({},bad)
    def test_wrong_market_invalidates_indicators(self):
        rs=rows(55);rs[-1]["market"]="BINANCE_SPOT"
        result=build(rs)[-1]
        self.assertFalse(result["input_validity"]["boundary"])
        self.assertIsNone(value(result,"ema_20"))
    def test_explicit_oi_provider_instrument_boundary(self):
        for key,bad in (("oi_provider","other"),("oi_instrument","OTHER_PERPETUAL")):
            rs=rows(4);reg=add_oi(rs);rs[1][key]=bad
            out=build(rs,reg)
            self.assertIsNone(value(out[2],"oi_change"))
            self.assertIsNotNone(value(out[3],"oi_change"))
    def test_output_manifest_tampering_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest,series=build_dataset({"15m":rows(2)},START+86400000)
            manifest["manifest_id"]="../../output"
            with self.assertRaises(ValueError):write_derived(Path(folder),manifest,series,{})
            self.assertEqual(list(Path(folder).iterdir()),[])
    def test_snapshot_suffix_invariance(self):
        rs=rows(90);time=rs[60]["time"]+900000
        prefix=synchronize({"15m":build(rs[:61])},time)
        whole=synchronize({"15m":build(rs)},time)
        self.assertEqual(prefix,whole)
    def test_pivot_pair_not_expired_after_200_bars(self):
        rs=rows(closes=[100]*230)
        for r,h in zip(rs[:9],[101,102,105,102,101,102,106,102,101]):r["high"]=h
        # Remaining equal highs cannot form strict pivots.
        self.assertEqual(value(build(rs)[-1],"structure_high_state"),"HH")

    def test_result_tampering_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest,series=build_dataset({"15m":rows(2)},START+86400000)
            series["15m"][0]["features"]["oi_absolute"]="999"
            with self.assertRaisesRegex(ValueError,"result hash"):write_derived(Path(folder),manifest,series,{})
            self.assertEqual(list(Path(folder).iterdir()),[])
    def test_manifest_and_result_versions_reproducible(self):
        inputs={"15m":rows(55)}
        a=build_dataset(inputs,START+86400000,git_revision="fixture revision")
        b=build_dataset(inputs,START+86400000,git_revision="fixture revision")
        self.assertEqual(a,b)
        manifest,series=a
        self.assertEqual(manifest["manifest_id"],digest({k:v for k,v in manifest.items() if k!="manifest_id"}))
        for record in series["15m"]:
            self.assertEqual(record["result_hash"],digest({k:v for k,v in record.items() if k!="result_hash"}))

    def test_invalid_oi_is_not_an_unavailable_observation(self):
        for invalid in ("-1","NaN",True,"bad"):
            rs=rows(3);reg=add_oi(rs);rs[1]["oi"]=invalid
            out=build(rs,reg)
            self.assertFalse(out[1]["input_validity"]["oi"])
            self.assertIsNone(value(out[1],"oi_absolute"))
            self.assertIsNone(value(out[2],"oi_change"))
        rs=rows(2);reg=add_oi(rs);rs[1]["oi_status"]="unavailable"
        self.assertEqual(build(rs,reg)[1]["oi_metadata"]["null_reason"],"oi_status_conflict")
    def test_oscillating_pivot_oi_suffix_invariance(self):
        rs=rows(70)
        for i,r in enumerate(rs):
            c=D(100)+(D(i%7)-3)
            r.update(open=str(c),close=str(c),high=str(c+2),low=str(c-2))
        reg=add_oi(rs,values=[100+i%5 for i in range(70)])
        a=build(rs[:45],reg);b=build(rs,reg)
        self.assertEqual(a,b[:45])
        self.assertIsNotNone(a[-1]["features"]["structure_high_state"])
        self.assertIsNotNone(a[-1]["features"]["structure_low_state"])

    def test_source_transition_and_pivot_pair_provenance(self):
        rs=rows(closes=[100]*9)
        for r,h in zip(rs,[101,102,105,102,101,102,106,102,101]):r["high"]=h
        rs[-1]["source"]="other official observation"
        result=build(rs)[-1]
        quality=result["feature_quality"]["structure_high_state"]
        self.assertTrue(quality["mixed_sources"]);self.assertTrue(quality["source_transition"])
        self.assertEqual(len(quality["pivot_pair"]),2)
        self.assertEqual([p["pivot_time"] for p in quality["pivot_pair"]],[iso(rs[2]["time"]),iso(rs[6]["time"])])
        self.assertTrue(all(p["confirmed_at"] for p in quality["pivot_pair"]))


if __name__=="__main__":unittest.main()

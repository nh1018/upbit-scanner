"""OI contract evidence and actual as-of clocks; existing formulas remain unchanged."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from btc_anytime.integrity import DURATIONS,iso,utc_ms
from btc_anytime.features.registry import load_registry,identity,contract_matches,DEFAULT_REGISTRY
from btc_anytime.features.engine import build_timeframe,digest
from btc_anytime.features.build import build_dataset,protected_hashes
from btc_anytime.features.snapshot import synchronize
from btc_anytime.features.availability import (observation_event,append_event,load_observations,evidence_map,
                                             mark_generated,event_id,validate_observation,validate_generation,EVENT_VERSION)
from btc_anytime.tests.test_features import rows,availability,add_oi,value,START

REPO=Path(__file__).resolve().parents[2]
REVISION="1"*40


def tv_rows(tf="1h",n=4):
    rs=rows(n,tf);add_oi(rs,"tradingview")
    for r in rs:r.update(market="BINANCE_USDT_M_FUTURES",source="tradingview_binance_usdm_htf",schema_version="btc-anytime-htf-v1",
                        received_at_utc=iso(r["time"]+DURATIONS[tf]))
    return rs


def approved_contract(tf="1h"):
    _,units=load_registry(REPO)
    return deepcopy(next(c for c in units if c["timeframe"]==tf))


def fixture_events(rs,observed=None):
    observed=observed if observed is not None else rs[-1]["time"]+DURATIONS[rs[0]["timeframe"]]+100
    return observation_event({rs[0]["timeframe"]:rs},{},REVISION,observed)


class RegistryTests(unittest.TestCase):
    def test_official_symbol_unit_evidence(self):
        registry,units=load_registry(REPO)
        self.assertEqual(len(units),3)
        self.assertTrue(all(c["unit"]=="BTC" and c["basis"]=="confirmed_oi_bar_close_boundary" for c in units))
        pending=[c for c in registry["contracts"] if c["provider"]=="binance"]
        self.assertEqual(len(pending),4)
        self.assertTrue(all(c["unit"] is None for c in pending))
    def test_continuous_validated_contract_change(self):
        rs=tv_rows();contract=approved_contract()
        result=build_timeframe(rs,"1h",availability(rs),[contract])
        self.assertIsNone(value(result[0],"oi_change"))
        self.assertEqual(value(result[1],"oi_change"),1)
        self.assertIsNotNone(value(result[1],"oi_change_pct"))
        self.assertEqual(len({r["oi_metadata"]["segment_id"] for r in result}),1)
    def test_exact_source_schema_scope(self):
        for key,bad in (("schema_version","other"),("source","other"),("symbol","OTHER"),("market","SPOT")):
            rs=tv_rows();rs[1][key]=bad
            result=build_timeframe(rs,"1h",availability(rs),[approved_contract()])
            self.assertIsNone(value(result[1],"oi_change"),key)
            self.assertIsNone(value(result[2],"oi_change"),key)
    def test_unit_mismatch_reset(self):
        rs=tv_rows();rs[1]["oi_unit"]="USDT"
        result=build_timeframe(rs,"1h",availability(rs),[approved_contract()])
        self.assertIsNone(value(result[1],"oi_change"));self.assertIsNone(value(result[2],"oi_change"))
        self.assertIsNotNone(value(result[3],"oi_change"))
    def test_provider_instrument_tf_mismatch_reset(self):
        for key,bad in (("oi_provider","binance"),("oi_instrument","OTHER"),("oi_timeframe","4h")):
            rs=tv_rows();rs[1][key]=bad
            result=build_timeframe(rs,"1h",availability(rs),[approved_contract()])
            self.assertIsNone(value(result[2],"oi_change"),key)
            self.assertIsNotNone(value(result[3],"oi_change"),key)
    def test_unavailable_reset(self):
        rs=tv_rows();rs[1].update(oi=None,oi_status="unavailable")
        result=build_timeframe(rs,"1h",availability(rs),[approved_contract()])
        self.assertIsNone(value(result[2],"oi_change"));self.assertIsNotNone(value(result[3],"oi_change"))
    def test_gap_reset(self):
        rs=tv_rows(n=5);del rs[2]
        result=build_timeframe(rs,"1h",availability(rs),[approved_contract()])
        self.assertIsNone(value(result[2],"oi_change"));self.assertIsNotNone(value(result[3],"oi_change"))
    def test_historical_live_basis_reset_even_same_unit(self):
        rs=rows(4,"1h");historical=add_oi(rs[:2]);add_oi(rs[2:],"tradingview")
        # Arithmetic regression fixture: both units independently verified in the fixture.
        live=add_oi(rs[2:],"tradingview")
        result=build_timeframe(rs,"1h",availability(rs),historical+live)
        self.assertIsNotNone(value(result[1],"oi_change"));self.assertIsNone(value(result[2],"oi_change"))
        self.assertIsNotNone(value(result[3],"oi_change"))
    def test_registry_validation_not_retroactively_available(self):
        rs=tv_rows();contract=approved_contract();time=rs[-1]["time"]+DURATIONS["1h"]
        contract["validation_available_at_ms"]=time+1000
        result=build_timeframe(rs,"1h",availability(rs),[contract])
        snap=synchronize({"1h":result},time)["timeframes"]["1h"]["record"]
        self.assertIsNone(snap["features"]["oi_change"])
        self.assertIsNotNone(snap["features"]["oi_absolute"])
    def test_registry_identity_tamper_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            registry=json.loads(DEFAULT_REGISTRY.read_text(encoding="utf-8"));registry["contracts"][0]["unit"]="BTC"
            path=Path(folder)/"registry.json";path.write_text(json.dumps(registry),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"identity"):load_registry(REPO,path)
    def test_no_unit_inference_for_binance(self):
        rs=rows(3,"1h");add_oi(rs)
        _,units=load_registry(REPO)
        result=build_timeframe(rs,"1h",availability(rs),units)
        self.assertTrue(all(r["features"]["oi_change"] is None for r in result))
    def test_historical_url_scope_exact_field(self):
        contract={**approved_contract(),"provider":"binance","symbol":"BTCUSDT","basis":"source_timestamp_equals_candle_open"}
        row={"symbol":"BTCUSDT","market":"BINANCE_USDT_M_FUTURES","oi_source_url":"https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=1h","oi_source_response_sha256":"a"*64}
        self.assertTrue(contract_matches(row,"1h",contract))
        row["oi_source_url"]=row["oi_source_url"].replace("1h","4h")
        self.assertFalse(contract_matches(row,"1h",contract))
    def test_feature_formulas_unchanged(self):
        rs=tv_rows(n=60);original=deepcopy(rs)
        a=build_timeframe(rs,"1h",availability(rs))
        b=build_timeframe(rs,"1h",availability(rs),[approved_contract()])
        for left,right in zip(a,b):
            self.assertEqual({k:v for k,v in left["features"].items() if k not in ("oi_change","oi_change_pct","price_oi_state")},
                             {k:v for k,v in right["features"].items() if k not in ("oi_change","oi_change_pct","price_oi_state")})
        self.assertEqual(rs,original)


class AvailabilityTests(unittest.TestCase):
    def test_initial_inventory_no_fake_past_availability(self):
        rs=rows(3);now=START+86400000;event=fixture_events(rs,now)
        mapping=evidence_map([event],{"15m":rs})
        self.assertTrue(all(e["observed_at_ms"]==now for e in mapping["15m"].values()))
        self.assertTrue(all(i["historical_availability_status"]=="unavailable" for i in event["observations"]))
        series=build_timeframe(rs,"15m",mapping["15m"])
        self.assertIsNone(synchronize({"15m":series},now-1)["timeframes"]["15m"]["record"])
    def test_new_live_only_and_noop(self):
        rs=tv_rows();first=fixture_events(rs[:2]);next_event=observation_event({"1h":rs},{},REVISION,START+86400000,previous=[first])
        self.assertEqual(len(next_event["observations"]),2)
        self.assertTrue(all(i["observation_role"]=="new_live_observation" for i in next_event["observations"]))
        self.assertIsNone(observation_event({"1h":rs},{},REVISION,START+86400001,previous=[first,next_event]))
    def test_first_observation_is_retained(self):
        rs=rows(3);first=fixture_events(rs,START+86400000);later=fixture_events(rs,START+2*86400000)
        mapping=evidence_map([later,first],{"15m":rs})
        self.assertTrue(all(x["observed_at_ms"]==START+86400000 for x in mapping["15m"].values()))
    def test_late_backfill_not_used_in_past(self):
        rs=rows(60);old=[r for i,r in enumerate(rs) if i!=5]
        first=fixture_events(old,START+86400000)
        late=observation_event({"15m":rs},{},REVISION,START+2*86400000,previous=[first])
        self.assertEqual(late["observations"][0]["observation_role"],"late_reference_observation")
        mapping=evidence_map([first,late],{"15m":rs})
        series=build_timeframe(rs,"15m",mapping["15m"])
        snap=synchronize({"15m":series},START+86400000)["timeframes"]["15m"]["record"]
        self.assertIsNone(snap["features"]["ema_20"])
        self.assertIsNotNone(snap["features"]["return_1"])
    def test_generation_and_decision_time_distinct(self):
        rs=rows(55);raw_time=rs[-1]["time"]+900000
        _,series=build_dataset({"15m":rs},raw_time)
        generated=mark_generated(series,raw_time+2000,"actual_run")
        self.assertIsNone(synchronize(generated,raw_time+1000)["timeframes"]["15m"]["record"]["features"]["ema_20"])
        self.assertIsNotNone(synchronize(generated,raw_time+2000)["timeframes"]["15m"]["record"]["features"]["ema_20"])
    def test_generation_cannot_precede_observation(self):
        rs=rows(3);_,series=build_dataset({"15m":rs},START+86400000)
        with self.assertRaises(ValueError):mark_generated(series,START+86400000-1,"fake_run")
    def test_committer_clock_not_availability(self):
        rs=rows(3);now=START+86400000
        event=observation_event({"15m":rs},{},REVISION,now,iso(START))
        self.assertEqual(evidence_map([event],{"15m":rs})["15m"][rs[0]["time"]]["observed_at_ms"],now)
    def test_source_received_does_not_replace_repository_observation(self):
        rs=tv_rows();now=START+86400000;event=fixture_events(rs,now)
        mapping=evidence_map([event],{"1h":rs})
        self.assertEqual(mapping["1h"][rs[0]["time"]]["observed_at_ms"],now)
        self.assertEqual(event["observations"][0]["source_received_at_utc"],rs[0]["received_at_utc"])
    def test_append_only_and_immutable_raw(self):
        with tempfile.TemporaryDirectory() as folder:
            repo=Path(folder);p=repo/"data_market/fixture";p.parent.mkdir();p.write_bytes(b"immutable")
            before=protected_hashes(repo);event=fixture_events(rows(3));original=deepcopy(event)
            path=append_event(repo,event);saved=path.read_bytes()
            self.assertEqual(append_event(repo,event),path);self.assertEqual(path.read_bytes(),saved)
            self.assertEqual(event,original);self.assertEqual(protected_hashes(repo),before)
            self.assertEqual(load_observations(repo),[event])
    def test_event_tampering_fails(self):
        event=fixture_events(rows(3));event["repository_observed_at_utc"]=iso(START)
        with self.assertRaises(ValueError):validate_observation(event)
    def test_raw_row_change_fails(self):
        rs=rows(3);event=fixture_events(rs);rs[0]["oi"]=999
        with self.assertRaisesRegex(ValueError,"row changed"):observation_event({"15m":rs},{},REVISION,START+86400000,previous=[event])
        with self.assertRaisesRegex(ValueError,"hash conflict"):evidence_map([event],{"15m":rs})
    def test_future_source_time_fails(self):
        rs=rows(3);rs[0]["retrieved_at_utc"]=iso(START+3*86400000)
        with self.assertRaises(ValueError):fixture_events(rs,START+86400000)
    def test_explicit_evidence_missing_does_not_default_to_now(self):
        rs=rows(55);_,series=build_dataset({"15m":rs},START+86400000,availability_evidence={"15m":{}})
        self.assertTrue(all(r["available_at_ms"] is None for r in series["15m"]))
    def test_future_generation_event_fails(self):
        e={"schema_version":EVENT_VERSION,"kind":"feature_generation","feature_generated_at_utc":iso(START+2000),"snapshot_decision_time_utc":iso(START+1000),"snapshot_reference":{"anchors":{}}}
        e["event_id"]=event_id(e)
        with self.assertRaises(ValueError):validate_generation(e)


class ObserverIntegrationTests(unittest.TestCase):
    def test_text_evidence_hash_survives_git_line_endings(self):
        from btc_anytime.features.registry import text_hash
        self.assertEqual(text_hash(b"first\r\nsecond\r\n"),text_hash(b"first\nsecond\n"))
        self.assertNotEqual(text_hash(b"first\nsecond\n"),text_hash(b"first\nchanged\n"))
    def repo_fixture(self,repo):
        import subprocess
        for tf in DURATIONS:
            path=repo/"data_market/btc_anytime"/tf;path.mkdir(parents=True)
            (path/f"btc_{tf}_history.jsonl").write_text("".join(json.dumps(r)+"\n" for r in rows(2,tf)),encoding="utf-8")
        subprocess.run(["git","init","-q","-b","main"],cwd=repo,check=True)
        self.commit_raw(repo)
    def commit_raw(self,repo):
        import subprocess
        subprocess.run(["git","add","--","data_market"],cwd=repo,check=True)
        subprocess.run(["git","-c","user.name=Fixture","-c","user.email=fixture@example.invalid","commit","-q","-m","Fixture raw"],cwd=repo,check=True)
    def test_observer_dry_run_and_record_noop_end_to_end(self):
        from unittest.mock import patch
        from btc_anytime.features.observe import observe
        with tempfile.TemporaryDirectory() as folder:
            repo=Path(folder);self.repo_fixture(repo);before=protected_hashes(repo)
            with patch("btc_anytime.features.observe.load_registry",return_value=({"registry_id":"fixture"},[])):
                result=observe(repo)
                self.assertEqual(result["new_observations"],8);self.assertEqual(result["written"],[])
                self.assertFalse((repo/"metadata_features").exists())
                first=observe(repo,True);self.assertEqual(len(first["written"]),2)
                original={p:p.read_bytes() for p in (repo/"metadata_features").rglob("*.json")}
                second=observe(repo,True)
                self.assertEqual(second["new_observations"],0);self.assertEqual(second["written"],[])
                self.assertEqual(original,{p:p.read_bytes() for p in original})
                self.assertEqual(protected_hashes(repo),before)
    def test_observer_new_row_evidence_only_and_no_overwrite(self):
        from unittest.mock import patch
        from btc_anytime.features.observe import observe
        with tempfile.TemporaryDirectory() as folder:
            repo=Path(folder);self.repo_fixture(repo)
            with patch("btc_anytime.features.observe.load_registry",return_value=({"registry_id":"fixture"},[])):
                observe(repo,True)
                original={p:p.read_bytes() for p in (repo/"metadata_features").rglob("*.json")}
                path=repo/"data_market/btc_anytime/15m/btc_15m_history.jsonl"
                with path.open("a",encoding="utf-8") as f:f.write(json.dumps(rows(3)[2])+"\n")
                self.commit_raw(repo);result=observe(repo,True)
                self.assertEqual(result["new_observations"],1)
                self.assertTrue(all(p.read_bytes()==b for p,b in original.items()))
    def test_uncommitted_raw_is_not_given_repository_evidence(self):
        from btc_anytime.features.observe import observe
        with tempfile.TemporaryDirectory() as folder:
            repo=Path(folder);self.repo_fixture(repo)
            path=repo/"data_market/btc_anytime/15m/btc_15m_history.jsonl"
            with path.open("a",encoding="utf-8") as f:f.write(json.dumps(rows(3)[2])+"\n")
            with self.assertRaisesRegex(ValueError,"pinned revision"):observe(repo)
            self.assertFalse((repo/"metadata_features").exists())
    def test_interrupted_generation_recovers_without_backdating(self):
        from unittest.mock import patch
        from btc_anytime.features.observe import observe
        with tempfile.TemporaryDirectory() as folder:
            repo=Path(folder);self.repo_fixture(repo)
            with patch("btc_anytime.features.observe.load_registry",return_value=({"registry_id":"fixture"},[])):
                first=observe(repo,True)
                # Test fixture crash simulation: only delete the new generation file in this temporary repo.
                Path(first["written"][1]).unlink()
                result=observe(repo,True)
                self.assertEqual(result["new_observations"],0);self.assertTrue(result["recovered_generation"])
                self.assertEqual(len(load_observations(repo)),1)
    def test_untracked_raw_cannot_claim_committed_evidence(self):
        from btc_anytime.features.observe import observe
        with tempfile.TemporaryDirectory() as folder:
            repo=Path(folder);self.repo_fixture(repo)
            path=repo/"data_market/btc_anytime/15m/btc_15m_new.jsonl"
            path.write_text(json.dumps(rows(3)[2])+"\n",encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"untracked raw"):observe(repo)
            self.assertFalse((repo/"metadata_features").exists())


if __name__=="__main__":unittest.main()

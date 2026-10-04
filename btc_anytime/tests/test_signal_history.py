"""Prospective cadence, immutable storage, concurrency and failure isolation."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from btc_anytime.direction.signal_history.runner import run_history,EXPECTED_PARAMETER_HASH
from btc_anytime.direction.signal_history.storage import root,persist,boundary_id,indexed_decision,writer_lock
from btc_anytime.features.engine import digest
from btc_anytime.features.build import protected_hashes
from btc_anytime.integrity import DURATIONS,iso
from test_features import rows,START
T=START+100*86400000


class SignalHistoryTests(unittest.TestCase):
    def setup_repo(self,repo):
        for tf,duration in DURATIONS.items():
            rs=rows(65,tf)
            for i,r in enumerate(rs):r["time"]=T-(65-i)*duration;r["candle_time_utc"]=iso(r["time"])
            directory=repo/"data_market/btc_anytime"/tf;directory.mkdir(parents=True)
            (directory/f"btc_{tf}_history.jsonl").write_text("".join(json.dumps(r)+"\n" for r in rs),encoding="utf-8")
        subprocess.run(["git","init","-q","-b","main"],cwd=repo,check=True);self.commit(repo)
    def commit(self,repo):
        subprocess.run(["git","add","--","data_market"],cwd=repo,check=True)
        subprocess.run(["git","-c","user.name=Fixture","-c","user.email=fixture@example.invalid","commit","-q","-m","Fixture raw"],cwd=repo,check=True)
    def invoke(self,repo,time,record=False):
        with patch("btc_anytime.direction.signal_history.runner.now_ms",return_value=time),patch("btc_anytime.direction.signal_history.runner.load_registry",return_value=({"registry_id":"fixture"},[])):
            return run_history(repo,record)
    def append_live(self,repo,n=1,received=True):
        path=repo/"data_market/btc_anytime/15m/btc_15m_history.jsonl"
        for i in range(n):
            rs=[json.loads(x) for x in path.read_text().splitlines()];last=rs[-1]
            r=deepcopy(last);r["time"]+=900000;r["candle_time_utc"]=iso(r["time"])
            if received:r["received_at_utc"]=iso(r["time"]+900006)
            else:r.pop("received_at_utc",None);r["source"]="official_backfill"
            with path.open("a",encoding="utf-8") as f:f.write(json.dumps(r)+"\n")
        self.commit(repo)
    def ready(self,repo):
        self.setup_repo(repo);self.invoke(repo,T,True);self.append_live(repo)
    def test_dry_run_no_files_or_past_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.setup_repo(repo);before=protected_hashes(repo)
            r=self.invoke(repo,T)
            self.assertEqual(r["status"],"INITIALIZATION_READY");self.assertIsNone(r["decision"])
            self.assertFalse(root(repo).exists());self.assertEqual(protected_hashes(repo),before)
    def test_activation_excludes_all_existing_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.setup_repo(repo);r=self.invoke(repo,T,True)
            self.assertEqual(r["status"],"INITIALIZED");self.assertFalse((root(repo)/"decisions").exists())
            self.assertEqual(self.invoke(repo,T+10,True)["status"],"NO_NEW_BOUNDARY")
    def test_first_append_replay_and_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.ready(repo);before=protected_hashes(repo)
            first=self.invoke(repo,T+900010,True);self.assertEqual(first["status"],"STORED")
            original={p:p.read_bytes() for p in root(repo).rglob("*.json")}
            second=self.invoke(repo,T+900100,True);self.assertEqual(second["status"],"REPLAY_NOOP")
            self.assertEqual(first["decision"],second["decision"]);self.assertEqual(original,{p:p.read_bytes() for p in original})
            d=first["decision"];self.assertEqual(d["parameter_hash"],EXPECTED_PARAMETER_HASH)
            self.assertEqual(d["input_snapshot"]["timeframes"]["15m"]["record"]["time"],T)
            for tf,s in d["input_snapshot"]["timeframes"].items():
                self.assertLessEqual(s["record"]["time"]+DURATIONS[tf],T+900010)
                self.assertLessEqual(s["record"]["feature_generated_at_ms"],T+900010)
            self.assertEqual(protected_hashes(repo),before)
    def test_same_boundary_different_content_hard_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.ready(repo);d=self.invoke(repo,T+900010,True)["decision"]
            changed=deepcopy(d);changed["generated_at_utc"]=iso(T+900020);changed["decision_id"]=digest({k:v for k,v in changed.items() if k!="decision_id"})
            with self.assertRaisesRegex(ValueError,"boundary conflict"):persist(repo,changed)
            self.assertEqual(len(list((root(repo)/"decisions").glob("*.json"))),1)
    def test_same_id_content_conflict_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.ready(repo);d=self.invoke(repo,T+900010,True)["decision"]
            changed=deepcopy(d);changed["direction_class"]="SHORT"
            with self.assertRaises(ValueError):persist(repo,changed)
    def test_skipped_prior_boundaries_not_reconstructed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.setup_repo(repo);self.invoke(repo,T,True);self.append_live(repo,3)
            r=self.invoke(repo,T+2700010,True)
            self.assertEqual(r["status"],"STORED");self.assertEqual(r["decision"]["skipped_prior_15m_boundaries"],[T,T+900000])
            self.assertEqual(len(list((root(repo)/"decisions").glob("*.json"))),1)
    def test_historical_backfill_never_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.setup_repo(repo);self.invoke(repo,T,True);self.append_live(repo,received=False)
            r=self.invoke(repo,T+900010,True)
            self.assertEqual(r["status"],"SKIPPED");self.assertEqual(r["reason"],"NON_LIVE_OR_PRESTART_TRIGGER")
            self.assertFalse((root(repo)/"decisions").exists())
    def test_late_backfill_does_not_rewrite_old_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.ready(repo);old=self.invoke(repo,T+900010,True)["decision"]
            self.append_live(repo);self.invoke(repo,T+1800010,True)
            self.assertEqual(indexed_decision(repo,old["trigger_boundary_id"]),old)
    def test_required_evidence_missing_skips(self):
        from btc_anytime.features.snapshot import synchronize
        def missing(series,time,event):
            s=synchronize(series,time,event);s["timeframes"]["4h"]={"record":None};s["snapshot_id"]=digest({k:v for k,v in s.items() if k!="snapshot_id"});return s
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.ready(repo)
            with patch("btc_anytime.direction.signal_history.runner.synchronize",side_effect=missing):r=self.invoke(repo,T+900010,True)
            self.assertEqual(r["reason"],"REQUIRED_EVIDENCE_MISSING");self.assertFalse((root(repo)/"decisions").exists())
    def test_stale_required_tf_skips(self):
        from btc_anytime.features.snapshot import synchronize
        def stale(series,time,event):
            s=synchronize(series,time,event);s["timeframes"]["1h"]["stale"]=True;return s
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.ready(repo)
            with patch("btc_anytime.direction.signal_history.runner.synchronize",side_effect=stale):r=self.invoke(repo,T+900010,True)
            self.assertEqual(r["reason"],"REQUIRED_DATA_STALE")
    def test_insufficient_features_not_normal_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.ready(repo)
            with patch("btc_anytime.direction.signal_history.runner.evaluate",return_value={"regime":"INSUFFICIENT_EVIDENCE"}):r=self.invoke(repo,T+900010,True)
            self.assertEqual(r["reason"],"REQUIRED_FEATURES_MISSING")
    def test_engine_exception_isolated_from_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.ready(repo);before=protected_hashes(repo)
            with patch("btc_anytime.direction.signal_history.runner.evaluate",side_effect=RuntimeError("fixture engine failure")):r=self.invoke(repo,T+900010,True)
            self.assertEqual(r["status"],"FAILED");self.assertEqual(r["operational_event"]["details"]["stage"],"direction")
            self.assertEqual(protected_hashes(repo),before);self.assertFalse((root(repo)/"decisions").exists())
    def test_future_availability_never_used(self):
        from btc_anytime.features.availability import evidence_map
        def future(events,data):
            m=evidence_map(events,data)
            for x in m["4h"].values():x["observed_at_ms"]=T+900011
            return m
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.ready(repo)
            with patch("btc_anytime.direction.signal_history.runner.evidence_map",side_effect=future):r=self.invoke(repo,T+900010,True)
            self.assertEqual(r["status"],"FAILED");self.assertFalse((root(repo)/"decisions").exists())
    def test_parameter_pinned(self):
        from btc_anytime.direction.engine import load_parameters
        p=load_parameters();p["parameter_hash"]="f"*64
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.setup_repo(repo)
            with patch("btc_anytime.direction.signal_history.runner.load_parameters",return_value=p):r=self.invoke(repo,T,True)
            self.assertEqual(r["status"],"FAILED");self.assertEqual(r["operational_event"]["details"]["stage"],"contract")
    def test_writer_lock_excludes_concurrent_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.setup_repo(repo)
            with writer_lock(repo):
                with self.assertRaisesRegex(ValueError,"concurrent"):self.invoke(repo,T,True)
            self.assertFalse((root(repo)/".writer.lock").exists())
    def test_interrupted_index_recovered_without_recalculation(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.ready(repo);d=self.invoke(repo,T+900010,True)["decision"]
            # Temporary fixture only: simulate crash after decision append, before index.
            (root(repo)/"boundaries"/(d["trigger_boundary_id"]+".json")).unlink()
            with patch("btc_anytime.direction.signal_history.runner.evaluate",side_effect=AssertionError("must not recalculate")):
                r=self.invoke(repo,T+900020,True)
            self.assertEqual(r["status"],"REPLAY_NOOP");self.assertEqual(r["decision"],d)
            self.assertTrue((root(repo)/"boundaries"/(d["trigger_boundary_id"]+".json")).exists())
    def test_existing_decision_corruption_hard_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.ready(repo);d=self.invoke(repo,T+900010,True)["decision"]
            path=root(repo)/"decisions"/(d["decision_id"]+".json");path.write_text('{"decision_id":"corrupt"}',encoding="utf-8")
            r=self.invoke(repo,T+900020,True);self.assertEqual(r["status"],"FAILED")
            self.assertEqual(path.read_text(),'{"decision_id":"corrupt"}')
    def test_workflow_disabled_and_separate(self):
        path=Path(__file__).resolve().parents[2]/".github/workflows/btc-direction-signal-history.yml"
        text=path.read_text(encoding="utf-8")
        self.assertIn("if: ${{ false &&",text);self.assertIn("cancel-in-progress: false",text)
        self.assertIn("workflow_run:",text);self.assertIn("Only append-only signal ledger JSON",text)
        self.assertNotIn("--force",text)


if __name__=="__main__":unittest.main()

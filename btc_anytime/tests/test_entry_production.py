"""Production wiring fixtures; all writes confined to temporary repositories."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from contextlib import ExitStack
from btc_anytime.entry import production as P
from btc_anytime.tests.test_entry import fixture,direction,follow,run as evaluate_fixture
from btc_anytime.entry.engine import digest,parameters,seal_manifest,direction_ref,evaluate,STEP
from btc_anytime.integrity import iso


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.repo=Path(self.temp.name).resolve();self.xs=fixture()
        self.t=max(x["time"] for x in self.xs if x["timeframe"]=="15m");self.clock=self.t+STEP+300
        self.data={tf:[x["raw"] for x in self.xs if x["timeframe"]==tf] for tf in ("15m","1h")}
        self.refs={tf:{x["time"]:x["raw_ref"] for x in self.xs if x["timeframe"]==tf} for tf in self.data}
        self.evidence={tf:{x["time"]:x["availability_evidence"] for x in self.xs if x["timeframe"]==tf} for tf in self.data}
        self.d=direction(self.t,side="NEUTRAL");self.d.pop("decision_id");self.d["parameter_hash"]=P.DIRECTION_HASH;self.d["decision_id"]=digest(self.d)
        self.stack=ExitStack()
        self.stack.enter_context(patch.object(P,"now_ms",side_effect=lambda:self.clock))
        self.stack.enter_context(patch.object(P,"git_revision",return_value="a"*40))
        self.stack.enter_context(patch.object(P,"committed"))
        self.stack.enter_context(patch.object(P,"protected_all",return_value={"protected":"unchanged"}))
        self.stack.enter_context(patch.object(P,"load_raw",side_effect=lambda _: (self.data,self.refs,{})))
        self.stack.enter_context(patch.object(P,"upstream_inputs",side_effect=lambda _: ([self.d],self.data,self.refs,self.evidence)))
        self.stack.enter_context(patch.object(P,"load_registry",return_value=({},[])))
        def materialize(*args):
            xs=[x for x in self.xs if x["time"]+P.DURATIONS[x["timeframe"]]<=self.clock]
            return seal_manifest(xs,direction_ref(args[-1],self.clock),self.clock),self.clock
        self.stack.enter_context(patch.object(P,"materialize",side_effect=materialize))
    def tearDown(self):self.stack.close();self.temp.cleanup()
    def advance(self,side="NEUTRAL"):
        self.xs=follow(self.xs);self.t+=STEP;self.clock+=STEP
        x=self.xs[-1];self.data["15m"].append(x["raw"]);self.refs["15m"][self.t]=x["raw_ref"];self.evidence["15m"][self.t]=x["availability_evidence"]
        self.d=direction(self.t,side=side);self.d.pop("decision_id");self.d["parameter_hash"]=P.DIRECTION_HASH;self.d["decision_id"]=digest(self.d)
        folder=self.repo/"output_direction/btc_anytime/v1/decisions";folder.mkdir(parents=True,exist_ok=True)
        (folder/(self.d["decision_id"]+".json")).write_text(json.dumps(self.d),encoding="utf-8")
    def initialized(self):return P.run(self.repo,True)
    def first(self):self.initialized();self.advance();return P.run(self.repo,True)
    def test_baseline_only(self):
        r=self.initialized();self.assertEqual(r["status"],"INITIALIZED");self.assertIsNone(r["evaluation"]);self.assertEqual(len(list(P.root(self.repo).rglob("*.json"))),1)
    def test_baseline_not_evaluated(self):
        self.initialized();self.assertEqual(P.run(self.repo,True)["status"],"NO_NEW_BOUNDARY")
    def test_initial_dry_run_no_artifacts(self):
        self.assertEqual(P.run(self.repo)["status"],"INITIALIZATION_READY");self.assertFalse(P.root(self.repo).exists())
    def test_first_neutral(self):
        r=self.first();e=r["record"]["evaluation"];self.assertEqual(e["execution_status"],"EVALUATED");self.assertEqual(e["entry_state"],"NO_ENTRY");self.assertIn("ENTRY.NO_DIRECTIONAL_ENTRY",e["reason_codes"])
    def test_replay_no_new_files(self):
        r=self.first();before={p:p.read_bytes() for p in P.root(self.repo).rglob("*.json")};self.clock+=1000
        q=P.run(self.repo,True);self.assertEqual(q["status"],"REPLAY_NOOP");self.assertEqual(q["record"],r["record"]);self.assertEqual(before,{p:p.read_bytes() for p in P.root(self.repo).rglob("*.json")})
    def test_independent_replay(self):
        r=self.first();self.assertEqual(P.verify(self.repo,r["record"])["status"],"EXACT_MATCH")
    def test_identity_excludes_run_time(self):
        a=self.initialized()["activation"];k=P.key(a,self.t+STEP,parameters());self.clock+=12345;self.assertEqual(k,P.key(a,self.t+STEP,parameters()))
    def test_no_evidence_waits(self):
        self.initialized();self.advance();self.evidence["15m"].pop(self.t);self.assertEqual(P.run(self.repo,True)["status"],"AWAITING_TRIGGER_EVIDENCE")
    def test_future_evidence_waits(self):
        self.initialized();self.advance();self.evidence["15m"][self.t]["observed_at_ms"]=self.clock+1;self.assertEqual(P.run(self.repo,True)["status"],"AWAITING_TRIGGER_EVIDENCE")
    def test_missing_direction_consumed_once(self):
        self.initialized();self.advance();self.d["decision_time_utc"]=iso(self.clock+1)
        r=P.run(self.repo,True);self.assertEqual(r["record"]["operational_status"],"SKIPPED_NO_DIRECTION")
        self.d["decision_time_utc"]=iso(self.clock-1);self.assertEqual(P.run(self.repo,True)["status"],"REPLAY_NOOP")
    def test_missed_boundaries_not_rebuilt(self):
        self.initialized();self.advance();missed=self.t;self.advance();r=P.run(self.repo,True)
        self.assertEqual(r["record"]["missed_boundaries"],[missed]);self.assertEqual(r["record"]["historical_reconstruction"],0);self.assertEqual(len(list((P.root(self.repo)/"records").glob("*.json"))),1)
    def test_previous_state_link(self):
        r=self.first();self.advance();q=P.run(self.repo,True);self.assertEqual(q["record"]["previous_publication_id"],r["record"]["publication_id"])
    def test_tampered_record_rejected(self):
        r=self.first();path=P.root(self.repo)/"records"/(r["record"]["publication_id"]+".json");record=json.loads(path.read_text());record["generated_at_utc"]="tampered";path.write_text(json.dumps(record))
        with self.assertRaises(ValueError):P.run(self.repo,True)
    def test_input_tamper_rejected(self):
        r=self.first();path=P.root(self.repo)/"inputs"/(r["record"]["publication_id"]+".json");path.write_text("{}")
        with self.assertRaises(ValueError):P.verify(self.repo,r["record"])
    def test_contract_pin(self):
        p=parameters();p["parameter_hash"]="different"
        with patch.object(P,"parameters",return_value=p),self.assertRaises(ValueError):P.run(self.repo,True)
    def test_writer_exclusive(self):
        with P.writer(self.repo):
            with self.assertRaises(FileExistsError):P.run(self.repo,True)
    def test_interrupted_index_recovery(self):
        r=self.first();(P.root(self.repo)/"boundaries"/(r["record"]["publication_id"]+".json")).unlink()
        self.assertEqual(P.run(self.repo,True)["status"],"REPLAY_NOOP");self.assertEqual(len(list((P.root(self.repo)/"boundaries").glob("*.json"))),1)
    def test_no_snapshot_duplication(self):
        r=self.first();self.assertNotIn("input_snapshot",r["record"]);self.assertNotIn("input_snapshot",r["input_manifest"]);self.assertLess(len(json.dumps(r["record"])),10000)
    def test_workflow_triggers_and_conditions(self):
        text=(Path(__file__).resolve().parents[2]/".github/workflows/btc-entry-timing.yml").read_text()
        self.assertIn("workflows: [BTC Direction Signal History V1]",text);self.assertIn("'14,29,44,59 * * * *'",text);self.assertIn("workflow_dispatch:",text);self.assertIn("cancel-in-progress: false",text)
        self.assertIn("github.event_name != 'workflow_run' || github.event.workflow_run.conclusion == 'success'",text)
        for event,success,expected in (("schedule",False,True),("workflow_dispatch",False,True),("workflow_run",True,True),("workflow_run",False,False)):
            self.assertEqual(event!="workflow_run" or success,expected)
    def test_workflow_staging_guard(self):
        text=(Path(__file__).resolve().parents[2]/".github/workflows/btc-entry-timing.yml").read_text()
        self.assertIn("status!='A'",text);self.assertIn("git rebase origin/main",text);self.assertNotIn("--force",text);self.assertNotIn("git add .",text)


if __name__=="__main__":unittest.main()

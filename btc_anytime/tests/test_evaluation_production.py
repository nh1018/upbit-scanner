"""Production publication orchestration without changing outcome algorithms."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_evaluation import fixture,START,STEP,sign_decision
from btc_anytime.direction.evaluation import production as p
from btc_anytime.direction.evaluation.engine import evaluate,ANCHORS,parameters
from btc_anytime.direction.evaluation.storage import persist
from btc_anytime.features.engine import digest


class ProductionEvaluationTests(unittest.TestCase):
    def setUp(self):self.d,self.rows,self.ev,self.refs=fixture();self.cutoff=START+6*STEP+100
    def invoke(self,repo,write=True,cutoff=None):
        with patch.object(p,'inputs',return_value=([self.d],{'15m':self.rows},{'15m':self.refs},{'15m':self.ev})),patch.object(p,'git_revision',return_value='a'*40),patch.object(p.subprocess,'check_output',return_value=b''),patch.object(p,'now_ms',return_value=self.cutoff if cutoff is None else cutoff):
            return p.record(repo,write)
    def test_first_two_anchor_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=self.invoke(Path(tmp));self.assertEqual(r['storage_counts']['APPENDED'],2);self.assertEqual(r['counts']['PENDING'],6)
            labels=p.finalized(Path(tmp));self.assertEqual(len(labels),2)
            self.assertEqual({x['payload']['excursion_status'] for x in labels.values()},{'PARTIAL','MATURED'})
    def test_finalized_replay_no_compute_no_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.invoke(repo);before={x:x.read_bytes() for x in repo.rglob('*.json')}
            with patch.object(p,'evaluate',wraps=evaluate) as spy:r=self.invoke(repo)
            self.assertEqual(r['status'],'NOOP');self.assertEqual(r['counts']['FINALIZED_NOOP'],2);self.assertEqual(spy.call_count,6)
            self.assertEqual(before,{x:x.read_bytes() for x in repo.rglob('*.json')});self.assertEqual(r['new_files'],0)
    def test_pending_no_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=self.invoke(Path(tmp),cutoff=START+STEP+100);self.assertEqual(r['counts']['PENDING'],8);self.assertEqual(list(Path(tmp).rglob('*')),[])
    def test_existing_decision_before_evaluation_activation_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=self.invoke(Path(tmp));self.assertEqual(r['decision_count'],1);self.assertEqual(r['storage_counts']['APPENDED'],2)
    def test_readonly_mode_no_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=self.invoke(Path(tmp),write=False);self.assertEqual(r['new_files'],0);self.assertEqual(list(Path(tmp).rglob('*')),[])
    def test_stored_payload_exact_pure_evaluator_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.invoke(repo)
            for envelope in p.finalized(repo).values():
                payload=envelope['payload'];a=payload['contract']['anchor'];self.assertEqual(payload,evaluate(self.d,self.rows,self.ev,self.refs,self.cutoff,a,1))
    def test_key_identical_existing_contract(self):
        for a in ANCHORS:
            for h in (1,4,12,24):self.assertEqual(p.evaluation_key(self.d,a,h,parameters()),evaluate(self.d,self.rows,self.ev,self.refs,self.cutoff,a,h)['evaluation_key'])
    def test_invalid_separate_events_failure(self):
        self.d['confidence']='0.9'
        with tempfile.TemporaryDirectory() as tmp:
            r=self.invoke(Path(tmp));self.assertEqual(r['status'],'FAILED');self.assertFalse(p.finalized(Path(tmp)));self.assertEqual(len(list(Path(tmp).rglob('events/*/*.json'))),8)
    def test_missing_endpoint_event_no_zero_return(self):
        self.rows.pop(4)
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);r=self.invoke(repo);self.assertEqual(r['counts']['SOURCE_MISSING'],1)
            events=list(repo.rglob('events/*/*.json'));self.assertEqual(len(events),1);self.assertIsNone(json.loads(events[0].read_text())['payload']['metrics'])
    def test_missing_event_repeat_no_commit(self):
        self.rows.pop(4)
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.invoke(repo);r=self.invoke(repo);self.assertEqual(r['new_files'],0);self.assertEqual(r['status'],'NOOP')
    def test_conflict_recorded_failure(self):
        self.rows.append(deepcopy(self.rows[2]))
        with tempfile.TemporaryDirectory() as tmp:
            r=self.invoke(Path(tmp));self.assertEqual(r['status'],'FAILED');self.assertEqual(r['counts']['SOURCE_CONFLICT'],2)
    def test_corrupt_label_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.invoke(repo);path=next(repo.rglob('labels/*/*.json'));obj=json.loads(path.read_text());obj['payload']['metrics']['raw_return_pct']='999';path.write_text(json.dumps(obj))
            with self.assertRaises(ValueError):self.invoke(repo)
    def test_duplicate_finalized_key_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.invoke(repo);path=next(repo.rglob('labels/*/*.json'));obj=json.loads(path.read_text());obj['payload']['metrics']['raw_return_pct']='999'
            obj['artifact_id']=digest(obj['payload']);obj['envelope_hash']=digest({k:v for k,v in obj.items() if k!='envelope_hash'});(path.parent/(obj['artifact_id']+'.json')).write_text(json.dumps(obj))
            with self.assertRaises(ValueError):self.invoke(repo)
    def test_parameter_pin(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(p,'parameters',return_value={'parameter_hash':'wrong'}):
            with self.assertRaises(ValueError):self.invoke(Path(tmp))
    def test_no_aggregate_files_or_snapshot_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.invoke(repo);text=''.join(x.read_text() for x in repo.rglob('*.json'))
            self.assertNotIn('input_snapshot',text);self.assertNotIn('cohorts',text);self.assertFalse(list(repo.rglob('reports')))
    def test_raw_and_decision_immutability(self):
        before=deepcopy((self.d,self.rows,self.ev))
        with tempfile.TemporaryDirectory() as tmp:self.invoke(Path(tmp))
        self.assertEqual(before,(self.d,self.rows,self.ev))
    def test_workflow_events_and_isolation(self):
        workflow=(Path(__file__).parents[2]/'.github/workflows/btc-direction-evaluation.yml').read_text()
        for event,conclusion,wanted in [('schedule',None,True),('workflow_dispatch',None,True),('workflow_run','success',True),('workflow_run','failure',False)]:
            self.assertEqual(event!='workflow_run' or conclusion=='success',wanted)
        self.assertIn("github.event_name != 'workflow_run' || github.event.workflow_run.conclusion == 'success'",workflow)
        self.assertIn('group: btc-direction-evaluation-v1',workflow);self.assertIn('cancel-in-progress: false',workflow)
        self.assertIn("status!='A'",workflow);self.assertNotIn('--force',workflow);self.assertIn('git rebase origin/main',workflow)
        self.assertNotIn('signal_history.runner',workflow);self.assertNotIn('cloudflare',workflow)


if __name__=='__main__':unittest.main()

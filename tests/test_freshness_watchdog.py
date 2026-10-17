"""Offline safety tests; no dispatch or production writes."""
import copy
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import base64
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from freshness_watchdog.core import ACTIVE, HOUR, TARGETS, ms, plan, source
from freshness_watchdog.reader import GitHubReadOnly, ObservationError, pages, observe
from freshness_watchdog.simulation import SimulationLedger

NOW = ms('2026-10-11T00:40:00Z')


def evidence():
    from upbit_b.history_contracts import cycle_path
    return dict(repository='nh1018/upbit-scanner', default_branch='main', revision='a'*40,
        queries_complete=True, started_at_ms=NOW-1000, observed_at_ms=NOW,
        workflows={s: dict(state='active', id=t['id'], config_verified=True) for s,t in TARGETS.items()},
        latest_runs={s: dict(status='completed', conclusion='success', publication_verified=True) for s in TARGETS},
        active_runs=[], sources={s: dict(path='published', cutoff_ms=NOW-100*60_000,
            complete=True, sha256='b'*64) for s in TARGETS},
        b_current=dict(path=cycle_path(NOW//HOUR*HOUR), revision='a'*40, exists=False))


class PlanningTests(unittest.TestCase):
    def test_priority_and_determinism(self):
        e=evidence(); before=copy.deepcopy(e); p=plan(e,NOW)
        self.assertEqual(p['proposal']['strategy'],'B'); self.assertEqual(p['deferred'],['A'])
        self.assertEqual(p,plan(e,NOW)); self.assertEqual(e,before)
        self.assertEqual(p['states'],{'A':'CURRENT','B':'CURRENT'})

    def test_current_b_no_duplicate_and_a_lead(self):
        e=evidence(); e['b_current']['exists']=True
        self.assertEqual(plan(e,NOW)['proposal']['strategy'],'A')
        e['sources']['A']['cutoff_ms']=NOW-80*60_000
        self.assertEqual(plan(e,NOW)['action'],'NO_ACTION')

    def test_b_waits_native_slots(self):
        e=evidence(); now=NOW-10*60_000
        e.update(started_at_ms=now,observed_at_ms=now)
        e['sources']['A']['cutoff_ms']=now
        self.assertEqual(plan(e,now)['action'],'NO_ACTION')

    def test_active_any_branch_blocks_both(self):
        for status in ACTIVE:
            with self.subTest(status=status):
                e=evidence(); e['active_runs']=[dict(workflow_id=TARGETS['A']['id'],status=status,head_branch='other')]
                self.assertEqual(plan(e,NOW)['reason'],'A_OR_B_RUN_ACTIVE')

    def test_failed_or_unpublished_not_retried(self):
        for conclusion in ['failure','cancelled','timed_out',None]:
            e=evidence()
            for r in e['latest_runs'].values(): r['conclusion']=conclusion
            self.assertEqual(plan(e,NOW)['action'],'NO_ACTION')
        e=evidence()
        for r in e['latest_runs'].values(): r['publication_verified']=False
        self.assertEqual(plan(e,NOW)['action'],'NO_ACTION')

    def test_unconfirmed_latest_state_blocks_all(self):
        for status in ['unknown','missing',None,'queued','waiting','in_progress']:
            e=evidence(); e['latest_runs']['B']['status']=status
            self.assertEqual(plan(e,NOW)['action'],'BLOCKED')

    def test_invalid_partial_and_missing_fail_closed(self):
        for s in TARGETS:
            e=evidence(); e['sources'][s]['complete']=False
            self.assertNotEqual((plan(e,NOW)['proposal'] or {}).get('strategy'),s)
            e['sources'][s]={'error':'missing'}
            self.assertNotEqual((plan(e,NOW)['proposal'] or {}).get('strategy'),s)

    def test_evidence_boundaries(self):
        for change in [dict(queries_complete=False),dict(queries_complete='yes'),dict(revision='bad'),
                       dict(observed_at_ms=NOW-90_001),dict(observed_at_ms=NOW+1),
                       dict(started_at_ms=NOW-120_001),dict(default_branch='other')]:
            e=evidence(); e.update(change)
            self.assertEqual(plan(e,NOW)['action'],'BLOCKED')

    def test_config_and_current_proof(self):
        for field,value in [('state','disabled_manually'),('config_verified',False),('id',0)]:
            e=evidence(); e['workflows']['B'][field]=value
            self.assertEqual(plan(e,NOW)['action'],'BLOCKED')
        for field,value in [('revision','c'*40),('path','old.jsonl'),('exists','yes')]:
            e=evidence(); e['b_current'][field]=value
            self.assertEqual(plan(e,NOW)['action'],'BLOCKED')

    def test_old_gap_never_backfilled(self):
        e=evidence(); e['b_current']['exists']=True
        e['sources']['A']['cutoff_ms']=NOW
        e['sources']['B']['cutoff_ms']=NOW-10*HOUR
        p=plan(e,NOW); self.assertEqual(p['action'],'NO_ACTION')
        self.assertEqual(p['states']['B'],'STALE')


class SourceTests(unittest.TestCase):
    def payload(self):
        return json.dumps(dict(scanner_version='1.3-cloud',scanned_count=294,candidate_count=30,
            generated_at_kst='2026-10-11T09:00:00+09:00')).encode()

    def test_a_hash_timezone_and_freshness(self):
        raw=self.payload(); s=source('A',raw,'output/latest_scan.json',NOW)
        self.assertEqual(s['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(s['cutoff_ms'],ms('2026-10-11T00:00:00Z'))
        self.assertEqual(s['freshness_status'],'CURRENT')

    def test_a_invalid(self):
        for raw in [b'',b'{}',b'{"x":1,"x":2}',self.payload().replace(b'294',b'1'),
                    self.payload().replace(b'+09:00',b''),self.payload().replace(b'09:00:00',b'10:00:00')]:
            with self.subTest(raw=raw), self.assertRaises((ValueError,KeyError)):
                source('A',raw,'output/latest_scan.json',NOW)

    def test_b_reuses_contract_rejects_corruption(self):
        from upbit_b.history_contracts import cycle_path
        boundary=NOW//HOUR*HOUR
        manifest=dict(source_cutoff=boundary,completed_at='2026-10-11T00:39:00Z',completeness='COMPLETE')
        with patch('upbit_b.history.validate_cycle',return_value=(manifest,[])) as validate:
            s=source('B',b'{}\n',cycle_path(boundary),NOW)
            validate.assert_called_once_with(b'{}\n'); self.assertTrue(s['complete'])
        with patch('upbit_b.history.validate_cycle',side_effect=ValueError('hash mismatch')):
            with self.assertRaises(ValueError): source('B',b'{}\n',cycle_path(boundary),NOW)


class TransportTests(unittest.TestCase):
    def test_repository_root_has_no_trailing_slash(self):
        seen=[]
        class Response(io.BytesIO): status=200
        def open_(req,timeout): seen.append(req); return Response(b'{}')
        GitHubReadOnly(opener=open_).get('/')
        self.assertIn('upbit-scanner?',seen[0].full_url)

    def test_network_errors_stop_without_retry(self):
        for error in [TimeoutError('test'),OSError('test')]:
            calls=[]
            def open_(req,timeout): calls.append(req); raise error
            with self.assertRaises(ObservationError): GitHubReadOnly(opener=open_).get('/branches/main')
            self.assertEqual(len(calls),1)

    def test_get_only_no_token_retained(self):
        seen=[]
        class Response(io.BytesIO): status=200
        def open_(req,timeout):
            seen.append(req); return Response(b'{}')
        api=GitHubReadOnly('secret-for-test',open_); api.get('/branches/main')
        self.assertEqual(seen[0].method,'GET')
        self.assertIn('watchdog_nonce=',seen[0].full_url)
        self.assertNotIn('secret-for-test',json.dumps(api.requests))
        self.assertEqual(seen[0].get_header('Cache-control'),'no-cache')

    def test_errors_no_retry(self):
        for code in [401,403,404,429,500]:
            calls=[]
            def open_(req,timeout):
                calls.append(req); raise HTTPError(req.full_url,code,'error',{},None)
            api=GitHubReadOnly(opener=open_)
            with self.assertRaises(ObservationError): api.get('/branches/main')
            self.assertEqual(len(calls),1)

    def test_missing_only_404_and_path_restriction(self):
        def open_(req,timeout): raise HTTPError(req.full_url,404,'missing',{},None)
        api=GitHubReadOnly(opener=open_); self.assertIsNone(api.get('/contents/x',missing=True))
        for path in ['//evil','/../x','https://evil']:
            with self.assertRaises(ObservationError): api.get(path)

    def test_pagination_and_budget(self):
        class API:
            def get(self,path): return {'runs':list(range(100)) if path.endswith('page=1') else [101]}
        self.assertEqual(len(pages(API(),'/runs','runs')),101)
        class Full:
            def get(self,path): return {'runs':list(range(100))}
        with self.assertRaises(ObservationError): pages(Full(),'/runs','runs')
        api=GitHubReadOnly(); api.requests=[{}]*40
        with self.assertRaises(ObservationError): api.get('/branches/main')


class LedgerTests(unittest.TestCase):
    def test_concurrent_reservation_single_winner(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=SimulationLedger(Path(tmp)/'simulation.db'); p=plan(evidence(),NOW)['proposal']
            with ThreadPoolExecutor(2) as pool:
                results=list(pool.map(lambda _:ledger.reserve(p,NOW),range(2)))
            self.assertEqual(sorted(results),[False,True])

    def test_ambiguous_and_accepted_never_automatically_resend(self):
        for state in ['UNKNOWN','SIMULATED_ACCEPTED']:
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'simulation.db'; ledger=SimulationLedger(path)
                p=plan(evidence(),NOW)['proposal']; self.assertTrue(ledger.reserve(p,NOW))
                ledger.simulated_result(p['key'],state)
                later={**p,'key':'A:later','strategy':'A'}
                self.assertFalse(SimulationLedger(path).reserve(later,NOW+24*HOUR))
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(db.execute('select count(*) from audit').fetchone()[0],2)
                with self.assertRaises(ValueError): ledger.simulated_result(p['key'],state)


class ObserverTests(unittest.TestCase):
    def test_existing_credential_noninteractive_and_fail_closed(self):
        from freshness_watchdog.credentials import existing_git_token
        from types import SimpleNamespace
        with patch('freshness_watchdog.credentials.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='username=test\npassword=unit-secret\n')) as run:
            self.assertEqual(existing_git_token(),'unit-secret')
            self.assertEqual(run.call_args.kwargs['env']['GCM_INTERACTIVE'],'Never')
            self.assertTrue(run.call_args.kwargs['capture_output'])
        with patch('freshness_watchdog.credentials.subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='')):
            with self.assertRaises(ValueError): existing_git_token()

    def test_cli_authentication_and_network_failure_are_nonzero(self):
        from freshness_watchdog.__main__ import main
        for reason in ['GitHub read rejected: HTTP 401','GitHub read rejected: HTTP 429','GitHub read unavailable or invalid']:
            output=io.StringIO()
            with patch('sys.argv',['watchdog']),patch('sys.stdout',output),patch('freshness_watchdog.__main__.observe',side_effect=ObservationError(reason)):
                self.assertEqual(main(),2)
            result=json.loads(output.getvalue())
            self.assertEqual(result['action'],'BLOCKED'); self.assertEqual(result['actual_dispatch_count'],0)

    def test_complete_session_and_fail_closed_changes(self):
        root=Path(__file__).resolve().parents[1]
        fixed=datetime.fromtimestamp(NOW/1000,timezone.utc)
        class Clock:
            @staticmethod
            def now(tz): return fixed
        class API:
            def __init__(self, defect=None): self.requests=[]; self.defect=defect; self.branch_reads=0
            def get(self,path,missing=False):
                self.requests.append(path)
                if path=='/': return dict(default_branch='main')
                if path=='/branches/main':
                    self.branch_reads+=1
                    return {'commit':{'sha':('c' if self.defect=='main_move' and self.branch_reads>1 else 'a')*40}}
                if '/contents/' in path:
                    relative=path.split('/contents/')[1].split('?')[0]
                    if relative.startswith('.github/'):
                        raw=(root/relative).read_bytes()
                    elif relative=='output/latest_scan.json': raw=SourceTests().payload()
                    elif relative.endswith('.jsonl'): raw=b'B fixture'
                    else: return [dict(type='file',name='23.jsonl',path='output_upbit_b/v1/history/2026-10-10/23.jsonl')]
                    return dict(type='file',encoding='base64',content=base64.b64encode(raw).decode(),
                        sha=('0'*40 if self.defect=='blob' else hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()))
                if path.startswith('/actions/runs?'): return {'workflow_runs':[]}
                if '/jobs?' in path:
                    return {'jobs':[{'steps':[dict(name=t['publish_step'],conclusion='success') for t in TARGETS.values()]}]}
                target=next(t for t in TARGETS.values() if str(t['id']) in path)
                if '/runs?' in path:
                    return {'workflow_runs':[dict(id=1,workflow_id=target['id'],created_at='2026-10-11T00:00:00Z',status='completed',conclusion='success')]}
                return dict(state='active',id=target['id'],path='.github/workflows/'+target['workflow'])
        original=source
        def checked(s,raw,path,now):
            return original(s,raw,path,now) if s=='A' else dict(path=path,cutoff_ms=NOW-HOUR,complete=True,sha256=hashlib.sha256(raw).hexdigest())
        with patch('freshness_watchdog.reader.datetime',Clock), patch('freshness_watchdog.reader.source',side_effect=checked):
            api=API(); e=observe(api); self.assertTrue(e['queries_complete'])
            self.assertEqual(plan(e,NOW)['action'],'NO_ACTION')
            self.assertLessEqual(len(api.requests),40)
            for defect in ['blob','main_move']:
                with self.subTest(defect=defect), self.assertRaises(ObservationError): observe(API(defect))

    def test_cli_offline_writes_nothing(self):
        from freshness_watchdog.__main__ import main
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'e.json'; path.write_text(json.dumps(evidence()))
            before=path.read_bytes(); output=io.StringIO()
            with patch('sys.argv',['watchdog','--evidence',str(path),'--now','2026-10-11T00:40:00Z']),patch('sys.stdout',output):
                self.assertEqual(main(),0)
            self.assertEqual(json.loads(output.getvalue())['actual_dispatch_count'],0)
            self.assertEqual(list(Path(tmp).iterdir()),[path]); self.assertEqual(path.read_bytes(),before)


if __name__=='__main__': unittest.main()

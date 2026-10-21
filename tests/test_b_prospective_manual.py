"""Synthetic evidence only. Never approval, activation or market collection."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from strategy_evaluation import b_activation_evidence as E
from strategy_evaluation import b_prospective_manual as M
from strategy_evaluation import b_prospective_v11 as B
from strategy_evaluation.contracts import HOUR,sha
from upbit_b.feature_contracts import digest
from tests.test_b_prospective import DISC,NOW,sig,cs,ev

COMMIT='1'*40

def ce(t):return {'request_started_ms':t,'response_received_ms':t,'elapsed_ms':0,'server_ms':t,'url':'fixture://github','response_sha256':'a'*64}
def comment(i,text,t):
    return {'id':i,'user':{'login':'nh1018','type':'User'},'issue_url':E.ISSUE,'body':text,'created_at':E.utc(t),
            'updated_at':E.utc(t),'html_url':'https://github.com/nh1018/upbit-scanner/pull/37#issuecomment-'+str(i)}

class FakeGitHub:
    witness_id=3
    def __init__(self,comments,t):self.comments=comments;self.t=t
    def comment(self,i):return copy.deepcopy(self.comments[i]),ce(self.t)

class FakeMarket:
    def __init__(self,t,fail=0):self.t=t;self.records=[];self.fail=fail;self.calls=[]
    def get(self,base,path,params):
        self.calls.append((base,path,params))
        if self.fail:self.fail-=1;raise OSError('fixture network failure')
        n=params.get('count',params.get('limit'));end=E.millis(params['to']) if 'to' in params else params['endTime']+1
        candles=cs('UPBIT' if base==M.UPBIT else 'BINANCE_SPOT',params.get('market','BTCUSDT'),end,n)
        if base==M.UPBIT:
            rows=[{'market':c.instrument,'unit':60,'candle_date_time_utc':E.utc(c.open_ms).replace('Z',''),
                'opening_price':'100','high_price':'110','low_price':'90','trade_price':'100',
                'candle_acc_trade_volume':'1','candle_acc_trade_price':'10'} for c in candles]
        else:rows=[[c.open_ms,'100','110','90','100','1',c.close_ms-1,'10',1,'1','10','0'] for c in candles]
        raw=json.dumps(rows).encode();url=base+path+'?'+M.urllib.parse.urlencode(params)
        evidence={'url':url,'response_sha256':sha(raw),'received_at_utc':E.utc(self.t)}
        self.records.append((raw,evidence,self.t));return json.loads(raw),evidence

class ManualTests(unittest.TestCase):
    def setUp(self):
        self.raw=DISC.read_bytes();self.c=B.contract(self.raw);self.boundary=NOW-1000
        self.record=comment(1,E.record_text(COMMIT),NOW-300000)
        self.approval=comment(2,E.approval_text(COMMIT,self.boundary,3,3600000),NOW-240000)
        self.a=E.prepare(self.c,self.raw,COMMIT,self.boundary,self.record,self.approval,ce(NOW-200000))
        self.witness=comment(3,E.witness_text(sha(E.encoded(self.a))),NOW-150000)
        self.t=NOW+8*24*HOUR;self.github=FakeGitHub({1:self.record,2:self.approval,3:self.witness},self.t)
        self.member=B.seal(dict(schema_version=B.VERSION,kind='POPULATION',activation_event_id=self.a['event_id'],contract_sha256=self.c['contract_sha256'],signal=sig(),initial_state='NOT_ATTEMPTED'))
    def runner(self,root,market=None):
        return M.Runner(Path(__file__).parents[1],root,self.c,self.raw,self.a,self.github,[],market or FakeMarket(self.t),lambda:self.t)
    def mocks(self):
        return patch.object(M,'git',side_effect=lambda repo,*args:COMMIT.encode() if args[0]=='rev-parse' else b''),patch.object(B,'population',return_value=[self.member])
    def test_valid_external_evidence(self):self.assertIn('witness',E.validate(self.a,self.c,self.raw,self.github))
    def test_no_approval(self):
        for user,body in [('other',self.approval['body']),('nh1018','')]:
            a=copy.deepcopy(self.approval);a['user']['login']=user;a['body']=body
            with self.assertRaises(ValueError):E.prepare(self.c,self.raw,COMMIT,self.boundary,self.record,a,ce(NOW-200000))
    def test_hash_mismatch(self):
        c=copy.deepcopy(self.c);c['contract_sha256']='0'*64
        with self.assertRaises(ValueError):E.validate(self.a,c,self.raw,self.github)
    def test_retroactive_creation(self):
        with self.assertRaises(ValueError):E.prepare(self.c,self.raw,COMMIT,NOW-300000,self.record,self.approval,ce(NOW))
    def test_activation_witness_after_boundary(self):
        self.github.comments[3]=comment(3,E.witness_text(sha(E.encoded(self.a))),NOW)
        with self.assertRaisesRegex(ValueError,'precede'):E.validate(self.a,self.c,self.raw,self.github)
    def test_self_declared_past_not_sufficient(self):
        a=copy.deepcopy(self.a);a['activation_time_ms']=0;a['event_id']=digest({k:v for k,v in a.items() if k!='event_id'})
        with self.assertRaises(ValueError):E.validate(a,self.c,self.raw,self.github)
    def test_edited_or_revoked_approval(self):
        self.github.comments[2]['updated_at']=E.utc(NOW)
        with self.assertRaises(ValueError):E.validate(self.a,self.c,self.raw,self.github)
    def test_clock_uncertainty_and_regression(self):
        for e in (dict(ce(NOW),elapsed_ms=2000),dict(ce(NOW),response_received_ms=NOW-1),dict(ce(NOW),server_ms=NOW+10000)):
            with self.assertRaises(ValueError):E.server_clock(e)
    def test_before_boundary(self):
        self.github.t=NOW-2000
        with self.assertRaises(ValueError):E.validate(self.a,self.c,self.raw,self.github)
    def test_dry_full_fixture_zero_writes(self):
        g,p=self.mocks()
        with tempfile.TemporaryDirectory() as root,g,p:
            r=self.runner(root);r.population(dry=True);r.collect(dry=True);out=r.evaluate(dry=True);report=r.report()
            self.assertEqual(len(out),3);self.assertTrue(all(e['status']=='MATURED' for e in out))
            self.assertEqual(report['eligible'],1);self.assertEqual(report['context_success'],1)
            self.assertEqual(list(Path(root).rglob('*')),[])
    def test_population_requires_activation_saved(self):
        g,p=self.mocks()
        with tempfile.TemporaryDirectory() as root,g,p:
            with self.assertRaises(ValueError):self.runner(root).population()
    def test_real_writer_fixture_idempotence(self):
        g,p=self.mocks()
        with tempfile.TemporaryDirectory() as root,g,p:
            M.write_once(Path(root)/'activation.json',E.encoded(self.a));r=self.runner(root)
            self.assertEqual(r.population()['created'],1);self.assertEqual(r.population()['replay'],1)
            self.assertEqual(r.collect()[0]['state'],'SUCCESS')
            before={str(x):sha(x.read_bytes()) for x in Path(root).rglob('*') if x.is_file()}
            self.assertEqual(r.collect()[0]['state'],'FROZEN_CONTEXT_NOOP')
            self.assertEqual(before,{str(x):sha(x.read_bytes()) for x in Path(root).rglob('*') if x.is_file()})
            r.evaluate();self.assertEqual(r.evaluate(),[]);self.assertEqual(r.report()['eligible'],1)
    def test_fail_retry_partial_then_success(self):
        g,p=self.mocks()
        with tempfile.TemporaryDirectory() as root,g,p:
            M.write_once(Path(root)/'activation.json',E.encoded(self.a));market=FakeMarket(self.t,fail=1);r=self.runner(root,market);r.population()
            self.assertEqual(r.collect()[0]['state'],'PARTIAL');self.assertEqual(r.collect()[0]['state'],'RETRY_NOT_DUE')
            self.t+=HOUR;self.github.t=self.t;market.t=self.t
            self.assertEqual(r.collect()[0]['state'],'SUCCESS');self.assertEqual(r.report()['attempt_count'],2)
    def test_no_context_population_denominator(self):
        g,p=self.mocks()
        with tempfile.TemporaryDirectory() as root,g,p:
            M.write_once(Path(root)/'activation.json',E.encoded(self.a));r=self.runner(root);r.population()
            report=r.report();self.assertEqual(report['eligible'],1);self.assertEqual(report['context_missing'],1)
            self.assertEqual(report['horizons'][0]['outcomes']['NOT_EVALUATED'],1)
    def test_engine_result_unchanged(self):
        from strategy_evaluation.runner import fetch_evaluation
        s=sig();a=fetch_evaluation(FakeMarket(self.t),s,1,self.t)
        rows,evidence=FakeMarket(self.t).get(M.UPBIT,'/v1/candles/minutes/60',{'market':s['market'],'count':24,'to':M.iso(s['evaluation_anchor']+24*HOUR)})
        candles,ev=M.decode(rows,evidence,'UPBIT',s['market'])
        self.assertEqual(a,M.engine_evaluate(s,1,candles,[ev],self.t))
    def test_raw_hashes_preserved(self):
        market=FakeMarket(self.t);market.get(M.UPBIT,'/v1/candles/minutes/60',{'market':'KRW-X','count':25,'to':E.utc(NOW)})
        self.assertTrue(all(sha(raw)==ev['response_sha256'] for raw,ev,_ in market.records))
    def test_guard_a_baseline_and_repo(self):
        repo=Path(__file__).parents[1]
        for path in (repo/'research-new',repo.parent/'a-publication-evidence-v11',repo.parent/'coin-trading-backup'):
            with self.assertRaises(ValueError):M.root_path(path,repo)
    def test_append_conflict(self):
        with tempfile.TemporaryDirectory() as p:
            f=Path(p)/'record';M.write_once(f,b'a')
            with self.assertRaises(ValueError):M.write_once(f,b'b')
    def test_status_no_network_or_files(self):
        with patch.object(E.GitHub,'get',side_effect=AssertionError('network forbidden')):
            self.assertEqual(M.cli(['status'])['writes'],0)
    def test_dry_plan_no_activation(self):
        self.assertEqual(M.cli(['dry-run'])['market_requests'],0)

    def test_missing_raw_response_refuses_report(self):
        g,p=self.mocks()
        with tempfile.TemporaryDirectory() as root,g,p:
            M.write_once(Path(root)/'activation.json',E.encoded(self.a));r=self.runner(root);r.population();r.collect()
            raw=next((Path(root)/'raw_responses').glob('*.bin'));raw.write_bytes(b'corrupt fixture copy')
            with self.assertRaisesRegex(ValueError,'corrupt'):r.report()
    def test_interrupted_attempt_recovery(self):
        g,p=self.mocks()
        with tempfile.TemporaryDirectory() as root,g,p:
            M.write_once(Path(root)/'activation.json',E.encoded(self.a));r=self.runner(root);r.population()
            key=digest([self.a['event_id'],self.member['event_id'],0])
            M.write_once(Path(root)/'attempt_starts'/(key+'.json'),(E.dumps({'population_event_id':self.member['event_id'],'retry_attempt':0,'request_started_ms':self.t-1000})+'\n').encode())
            self.assertEqual(r.collect()[0]['state'],'FAILED_INTERRUPTED_RECOVERED')
            self.assertEqual(r.report()['context_missing'],1)
    def test_stale_writer_lock_refused(self):
        g,p=self.mocks()
        with tempfile.TemporaryDirectory() as root,g,p:
            M.write_once(Path(root)/'activation.json',E.encoded(self.a));(Path(root)/'.manual-writer-lock').mkdir()
            with self.assertRaisesRegex(ValueError,'stale lock'):self.runner(root).population()
    def test_max_attempts_not_silently_removed(self):
        g,p=self.mocks()
        with tempfile.TemporaryDirectory() as root,g,p:
            M.write_once(Path(root)/'activation.json',E.encoded(self.a));market=FakeMarket(self.t,fail=100);r=self.runner(root,market);r.population()
            for _ in range(3):
                r.collect();self.t+=HOUR;self.github.t=self.t;market.t=self.t
            self.assertEqual(r.collect()[0]['state'],'MAX_ATTEMPTS')
            self.assertEqual(r.report()['eligible'],1)
    def test_fixture_forbidden_in_real_collect(self):
        with self.assertRaises(SystemExit):M.cli(['collect','--fixture-bundle','not-read.json'])
    def test_fixture_market_hash_and_simulation_label(self):
        url=M.UPBIT+'/v1/candles/minutes/60?market=KRW-X&count=25'
        item={'raw_base64':M.base64.b64encode(b'[]').decode(),'sha256':sha(b'[]')}
        market=M.FixtureMarket({'kind':'OFFLINE_FIXTURE_ONLY','responses':{url:item}},lambda:self.t)
        _,ev=market.get(M.UPBIT,'/v1/candles/minutes/60',{'market':'KRW-X','count':25})
        self.assertTrue(ev['simulation_only'])
        item['sha256']='0'*64
        with self.assertRaises(ValueError):market.get(M.UPBIT,'/v1/candles/minutes/60',{'market':'KRW-X','count':25})
    def test_serialized_context_replay(self):
        from tests.test_b_prospective_v11 import V11Tests
        obj=V11Tests();obj.setUp()
        try:
            x=obj.ctx();serialized=json.loads(E.dumps(x));B.validate_context(serialized)
        finally:obj.doCleanups()
    def test_witness_wrong_record_hash(self):
        self.github.comments[3]['body']=E.witness_text('f'*64)
        with self.assertRaises(ValueError):E.validate(self.a,self.c,self.raw,self.github)
    def test_code_revision_change_refused(self):
        with tempfile.TemporaryDirectory() as root,patch.object(M,'git',return_value=b'wrong'):
            with self.assertRaisesRegex(ValueError,'revision'):self.runner(root).authorize()
    def test_snapshot_uses_git_blob_bytes(self):
        commit=M.git(Path(__file__).parents[1],'rev-parse','HEAD').decode().strip()
        src=M.sources(Path(__file__).parents[1],commit)
        self.assertGreater(len(src),0)
        self.assertTrue(all(sha(raw)==expected for raw,expected,ref in src))
    def test_symlink_destination_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            target=Path(root)/'target';target.mkdir();link=Path(root)/'link'
            try:link.symlink_to(target,target_is_directory=True)
            except OSError:return  # Windows without symlink privilege: other namespace guards still apply.
            with self.assertRaises(ValueError):M.root_path(link,Path(__file__).parents[1])

    def test_github_clock_evidence_is_canonical_serializable(self):
        from email.utils import formatdate
        class Response:
            headers={'Date':formatdate(NOW/1000,usegmt=True)}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def geturl(self):return E.API+'/issues/comments/1'
            def read(self):return b'{}'
        with patch.object(E,'now',return_value=NOW),patch.object(E.time,'monotonic',side_effect=[0,.0001]):
            data,evidence=E.GitHub(opener=lambda req,timeout:Response()).get('/issues/comments/1')
        self.assertIsInstance(evidence['elapsed_ms'],int);self.assertIsInstance(E.dumps(evidence),str)
    def test_fixture_transport_rejected_by_writer(self):
        g,p=self.mocks()
        with tempfile.TemporaryDirectory() as root,g,p:
            M.write_once(Path(root)/'activation.json',E.encoded(self.a));r=self.runner(root)
            r.market=M.FixtureMarket({'kind':'OFFLINE_FIXTURE_ONLY','responses':{}})
            with self.assertRaisesRegex(ValueError,'fixture transport'):r.collect()

if __name__=='__main__':unittest.main()

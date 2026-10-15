import json,tempfile,unittest,copy
from dataclasses import replace
from decimal import Decimal
from tests.test_b_prospective import sig,cs,ev,activation,DISC,NOW
from strategy_evaluation import b_prospective_v11 as B
from upbit_b.feature_contracts import digest
from strategy_evaluation.contracts import HOUR

from unittest.mock import patch

class V11Tests(unittest.TestCase):
    def setUp(self):
        self.raw=DISC.read_bytes();self.c=B.contract(self.raw);self.a=activation(self.c)
        self.pop=patch.object(B,'population',return_value=[]);self.mockpop=self.pop.start();self.addCleanup(self.pop.stop)
        self.s=sig();self.m=B.seal(dict(schema_version=B.VERSION,kind='POPULATION',activation_event_id=self.a['event_id'],contract_sha256=self.c['contract_sha256'],signal=self.s,initial_state='NOT_ATTEMPTED'))
        self.mockpop.return_value=[self.m]
    def report(self,members,attempts,outcomes):
        return B.report(members,attempts,outcomes,self.c,self.raw,self.a,sources=[])
    def ctx(self,up=None):
        up=up or cs();btc=cs('BINANCE_SPOT','BTCUSDT');t=NOW+2*HOUR
        return B.context(self.s,up,ev(up,t-100),btc,ev(btc,t-100),t)
    def attempt(self,ctx=None,prior=()):
        ctx=ctx or self.ctx();return B.attempt(self.m,prior,NOW+HOUR,NOW+2*HOUR-100,NOW+2*HOUR,ctx['source_evidence'],ctx)
    def test_contract_separate_not_activated(self):
        self.assertNotEqual(self.c['contract_sha256'],self.c['previous_contract_sha256'])
        with self.assertRaisesRegex(ValueError,'NOT_ACTIVATED'):B.activation(None,self.c,self.raw)
    def test_population_without_context_or_outcome(self):
        r=self.report([self.m],[],[])
        self.assertEqual(r['eligible'],1);self.assertEqual(r['context_states'],{'NOT_ATTEMPTED':1})
        self.assertEqual(r['horizons'][0]['outcomes']['NOT_EVALUATED'],1)
    def test_late_context_allowed(self):
        c=self.ctx();B.validate_context(c)
        self.assertGreater(c['registered_at_ms'],self.s['evaluation_anchor'])
        self.assertEqual(c['source_vintage'],'HISTORICAL_AS_RETRIEVED')
    def test_future_suffix_does_not_change_features(self):
        c=self.ctx();future=replace(cs()[-1],open_ms=NOW,close_ms=NOW+HOUR)
        d=self.ctx(cs()+[future]);self.assertEqual([c[h] for h in ('H1','H2','H3')],[d[h] for h in ('H1','H2','H3')])
    def test_forming_rejected(self):
        u=cs();u[-1]=replace(u[-1],completed=False)
        self.assertEqual(self.ctx(u)['H3']['status'],'UNAVAILABLE')
    def test_price_conflict(self):
        u=cs();u[-1]=replace(u[-1],close=Decimal(101))
        c=self.ctx(u);self.assertEqual(c['source_vintage'],'SOURCE_REVISION_CONFLICT')
        self.assertEqual(self.attempt(c)['status'],'CONFLICT')
    def test_retry_chain_and_frozen(self):
        e=self.attempt();f=self.attempt(prior=[e]);self.assertEqual(f['retry_attempt'],1)
        r=self.report([self.m],[f,e],[])
        self.assertEqual(r['context_states'],{'SUCCESS':1})
        with self.assertRaises(ValueError):B.validate_attempts(self.m,[f])
    def test_append_noop_and_conflict(self):
        with tempfile.TemporaryDirectory() as p:
            self.assertEqual(B.append(p,self.m,self.c,self.raw,self.a),'CREATED');self.assertEqual(B.append(p,self.m,self.c,self.raw,self.a),'REPLAY_NOOP')
            x=dict(self.m,initial_state='CHANGED');x=B.seal({k:v for k,v in x.items() if k!='event_id'})
            with self.assertRaises(ValueError):B.append(p,x,self.c,self.raw,self.a)
    def test_duplicate_population_rejected(self):
        with self.assertRaises(ValueError):self.report([self.m,self.m],[],[])
    def test_outside_population_attempt_rejected(self):
        with self.assertRaises(ValueError):self.report([], [self.attempt()],[])
    def test_tamper_rejected(self):
        e=self.attempt();e['status']='FAILED'
        with self.assertRaises(ValueError):B.validate_attempts(self.m,[e])
    def test_failed_stays_denominator(self):
        e=B.attempt(self.m,[],NOW+1001,NOW+1002,NOW+1003,[],error='TIMEOUT')
        r=self.report([self.m],[e],[])
        self.assertEqual(r['eligible'],1);self.assertEqual(r['failure_reasons'],{'TIMEOUT':1})
    def test_missing_lineage_fails(self):
        self.pop.stop()
        with self.assertRaises(ValueError):B.population([],self.c,self.raw,self.a)
    def test_clock_order(self):
        with self.assertRaises(ValueError):B.attempt(self.m,[],NOW+2,NOW+1,NOW+3,[])

    def test_actual_journal_lineage_discovery(self):
        from pathlib import Path
        from strategy_evaluation.contracts import sha
        from strategy_evaluation import b_prospective as old
        sources=[(p.read_bytes(),sha(p.read_bytes()),p.relative_to(DISC.parents[2]).as_posix()) for p in sorted((DISC.parents[2]/'output_upbit_b/v1/history').rglob('*.jsonl'))]
        self.assertTrue(B.verify_discovery_sources(json.loads(self.raw),sources))
    def test_omitted_population_fails_before_outcomes(self):
        with self.assertRaisesRegex(ValueError,'incomplete'):self.report([],[],[])
    def test_append_activation_required(self):
        with tempfile.TemporaryDirectory() as p:
            with self.assertRaisesRegex(ValueError,'NOT_ACTIVATED'):B.append(p,self.m,self.c,self.raw,None)
    def test_attempt_requires_persisted_population(self):
        e=self.attempt()
        with tempfile.TemporaryDirectory() as p:
            with self.assertRaises(ValueError):B.append(p,e,self.c,self.raw,self.a,self.m)
            B.append(p,self.m,self.c,self.raw,self.a)
            self.assertEqual(B.append(p,e,self.c,self.raw,self.a,self.m),'CREATED')
            self.assertEqual(B.append(p,e,self.c,self.raw,self.a,self.m),'REPLAY_NOOP')
    def test_rehashed_status_tamper(self):
        e=self.attempt();e['status']='FAILED';e=B.seal({k:v for k,v in e.items() if k!='event_id'})
        with self.assertRaisesRegex(ValueError,'status replay'):B.validate_attempts(self.m,[e])
    def test_later_changed_window_conflict_freezes_success(self):
        e=self.attempt();u=cs();u[0]=replace(u[0],high=Decimal(111));f=self.attempt(self.ctx(u),[e])
        self.assertEqual(f['status'],'CONFLICT')
        r=self.report([self.m],[f,e],[])
        self.assertEqual(r['context_missing'],0)
        self.assertEqual(r['vintage_counts'],{'HISTORICAL_AS_RETRIEVED':1})

    def test_timeout_does_not_invent_received_time(self):
        e=B.attempt(self.m,[],NOW+1001,None,NOW+2000,[],error='TIMEOUT')
        self.assertIsNone(e['response_received_at_ms'])
        self.assertEqual(B.validate_attempts(self.m,[e])[0]['status'],'FAILED')
    def test_source_grade_not_promoted_by_clock(self):
        self.assertNotEqual(self.ctx()['source_vintage'],'AS_OBSERVED')
    def test_discovery_file_and_v1_contract_unchanged(self):
        from strategy_evaluation import b_prospective as old
        self.assertEqual(old.contract(self.raw),json.loads((DISC.parent/'b_prospective_contract_v1.json').read_bytes()))

    def test_late_raw_ingestion_hash_bound(self):
        from strategy_evaluation.contracts import sha
        from upbit_b.market_data import iso
        up=cs();btc=cs('BINANCE_SPOT','BTCUSDT');t=NOW+2*HOUR
        ur=[{'market':x.instrument,'unit':60,'candle_date_time_utc':iso(x.open_ms).replace('Z',''),
             'opening_price':'100','high_price':'110','low_price':'90','trade_price':'100',
             'candle_acc_trade_volume':'1','candle_acc_trade_price':'10'} for x in up]
        br=[[x.open_ms,'100','110','90','100','1',x.close_ms-1,'10',1,'1','10','0'] for x in btc]
        ub,bb=json.dumps(ur).encode(),json.dumps(br).encode();ue,be=ev(up,t-100),ev(btc,t-100)
        ue['response_sha256']=sha(ub);be['response_sha256']=sha(bb)
        c=B.context_from_responses(self.s,ub,ue,bb,be,t);B.validate_context(c)
        with self.assertRaises(ValueError):B.context_from_responses(self.s,ub+b' ',ue,bb,be,t)

    def test_v11_raw_sidecar(self):
        from strategy_evaluation.contracts import sha
        raw=b'[]';e={'response_sha256':sha(raw)}
        with tempfile.TemporaryDirectory() as p:
            with self.assertRaises(ValueError):B.append_response(p,raw,e,self.c,self.raw,None)
            self.assertEqual(B.append_response(p,raw,e,self.c,self.raw,self.a),'CREATED')
            self.assertEqual(B.append_response(p,raw,e,self.c,self.raw,self.a),'REPLAY_NOOP')
            with self.assertRaises(ValueError):B.append_response(p,b'[1]',e,self.c,self.raw,self.a)

    def test_discovery_git_lf_representation(self):
        from pathlib import Path
        from strategy_evaluation.contracts import sha
        sources=[]
        for p in sorted((DISC.parents[2]/'output_upbit_b/v1/history').rglob('*.jsonl')):
            raw=p.read_bytes().replace(b'\r\n',b'\n')
            sources.append((raw,sha(raw),p.relative_to(DISC.parents[2]).as_posix()))
        self.assertTrue(B.verify_discovery_sources(json.loads(self.raw),sources))
        bad=copy.deepcopy(json.loads(self.raw));bad['signals'][0]['signal_contract']['source_hash']='a'*64
        # Rehash cannot invent a valid byte representation.
        from strategy_evaluation.adapters import seal_signal
        bad['signals'][0]['signal_contract']=seal_signal(bad['signals'][0]['signal_contract'])
        with self.assertRaises(ValueError):B.verify_discovery_sources(bad,sources)

    def test_population_enumeration_excludes_discovery_without_outcomes(self):
        self.pop.stop()
        existing=[x['signal_contract'] for x in json.loads(self.raw)['signals']]
        with patch('strategy_evaluation.adapters.b_journals',return_value=existing+[self.s,self.s]):
            ledger=B.population([],self.c,self.raw,self.a)
        self.assertEqual([x['signal']['signal_id'] for x in ledger],[self.s['signal_id']])
        self.assertEqual(ledger[0]['initial_state'],'NOT_ATTEMPTED')

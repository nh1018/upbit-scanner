"""Analysis projection fixtures; production algorithms remain unchanged."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from btc_anytime import analysis_snapshot as S
from btc_anytime.features.build import build_dataset
from btc_anytime.features.availability import observation_event,evidence_map,mark_generated
from btc_anytime.features.snapshot import synchronize
from btc_anytime.direction.engine import evaluate
from btc_anytime.features.engine import digest
from btc_anytime.integrity import DURATIONS,iso
from btc_anytime.entry.engine import parameters
from test_features import rows,START


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.repo=Path(self.tmp.name);self.T=START+100*86400000
        self.data={};self.refs={}
        for tf,dt in DURATIONS.items():
            self.data[tf]=rows(65,tf);self.refs[tf]={}
            for i,r in enumerate(self.data[tf]):
                r['time']=self.T-(65-i)*dt;r['candle_time_utc']=iso(r['time'])
                self.refs[tf][r['time']]={'file':'fixture/'+tf,'line':i+1,'time':r['time']}
        event=observation_event(self.data,self.refs,'a'*40,self.T)
        self.mapping=evidence_map([event],self.data)
        _,series=build_dataset(self.data,self.T,references=self.refs,availability_evidence=self.mapping)
        snap=synchronize(mark_generated(series,self.T,'fixture_generation'),self.T)
        self.d=evaluate(snap);self.d.pop('decision_id')
        self.d.update(input_snapshot=snap,generated_at_utc=iso(self.T));self.d['decision_id']=digest(self.d)
        self.ev={'entry_state':'NO_ENTRY','entry_evaluation_id':None,'parameter_hash':parameters()['parameter_hash'],
            'schema_version':'btc-entry-v1','algorithm_version':'1.0.0','parameter_version':'entry-initial-hypothesis-1',
            'execution_status':'EVALUATED','state':{'setup':None},'reason_codes':['ENTRY.NO_DIRECTIONAL_ENTRY']}
        self.manifest={'items':[]};self.manifest['manifest_id']=digest(self.manifest)
        self.ev['input_manifest_id']=self.manifest['manifest_id'];self.ev.pop('entry_evaluation_id');self.ev['entry_evaluation_id']=digest(self.ev)
        self.e={'publication_id':'b'*64,'generated_at_utc':iso(self.T),'evaluation':self.ev,
            'upstream_direction_decision_id':self.d['decision_id'],'operational_status':'EVALUATED','trigger_time':self.T-DURATIONS['15m']}
        self.persist()
        self.stack=[]
        for name,kw in [('git_revision',{'return_value':'a'*40}),('load_raw',{'side_effect':lambda _: (deepcopy(self.data),deepcopy(self.refs),{})}),
                        ('load_observations',{'return_value':[event]})]:
            p=patch.object(S,name,**kw);p.start();self.stack.append(p)
    def tearDown(self):
        for p in self.stack:p.stop()
        self.tmp.cleanup()
    def persist(self):
        folder=self.repo/'output_direction/btc_anytime/v1/decisions';folder.mkdir(parents=True,exist_ok=True)
        for f in folder.glob('*.json'):f.unlink()
        self.d.pop('decision_id',None);self.d['decision_id']=digest(self.d)
        (folder/(self.d['decision_id']+'.json')).write_text(json.dumps(self.d),encoding='utf-8')
        folder=self.repo/'output_entry/btc_anytime/v1/records';folder.mkdir(parents=True,exist_ok=True)
        self.e.pop('integrity_hash',None);self.e['integrity_hash']=digest(self.e)
        (folder/(self.e['publication_id']+'.json')).write_text(json.dumps(self.e),encoding='utf-8')
        folder=self.repo/'output_entry/btc_anytime/v1/inputs';folder.mkdir(parents=True,exist_ok=True)
        obj={'manifest':self.manifest};obj['integrity_hash']=digest(obj)
        (folder/(self.e['publication_id']+'.json')).write_text(json.dumps(obj),encoding='utf-8')
    def result(self,T=None):return S.build(self.repo,self.T if T is None else T)
    def test_schema_and_four_timeframes(self):
        r=self.result();self.assertEqual(r['snapshot_status'],'READY');self.assertEqual(set(r['market_data']),set(DURATIONS));S.validate(r)
    def test_exact_feature_reuse(self):
        r=self.result()
        for tf in DURATIONS:self.assertEqual(r['feature']['timeframes'][tf]['values'],self.d['input_snapshot']['timeframes'][tf]['record']['features'])
    def test_direction_matches(self):
        r=self.result()['direction']
        for k in ('direction_class','direction_score','confidence','regime','reason_codes'):self.assertEqual(r[k],self.d[k])
    def test_entry_matches(self):self.assertEqual(self.result()['entry']['evaluation'],self.ev)
    def test_in_progress_excluded(self):
        r=deepcopy(self.data['15m'][-1]);r['time']=self.T;r['is_closed']=False;self.data['15m'].append(r)
        self.assertEqual(self.result()['market_data']['15m']['latest_completed']['time'],self.T-DURATIONS['15m'])
    def test_explicit_unclosed_old_excluded(self):
        r=deepcopy(self.data['15m'][0]);r['time']-=DURATIONS['15m'];r['is_closed']=False;self.data['15m'].insert(0,r)
        self.assertEqual(self.result()['market_data']['15m']['latest_completed']['time'],self.T-DURATIONS['15m'])
    def test_observed_raw_change_rejected(self):
        self.data['15m'][-1]['is_closed']=False
        with self.assertRaises(ValueError):self.result()
    def test_stale(self):self.assertEqual(self.result(self.T+3600000)['snapshot_status'],'STALE')
    def test_publication_grace(self):self.assertFalse(self.result(self.T+1200000)['data_freshness']['15m']['market_stale'])
    def test_feature_lag_explicit(self):
        r=deepcopy(self.data['15m'][-1]);r['time']=self.T;self.data['15m'].append(r);self.refs['15m'][self.T]={'file':'new','line':1}
        x=self.result(self.T+900000);self.assertIn('15m:FEATURE_LAGS_MARKET',x['warnings'])
    def test_no_evidence_not_invented(self):
        with patch.object(S,'load_observations',return_value=[]):
            r=self.result();self.assertIsNone(r['market_data']['15m']['latest_completed']['availability']['available_at_utc']);self.assertEqual(r['snapshot_status'],'PARTIAL')
    def test_oi_basis_and_null_preserved(self):
        r=self.result()
        for tf in DURATIONS:
            f=self.d['input_snapshot']['timeframes'][tf]['record'];self.assertEqual(r['feature']['timeframes'][tf]['oi_contract'],f['oi_metadata'])
            self.assertIsNone(r['feature']['timeframes'][tf]['values']['oi_change'])
    def test_no_engine_replay(self):
        with patch('btc_anytime.direction.engine.evaluate',side_effect=AssertionError('replay')),patch('btc_anytime.entry.engine.evaluate',side_effect=AssertionError('replay')),patch('btc_anytime.features.engine.build_timeframe',side_effect=AssertionError('recompute')):self.result()
    def test_boundary_rejected(self):
        self.data['15m'][0]['time']+=1
        with self.assertRaises(ValueError):self.result()
    def test_ohlcv_rejected(self):
        self.data['15m'][0]['high']='0'
        with self.assertRaises(ValueError):self.result()
    def test_gap_is_degraded(self):
        self.data['15m'].pop(1);r=self.result();self.assertIn('15m:RAW_CONTINUITY_ERROR',r['warnings']);self.assertEqual(r['snapshot_status'],'PARTIAL')
    def test_tampered_direction(self):
        f=next((self.repo/'output_direction/btc_anytime/v1/decisions').glob('*.json'));f.write_text('{}')
        with self.assertRaises(ValueError):self.result()
    def test_wrong_feature_version(self):
        self.d['input_snapshot']['timeframes']['15m']['record']['parameter_hash']='x';self.persist()
        with self.assertRaises(ValueError):self.result()
    def test_future_direction_excluded(self):
        self.d['decision_time_utc']=iso(self.T+1);self.persist();self.assertFalse(self.result()['direction']['available'])
    def test_future_direction_generation_excluded(self):
        self.d['generated_at_utc']=iso(self.T+1);self.persist();self.assertFalse(self.result()['direction']['available'])
    def test_entry_trigger_lag(self):
        self.e['trigger_time']-=DURATIONS['15m'];self.persist();self.assertIn('ENTRY_LAGS_MARKET',self.result()['warnings'])
    def test_entry_hash_mismatch(self):
        f=self.repo/'output_entry/btc_anytime/v1/records'/(self.e['publication_id']+'.json');obj=json.loads(f.read_text());obj['evaluation']['entry_state']='ENTRY_CANDIDATE';f.write_text(json.dumps(obj))
        with self.assertRaises(ValueError):self.result()
    def test_entry_manifest_corruption(self):
        f=self.repo/'output_entry/btc_anytime/v1/inputs'/(self.e['publication_id']+'.json');f.write_text('{}')
        with self.assertRaises(ValueError):self.result()
    def test_entry_direction_mismatch(self):
        self.e['upstream_direction_decision_id']='x';self.persist();self.assertIn('ENTRY_DIRECTION_MISMATCH',self.result()['warnings'])
    def test_deterministic_serialization(self):self.assertEqual(S.text(self.result()),S.text(self.result()))
    def test_read_only_no_output(self):
        before={f:f.read_bytes() for f in self.repo.rglob('*.json')};self.result();self.assertEqual(before,{f:f.read_bytes() for f in self.repo.rglob('*.json')});self.assertFalse((self.repo/S.TARGET).exists())
    def test_atomic_update_and_noop(self):
        r=self.result();self.assertEqual(S.atomic_write(self.repo,r),'UPDATED');self.assertEqual(S.atomic_write(self.repo,r),'NOOP');self.assertFalse(list((self.repo/'output_btc_anytime').glob('.analysis-*')))
    def test_invalid_payload_protects_old(self):
        r=self.result();S.atomic_write(self.repo,r);before=(self.repo/S.TARGET).read_bytes();r['direction']['direction_score']='wrong'
        with self.assertRaises(ValueError):S.atomic_write(self.repo,r)
        self.assertEqual(before,(self.repo/S.TARGET).read_bytes())
    def test_replace_failure_protects_old(self):
        S.atomic_write(self.repo,self.result());before=(self.repo/S.TARGET).read_bytes()
        with patch.object(S.os,'replace',side_effect=OSError('fixture')):
            with self.assertRaises(OSError):S.atomic_write(self.repo,self.result(self.T+1))
        self.assertEqual(before,(self.repo/S.TARGET).read_bytes());self.assertFalse(list((self.repo/'output_btc_anytime').glob('.analysis-*')))
    def test_older_snapshot_refused(self):
        S.atomic_write(self.repo,self.result(self.T+1))
        with self.assertRaises(ValueError):S.atomic_write(self.repo,self.result())
    def test_chatgpt_not_claimed_verified(self):self.assertEqual(self.result()['analysis_compatibility']['chatgpt_access'],'ACCESS_UNVERIFIED')


if __name__=='__main__':unittest.main()

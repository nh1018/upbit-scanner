"""Offline synthetic context tests; discovery file is real read-only evidence."""
import copy
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

from strategy_evaluation import b_prospective as B
from strategy_evaluation.adapters import base
from strategy_evaluation.contracts import HOUR,sha
from strategy_evaluation.engine import evaluate
from upbit_b.contracts import Candle
from upbit_b.feature_contracts import digest
from upbit_b.market_data import iso

ROOT=Path(__file__).resolve().parents[1]
DISC=ROOT/'strategy_evaluation/research/b_discovery16_v1.json'
CONTRACT=ROOT/'strategy_evaluation/research/b_prospective_contract_v1.json'
NOW=1800000000000//HOUR*HOUR


def sig(sid='synthetic',observed=NOW+1000,market='KRW-X'):
    frozen=json.loads(CONTRACT.read_bytes())
    return base('B',frozen['allowed_strategy_versions'][0],sid,market,observed,NOW,b'fixture','fixture://b',
                {'price':'100','type':'COMPLETED_1H_CLOSE_DIAGNOSTIC'},frozen['allowed_cohorts'][0],'70')


def cs(provider='UPBIT',instrument='KRW-X',end=NOW,count=25):
    return [Candle(provider,instrument,'1h',end-(count-i)*HOUR,end-(count-i-1)*HOUR,
                   D(100),D(110),D(90),D(100),D(1),D(10),True) for i in range(count)]


def ev(candles,received=NOW+1500):
    binance=candles[0].provider=='BINANCE_SPOT'
    return {'url':('https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1h' if binance else
                   'https://api.upbit.com/v1/candles/minutes/60?market='+candles[0].instrument),
            'response_sha256':'a'*64,'received_at_utc':iso(received),
            'source_row_open_times_ms':[c.open_ms for c in candles]}


def activation(c):
    a={'state':'APPROVED_ACTIVATION','contract_sha256':c['contract_sha256'],
       'approval_reference':'FIXTURE_ONLY_NOT_REAL_APPROVAL','contract_recorded_at_ms':NOW-100,
       'approved_at_ms':NOW-50,'activation_time_ms':NOW}
    return dict(a,event_id=digest(a))


def context(s=None,up=None,btc=None,registered=NOW+2000):
    up=up if up is not None else cs();btc=btc if btc is not None else cs('BINANCE_SPOT','BTCUSDT')
    return B.context(s or sig(),up,ev(up),btc,ev(btc),registered)


class ProspectiveTests(unittest.TestCase):
    def setUp(self):
        self.raw=DISC.read_bytes();self.c=json.loads(CONTRACT.read_bytes());self.a=activation(self.c)

    def test_real_discovery16_and_draft(self):
        B.validate_discovery(json.loads(self.raw));B.validate_contract(self.c)
        self.assertEqual(self.c['discovery_file_sha256'],sha(self.raw))
        self.assertIsNone(self.c['activation_time_utc'])
        self.assertEqual(self.c,B.contract(self.raw))

    def test_discovery_outcome_tamper(self):
        d=json.loads(self.raw);d['signals'][0]['prior_evaluation']['return_pct']='999'
        with self.assertRaises(ValueError):B.validate_discovery(d)

    def test_contract_mutation_rejected_even_rehashed(self):
        c=copy.deepcopy(self.c);c['horizons_days']=[1];c['contract_sha256']=digest({k:v for k,v in c.items() if k!='contract_sha256'})
        with self.assertRaises(ValueError):B.validate_contract(c)

    def test_activation_required(self):
        with self.assertRaisesRegex(ValueError,'NOT_ACTIVATED'):B.eligibility(sig(),self.c,self.raw,None)

    def test_activation_no_retroactivity(self):
        a=dict(self.a,activation_time_ms=NOW-60);a['event_id']=digest({k:v for k,v in a.items() if k!='event_id'})
        with self.assertRaises(ValueError):B.validate_activation(a,self.c,self.raw)

    def test_signal_boundary_and_discovery_exclusion(self):
        self.assertEqual(B.eligibility(sig(),self.c,self.raw,self.a),'ELIGIBLE')
        self.assertEqual(B.eligibility(sig(observed=NOW),self.c,self.raw,self.a),'PRE_ACTIVATION_EXCLUDED')
        old=json.loads(self.raw)['signals'][0]['signal_contract']
        self.assertEqual(B.eligibility(old,self.c,self.raw,self.a),'DISCOVERY_EXCLUDED')

    def test_no_relabel_old_signal(self):
        old=copy.deepcopy(json.loads(self.raw)['signals'][0]['signal_contract']);old['signal_observed_at']=NOW+1
        with self.assertRaises(ValueError):B.eligibility(old,self.c,self.raw,self.a)

    def test_equal_zero_and_null_denominator(self):
        e=context();self.assertEqual(e['H1']['group'],'EQUAL');self.assertEqual(e['H2']['group6'],'ZERO')
        up=[replace(c,quote_trade_amount=D(0)) for c in cs()]
        self.assertEqual(context(up=up)['H1']['status'],'UNAVAILABLE')

    def test_increase_decrease_exact_boundary(self):
        for q,label in [(D('10.00001'),'INCREASE'),(D('9.99999'),'DECREASE')]:
            up=cs();up[-1]=replace(up[-1],quote_trade_amount=q)
            self.assertEqual(context(up=up)['H1']['group'],label)

    def test_manual_indicator_calculation(self):
        up=cs();up[-7]=replace(up[-7],close=D(80));up[-25]=replace(up[-25],close=D(50))
        # Adjust lows for valid synthetic OHLC.
        up=[replace(c,low=min(D(90),c.close)) for c in up]
        e=context(up=up);self.assertEqual(D(e['H3']['return6_pct']),D(25));self.assertEqual(D(e['H3']['return24_pct']),D(100))

    def test_gap_duplicate_forming_fail_closed(self):
        for up in (cs()[:-1],cs()+[cs()[0]],[replace(cs()[0],completed=False),*cs()[1:]]):
            self.assertEqual(context(up=up)['H3']['status'],'UNAVAILABLE')

    def test_independent_lookbacks(self):
        e=context(up=cs()[-12:],btc=cs('BINANCE_SPOT','BTCUSDT')[-7:])
        self.assertEqual(e['H1']['status'],'AVAILABLE');self.assertEqual(e['H3']['status'],'UNAVAILABLE')
        self.assertEqual(e['H2']['status6'],'AVAILABLE');self.assertEqual(e['H2']['status24'],'UNAVAILABLE')

    def test_rehashed_arithmetic_tamper(self):
        e=context();e['H1']['ratio']='999';e['event_id']=digest({k:v for k,v in e.items() if k!='event_id'})
        with self.assertRaisesRegex(ValueError,'replay mismatch'):B.validate_context(e)

    def test_future_suffix_and_immutability(self):
        up=cs();bc=cs('BINANCE_SPOT','BTCUSDT');before=copy.deepcopy((up,bc));e=context(up=up,btc=bc)
        future=replace(up[-1],open_ms=NOW,close_ms=NOW+HOUR,close=D(999),high=D(999),completed=False)
        other=context(up=up+[future],btc=bc)
        for key in ('H1','H2','H3'):self.assertEqual(other[key],e[key])
        self.assertEqual((up,bc),before)

    def test_late_registration_rejected(self):
        with self.assertRaises(ValueError):context(registered=NOW+HOUR)
        with self.assertRaises(ValueError):context(registered=NOW)

    def test_received_clock_future_rejected(self):
        up=cs();bc=cs('BINANCE_SPOT','BTCUSDT');ue=ev(up,NOW+3000)
        e=B.context(sig(),up,ue,bc,ev(bc),NOW+2000)
        self.assertEqual(e['H1']['status'],'UNAVAILABLE')

    def test_source_cutoff_conservative(self):
        s=sig(observed=NOW+HOUR-1);e=context(s=s,registered=NOW+HOUR-1)
        self.assertEqual(e['data_cutoff_ms'],NOW)

    def test_provider_instrument_mismatch(self):
        for up in ([replace(c,instrument='KRW-Y') for c in cs()],[replace(c,provider='BINANCE_SPOT') for c in cs()]):
            self.assertEqual(context(up=up)['H3']['status'],'UNAVAILABLE')

    def test_api_hash_mismatch(self):
        with self.assertRaises(ValueError):B.verify_response(b'[]',{'response_sha256':'a'*64})

    def test_classification_default_unknown(self):
        self.assertEqual(context()['classification'],'UNKNOWN')
        with self.assertRaises(ValueError):B.context(sig(),cs(),ev(cs()),cs('BINANCE_SPOT','BTCUSDT'),ev(cs('BINANCE_SPOT','BTCUSDT')),NOW+2000,{'category':'STABLECOIN'})

    def test_zero_price_range(self):
        up=[replace(c,high=D(100),low=D(100)) for c in cs()]
        self.assertIsNone(context(up=up)['H3']['range_position_pct'])

    def test_append_only_replay_conflict_and_inactive(self):
        e=context()
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):B.append_context(root,e,self.c,self.raw,None)
            self.assertEqual(list(Path(root).iterdir()),[])
            self.assertEqual(B.append_context(root,e,self.c,self.raw,self.a),'CREATED')
            first=next(Path(root).glob('*.json')).read_bytes()
            self.assertEqual(B.append_context(root,e,self.c,self.raw,self.a),'REPLAY_NOOP')
            other=context(registered=NOW+2001)
            with self.assertRaises(ValueError):B.append_context(root,other,self.c,self.raw,self.a)
            self.assertEqual(next(Path(root).glob('*.json')).read_bytes(),first)

    def test_path_uses_existing_engine_and_pending(self):
        s=sig();path=cs(end=s['evaluation_anchor']+24*HOUR,count=24)
        evs=[ev(path,path[-1].close_ms)]
        self.assertEqual(B.path_diagnostics(s,path,evs,NOW+1000)['status'],'PENDING')
        d=B.path_diagnostics(s,path,evs,path[-1].close_ms)
        self.assertEqual(d['status'],'MATURED');self.assertEqual(D(d['hours']['24']['return_pct']),0)
        self.assertEqual(B.path_diagnostics(s,path[:-1],evs,path[-1].close_ms)['status'],'UNVERIFIABLE')

    def test_btc_future_is_outcome_only(self):
        s=sig();path=cs('BINANCE_SPOT','BTCUSDT',s['evaluation_anchor']+24*HOUR,24)
        evidence=ev(path,path[-1].close_ms)
        self.assertEqual(B.btc_outcome(s,1,path,evidence,NOW)['status'],'PENDING')
        b=B.btc_outcome(s,1,path,evidence,path[-1].close_ms)
        self.assertEqual(b['use'],'OUTCOME_ONLY_NOT_HEDGE_PNL');self.assertEqual(D(b['return_pct']),0)
        self.assertEqual(B.btc_outcome(s,1,path[:-1],evidence,path[-1].close_ms)['status'],'UNVERIFIABLE')

    def test_statistics_duplicate_and_overlap_before_grouping(self):
        s=sig();s2=sig('synthetic2',NOW+2000);path=cs(end=s['evaluation_anchor']+24*HOUR,count=24);evs=[ev(path,path[-1].close_ms)]
        e=evaluate(s,1,path,evs,path[-1].close_ms);e2=evaluate(s2,1,path,evs,path[-1].close_ms)
        out=B.report([e,e,e2],[context()],self.c,self.raw,self.a)
        self.assertEqual(out[0]['statistics']['signals'],1);self.assertEqual(len(out[0]['overlap_excluded']),1)
        self.assertEqual(out[0]['time_clusters'],1);self.assertEqual(out[0]['hypothesis_groups']['H1']['EQUAL']['MATURED'],1)

    def test_context_conflict_and_tamper(self):
        e=context();e['H1']['ratio']='999'
        with self.assertRaises(ValueError):B.validate_context(e)

    def test_raw_sidecar_hash_idempotence_and_activation(self):
        raw=b'[]';evidence={'response_sha256':sha(raw)}
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):B.append_response(root,raw,evidence,self.c,self.raw,None)
            self.assertEqual(B.append_response(root,raw,evidence,self.c,self.raw,self.a),'CREATED')
            self.assertEqual(B.append_response(root,raw,evidence,self.c,self.raw,self.a),'REPLAY_NOOP')
            with self.assertRaises(ValueError):B.append_response(root,b'[1]',evidence,self.c,self.raw,self.a)

    def test_changed_strategy_cohort_excluded(self):
        from strategy_evaluation.adapters import seal_signal
        s=dict(sig(),cohort='changed');s=seal_signal(s)
        self.assertEqual(B.eligibility(s,self.c,self.raw,self.a),'STRATEGY_COHORT_CHANGED')

    def test_validated_storage_envelope_replay(self):
        with tempfile.TemporaryDirectory() as root:
            B.append_context(root,context(),self.c,self.raw,self.a)
            envelope=json.loads(next(Path(root).glob('*.json')).read_bytes())
            B.validate_context(envelope['context'])

    def test_btc_relative_report_and_discovery_not_in_statistics(self):
        s=sig();path=cs(end=s['evaluation_anchor']+24*HOUR,count=24);end=path[-1].close_ms
        e=evaluate(s,1,path,[ev(path,end)],end)
        bp=cs('BINANCE_SPOT','BTCUSDT',end,24);benchmark=B.btc_outcome(s,1,bp,ev(bp,end),end)
        old=json.loads(self.raw)['signals'][0]['evaluation_24h']
        out=B.report([old,e],[context()],self.c,self.raw,self.a,[benchmark])
        self.assertEqual(len(out),1);self.assertEqual(out[0]['statistics']['signals'],1)
        self.assertEqual(out[0]['hypothesis_groups']['H1']['EQUAL']['mean_simple_excess_pp'],'0')

    def test_complete_lineage_required_for_discovery(self):
        with self.assertRaises(ValueError):B.verify_discovery_sources(json.loads(self.raw),[])

    def test_continuous_rank_associations(self):
        self.assertEqual(D(B._rank_corr([D(1),D(2),D(3)],[D(3),D(2),D(1)])),D(-1))
        self.assertIsNone(B._rank_corr([D(1)]*3,[D(1),D(2),D(3)]))


if __name__=='__main__':unittest.main()

"""Synthetic fixtures only; never production signals or profitability evidence."""
import copy
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal as D, localcontext
from pathlib import Path
from unittest.mock import Mock

from upbit_b.contracts import Candle
from upbit_b.feature_contracts import digest, dumps
from upbit_b.market_data import iso, UPBIT
from strategy_evaluation import adapters as A, engine as E, contracts as C
from strategy_evaluation.aggregate import aggregate, latest
from strategy_evaluation.storage import append
from strategy_evaluation.runner import fetch_evaluation

NOW=1791504000000


def signal(strategy='B', market='KRW-X', observed=NOW+1000, sid='fixture'):
    return A.base(strategy,'fixture-v1',sid,market,observed,NOW,b'fixture','fixture://sealed',
        {'price':'100','type':'COMPLETED_CLOSE_DIAGNOSTIC'},'synthetic-cohort','70')


def candles(s,days=1,price='110'):
    anchor=s['evaluation_anchor']
    return [Candle('UPBIT',s['market'],'1h',anchor+i*C.HOUR,anchor+(i+1)*C.HOUR,
        D(100),D(120),D(80),D(price),D(1),D(100),True) for i in range(days*24)]


def evidence(cs,end,market='KRW-X'):
    return [{'url':UPBIT+'/v1/candles/minutes/60?market='+market,'response_sha256':'a'*64,
        'source_row_open_times_ms':[c.open_ms for c in cs],'received_at_utc':iso(end)}]


def outcome(s=None,days=1,price='110'):
    s=s or signal();cs=candles(s,days,price);end=cs[-1].close_ms
    return E.evaluate(s,days,cs,evidence(cs,end,s['market']),end)


class PerformanceTests(unittest.TestCase):
    def test_exact_metrics_and_horizons(self):
        for days in (1,3,7):
            e=outcome(days=days)
            self.assertEqual([e[k] for k in ('status','return_pct','mfe_pct','mae_pct')],['MATURED','10.0','20.0','-20.0'])
            E.validate_evaluation(e)

    def test_pending_to_matured_and_append_only(self):
        s=signal();pending=E.evaluate(s,1,[],[],NOW+1000);mature=outcome(s)
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(append(root,pending),'CREATED');first=list(Path(root).rglob('*.json'))[0].read_bytes()
            self.assertEqual(append(root,mature),'CREATED')
            self.assertEqual(append(root,mature),'REPLAY_NOOP')
            self.assertEqual(len(list(Path(root).rglob('*.json'))),2)
            self.assertIn(first,[p.read_bytes() for p in Path(root).rglob('*.json')])
        self.assertEqual(latest([pending,mature])[0],mature)

    def test_future_suffix_invariance_and_immutability(self):
        s=signal();cs=candles(s);end=cs[-1].close_ms;ev=evidence(cs,end);before=copy.deepcopy((s,cs,ev))
        a=E.evaluate(s,1,cs,ev,end)
        self.assertEqual(a,E.evaluate(s,1,cs+[replace(cs[-1],open_ms=end,close_ms=end+C.HOUR,completed=False)],ev,end))
        self.assertEqual((s,cs,ev),before)

    def test_missing_duplicate_forming_invalid(self):
        s=signal();cs=candles(s);end=cs[-1].close_ms
        for path in (cs[:-1],cs+[cs[0]],[replace(cs[0],completed=False),*cs[1:]],
                     [replace(cs[0],high=D(90)),*cs[1:]],[replace(cs[0],close=D('NaN')),*cs[1:]]):
            self.assertEqual(E.evaluate(s,1,path,evidence(cs,end),end)['status'],'UNVERIFIABLE')

    def test_wrong_market_provider_timeframe(self):
        s=signal();cs=candles(s);end=cs[-1].close_ms
        for changes in ({'instrument':'KRW-Y'},{'provider':'BINANCE_SPOT'},{'timeframe':'4h'}):
            self.assertEqual(E.evaluate(s,1,[replace(cs[0],**changes),*cs[1:]],evidence(cs,end),end)['status'],'UNVERIFIABLE')

    def test_evidence_missing_wrong_market_future_and_hash(self):
        s=signal();cs=candles(s);end=cs[-1].close_ms;ev=evidence(cs,end)
        for e in ([],evidence(cs,end,'KRW-Y'),[{**ev[0],'received_at_utc':iso(end+1)}],
                  [{**ev[0],'response_sha256':'bad'}],[{**ev[0],'source_row_open_times_ms':[]}]):
            self.assertEqual(E.evaluate(s,1,cs,e,end)['status'],'UNVERIFIABLE')

    def test_unknown_availability_is_not_ready(self):
        s=signal(observed=None)
        self.assertEqual(E.evaluate(s,1,[],[],NOW)['status'],'UNVERIFIABLE')

    def test_future_cutoff_rejected(self):
        with self.assertRaises(ValueError):signal(observed=NOW-1)

    def test_pre_observation_rejected(self):
        with self.assertRaises(ValueError):E.evaluate(signal(),1,[],[],NOW)

    def test_horizon_type_boundaries(self):
        for day in (0,2,True,1.0):
            with self.assertRaises(ValueError):E.evaluate(signal(),day,[],[],NOW+1000)

    def test_decimal_context_isolation(self):
        with localcontext() as ctx:
            ctx.prec=5
            self.assertEqual(outcome()['return_pct'],'10.0')

    def test_timezone_next_boundary(self):
        s=signal(observed=NOW+23*C.HOUR+59999)
        self.assertEqual(s['evaluation_anchor'],NOW+24*C.HOUR)
        self.assertEqual(iso(NOW+24*C.HOUR)[11:19],'00:00:00')

    def test_source_hash_and_seal(self):
        with self.assertRaises(ValueError):C.external(b'a',C.sha(b'b'))
        s=signal();s['market']='KRW-Y'
        with self.assertRaises(ValueError):C.validate_signal(s)
        e=outcome();e['return_pct']='99'
        with self.assertRaises(ValueError):E.validate_evaluation(e)

    def test_event_metric_consistency(self):
        e=outcome();e.pop('event_id');e['return_pct']='99'
        with self.assertRaises(ValueError):E.validate_evaluation(E.seal(e))

    def test_duplicate_and_nonoverlap_selection(self):
        a=outcome();s=signal(observed=NOW+C.HOUR+1000,sid='second');b=outcome(s)
        g=aggregate([a,a,b])[0]
        self.assertEqual(g['statistics']['signals'],1);self.assertEqual(g['overlap_excluded'],['second'])

    def test_overlap_selection_independent_of_outcome_status(self):
        a=E.evaluate(signal(),1,[],[],NOW+1000);b=outcome(signal(observed=NOW+C.HOUR+1000,sid='second'))
        self.assertEqual(aggregate([a,b])[0]['statistics']['MATURED'],0)

    def test_exact_nonoverlap_boundary_allowed(self):
        a=outcome();b=outcome(signal(observed=NOW+24*C.HOUR+1000,sid='next'))
        self.assertEqual(aggregate([a,b])[0]['statistics']['signals'],2)

    def test_strategy_cohort_horizon_isolation(self):
        a=outcome();b=outcome(signal('A'));c=outcome(days=3)
        self.assertEqual(len(aggregate([a,b,c])),3)

    def test_denominators_and_expectancy(self):
        rows=[outcome(signal(market='KRW-'+str(i),sid=str(i)),price=p) for i,p in enumerate(['110','90','100'])]
        pending=E.evaluate(signal(market='KRW-P',sid='p'),1,[],[],NOW+1000)
        missing=E.evaluate(signal(market='KRW-M',sid='m'),1,[],[],NOW+25*C.HOUR)
        g=aggregate([*rows,pending,missing])[0]['statistics']
        self.assertEqual((g['signals'],g['MATURED'],g['PENDING'],g['UNVERIFIABLE']),(5,3,1,1))
        self.assertEqual((g['wins'],g['losses'],g['flats']),(1,1,1))
        self.assertEqual(D(g['win_rate']),D('0.3333333333333333333333333333333333'))
        self.assertEqual(D(g['payoff_ratio']),1);self.assertEqual(D(g['expectancy_pct']),0)
        self.assertEqual(g['mfe_pct']['p50'],'20.0')

    def test_no_signal_no_statistics(self):self.assertEqual(aggregate([]),[])

    def test_conflicting_matured_and_terminal_regression(self):
        a=outcome();b=outcome(price='90')
        with self.assertRaises(ValueError):latest([a,b])
        with tempfile.TemporaryDirectory() as tmp:
            append(tmp,a)
            with self.assertRaises(ValueError):append(tmp,b)

    def test_terminal_reverification_no_duplicate(self):
        a=outcome();b={k:v for k,v in a.items() if k!='event_id'};b['as_of_ms']+=1;b=E.seal(b)
        with tempfile.TemporaryDirectory() as tmp:
            append(tmp,a);self.assertEqual(append(tmp,b),'REPLAY_NOOP')
            self.assertEqual(len(list(Path(tmp).rglob('*.json'))),1)

    def test_same_signal_different_source_conflict(self):
        s=signal();s['source_hash']='b'*64;s=A.seal_signal(s)
        with self.assertRaises(ValueError):latest([outcome(),outcome(s)])

    def test_writer_lock_never_silently_resets(self):
        event=outcome()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/event['strategy']/event['evaluation_id'];path.mkdir(parents=True)
            (path/'.lock').mkdir()
            with self.assertRaises(ValueError):append(tmp,event)
            self.assertTrue((path/'.lock').exists())

    def test_manual_cli_pinned_bundle_default_no_write(self):
        from strategy_evaluation.runner import main
        from dataclasses import asdict
        from unittest.mock import patch
        s=signal();cs=candles(s);end=cs[-1].close_ms
        rows=[]
        for c in cs:
            row=asdict(c)
            for k,v in row.items():
                if isinstance(v,D):row[k]=str(v)
            rows.append(row)
        raw=json.dumps({'signal':s,'days':1,'candles':rows,'evidence':evidence(cs,end),'as_of_ms':end}).encode()
        with tempfile.TemporaryDirectory() as tmp,patch('builtins.print'):
            file=Path(tmp)/'bundle.json';file.write_bytes(raw)
            self.assertEqual(main(['--bundle',str(file),'--bundle-sha256',C.sha(raw)])['status'],'MATURED')
            self.assertEqual(len(list(Path(tmp).rglob('*'))),1)
            with self.assertRaises(ValueError):main(['--bundle',str(file),'--bundle-sha256','0'*64])

    def test_recorded_real_bundle_and_response_offline(self):
        from strategy_evaluation.runner import main
        from upbit_b.market_data import normalize
        from upbit_c.research_scan import candle_record
        from unittest.mock import patch
        root=Path(__file__).resolve().parents[1]/'strategy_evaluation/audits'
        bundle=root/'real_B_1d_bundle_20261010.json'
        source=root/'6610bfc53fdc8667d6f4b8a67819120b88783797e8478c5a844766369ade20bc.source.json'
        self.assertEqual(C.sha(source.read_bytes()),source.name.split('.')[0])
        rows=json.loads(source.read_bytes(),parse_float=D)
        cs=sorted([replace(normalize('UPBIT','KRW-JST','1h',r),completed=True) for r in rows],key=lambda c:c.open_ms)
        recorded=json.loads(bundle.read_bytes())
        self.assertEqual([candle_record(c) for c in cs],recorded['candles'])
        with patch('builtins.print'),patch('urllib.request.urlopen',side_effect=AssertionError('network prohibited')):
            e=main(['--bundle',str(bundle),'--bundle-sha256','7235db3d6b3b08a18088fa77d8bc6560949b472401341141c35a51a9ee03ca96'])
        self.assertEqual(e['status'],'MATURED')
        self.assertEqual(e['return_pct'],'0.529100529100529100529100529100500')

    def test_protected_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):append(Path(tmp)/'data',outcome())

    def test_api_failure_recovery(self):
        client=Mock();client.get.side_effect=OSError('fixture')
        s=signal();a=fetch_evaluation(client,s,1,NOW+26*C.HOUR)
        self.assertEqual(a['status'],'UNVERIFIABLE')
        cs=candles(s);b=E.evaluate(s,1,cs,evidence(cs,NOW+27*C.HOUR),NOW+27*C.HOUR)
        self.assertEqual(latest([a,b])[0]['status'],'MATURED')

    def test_no_api_for_unmatured(self):
        client=Mock();fetch_evaluation(client,signal(),1,NOW+1000);client.get.assert_not_called()

    def test_a_snapshot_availability_not_invented(self):
        r={'scanner_version':'1.3-cloud','candidate_count':1,'candidates':[{'upbit_market':'KRW-X','state':'관찰','warning':False,
            'scan_time_kst':'2026-10-09T09:00:00+09:00','upbit_timestamp_ms':NOW,'upbit_price':100,'score':70}]}
        raw=json.dumps(r,ensure_ascii=False).encode();s=A.a_snapshot(raw,C.sha(raw),'fixture')[0]
        self.assertIsNone(s['signal_observed_at']);self.assertFalse(s['performance_eligible'])
        r['candidates']*=2;r['candidate_count']=2;raw=json.dumps(r).encode()
        with self.assertRaises(ValueError):A.a_snapshot(raw,C.sha(raw),'fixture')

    def test_c_existing_contract_exact_compatibility(self):
        sys.path.insert(0,str(Path(__file__).parent/'upbit_b'))
        from test_c_market_research import signal as c_fixture
        from upbit_c.research_outcomes import evaluate as c_evaluate
        original=c_fixture();raw=(dumps({'record':original,'record_sha256':digest(original)})+'\n').encode()
        s=A.c_signal(original,raw,C.sha(raw),'fixture')
        for days in (1,3,7):
            cs=candles(s,days);end=cs[-1].close_ms;ev=evidence(cs,end)
            old=c_evaluate(original,days,cs,ev,end);new=E.evaluate(s,days,cs,ev,end,original)
            self.assertEqual({k:old[k] for k in ('status','return_pct','mfe_pct','mae_pct')},{k:new[k] for k in ('status','return_pct','mfe_pct','mae_pct')})
            oldraw=(dumps({'record':old,'record_sha256':digest(old)})+'\n').encode()
            imported=A.c_outcome(s,original,oldraw,C.sha(oldraw),'fixture-old-outcome')
            self.assertEqual(imported['return_pct'],old['return_pct'])
        with self.assertRaises(ValueError):E.evaluate(s,1,[],[],NOW+1000)

    def test_b_baseline_and_verified_transition(self):
        sys.path.insert(0,str(Path(__file__).parent/'upbit_b'))
        from test_history import cycle,entry,state_after,CUTOFF
        raw=cycle({'KRW-X':entry(state='TREND_BUILDING')})
        sources=[(raw,C.sha(raw),'baseline')]
        self.assertEqual(A.b_journals(sources),[])
        second=cycle({'KRW-X':entry(boundary=CUTOFF+C.HOUR)},CUTOFF+C.HOUR,state_after(raw))
        imported=A.b_journals([*sources,(second,C.sha(second),'transition')])
        self.assertEqual(len(imported),1)
        self.assertEqual(imported[0]['signal_kind'],'VERIFIED_POSITIVE_STATE_TRANSITION')
        with self.assertRaises(ValueError):A.b_journals([(second,C.sha(second),'missing-parent')])


if __name__=='__main__':unittest.main()

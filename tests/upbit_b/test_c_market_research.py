import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from upbit_b.contracts import Candle, Window, DURATIONS, WINDOWS
from upbit_b.feature_contracts import digest
from upbit_b.features import _input_hash
from upbit_b.market_data import iso, UPBIT, DataError
from upbit_c import research_scan as S, research_history as H, research_outcomes as O
from test_c_research_score import observation

NOW = 1791504000000


def signal():
    score = __import__('upbit_c.research_score',fromlist=['evaluate']).evaluate({tf:observation(tf) for tf in S.TFS})
    item = {"market":"KRW-X","collected_at_ms":NOW+1000,"score":score,"timeframes":{}}
    for tf in S.TFS:
        item['timeframes'][tf]={'source_input_sha256':digest(tf),'source_evidence':[],
            'candles':[{'instrument':'KRW-X','close_ms':NOW,'close':'100','completed':True}]}
    return next(H.signals({'results':[item],'finished_at_ms':NOW+2000,'scan_id':digest('scan')}))


def path(s, days=1):
    return [Candle('UPBIT',s['market'],'1h',s['proxy_anchor_open_ms']+i*H.HOUR,
        s['proxy_anchor_open_ms']+(i+1)*H.HOUR,D(100),D(120),D(80),D(110),D(1),D(100),True)
        for i in range(days*24)]


def evidence(rows, asof):
    return [{'url':UPBIT+'/v1/candles/minutes/60?market=KRW-X',
        'response_sha256':'a'*64,'received_at_utc':iso(asof),
        'source_row_open_times_ms':[c.open_ms for c in rows]}]


class OutcomeTests(unittest.TestCase):
    def test_all_three_horizons_hand_calculation(self):
        s=signal()
        for days in (1,3,7):
            rows=path(s,days); end=rows[-1].close_ms
            out=O.evaluate(s,days,rows,evidence(rows,end),end)
            self.assertEqual(out['status'],'MATURED')
            self.assertEqual(D(out['return_pct']),D(10))
            self.assertEqual(D(out['mfe_pct']),D(20))
            self.assertEqual(D(out['mae_pct']),D(-20))
            self.assertFalse(out['fees_included'])
            self.assertFalse(out['execution_price_claim'])

    def test_future_is_pending_and_not_loss(self):
        s=signal(); rows=path(s)
        out=O.evaluate(s,1,rows,[],rows[-1].close_ms-1)
        self.assertEqual(out['status'],'PENDING')
        self.assertIsNone(out['return_pct'])
        self.assertEqual(out['path'],[])

    def test_gap_missing_duplicate_or_conflict(self):
        s=signal(); rows=path(s); end=rows[-1].close_ms
        for bad in (rows[:-1],rows+[rows[0]],rows+[replace(rows[0],close=D(105))]):
            out=O.evaluate(s,1,bad,evidence(bad,end),end)
            self.assertEqual(out['status'],'UNVERIFIABLE')
            self.assertIsNone(out['return_pct'])

    def test_forming_wrong_symbol_wrong_tf(self):
        s=signal(); rows=path(s); end=rows[-1].close_ms
        for change in ({'completed':False},{'instrument':'KRW-Y'},{'timeframe':'4h'}):
            bad=[replace(rows[0],**change)]+rows[1:]
            self.assertEqual(O.evaluate(s,1,bad,evidence(bad,end),end)['status'],'UNVERIFIABLE')

    def test_invalid_numbers_and_ohlc(self):
        s=signal(); rows=path(s); end=rows[-1].close_ms
        for change in ({'low':D(130)},{'high':D('NaN')},{'close':D(0)},{'base_volume':D(-1)}):
            bad=[replace(rows[0],**change)]+rows[1:]
            self.assertEqual(O.evaluate(s,1,bad,evidence(bad,end),end)['status'],'UNVERIFIABLE')

    def test_evidence_absent_future_or_unrelated(self):
        s=signal(); rows=path(s); end=rows[-1].close_ms
        for ev in ([],evidence(rows,end+1),evidence([],end)):
            self.assertEqual(O.evaluate(s,1,rows,ev,end)['status'],'UNVERIFIABLE')

    def test_future_suffix_invariance(self):
        s=signal(); rows=path(s); end=rows[-1].close_ms
        a=O.evaluate(s,1,rows,evidence(rows,end),end)
        b=O.evaluate(s,1,rows+path(s,3)[24:],evidence(rows,end),end)
        self.assertEqual(a,b)

    def test_pending_fetch_makes_no_requests(self):
        from unittest.mock import Mock
        client=Mock(); s=signal()
        self.assertEqual(O.fetch_outcome(client,s,7,NOW)['status'],'PENDING')
        client.get.assert_not_called()

    def test_api_failure_not_loss(self):
        from unittest.mock import Mock
        client=Mock(); client.get.side_effect=DataError('HTTP 500')
        s=signal()
        out=O.fetch_outcome(client,s,1,path(s)[-1].close_ms)
        self.assertEqual(out['status'],'UNVERIFIABLE')
        self.assertIsNone(out['return_pct'])

    def test_bad_horizons(self):
        for days in (True,0,2,8):
            with self.assertRaises(ValueError):O.evaluate(signal(),days,[],[],NOW)

    def test_anchor_after_actual_observation(self):
        s=signal()
        self.assertGreater(s['proxy_anchor_open_ms'],s['observed_at_ms'])
        self.assertEqual(s['trigger_close_ms'],NOW)
        s['proxy_anchor_open_ms']=NOW
        with self.assertRaises(ValueError):H.validate_signal(s)


class HistoryTests(unittest.TestCase):
    def test_id_duplicate_first_clock_preserved(self):
        s=signal(); later=deepcopy(s)
        later['observed_at_ms']+=H.HOUR
        later['proxy_anchor_open_ms']+=H.HOUR
        later['scan_id']=digest('later')
        later['score']['sources']['1h']['source_cutoff_ms']+=1
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(H.write_once(root,'signals',s['signal_id'],s,True),'CREATED')
            self.assertEqual(H.write_once(root,'signals',s['signal_id'],later,True),'REPLAY_NOOP')
            self.assertEqual(H.read_record(Path(root)/'signals'/(s['signal_id']+'.json')),s)
            self.assertEqual(len(list(Path(root).rglob('*.json'))),1)

    def test_conflict_no_overwrite(self):
        s=signal(); changed=deepcopy(s); changed['score']['score']='99'
        with tempfile.TemporaryDirectory() as root:
            H.write_once(root,'signals',s['signal_id'],s,True)
            with self.assertRaises(ValueError):H.write_once(root,'signals',s['signal_id'],changed,True)
            self.assertEqual(H.read_record(Path(root)/'signals'/(s['signal_id']+'.json')),s)

    def test_tamper_detected(self):
        s=signal()
        with tempfile.TemporaryDirectory() as root:
            H.write_once(root,'signals',s['signal_id'],s)
            p=Path(root)/'signals'/(s['signal_id']+'.json')
            obj=json.loads(p.read_text());obj['record']['reference_price']='123'
            p.write_text(json.dumps(obj))
            with self.assertRaises(ValueError):H.read_record(p)

    def test_no_reconstruction_blocked(self):
        self.assertEqual(list(H.signals({'results':[{'score':{'research_setup':'BLOCKED'}}]})),[])

    def test_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):H.write_once(root,'scans','../x',{})


class ScanTests(unittest.TestCase):
    def run_scan(self, windows=None, **kw):
        windows=windows or {}
        class MD:
            def __init__(self,*a,**k):self.cache={}
            def window(self,provider,market,tf):
                kind=windows.get((market,tf),'AVAILABLE')
                if kind=='throw':raise DataError('HTTP 500')
                c=Candle('UPBIT',market,tf,NOW-DURATIONS[tf],NOW,D(100),D(120),D(80),D(110),D(1),D(100),True)
                return Window(kind,(c,),NOW,(),digest([market,tf]),reason='API 418' if kind=='API_ERROR' else None)
        with patch.object(S,'universe',return_value=([{'market':'KRW-X'},{'market':'KRW-Y'}],{})),patch.object(S,'MarketData',MD):
            # Real score requires correct TF; collect it via the window snapshot hook.
            state={}
            def snap(w,clock):state['tf']=w.candles[0].timeframe;return {}
            with patch.object(S,'snapshot',side_effect=snap),patch.object(S,'observe',side_effect=lambda f:observation(state['tf'])):
                return S.scan(object(),clock=lambda:NOW+2000,**kw)

    def test_full_universe_and_reproducible(self):
        a=self.run_scan(); b=self.run_scan()
        self.assertEqual(a,b)
        self.assertEqual(a['selected_count'],2)
        self.assertEqual(a['success_count'],2)
        self.assertEqual(a['candidates_created'],0)

    def test_score_is_independent_of_caller_decimal_context(self):
        from decimal import localcontext
        a=self.run_scan()
        with localcontext() as ctx:
            ctx.prec=9
            b=self.run_scan()
        self.assertEqual(a,b)

    def test_partial_failure_isolated(self):
        r=self.run_scan({('KRW-X','1h'):'throw'})
        self.assertEqual(r['success_count'],1)
        self.assertEqual(r['failure_count'],1)

    def test_insufficient_reported(self):
        r=self.run_scan({('KRW-X','1h'):'INSUFFICIENT_DATA'})
        self.assertEqual(r['failure_reasons']['INSUFFICIENT_DATA'],1)

    def test_block_418_stops_remaining_requests(self):
        r=self.run_scan({('KRW-X','1h'):'API_ERROR'})
        self.assertTrue(r['api_blocked'])
        self.assertEqual(r['failure_reasons']['NOT_ATTEMPTED_API_BLOCK'],5)

    def test_batches_cover_market_once(self):
        a=self.run_scan(batch_index=0,batch_count=2)
        b=self.run_scan(batch_index=1,batch_count=2)
        self.assertEqual({r['market'] for r in a['results']+b['results']},{'KRW-X','KRW-Y'})
        self.assertEqual(a['selected_count'],1)

    def test_unknown_and_duplicate_markets_rejected(self):
        for markets in (['KRW-Z'],['KRW-X','KRW-X']):
            with self.assertRaises(ValueError):self.run_scan(markets=markets)

    def test_invalid_batch(self):
        with self.assertRaises(ValueError):self.run_scan(batch_index=2,batch_count=2)

    def test_read_only_output_guard(self):
        from upbit_c.research_runner import main
        with self.assertRaises(SystemExit):main(['--output','output_upbit_b/c-research'])

    def test_workflow_manual_and_readonly(self):
        text=Path('.github/workflows/upbit-c-market-research.yml').read_text()
        self.assertNotIn('schedule:',text)
        self.assertNotIn('contents: write',text)
        self.assertNotIn('git push',text)
        self.assertIn('workflow_dispatch:',text)

    def test_real_collector_features_and_score_integration(self):
        class Client:
            def get(self,base,endpoint,params):
                if endpoint=='/v1/market/all':return [{'market':'KRW-X'}],{'url':'universe'}
                tf='1d' if endpoint.endswith('/days') else '4h' if endpoint.endswith('/240') else '1h'
                step=DURATIONS[tf]; end=NOW//step*step
                rows=[]
                for i in range(WINDOWS[tf]):
                    start=end-(WINDOWS[tf]-i)*step
                    price=100+abs((i%12)-6)
                    rows.append({'market':'KRW-X','unit':step//60000,'candle_date_time_utc':iso(start),
                        'opening_price':str(price),'high_price':str(price+2),'low_price':str(price-2),
                        'trade_price':str(price),'candle_acc_trade_volume':'10','candle_acc_trade_price':'1000'})
                return rows,{'url':UPBIT+endpoint,'response_sha256':'a'*64,'received_at_utc':iso(NOW+1000)}
        a=S.scan(Client(),clock=lambda:NOW+2000)
        self.assertEqual(a['success_count'],1)
        for tf in S.TFS:
            item=a['results'][0]['timeframes'][tf]
            self.assertTrue(all(c['completed'] for c in item['candles']))
            self.assertEqual(item['status'],'EVALUATED')
        original=deepcopy(a)
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(H.record_scan(root,a)['scan'],'CREATED')
            self.assertEqual(H.record_scan(root,a)['scan'],'REPLAY_NOOP')
            saved=H.read_record(Path(root)/'scans'/(a['scan_id']+'.json'))
            self.assertEqual(saved,a)
        self.assertEqual(original,a)

    def test_decimal_source_lexical_form_preserved(self):
        rows=path(signal()); rows[0]=replace(rows[0],open=D('100.0000'))
        stored=S.candle_record(rows[0])
        self.assertEqual(stored['open'],'100.0000')
        rebuilt=Candle(**{k:D(v) if k in ('open','high','low','close','base_volume','quote_trade_amount') else v for k,v in stored.items()})
        self.assertEqual(_input_hash([rows[0]]),_input_hash([rebuilt]))

    def test_mature_runner_replay_does_not_refetch(self):
        from upbit_c.research_runner import main
        from contextlib import redirect_stdout
        import io
        s=signal(); result={'status':'MATURED','signal_id':s['signal_id']}
        with tempfile.TemporaryDirectory() as root:
            H.write_once(root,'signals',s['signal_id'],s)
            with patch('upbit_c.research_runner.fetch_outcome',return_value=result) as fetch,redirect_stdout(io.StringIO()):
                main(['--output',root,'--outcomes-only'])
                self.assertEqual(fetch.call_count,3)
                main(['--output',root,'--outcomes-only'])
                self.assertEqual(fetch.call_count,3)

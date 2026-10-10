import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from upbit_c.coverage_review import review, main
from upbit_b.feature_contracts import digest, dumps


class CoverageReviewTests(unittest.TestCase):
    def report(self, status='INSUFFICIENT_DATA', **extra):
        return {'activation': 'RESEARCH_ONLY', 'scan_id': 'fixture', 'source_cutoff_ms': 7200000,
            'results': [{'market': 'KRW-X', 'score_status': 'NOT_EVALUATED', 'timeframes': {
                '1h': {'status': status, 'candles': [], **extra}}}]}

    def test_exhaustion_not_listing_proof_and_input_immutable(self):
        r = self.report(source_evidence=[{'response_sha256': hashlib.sha256(b'[]').hexdigest()}])
        original = copy.deepcopy(r); out = review(r)
        item = out['markets'][0]['timeframes']['1h']
        self.assertEqual(r, original)
        self.assertEqual(item['required_candles'], 200)
        self.assertFalse(item['listing_date_confirmed'])
        self.assertFalse(item['proven_collection_defect'])
        self.assertEqual(out['failed_market_count'], 1)

    def test_omission_not_no_trade_proof(self):
        out = review(self.report('MISSING_CANDLES', missing_slots=[3600000], source_evidence=[{'source_row_open_times_ms':[0]}]))
        i = out['markets'][0]['timeframes']['1h']
        self.assertEqual(i['category'], 'SOURCE_OMISSION_NO_TRADE_POSSIBLE')
        self.assertFalse(i['no_trade_confirmed'])
        self.assertEqual(i['full_missing_open_ms'], [3600000])

    def test_present_gap_unresolved(self):
        i=review(self.report('MISSING_CANDLES',missing_slots=[0],source_evidence=[{'source_row_open_times_ms':[0]}]))['markets'][0]['timeframes']['1h']
        self.assertEqual(i['confidence'], 'LOW')

    def test_forming_clock_contradiction(self):
        c={'open_ms':7200000,'close_ms':10800000,'completed':False}
        i=review(self.report('STALE_DATA',candles=[c]))['markets'][0]['timeframes']['1h']
        self.assertIn('CANDLE_CLOCK_OR_COMPLETENESS',i['integrity_violations'])

    def test_duplicate_market(self):
        r=self.report();r['results']*=2
        with self.assertRaises(ValueError):review(r)

    def test_external_hash_gate_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'src';dst=Path(tmp)/'out'
            r=self.report();src.write_text(dumps({'record':r,'record_sha256':digest(r)}),encoding='utf-8')
            with self.assertRaises(ValueError):main([str(src),'--sha256','0'*64,'--output',str(dst)])
            sha=hashlib.sha256(src.read_bytes()).hexdigest()
            main([str(src),'--sha256',sha,'--output',str(dst)])
            with self.assertRaises(FileExistsError):main([str(src),'--sha256',sha,'--output',str(dst)])

    def test_statuses_separate(self):
        for status in ['STALE_DATA','INSUFFICIENT_FEATURES','API_ERROR']:
            self.assertEqual(review(self.report(status))['timeframe_status_counts'][0]['status'],status)

import unittest
from upbit_c.research_diagnostics import diagnose,probe_sources


class DiagnosticsTests(unittest.TestCase):
    def report(self,status,**extra):
        return {'scan_id':'fixture','source_cutoff_ms':100,'results':[{
            'market':'KRW-FIXTURE','score_status':'NOT_EVALUATED','timeframes':{'1h':{
                'status':status,'candles':[{'open_ms':0,'close_ms':10}],**extra}}}]}
    def test_gap_source_omission_not_synthetic_fill(self):
        r=diagnose(self.report('MISSING_CANDLES',missing_slots=[20],source_evidence=[{'source_row_open_times_ms':[0,30]}]))
        item=r['markets'][0]['timeframes']['1h']
        self.assertEqual(item['classification'],'SOURCE_RESPONSES_OMIT_CALENDAR_SLOTS')
        self.assertEqual(item['confirmed_zero_trades'],'unavailable')
        self.assertEqual(r['candles_imputed'],0)
        self.assertFalse(r['eligibility_changed'])
    def test_gap_present_in_raw_response_is_not_called_no_trade(self):
        r=diagnose(self.report('MISSING_CANDLES',missing_slots=[20],source_evidence=[{'source_row_open_times_ms':[20]}]))
        self.assertEqual(r['markets'][0]['timeframes']['1h']['classification'],'UNRESOLVED_GAP_OR_COLLECTION_RANGE')
    def test_empty_page_not_proof_of_listing_date(self):
        r=diagnose(self.report('INSUFFICIENT_DATA',source_evidence=[{'source_row_open_times_ms':[]}]))
        item=r['markets'][0]['timeframes']['1h']
        self.assertEqual(item['classification'],'OFFICIAL_ACCESSIBLE_HISTORY_EXHAUSTED')
        self.assertEqual(item['official_listing_date'],'unavailable')
    def test_legacy_empty_page_evidence_verified_by_response_hash(self):
        import hashlib
        r=diagnose(self.report('INSUFFICIENT_DATA',source_evidence=[{'response_sha256':hashlib.sha256(b'[]').hexdigest()}]))
        item=r['markets'][0]['timeframes']['1h']
        self.assertEqual(item['classification'],'OFFICIAL_ACCESSIBLE_HISTORY_EXHAUSTED')
        self.assertTrue(item['exact_empty_array_response_hash_matched'])
    def test_stale_and_feature_insufficiency_separate(self):
        for status,wanted in [('STALE_DATA','LATEST_COMPLETED_BOUNDARY_ABSENT'),('INSUFFICIENT_FEATURES','MANDATORY_C_FEATURE_UNAVAILABLE')]:
            self.assertEqual(diagnose(self.report(status))['markets'][0]['timeframes']['1h']['classification'],wanted)
    def test_official_probe_absence_is_not_fabricated_trade_evidence(self):
        from unittest.mock import Mock
        client=Mock();client.get.return_value=([],{'response_sha256':'fixture','received_at_utc':'fixture'})
        audit=diagnose(self.report('MISSING_CANDLES',missing_slots=[20],source_evidence=[{'source_row_open_times_ms':[0]}]))
        r=probe_sources(audit,client)
        self.assertEqual(r['result_counts'],{'ORIGINAL_MISSING_SLOT_STILL_ABSENT':1})
        self.assertEqual(r['observations'][0]['independent_trade_history'],'unavailable')
        self.assertFalse(r['eligibility_changed'])
    def test_official_probe_api_failure_remains_unavailable(self):
        from unittest.mock import Mock
        client=Mock();client.get.side_effect=ValueError('API retries exhausted')
        audit=diagnose(self.report('STALE_DATA'))
        self.assertEqual(probe_sources(audit,client)['result_counts'],{'PROBE_UNAVAILABLE':1})

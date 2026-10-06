"""Lossless storage, logical compatibility and append-only regression fixtures."""
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from upbit_b import history_compact as K,history_contracts as C,feature_contracts as F
from upbit_b import history as H,history_runner as R
import test_history as V


def compact_cycle(entries,boundary=V.CUTOFF,previous=None,contract=None,publishable=True):
    c={**(contract or C.versions()),'history_storage_schema':K.SCHEMA,'history_storage_hash':K.STORAGE_HASH}
    return K.pack(V.cycle(entries,boundary,previous,c,publishable))


class CompactApprovedFixtures(V.FixtureTests):
    """All twelve original approved state/event fixtures run through the new codec."""
    def setUp(self):
        original=V.cycle
        def wrapped(entries,boundary=V.CUTOFF,previous=None,contract=None,publishable=True):
            c={**(contract or C.versions()),'history_storage_schema':K.SCHEMA,'history_storage_hash':K.STORAGE_HASH}
            return K.pack(original(entries,boundary,previous,c,publishable))
        self.p=patch.object(V,'cycle',wrapped);self.p.start()
    def tearDown(self):self.p.stop()


class CompactTests(unittest.TestCase):
    def setUp(self):self.entries={'KRW-X':V.entry(),'KRW-Y':V.entry('KRW-Y',state='NO_UPTREND')}
    def payload(self):return V.cycle(self.entries,contract=K.versions())
    def test_exact_byte_roundtrip(self):
        p=self.payload();self.assertEqual(K.unpack(K.pack(p)),p)
    def test_every_field_preserved(self):
        p=self.payload();self.assertEqual(H.validate_cycle(p),H.validate_cycle(K.pack(p)))
    def test_candidates_states_events_sampling_same(self):
        p=self.payload();m,rs=H.validate_cycle(p);n,ss=H.validate_cycle(K.pack(p))
        self.assertEqual(m,n);self.assertEqual(rs,ss)
    def test_deterministic_bytes(self):self.assertEqual(K.pack(self.payload()),K.pack(self.payload()))
    def test_pool_saves_repeated_evidence(self):
        p=self.payload();q=K.pack(p);self.assertLess(len(q),len(p));self.assertTrue(json.loads(q.splitlines()[0])['strings'])
    def test_metadata_never_coalesced_if_different(self):
        p=self.payload();_,rs=H.validate_cycle(K.pack(p));self.assertNotEqual(rs[0]['instrument'],rs[1]['instrument']);self.assertEqual(K.unpack(K.pack(p)),p)
    def test_no_input_mutation(self):
        before=copy.deepcopy(self.entries);K.pack(self.payload());self.assertEqual(before,self.entries)
    def test_null_boolean_list_exact(self):
        m,rs=H.validate_cycle(self.payload());m['scan_evidence']={'literal':[None,True,False,[-1,-2,-3],{},[],'same string repeated','same string repeated']}
        p=(F.dumps(m)+'\n'+''.join(F.dumps(r)+'\n' for r in rs)).encode();self.assertEqual(K.unpack(K.pack(p)),p)
    def test_physical_schema_explicit(self):self.assertEqual(json.loads(K.pack(self.payload()).splitlines()[0])['schema_version'],K.SCHEMA)
    def test_legacy_still_readable(self):
        p=V.cycle(self.entries);self.assertEqual(H.validate_cycle(p)[0]['schema_version'],C.SCHEMA_VERSION)
    def test_legacy_does_not_change(self):
        self.assertNotIn('history_storage_schema',H.validate_cycle(V.cycle(self.entries))[0]['versions'])
    def test_new_storage_changes_cohort(self):
        a=H.validate_cycle(V.cycle(self.entries))[0];b=H.validate_cycle(self.payload())[0];self.assertNotEqual(a['cohort_id'],b['cohort_id'])
    def test_cross_cohort_baseline_not_transition(self):
        old=V.cycle(self.entries);previous=H.advance(H.empty_previous(),old)
        es={m:V.entry(m,state='REACCELERATION',boundary=V.CUTOFF+C.HOUR) for m in self.entries}
        p=compact_cycle(es,V.CUTOFF+C.HOUR,previous)
        m,rs=H.validate_cycle(p);self.assertTrue(m['cohort_baseline'])
        self.assertTrue(all([e['event_type'] for e in r['events']]==['BASELINE_STATE'] for r in rs))
        self.assertTrue(all(not e['transition_verified'] for r in rs for e in r['events']))
    def test_unknown_not_exit(self):
        p=compact_cycle({'KRW-X':V.entry()});previous=H.advance(H.empty_previous(),p)
        e={'engine':None,'status':'API_ERROR','reason':'fixture unavailable'}
        _,rs=H.validate_cycle(compact_cycle({'KRW-X':e},V.CUTOFF+C.HOUR,previous))
        self.assertEqual(rs[0]['summary']['candidate'],'UNKNOWN');self.assertNotIn('EXIT_B_CANDIDATE',[e['event_type'] for e in rs[0]['events']])
    def test_recovery_unverified(self):
        p=compact_cycle({'KRW-X':{'engine':None,'status':'API_ERROR','reason':'fixture'}});previous=H.advance(H.empty_previous(),p)
        _,rs=H.validate_cycle(compact_cycle({'KRW-X':V.entry(boundary=V.CUTOFF+C.HOUR)},V.CUTOFF+C.HOUR,previous))
        self.assertIn('DATA_RECOVERED',[e['event_type'] for e in rs[0]['events']]);self.assertTrue(all(not e['transition_verified'] for e in rs[0]['events']))
    def test_gap_resets_transition(self):
        p=compact_cycle({'KRW-X':V.entry()});previous=H.advance(H.empty_previous(),p)
        _,rs=H.validate_cycle(compact_cycle({'KRW-X':V.entry(state='REACCELERATION',boundary=V.CUTOFF+2*C.HOUR)},V.CUTOFF+2*C.HOUR,previous))
        self.assertTrue(all(not e['transition_verified'] for e in rs[0]['events']))
    def test_frozen_retry_same_noop(self):
        with TemporaryDirectory() as t:
            p=K.pack(self.payload());self.assertEqual(H.write_once(t,p,V.END),'CREATED');self.assertEqual(H.write_once(t,p,V.END),'NOOP');self.assertEqual(H.check_existing(t,V.CUTOFF),'REPLAY_NOOP')
    def test_different_payload_conflict(self):
        with TemporaryDirectory() as t:
            p=K.pack(self.payload());H.write_once(t,p,V.END);m,rs=H.validate_cycle(self.payload());m['code_revision']='b'*40
            q=K.pack((F.dumps(m)+'\n'+''.join(F.dumps(r)+'\n' for r in rs)).encode())
            with self.assertRaises(H.Conflict):H.write_once(t,q,V.END)
    def test_preview_unpublishable(self):
        with TemporaryDirectory() as t:
            p=compact_cycle(self.entries,publishable=False)
            with self.assertRaises(ValueError):H.write_once(t,p,V.END)
            self.assertFalse((Path(t)/C.cycle_path(V.CUTOFF)).exists())
    def test_repacking_legacy_not_publishable(self):
        with TemporaryDirectory() as t:
            with self.assertRaises(ValueError):H.write_once(t,K.pack(V.cycle(self.entries)),V.END)
    def test_mixed_chain_old_bytes_preserved(self):
        with TemporaryDirectory() as t:
            root=Path(t);old=V.cycle(self.entries);H.write_once(root,old,V.END);previous=H.load_previous(root,V.CUTOFF+C.HOUR)
            es={m:V.entry(m,boundary=V.CUTOFF+C.HOUR) for m in self.entries};new=compact_cycle(es,V.CUTOFF+C.HOUR,previous)
            H.write_once(root,new,V.END+C.HOUR);self.assertEqual((root/C.cycle_path(V.CUTOFF)).read_bytes(),old)
            state=H.load_previous(root,V.CUTOFF+2*C.HOUR);self.assertEqual(state['cycle_id'],H.validate_cycle(new)[0]['cycle_id'])
    def test_unknown_schema_rejected(self):
        h=json.loads(K.pack(self.payload()).splitlines()[0]);h['schema_version']='unsupported'
        with self.assertRaises(ValueError):K.unpack((F.dumps(h)+'\n').encode())
    def test_corrupt_record_hash_rejected(self):
        p=K.pack(self.payload());a,b=p.split(b'\n',1)
        with self.assertRaises(ValueError):K.unpack(a+b'\n'+b.replace(b'KRW-X',b'KRW-Q'))
    def test_corrupt_header_rejected(self):
        p=K.pack(self.payload());h=json.loads(p.splitlines()[0]);h['strings'].append('tamper')
        with self.assertRaises(ValueError):K.unpack((F.dumps(h)+'\n').encode()+p.split(b'\n',1)[1])
    def test_duplicate_keys_rejected(self):
        p=K.pack(self.payload());p=p.replace(b'{',b'{"codec":"x",',1)
        with self.assertRaises(ValueError):K.unpack(p)
    def test_missing_newline_rejected(self):
        with self.assertRaises(ValueError):K.unpack(K.pack(self.payload()).rstrip(b'\n'))
    def test_encoded_reference_cycle_rejected(self):
        p=K.pack(self.payload());h=json.loads(p.splitlines()[0]);h['shared_values']=[[-3,0]];h['header_sha256']=F.digest({k:v for k,v in h.items() if k!='header_sha256'})
        with self.assertRaises(ValueError):K.unpack((F.dumps(h)+'\n').encode()+p.split(b'\n',1)[1])
    def test_storage_report_all_overhead_included(self):
        r=K.storage_report(self.payload(),self.entries);self.assertTrue(r['logical_roundtrip_bytes_equal']);self.assertEqual(r['production_files_created'],0)
        for s in r['normal_cycle_sizing_scenarios']:self.assertGreater(s['compact_bytes'],s['dictionary_header_bytes'])
    def test_forecast_not_claimed_actual(self):
        r=K.storage_report(self.payload(),self.entries);self.assertFalse(r['assumptions']['future_event_rate_measured']);self.assertTrue(r['assumptions']['normal_projections_are_not_signals'])
    def test_gate_false_unchanged(self):
        path=Path(__file__).resolve().parents[2]/'.github/workflows/upbit-b-history.yml';s=path.read_text();self.assertIn('active=true',s);self.assertIn('--record --compact',s)
    def test_compact_record_gate_before_api(self):
        with patch.dict('os.environ',{},clear=True),patch.object(R,'collect',side_effect=AssertionError('API')):
            R.main(['--record','--compact'])
    def test_heartbeat_unchanged(self):
        boundary=(V.CUTOFF//(4*C.HOUR)+1)*4*C.HOUR
        first=V.cycle({'KRW-X':V.entry(boundary=boundary-C.HOUR)},boundary-C.HOUR,contract=K.versions())
        previous=H.advance(H.empty_previous(),K.pack(first))
        next_entry={'KRW-X':V.entry(boundary=boundary)}
        logical=V.cycle(next_entry,boundary,previous,K.versions());m,rs=H.validate_cycle(K.pack(logical))
        self.assertEqual(rs[0]['record_kind'],'HEARTBEAT');self.assertEqual(rs[0]['events'],[]);self.assertEqual(K.unpack(K.pack(logical)),logical)
    def test_controls_max12_all_evidence_preserved(self):
        es={'KRW-X'+str(i):V.entry('KRW-X'+str(i),state='NO_UPTREND') for i in range(20)}
        p=V.cycle(es,contract=K.versions());m,rs=H.validate_cycle(K.pack(p))
        self.assertEqual(len(m['selected_controls']),12);self.assertEqual(K.unpack(K.pack(p)),p)
    def test_human_readable_view(self):
        lines=K.pack(self.payload()).splitlines();r=json.loads(lines[1]);self.assertEqual(r['instrument'],'KRW-X');self.assertEqual(r['candidate'],'TRUE');self.assertEqual(r['state'],'TREND_CONTINUATION')
    def test_view_mismatch_rejected_even_resealed(self):
        p=K.pack(self.payload());lines=p.splitlines();r=json.loads(lines[1]);r['candidate']='FALSE';lines[1]=F.dumps(r).encode()
        h=json.loads(lines[0]);h['encoded_records_sha256']=hashlib.sha256(b''.join(x+b'\n' for x in lines[1:])).hexdigest();h['header_sha256']=F.digest({k:v for k,v in h.items() if k!='header_sha256'});lines[0]=F.dumps(h).encode()
        with self.assertRaises(ValueError):K.unpack(b''.join(x+b'\n' for x in lines))
    def test_dictionary_scope_independent(self):
        first=K.pack(self.payload());second=K.pack(V.cycle({'KRW-Q':V.entry('KRW-Q')},contract=K.versions()))
        self.assertEqual(K.unpack(first),self.payload());self.assertEqual(H.validate_cycle(second)[1][0]['instrument'],'KRW-Q')
    def test_old_same_hour_never_overwritten(self):
        with TemporaryDirectory() as t:
            old=V.cycle(self.entries);H.write_once(t,old,V.END)
            with self.assertRaises(H.Conflict):H.write_once(t,K.pack(self.payload()),V.END)
            self.assertEqual((Path(t)/C.cycle_path(V.CUTOFF)).read_bytes(),old)
    def test_decimal_forecast_with_three_plus_candidates(self):
        es={'KRW-X'+str(i):V.entry('KRW-X'+str(i)) for i in range(4)}
        r=K.storage_report(V.cycle(es,contract=K.versions()),es)
        self.assertEqual(r['assumptions']['details_per_day'],'60');F.dumps(r)


if __name__=='__main__':unittest.main()

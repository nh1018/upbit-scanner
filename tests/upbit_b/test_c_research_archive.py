"""V1.2 storage tests; every performance case is an explicit synthetic fixture."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from upbit_b.feature_contracts import digest, dumps
from upbit_c import research_archive as A, research_segments as S
from upbit_c import research_history as H, research_outcomes as O
from test_c_market_research import signal, path, evidence


def envelope(record):
    return (dumps({'record':record, 'record_sha256':digest(record)})+'\n').encode()


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = A.Archive(self.root/'cold', allow_fixtures=True)
        self.sig = signal()
        self.sig['test_fixture'] = True
        self.name = 'signals/' + self.sig['signal_id'] + '.json'
        self.records = {self.name:envelope(self.sig)}
        self.m = self.store.commit('root', self.records)

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self, run, records, parent=None):
        prior = parent or self.m
        return self.store.commit(run, records, (prior['run_id'], prior['sha256']))

    def outcome(self, days=1, pending=False, failed=False):
        rows = path(self.sig, days)
        end = rows[-1].close_ms
        result = O.evaluate(self.sig, days, [] if failed else rows,
                            evidence(rows,end), self.sig['observed_at_ms'] if pending else end)
        result['test_fixture'] = True
        key = digest({'signal_id':self.sig['signal_id'],'days':days,'contract':O.SCHEMA}) if result['status']=='MATURED' else digest(result)
        return 'evaluations/'+key+'.json', envelope(result)

    def test_original_bytes_and_restore_no_overwrite(self):
        self.assertEqual(self.store.audit(self.m)['status'], 'VERIFIED')
        dest = self.root/'restore'
        self.store.restore(self.m, dest)
        self.assertEqual((dest/self.name).read_bytes(), self.records[self.name])
        self.store.restore(self.m, dest)
        (dest/self.name).write_bytes(b'bad')
        with self.assertRaises(ValueError): self.store.restore(self.m, dest)
        self.assertEqual((dest/self.name).read_bytes(), b'bad')

    def test_duplicate_delta_deduplicated(self):
        before = list((self.store.root/'objects').glob('*'))
        m = self.commit('second', self.records)
        self.assertEqual(m['delta_files'], [])
        self.assertEqual(m['inventory'], self.m['inventory'])
        self.assertEqual(before, list((self.store.root/'objects').glob('*')))

    def test_identical_run_replay_and_conflict(self):
        self.assertEqual(self.store.commit('root', self.records), self.m)
        new = copy.deepcopy(self.sig); new['score']['unexpected']='changed'
        with self.assertRaises(ValueError): self.commit('overwrite', {self.name:envelope(new)})
        self.assertFalse((self.store.root/'runs/overwrite.json').exists())

    def test_no_silent_root_or_missing_parent(self):
        with self.assertRaises(ValueError): self.store.commit('fresh', {})
        with self.assertRaises(FileNotFoundError): self.store.commit('child', {}, ('missing','a'*64))

    def test_manifest_corruption_and_wrong_pin(self):
        with self.assertRaises(ValueError): self.store.manifest('root','a'*64)
        p = self.store.root/'runs/root.json'
        v=json.loads(p.read_bytes());v['inventory']={};p.write_text(json.dumps(v))
        with self.assertRaises(ValueError): self.store.manifest('root',self.m['sha256'])

    def test_missing_cold_object_blocks_audit_restore_commit(self):
        next((self.store.root/'objects').glob('*')).unlink()
        with self.assertRaises(FileNotFoundError): self.store.audit(self.m)
        with self.assertRaises(FileNotFoundError): self.store.restore(self.m,self.root/'dest')
        with self.assertRaises(ValueError): self.commit('next', {})
        self.assertFalse((self.root/'dest').exists())

    def test_corrupt_object_blocks_publish(self):
        next((self.store.root/'objects').glob('*')).write_bytes(b'corrupt')
        with self.assertRaises(ValueError): self.store.restore(self.m,self.root/'dest')
        self.assertFalse((self.root/'dest').exists())

    def test_active_missing_signal_detected(self):
        value=self.store.active(self.m);value['signals']={};value=A.seal({k:v for k,v in value.items() if k!='sha256'})
        fake=copy.deepcopy(self.m);fake['active_sha256']=value['sha256']
        fake=A.seal({k:v for k,v in fake.items() if k!='sha256'})
        A.publish(self.store.root/'active'/(value['sha256']+'.json'),(dumps(value)+'\n').encode())
        with self.assertRaises(ValueError): self.store.active(fake)

    def test_active_corruption_recovery_does_not_overwrite(self):
        p=self.store.root/'active'/(self.m['active_sha256']+'.json')
        raw=p.read_bytes();p.write_bytes(b'bad')
        with self.assertRaises(ValueError): self.store.active(self.m)
        rebuilt=self.store.rebuild_active(self.m)
        self.assertEqual((dumps(rebuilt)+'\n').encode(),raw)
        self.assertEqual(p.read_bytes(),b'bad')

    def test_active_preparation_without_cold_reads(self):
        active=self.store.active(self.m)
        with patch.object(self.store,'read_record',side_effect=AssertionError('cold read')):
            work=A.pending_work(active,self.sig['observed_at_ms'])
            self.assertEqual(len(work),3)
            self.assertTrue(all(not x['due'] for x in work))
            client=Mock()
            records=A.evaluate_active(active,client,self.sig['observed_at_ms'])
            client.get.assert_not_called()
            self.assertEqual(len(records),3)
            self.assertTrue(all(json.loads(raw)['record']['status']=='PENDING' for raw in records.values()))

    def test_all_horizons_matured_never_recalculated(self):
        records=dict(self.outcome(d) for d in (1,3,7))
        m=self.commit('matured',records)
        active=self.store.active(m)
        self.assertEqual(A.pending_work(active,10**15),[])
        with patch('upbit_c.research_outcomes.fetch_outcome',side_effect=AssertionError('matured recalculated')):
            self.assertEqual(A.evaluate_active(active,Mock(),10**15),{})
        self.assertEqual(self.store.audit(m)['records'],4)

    def test_pending_failed_recovery_and_prior_bytes_preserved(self):
        a=self.commit('pending',dict([self.outcome(pending=True)]))
        b=self.commit('failure',dict([self.outcome(failed=True)]),a)
        self.assertEqual(self.store.active(b)['signals'][self.sig['signal_id']]['outcomes']['1']['status'],'UNVERIFIABLE')
        c=self.commit('recovered',dict([self.outcome()]),b)
        self.assertEqual(self.store.active(c)['signals'][self.sig['signal_id']]['outcomes']['1']['status'],'MATURED')
        for name,ref in a['inventory'].items():
            self.assertEqual(c['inventory'][name],ref)
        self.assertEqual(self.store.audit(c)['status'],'VERIFIED')

    def test_matured_conflict_rejected(self):
        name,raw=self.outcome();m=self.commit('matured',{name:raw})
        result=json.loads(raw)['record'];result['return_pct']='999'
        with self.assertRaises(ValueError): self.commit('bad',{'evaluations/'+digest(result)+'.json':envelope(result)},m)

    def test_incremental_update_reads_only_new_evaluation(self):
        m=self.commit('pending',dict([self.outcome(pending=True)]))
        names=[];original=self.store.read_record
        def traced(name,ref): names.append(name);return original(name,ref)
        newname,newraw=self.outcome()
        with patch.object(self.store,'read_record',side_effect=traced):
            self.commit('matured',{newname:newraw},m)
        self.assertEqual(names,[newname])

    def test_checkpoint_independent_from_ancestor_manifest(self):
        m=self.commit('checkpoint',{})
        (self.store.root/'runs/root.json').unlink()
        self.assertEqual(self.store.audit(self.store.manifest('checkpoint',m['sha256']))['status'],'VERIFIED')

    def test_checkpoint_complete_independent_copy_and_partial_retry(self):
        m=self.commit('child',dict([self.outcome()]))
        dest=self.root/'backup'
        self.store.checkpoint(m,dest)
        self.store.checkpoint(m,dest)
        backed=A.Archive(dest,True)
        self.assertEqual(backed.audit(backed.manifest('child',m['sha256']))['records'],2)
        self.assertFalse((dest/'runs/root.json').exists())

    def test_recovery_checkpoint_keeps_corrupt_original_untouched(self):
        p=self.store.root/'active'/(self.m['active_sha256']+'.json')
        p.write_bytes(b'corrupted original')
        with self.assertRaises(ValueError): self.store.checkpoint(self.m,self.root/'normal-copy')
        result=self.store.checkpoint(self.m,self.root/'recovered-copy',recover_active=True)
        self.assertEqual(result['status'],'VERIFIED')
        self.assertEqual(p.read_bytes(),b'corrupted original')

    def test_corrupt_descriptor_and_missing_descriptor_detected(self):
        p=next((self.store.root/'descriptors').glob('*.json'))
        p.write_bytes(b'corrupt')
        with self.assertRaises(ValueError): self.store.audit(self.m)
        p.unlink()
        with self.assertRaises(FileNotFoundError): self.store.audit(self.m)

    def test_repeated_signal_preserves_first_observation(self):
        new=copy.deepcopy(self.sig);new['observed_at_ms']+=H.HOUR
        new['proxy_anchor_open_ms']+=H.HOUR
        m=self.commit('reobserve',{self.name:envelope(new)})
        self.assertEqual(m['delta_files'],[])
        self.assertEqual(self.store.active(m)['signals'][self.sig['signal_id']]['signal'],self.sig)

    def test_out_of_order_pending_does_not_replace_later_failure(self):
        failure=dict([self.outcome(failed=True)])
        m=self.commit('failure',failure)
        newer=self.commit('older-pending',dict([self.outcome(pending=True)]),m)
        self.assertEqual(self.store.active(newer)['signals'][self.sig['signal_id']]['outcomes']['1']['status'],'UNVERIFIABLE')
        self.assertEqual(self.store.audit(newer)['status'],'VERIFIED')

    def test_unchanged_original_input_after_full_lifecycle(self):
        original=self.records[self.name]
        m=self.commit('mature',dict([self.outcome()]))
        self.store.restore(m,self.root/'immutable')
        self.assertEqual((self.root/'immutable'/self.name).read_bytes(),original)

    def test_interruption_orphan_objects_retry(self):
        name,raw=self.outcome()
        real=A.publish
        def broken(p,b):
            if p.parent.name=='runs' and p.name=='interrupted.json':raise OSError('interrupted')
            return real(p,b)
        with patch.object(A,'publish',side_effect=broken):
            with self.assertRaises(OSError): self.commit('interrupted',{name:raw})
        self.assertFalse((self.store.root/'runs/interrupted.json').exists())
        m=self.commit('interrupted',{name:raw})
        self.assertEqual(self.store.audit(m)['records'],2)

    def test_v11_explicit_lossless_import(self):
        state=self.root/'v11';H.write_once(state,'signals',self.sig['signal_id'],self.sig)
        legacy=S.export_segment(state,self.root/'segment','123',allow_fixtures=True)
        other=A.Archive(self.root/'imported',True)
        m=A.import_v11(other,'import-123',state,legacy)
        self.assertEqual(other.read_record(self.name,m['inventory'][self.name]),(state/self.name).read_bytes())
        self.assertEqual(m['migration']['manifest_sha256'],legacy['manifest_sha256'])
        self.assertEqual(other.audit(m)['signals'],1)

    def test_v11_missing_record_fails(self):
        state=self.root/'v11';H.write_once(state,'signals',self.sig['signal_id'],self.sig)
        legacy=S.export_segment(state,self.root/'segment','123',allow_fixtures=True)
        next((state/'signals').glob('*')).unlink()
        with self.assertRaises(ValueError): A.import_v11(A.Archive(self.root/'missing',True),'import',state,legacy)

    def test_fixture_default_rejected(self):
        with self.assertRaises(ValueError): A.Archive(self.root/'real').commit('bad',self.records)

    def test_protected_namespaces(self):
        for name in ('data_market','output_upbit_b','btc_anytime','upbit_b','.github','upbit_c'):
            with self.assertRaises(ValueError): A.Archive(self.root/name)

    def test_path_traversal_and_bad_ids(self):
        with self.assertRaises(ValueError): self.commit('../bad',{})
        with self.assertRaises(ValueError): self.commit('bad',{'../signal.json':b'{}'})
        with self.assertRaises(ValueError): self.store.object_path('../bad')

    def test_outcome_contract_proxy_and_no_fabricated_returns(self):
        name,raw=self.outcome();out=json.loads(raw)['record']
        self.assertEqual(out['anchor_type'],'NEXT_1H_OPEN_PROXY')
        self.assertEqual(out['return_pct'],'10.0')
        self.assertEqual(out['mfe_pct'],'20.0');self.assertEqual(out['mae_pct'],'-20.0')
        self.assertFalse(out['fees_included']);self.assertFalse(out['execution_price_claim'])

    def test_matured_priority_and_limit(self):
        active=self.store.active(self.m)
        work=A.pending_work(active,self.sig['proxy_anchor_open_ms']+86400000)
        self.assertTrue(work[0]['due']);self.assertEqual(work[0]['days'],1)
        with self.assertRaises(ValueError): A.evaluate_active(active,Mock(),0,0)

    def test_remote_expired_artifact_rejected_without_fresh_root(self):
        from test_c_research_segments import zip_dir
        from datetime import datetime,timezone,timedelta
        state=self.root/'v11';H.write_once(state,'signals',self.sig['signal_id'],self.sig)
        dest=self.root/'segment';S.export_segment(state,dest,'123',allow_fixtures=True)
        raw=zip_dir(dest)
        metadata={'workflow_path':S.WORKFLOW,'conclusion':'success','expired':True,
                  'expires_at':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),
                  'archive_sha256':S.sha(raw)}
        other=A.Archive(self.root/'expired',True)
        with self.assertRaises(ValueError): A.import_remote_v11(other,'import','123',lambda key:(raw,metadata))
        self.assertFalse(other.root.exists())

    def test_remote_migration_preserves_chain_manifests(self):
        from test_c_research_segments import zip_dir
        from datetime import datetime,timezone,timedelta
        state=self.root/'v11';H.write_once(state,'signals',self.sig['signal_id'],self.sig)
        d1=self.root/'seg1';m1=S.export_segment(state,d1,'123',allow_fixtures=True)
        d2=self.root/'seg2';S.export_segment(state,d2,'124',m1,allow_fixtures=True)
        archives={key:zip_dir(dest) for key,dest in [('123',d1),('124',d2)]}
        def loader(key):
            raw=archives[key]
            return raw,{'workflow_path':S.WORKFLOW,'conclusion':'success','expired':False,
                'expires_at':(datetime.now(timezone.utc)+timedelta(days=90)).isoformat(),
                'archive_sha256':S.sha(raw)}
        other=A.Archive(self.root/'remote-import',True)
        m,report=A.import_remote_v11(other,'import','124',loader)
        self.assertEqual(report['verified_segments'],2)
        self.assertEqual(len(m['migration']['chain_manifests']),2)
        self.assertEqual(other.audit(m)['status'],'VERIFIED')
        self.assertEqual((other.root/'imports'/ (S.sha((d1/'manifest.json').read_bytes())+'.json')).read_bytes(),(d1/'manifest.json').read_bytes())

    def test_import_manifest_corruption_detected(self):
        state=self.root/'v11';H.write_once(state,'signals',self.sig['signal_id'],self.sig)
        segment=self.root/'segment';legacy=S.export_segment(state,segment,'123',allow_fixtures=True)
        other=A.Archive(self.root/'imported',True)
        m=A.import_v11(other,'import',state,legacy,(segment/'manifest.json').read_bytes())
        next((other.root/'imports').glob('*')).write_bytes(b'changed')
        with self.assertRaises(ValueError): other.audit(m)

    def test_missing_outcome_reference_rejected(self):
        m=self.commit('matured',dict([self.outcome()]))
        active=self.store.active(m)
        active['signals'][self.sig['signal_id']]['outcomes']['1']['reference']='evaluations/'+ '0'*64+'.json'
        active=A.seal({k:v for k,v in active.items() if k!='sha256'})
        A.publish(self.store.root/'active'/(active['sha256']+'.json'),(dumps(active)+'\n').encode())
        m=A.seal({**{k:v for k,v in m.items() if k!='sha256'},'active_sha256':active['sha256']})
        with self.assertRaises(ValueError): self.store.active(m)

    def test_storage_cli_protected_root_and_pinned_audit(self):
        from upbit_c.research_storage import main
        from contextlib import redirect_stdout
        import io
        with self.assertRaises(ValueError): main(['audit','--archive',str(self.root/'data'),'--run','root','--manifest-sha',self.m['sha256']])
        with self.assertRaises(ValueError): main(['audit','--archive',str(self.store.root),'--run','root','--manifest-sha',self.m['sha256']])
        # Production CLI rejects this fixture archive, even with a correct pin.

    def test_future_pending_and_failed_api_recover_via_existing_calculator(self):
        active=self.store.active(self.m)
        client=Mock();client.get.side_effect=OSError('API unavailable')
        records=A.evaluate_active(active,client,self.sig['proxy_anchor_open_ms']+7*86400000)
        self.assertTrue(all(json.loads(v)['record']['status']=='UNVERIFIABLE' for v in records.values()))
        self.assertEqual(client.get.call_count,3)

    def test_wrong_proxy_anchor_rejected(self):
        name,raw=self.outcome()
        result=json.loads(raw)['record'];result['anchor_open_ms']+=H.HOUR
        with self.assertRaises(ValueError): self.commit('wrong-anchor',{name:envelope(result)})


if __name__=='__main__': unittest.main()

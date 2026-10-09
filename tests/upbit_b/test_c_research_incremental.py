"""V1.6 isolated synthetic preservation cases; never production signals."""
import copy
import io
import json
import unittest
import zipfile
from unittest.mock import patch
from upbit_b.feature_contracts import digest
from upbit_c import research_incremental as I, research_incremental_release as X
import test_c_research_release as fixtures


class IncrementalTests(unittest.TestCase):
    setUp=fixtures.ReleaseTests.setUp
    tearDown=fixtures.ReleaseTests.tearDown

    def setup_chain(self):
        self.catalog=I.initial_catalog('o/r',1,1,self.sha)
        self.payload={self.sha:self.raw}
        self.fetch=lambda x:self.payload[x['sha256']]
        self.current=self.manifest

    def child(self,run='124',add=True):
        records={}
        if add:
            report={'activation':'RESEARCH_ONLY','test_fixture':True,'results':[],
                'started_at_ms':int(run)*1000,'finished_at_ms':int(run)*1000+1,
                'universe_size':0,'selected_count':0,'success_count':0,'failure_count':0,
                'failure_reasons':{},'logical_api_requests':0,'api_blocked':False}
            report['scan_id']=digest(report)
            records['scans/'+report['scan_id']+'.json']=fixtures.envelope(report)
        self.current=self.store.commit(run,records,(self.current['run_id'],self.current['sha256']))
        return self.current

    def prepare(self,manifest=None,dest=None):
        return I.prepare(self.store,manifest or self.current,self.catalog,self.fetch,
                         '2026-10-09T00:00:00Z',(manifest or self.current)['run_id'],dest or self.root/'delta',True)

    def append(self):
        result=self.prepare();p=result['package']
        self.payload[p['sha256']]=(self.root/'delta'/p['asset_name']).read_bytes()
        self.catalog=result['catalog'];return result

    def test_new_original_and_existing_reference(self):
        self.setup_chain();self.child();r=self.append()
        self.assertEqual(r['new_original_count'],1);self.assertEqual(r['referenced_original_count'],1)
        m,files=I.unpack(self.payload[r['package']['sha256']],r['package']['sha256'],True)
        self.assertEqual(m['referenced_originals'][self.name]['owner_zip_sha256'],self.sha)
        with I.verified_chain(I.initial_catalog('o/r',1,1,self.sha),self.fetch,True) as base:
            self.assertFalse(set(files)&set(base.files))
        restored=I.restore(self.catalog,self.fetch,self.root/'restore',True)
        self.assertEqual(restored['records'],2)

    def test_several_increment_full_restore_exact_originals(self):
        self.setup_chain()
        for run in ('124','125','126'):self.child(run);self.append()
        r=I.restore(self.catalog,self.fetch,self.root/'restore',True)
        self.assertEqual(r['increments'],3);self.assertEqual(r['records'],4)
        target=I.A.Archive(self.root/'restore',True)
        for name,ref in self.current['inventory'].items():
            self.assertEqual(target.read_record(name,ref),self.store.read_record(name,ref))
        self.assertEqual(target.active(self.current),self.store.active(self.current))

    def test_same_checkpoint_no_package(self):
        self.setup_chain();r=self.prepare()
        self.assertEqual(r['status'],'NOOP');self.assertEqual(r['package_bytes'],0)
        self.assertFalse((self.root/'delta').exists())

    def test_metadata_only_checkpoint_not_raw_duplicate(self):
        self.setup_chain();self.child(add=False);r=self.append()
        self.assertEqual(r['new_original_count'],0);self.assertEqual(r['package']['kind'],'METADATA_ONLY')
        self.assertFalse(r['active_change']['hash_changed'])
        self.assertEqual(I.restore(self.catalog,self.fetch,self.root/'restore',True)['records'],1)

    def test_active_metadata_change_without_new_original(self):
        self.setup_chain();self.child(add=False)
        body=copy.deepcopy(self.current);body['inventory'][self.name]['verified_annotation']='fixture'
        body['inventory_sha256']=digest(body['inventory'])
        active=self.store.build_active(body['inventory'])
        I.A.publish(self.store.root/'active'/(active['sha256']+'.json'),I.encoded(active))
        body['active_sha256']=active['sha256'];body.pop('sha256');body['run_id']='125'
        self.current=I.A.seal(body)
        I.A.publish(self.store.root/'runs/125.json',I.encoded(self.current))
        r=self.append();self.assertTrue(r['active_change']['hash_changed'])
        self.assertFalse(r['active_change']['signals_changed'])

    def test_same_raw_identity_is_reference(self):
        self.setup_chain();self.child(add=False)
        self.assertEqual(I.inventory_delta(self.manifest['inventory'],self.current['inventory']),[])

    def test_same_id_different_hash_conflict(self):
        changed=copy.deepcopy(self.manifest['inventory']);changed[self.name]['sha256']='a'*64
        with self.assertRaises(ValueError):I.inventory_delta(self.manifest['inventory'],changed)

    def test_original_missing_fail(self):
        with self.assertRaises(ValueError):I.inventory_delta(self.manifest['inventory'],{})

    def test_missing_base(self):
        self.setup_chain();self.payload.clear()
        with self.assertRaises(KeyError):I.restore(self.catalog,self.fetch,self.root/'bad',True)
        self.assertFalse((self.root/'bad').exists())

    def test_missing_middle(self):
        self.setup_chain();self.child();self.append();lost=self.catalog['increments'][0]['sha256']
        self.child('125');self.append();del self.payload[lost]
        with self.assertRaises(KeyError):I.restore(self.catalog,self.fetch,self.root/'bad',True)
        self.assertFalse((self.root/'bad').exists())

    def test_corrupt_zip_hash(self):
        self.setup_chain();self.child();self.append();sha=self.catalog['increments'][0]['sha256']
        self.payload[sha]+=b'bad'
        with self.assertRaises(ValueError):I.restore(self.catalog,self.fetch,self.root/'bad',True)
        self.assertFalse((self.root/'bad').exists())

    def test_reordered_catalog(self):
        self.setup_chain();self.child();self.append();self.child('125');self.append()
        body=copy.deepcopy(self.catalog);body.pop('sha256');body['increments'].reverse()
        with self.assertRaises(ValueError):I.check_catalog(I.A.seal(body))

    def test_duplicate_catalog(self):
        self.setup_chain();self.child();self.append()
        body=copy.deepcopy(self.catalog);body.pop('sha256');entry=copy.deepcopy(body['increments'][0]);entry['sequence']=2;body['increments'].append(entry)
        with self.assertRaises(ValueError):I.check_catalog(I.A.seal(body))

    def test_deterministic_retry(self):
        self.setup_chain();self.child();first=self.prepare();second=self.prepare()
        self.assertEqual(first,second)

    def test_interrupted_restore_retry(self):
        self.setup_chain();self.child();self.append();original=I.A.publish;writes=[]
        def interrupt(path,raw):
            if str(self.root/'restore') in str(path):
                writes.append(str(path))
                if len(writes)==3:raise OSError('synthetic interruption')
            return original(path,raw)
        with patch.object(I.A,'publish',side_effect=interrupt),self.assertRaises(OSError):
            I.restore(self.catalog,self.fetch,self.root/'restore',True)
        self.assertEqual(I.restore(self.catalog,self.fetch,self.root/'restore',True)['status'],'VERIFIED')

    def test_restore_conflict_preflight(self):
        self.setup_chain();self.child();self.append();dest=self.root/'restore'
        (dest/'runs').mkdir(parents=True);(dest/'runs/123.json').write_bytes(b'wrong')
        before=list(dest.rglob('*'))
        with self.assertRaises(ValueError):I.restore(self.catalog,self.fetch,dest,True)
        self.assertEqual(before,list(dest.rglob('*')))

    def test_skip_source_checkpoint_has_ancestry_proof(self):
        self.setup_chain();mid=self.child();self.child('125');r=self.append()
        m,_=I.unpack(self.payload[r['package']['sha256']],r['package']['sha256'],True)
        self.assertEqual(m['lineage_proofs'],[{'run_id':'124','sha256':mid['sha256']}])

    def test_missing_source_ancestry_fails(self):
        self.setup_chain();self.child();self.child('125');(self.store.root/'runs/124.json').unlink()
        with self.assertRaises(FileNotFoundError):self.prepare()

    def test_lineage_mismatch_fails(self):
        self.setup_chain();body=copy.deepcopy(self.child());body.pop('sha256');body['lineage_id']='other'
        with self.assertRaises(ValueError):I.ancestry(self.store,I.A.seal(body),self.manifest)

    def test_cache_hash_always_verified(self):
        self.setup_chain();cache=I.PackageCache(self.fetch,self.root/'cache')
        cache(self.catalog['base']);cache(self.catalog['base'])
        self.assertEqual(cache.metrics['fetches'],1);self.assertEqual(cache.metrics['cache_hits'],1)
        (self.root/'cache'/(self.sha+'.zip')).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):cache(self.catalog['base'])

    def test_external_catalog_hash(self):
        self.setup_chain();raw=I.encoded(self.catalog)
        self.assertEqual(I.read_catalog(raw,I.R.S.sha(raw)),self.catalog)
        with self.assertRaises(ValueError):I.read_catalog(raw,'0'*64)

    def test_fixture_not_production(self):
        self.setup_chain();self.child();r=self.append()
        with self.assertRaises(ValueError):I.unpack(self.payload[r['package']['sha256']],r['package']['sha256'])

    def test_clock_explicit_utc(self):
        for clock in ('2026-10-09T00:00:00','2026-10-09T09:00:00+09:00'):
            with self.assertRaises(ValueError):I.utc(clock)

    def test_v15_reader_rejects_delta(self):
        self.setup_chain();self.child();r=self.append()
        with self.assertRaises((ValueError,KeyError)):
            I.R.verify(self.payload[r['package']['sha256']],r['package']['sha256'],True)

    def rewrite_delta(self,change):
        self.setup_chain();self.child();r=self.prepare()
        raw=(self.root/'delta'/r['package']['asset_name']).read_bytes()
        manifest,files=I.unpack(raw,r['package']['sha256'],True)
        manifest.pop('sha256');change(manifest,files);raw=I.pack(I.A.seal(manifest),files)
        return raw,I.R.S.sha(raw)

    def bad_delta(self,change):
        raw,sha=self.rewrite_delta(change)
        with I.verified_chain(self.catalog,self.fetch,True) as chain,self.assertRaises((ValueError,KeyError)):
            chain.apply(raw,sha)

    def test_wrong_parent_rejected(self):
        self.bad_delta(lambda m,f:m.update(parent_zip_sha256='0'*64))

    def test_wrong_sequence_rejected(self):
        self.bad_delta(lambda m,f:m.update(sequence=3))

    def test_reference_owner_conflict(self):
        self.bad_delta(lambda m,f:m['referenced_originals'][self.name].update(owner_zip_sha256='0'*64))

    def test_reference_missing_rejected(self):
        self.bad_delta(lambda m,f:m.update(referenced_originals={}))

    def test_fabricated_active_change_rejected(self):
        self.bad_delta(lambda m,f:m['active_change'].update(signals_changed=True))

    def test_payload_size_mismatch_rejected(self):
        def change(m,f):m['new_files'][next(iter(f))]['bytes']+=1
        self.bad_delta(change)

    def test_path_traversal_rejected(self):
        def change(m,f):
            f['../escape']=b'bad';m['new_files']['../escape']=I.R.ref(b'bad')
        self.bad_delta(change)

    def test_extra_payload_rejected(self):
        self.bad_delta(lambda m,f:f.update({'runs/999.json':b'bad'}))

    def test_wrong_source_actions_id_rejected(self):
        self.bad_delta(lambda m,f:m.update(source_actions_run_id='../../token'))

    def test_numeric_source_actions_checkpoint_mismatch(self):
        self.bad_delta(lambda m,f:m.update(source_actions_run_id='999'))

    def test_v15_release_namespace_not_intercepted(self):
        self.assertFalse(I.PREFIX.startswith(I.P.PREFIX))

    def test_catalog_descriptor_tampering_rejected(self):
        self.setup_chain();self.child();self.append()
        body=copy.deepcopy(self.catalog);body.pop('sha256');body['increments'][0]['kind']='METADATA_ONLY'
        with self.assertRaises(ValueError):I.restore(I.A.seal(body),self.fetch,self.root/'bad',True)

    def test_prepared_output_conflict_never_overwrites(self):
        self.setup_chain();self.child();first=self.prepare();path=self.root/'delta'/first['package']['asset_name']
        path.write_bytes(b'bad')
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(path.read_bytes(),b'bad')

    def test_cached_bytes_do_not_bypass_inner_manifest(self):
        self.setup_chain();bad=self.raw.replace(b'release-manifest.json',b'broken--manifest.json')
        sha=I.R.S.sha(bad);catalog=I.initial_catalog('o/r',1,1,sha)
        cache=I.PackageCache(lambda e:bad,self.root/'cache');cache(catalog['base'])
        with self.assertRaises((ValueError,KeyError,zipfile.BadZipFile)):I.restore(catalog,cache,self.root/'bad',True)

    def test_original_source_files_unchanged(self):
        self.setup_chain();self.child()
        before={str(p):p.read_bytes() for p in self.store.root.rglob('*') if p.is_file()}
        self.append();I.restore(self.catalog,self.fetch,self.root/'restore',True)
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.store.root.rglob('*') if p.is_file()})


if __name__=='__main__':unittest.main()

"""V1.4 Release transport tests. Every signal is explicitly synthetic."""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from upbit_b.feature_contracts import dumps, digest
from upbit_c import research_archive as A, research_release as R
from test_c_research_archive import envelope
from test_c_market_research import signal, path, evidence


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = A.Archive(self.root/'cold', True)
        sig = signal(); sig['test_fixture'] = True
        self.name = 'signals/' + sig['signal_id'] + '.json'
        self.original = envelope(sig)
        self.manifest = self.store.commit('123', {self.name:self.original})
        self.prepared = R.prepare(self.store, self.manifest, self.root/'packages')
        self.package = self.prepared['package']
        self.raw = (self.root/'packages'/self.package['asset_name']).read_bytes()
        self.sha = self.package['sha256']

    def tearDown(self):
        self.tmp.cleanup()

    def rewrite(self, change):
        with zipfile.ZipFile(io.BytesIO(self.raw)) as z:
            files = {i.filename:z.read(i) for i in z.infolist()}
        change(files)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_STORED) as z:
            for name, raw in files.items():z.writestr(name, raw)
        return buf.getvalue()

    def check_bad(self, raw):
        with self.assertRaises((ValueError, KeyError, zipfile.BadZipFile)):
            R.restore(raw, R.S.sha(raw), self.root/'bad-restore', True)
        self.assertFalse((self.root/'bad-restore').exists())

    def test_dry_run_no_files(self):
        before = sorted(str(p) for p in self.root.rglob('*'))
        value = R.prepare(self.store, self.manifest)
        self.assertEqual(value['mode'], 'DRY_RUN')
        self.assertEqual(value['remote_writes'], 0)
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob('*')))

    def test_deterministic_package_retry(self):
        again = R.prepare(self.store, self.manifest, self.root/'packages')
        self.assertEqual(self.prepared, again)
        other = R.prepare(self.store, self.manifest, self.root/'other')
        self.assertEqual(self.package, other['package'])

    def test_zip_origin_explicitly_preserves_original_windows_encoding(self):
        with zipfile.ZipFile(io.BytesIO(self.raw)) as zipped:
            self.assertTrue(all(i.create_system == 0 for i in zipped.infolist()))

    def test_zip_origin_platform_defaults_do_not_change_package_bytes(self):
        original_info = zipfile.ZipInfo
        for host_default in (0, 3):
            class HostZipInfo(original_info):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.create_system = host_default
            with self.subTest(host_default=host_default), patch.object(R.zipfile, 'ZipInfo', HostZipInfo):
                package = R.prepare(self.store, self.manifest,
                                    self.root / ('host-' + str(host_default)))['package']
            self.assertEqual(package, self.package)
            self.assertEqual((self.root / ('host-' + str(host_default)) /
                              package['asset_name']).read_bytes(), self.raw)

    def test_original_active_and_lineage_restore(self):
        report = R.restore(self.raw, self.sha, self.root/'restore', True)
        self.assertEqual(report['status'], 'VERIFIED')
        dest = A.Archive(self.root/'restore', True)
        m = dest.manifest('123', self.manifest['sha256'])
        self.assertEqual(dest.read_record(self.name,m['inventory'][self.name]),self.original)
        self.assertEqual(dest.active(m),self.store.active(self.manifest))
        self.assertEqual(m,self.manifest)

    def test_checkpoint_without_ancestors(self):
        m = self.store.commit('124',{},('123',self.manifest['sha256']))
        p = R.prepare(self.store,m,self.root/'child')
        raw = (self.root/'child'/p['package']['asset_name']).read_bytes()
        R.restore(raw,p['package']['sha256'],self.root/'restored-child',True)
        self.assertFalse((self.root/'restored-child/runs/123.json').exists())
        dst = A.Archive(self.root/'restored-child',True)
        self.assertEqual(dst.audit(dst.manifest('124',m['sha256']))['status'],'VERIFIED')

    def test_nonzero_outcome_active_state_fixture(self):
        from upbit_c import research_outcomes as O
        sig=json.loads(self.original)['record'];rows=path(sig,1);end=rows[-1].close_ms
        result=O.evaluate(sig,1,rows,evidence(rows,end),end);result['test_fixture']=True
        name='evaluations/'+digest({'signal_id':sig['signal_id'],'days':1,'contract':O.SCHEMA})+'.json'
        m=self.store.commit('125',{name:envelope(result)},('123',self.manifest['sha256']))
        p=R.prepare(self.store,m,self.root/'outcome-package');raw=(self.root/'outcome-package'/p['package']['asset_name']).read_bytes()
        R.restore(raw,p['package']['sha256'],self.root/'outcome-restored',True)
        dest=A.Archive(self.root/'outcome-restored',True);dm=dest.manifest('125',m['sha256'])
        self.assertEqual(dest.active(dm),self.store.active(m))
        self.assertEqual(dest.read_record(name,dm['inventory'][name]),envelope(result))

    def test_restore_idempotent(self):
        a=R.restore(self.raw,self.sha,self.root/'restore',True)
        self.assertEqual(a,R.restore(self.raw,self.sha,self.root/'restore',True))

    def test_restore_conflict_no_partial_publication(self):
        target=self.root/'restore'/self.name
        # Conflict on a package checkpoint path, not restored raw namespace.
        path='active/'+self.manifest['active_sha256']+'.json'
        target=self.root/'restore'/path;target.parent.mkdir(parents=True);target.write_bytes(b'bad')
        before=sorted(str(p) for p in (self.root/'restore').rglob('*'))
        with self.assertRaises(ValueError):R.restore(self.raw,self.sha,self.root/'restore',True)
        self.assertEqual(before,sorted(str(p) for p in (self.root/'restore').rglob('*')))
        self.assertEqual(target.read_bytes(),b'bad')

    def test_external_hash_required(self):
        with self.assertRaises(ValueError):R.verify(self.raw,'0'*64,True)

    def test_truncated_download(self):
        with self.assertRaises(ValueError):R.verify(self.raw[:-5],self.sha,True)

    def test_real_namespace_rejects_fixture(self):
        with self.assertRaises(ValueError):R.verify(self.raw,self.sha)

    def test_missing_object(self):
        self.check_bad(self.rewrite(lambda f:f.pop(next(x for x in f if x.startswith('objects/')))))

    def test_corrupt_object(self):
        def change(f):f[next(x for x in f if x.startswith('objects/'))]=b'bad'
        self.check_bad(self.rewrite(change))

    def test_extra_traversal(self):
        self.check_bad(self.rewrite(lambda f:f.update({'../escaped':b'bad'})))

    def test_manifest_seal(self):
        def change(f):
            m=json.loads(f['release-manifest.json']);m['run_id']='456'
            f['release-manifest.json']=dumps(m).encode()
        self.check_bad(self.rewrite(change))

    def test_resealed_wrong_linkage(self):
        def change(f):
            m=json.loads(f['release-manifest.json']);m.pop('sha256');m['lineage_id']='different'
            f['release-manifest.json']=dumps(A.seal(m)).encode()
        self.check_bad(self.rewrite(change))

    def test_duplicate_zip_members(self):
        buf=io.BytesIO(self.raw)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(buf,'a') as z:z.writestr('release-manifest.json',b'{}')
        self.check_bad(buf.getvalue())

    def test_unsupported_compression(self):
        with zipfile.ZipFile(io.BytesIO(self.raw)) as src:
            buf=io.BytesIO()
            with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as dst:
                for i in src.infolist():dst.writestr(i.filename,src.read(i))
        self.check_bad(buf.getvalue())

    def test_size_limit(self):
        with patch.object(R,'MAX_BYTES',10):
            with self.assertRaises(ValueError):R.prepare(self.store,self.manifest)
            with self.assertRaises(ValueError):R.verify(self.raw,self.sha,True)

    def test_entry_limit(self):
        with patch.object(R,'MAX_ENTRIES',1):
            with self.assertRaises(ValueError):R.prepare(self.store,self.manifest)

    def test_protected_destination(self):
        with self.assertRaises(ValueError):R.restore(self.raw,self.sha,self.root/'data',True)

    def test_source_corrupt_prevents_package(self):
        chunk=next((self.store.root/'objects').glob('*'));chunk.write_bytes(b'bad')
        with self.assertRaises(ValueError):R.prepare(self.store,self.manifest,self.root/'new')
        self.assertFalse((self.root/'new').exists())

    def test_duplicate_publication_plan(self):
        asset={'name':self.package['asset_name'],'size':self.package['bytes'],
               'digest':'sha256:'+self.sha,'state':'uploaded','id':12}
        self.assertEqual(R.publication_plan(self.package,[asset])['action'],'ALREADY_PRESENT_REVERIFY_DOWNLOAD')
        self.assertEqual(R.publication_plan(self.package,[])['action'],'APPROVAL_REQUIRED_UPLOAD')

    def test_cross_package_original_overlap(self):
        receipt=self.prepared['receipt']
        report=R.compare_originals(receipt,[receipt])
        self.assertEqual(report['duplicate_originals'],[self.name])
        self.assertTrue(report['same_package_present'])
        self.assertEqual(R.compare_originals(receipt,[])['new_originals'],[self.name])

    def test_cross_package_original_conflict(self):
        prior=json.loads(dumps(self.prepared['receipt']));prior.pop('sha256')
        prior['original_records'][self.name]['sha256']='0'*64
        with self.assertRaises(ValueError):R.compare_originals(self.prepared['receipt'],[A.seal(prior)])

    def test_same_original_different_gzip_descriptor_not_conflict(self):
        prior=json.loads(dumps(self.prepared['receipt']));prior.pop('sha256')
        prior['original_records'][self.name]['descriptor_sha256']='0'*64
        value=R.compare_originals(self.prepared['receipt'],[A.seal(prior)])
        self.assertEqual(value['duplicate_originals'],[self.name])
        self.assertFalse(value['same_package_present'])

    def test_cross_package_wrong_catalog(self):
        prior=json.loads(dumps(self.prepared['receipt']));prior.pop('sha256');prior['schema_version']='wrong'
        with self.assertRaises(ValueError):R.compare_originals(self.prepared['receipt'],[A.seal(prior)])

    def test_publication_conflict_and_partial(self):
        asset={'name':self.package['asset_name'],'size':self.package['bytes'],
               'digest':'sha256:'+self.sha,'state':'uploaded','id':12}
        for field,value in [('size',1),('digest','sha256:'+'0'*64),('state','starter')]:
            bad=dict(asset);bad[field]=value
            with self.assertRaises(ValueError):R.publication_plan(self.package,[bad])
        with self.assertRaises(ValueError):R.publication_plan(self.package,[asset,asset])

    def test_read_only_api_pagination(self):
        reader=R.ReleaseReader('owner/repo')
        reader.get=Mock(side_effect=[[{'id':x} for x in range(100)],[]])
        self.assertEqual(len(reader.assets(1)),100)
        self.assertEqual(reader.get.call_args_list[1].args[0],'releases/1/assets?per_page=100&page=2')

    def test_reader_download_verifies_before_return(self):
        reader=R.ReleaseReader('owner/repo');reader.get=Mock(return_value=b'bad')
        with self.assertRaises(ValueError):reader.download(1,self.sha)
        with self.assertRaises(ValueError):reader.download('../secret',self.sha)

    def test_reader_has_no_mutation_or_credentials_in_repr(self):
        reader=R.ReleaseReader('owner/repo','SECRET')
        self.assertNotIn('SECRET',repr(reader))
        self.assertFalse(any(hasattr(reader,x) for x in ['post','put','delete','upload','create']))

    def test_download_redirect_never_forwards_auth(self):
        reader=R.ReleaseReader('owner/repo','SECRET')
        error=R.urllib.error.HTTPError('https://api.github.com/x',302,'redirect',
            {'Location':'https://release-assets.githubusercontent.com/signed'},None)
        opener=Mock();opener.open.side_effect=error
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'bytes'
        with patch.object(R.urllib.request,'build_opener',return_value=opener),patch.object(R.urllib.request,'urlopen',return_value=response) as redirected:
            self.assertEqual(reader.get('releases/assets/1',True),b'bytes')
        request=opener.open.call_args.args[0]
        self.assertEqual(request.get_method(),'GET')
        self.assertEqual(request.get_header('Authorization'),'Bearer SECRET')
        self.assertEqual(redirected.call_args.args,('https://release-assets.githubusercontent.com/signed',))
        self.assertNotIn('headers',redirected.call_args.kwargs)

    def test_reader_error_no_secret_and_http_redirect_rejected(self):
        reader=R.ReleaseReader('owner/repo','SECRET')
        for code,headers in [(401,{}),(302,{'Location':'http://unsafe'})]:
            opener=Mock();opener.open.side_effect=R.urllib.error.HTTPError('https://api.github.com/x',code,'SECRET',headers,None)
            with patch.object(R.urllib.request,'build_opener',return_value=opener):
                with self.assertRaises(ValueError) as error:reader.get('releases/assets/1',True)
            self.assertNotIn('SECRET',str(error.exception))

    def test_symlink_destination_rejected_before_restore(self):
        with patch.object(Path,'is_symlink',return_value=True):
            with self.assertRaises(ValueError):R.restore(self.raw,self.sha,self.root/'link',True)
        self.assertFalse((self.root/'link').exists())

    def test_no_restore_commit_after_corrupt_active(self):
        def change(files):
            name=next(n for n in files if n.startswith('active/'))
            files[name]=b'{}'
            receipt=json.loads(files['release-manifest.json']);receipt.pop('sha256')
            receipt['files'][name]=R.ref(files[name])
            files['release-manifest.json']=dumps(A.seal(receipt)).encode()
        self.check_bad(self.rewrite(change))

    def test_package_destination_conflict_does_not_replace(self):
        target=self.root/'other'/self.package['asset_name'];target.parent.mkdir();target.write_bytes(b'old')
        with self.assertRaises(ValueError):R.prepare(self.store,self.manifest,target.parent)
        self.assertEqual(target.read_bytes(),b'old')

    def test_cli_default_prepare_is_read_only(self):
        with patch.object(R,'prepare',return_value={'mode':'DRY_RUN'}) as prep,patch.object(R.A,'Archive') as archive,patch('builtins.print'):
            R.main(['prepare','--archive',str(self.root/'cold'),'--run-id','123','--manifest-sha',self.manifest['sha256']])
        self.assertIsNone(prep.call_args.args[2])

    def test_original_files_immutable(self):
        before={p.relative_to(self.store.root).as_posix():p.read_bytes() for p in self.store.root.rglob('*') if p.is_file()}
        R.prepare(self.store,self.manifest,self.root/'new')
        R.restore(self.raw,self.sha,self.root/'restore',True)
        self.assertEqual(before,{p.relative_to(self.store.root).as_posix():p.read_bytes() for p in self.store.root.rglob('*') if p.is_file()})


if __name__=='__main__':unittest.main()

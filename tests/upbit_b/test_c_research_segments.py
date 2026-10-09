"""Explicit synthetic fixtures only: never published as live research cases."""
import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout,redirect_stderr
from upbit_b.feature_contracts import digest
from upbit_c import research_segments as S
from upbit_c import research_history as H
from upbit_c import research_outcomes as O
from test_c_market_research import signal,path,evidence


def zip_dir(root):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(Path(root).rglob('*')):
            if p.is_file():z.writestr(p.relative_to(root).as_posix(),p.read_bytes())
    return b.getvalue()


class SegmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.state=self.root/'state';self.sig=signal();self.sig['test_fixture']=True
        H.write_once(self.state,'signals',self.sig['signal_id'],self.sig)
        self.now=datetime(2026,10,9,tzinfo=timezone.utc)
        self.archives={}
    def tearDown(self):self.tmp.cleanup()
    def export(self,run_id,parent=None,checkpoint=False):
        dest=self.root/('segment-'+str(run_id))
        m=S.export_segment(self.state,dest,str(run_id),parent,checkpoint,True)
        raw=zip_dir(dest)
        self.archives[str(run_id)]=(raw,{'workflow_path':S.WORKFLOW,'conclusion':'success',
            'expired':False,'expires_at':(self.now+timedelta(days=90)).isoformat(),'archive_sha256':S.sha(raw)})
        return m
    def restore(self,run_id,dest=None,**kw):
        return S.restore_chain(str(run_id),lambda i:self.archives[i],dest or self.root/'restored',now=self.now,allow_fixtures=True,**kw)
    def test_fixture_cannot_enter_real_lineage(self):
        with self.assertRaises(ValueError):S.export_segment(self.state,self.root/'bad','10')
    def test_delta_does_not_reupload_prior_bytes(self):
        a=self.export(10);b=self.export(11,a)
        self.assertEqual(b['exported_records'],0)
        self.assertEqual(b['inventory'],a['inventory'])
        self.assertNotEqual(a['manifest_sha256'],b['manifest_sha256'])
        _,v=self.restore(11)
        self.assertEqual(v['verified_segments'],2)
        self.assertEqual(S.inventory(self.root/'restored',True),S.inventory(self.state,True))
    def test_retry_recovery_and_outcomes_after_transfer(self):
        a=self.export(10)
        dest=self.root/'restored';self.restore(10,dest)
        saved=H.read_record(next((dest/'signals').glob('*.json')))
        before=next((dest/'signals').glob('*.json')).read_bytes()
        from unittest.mock import Mock
        from upbit_b.market_data import DataError
        client=Mock();client.get.side_effect=DataError('API retries exhausted')
        end=path(saved)[-1].close_ms
        failed=O.fetch_outcome(client,saved,1,end)
        self.assertEqual(failed['status'],'UNVERIFIABLE')
        failed['test_fixture']=True
        H.write_once(dest,'evaluations',digest(failed),failed)
        for days in (1,3,7):
            rows=path(saved,days);asof=rows[-1].close_ms
            mature=O.evaluate(saved,days,rows,evidence(rows,asof),asof)
            mature['test_fixture']=True
            self.assertEqual(mature['status'],'MATURED')
            self.assertEqual(mature['return_pct'],'10.0')
            self.assertEqual(mature['mfe_pct'],'20.0')
            self.assertEqual(mature['mae_pct'],'-20.0')
            key=digest({'signal':saved['signal_id'],'horizon':days})
            self.assertEqual(H.write_once(dest,'evaluations',key,mature),'CREATED')
            self.assertEqual(H.write_once(dest,'evaluations',key,mature),'REPLAY_NOOP')
        self.assertEqual(before,next((dest/'signals').glob('*.json')).read_bytes())
        m=S.export_segment(dest,self.root/'recovery-segment','11',a,allow_fixtures=True)
        self.assertEqual(m['exported_records'],4)
        self.assertNotIn('signals/'+saved['signal_id']+'.json',m['delta_files'])
    def test_pending_event_survives_mature_event(self):
        pending=O.evaluate(self.sig,1,[],[],self.sig['observed_at_ms'])
        pending['test_fixture']=True
        H.write_once(self.state,'evaluations',digest(pending),pending)
        a=self.export(10)
        rows=path(self.sig);end=rows[-1].close_ms
        mature=O.evaluate(self.sig,1,rows,evidence(rows,end),end);mature['test_fixture']=True
        H.write_once(self.state,'evaluations',digest(mature),mature)
        self.export(11,a);self.restore(11)
        states={H.read_record(p)['status'] for p in (self.root/'restored/evaluations').glob('*.json')}
        self.assertEqual(states,{'PENDING','MATURED'})
    def test_missing_parent_fail_no_state(self):
        a=self.export(10);self.export(11,a);del self.archives['10']
        with self.assertRaises(KeyError):self.restore(11)
        self.assertFalse((self.root/'restored').exists())
    def test_missing_old_record_on_export_fails(self):
        a=self.export(10);next((self.state/'signals').glob('*.json')).unlink()
        with self.assertRaises(ValueError):self.export(11,a)
    def test_overwrite_old_record_on_export_fails(self):
        a=self.export(10);p=next((self.state/'signals').glob('*.json'))
        obj=json.loads(p.read_text());obj['record']['scan_id']=digest('changed');obj['record_sha256']=digest(obj['record']);p.write_text(json.dumps(obj))
        with self.assertRaises(ValueError):self.export(11,a)
    def test_corrupt_archive_digest_fails(self):
        self.export(10);raw,meta=self.archives['10'];meta['archive_sha256']='0'*64
        with self.assertRaises(ValueError):self.restore(10)
    def test_corrupt_manifest_fails(self):
        self.export(10);root=self.root/'segment-10';m=json.loads((root/'manifest.json').read_text());m['run_id']='12';(root/'manifest.json').write_text(json.dumps(m))
        with self.assertRaises(ValueError):S.decode_archive(zip_dir(root),True)
    def test_corrupt_record_with_resealed_zip_still_fails(self):
        self.export(10);root=self.root/'segment-10';p=next((root/'records/signals').glob('*.json'));obj=json.loads(p.read_text());obj['record']['reference_price']='0';p.write_text(json.dumps(obj))
        with self.assertRaises(ValueError):S.decode_archive(zip_dir(root),True)
    def test_missing_record_rejected(self):
        self.export(10);root=self.root/'segment-10';next((root/'records/signals').glob('*.json')).unlink()
        with self.assertRaises(ValueError):S.decode_archive(zip_dir(root),True)
    def test_bad_parent_reference_rejected(self):
        a=self.export(10);b=self.export(11,a);root=self.root/'segment-11'
        b['parent']['manifest_sha256']='0'*64;b.pop('manifest_sha256');b=S.seal(b);(root/'manifest.json').write_text(json.dumps(b))
        raw=zip_dir(root);self.archives['11']=(raw,{**self.archives['11'][1],'archive_sha256':S.sha(raw)})
        with self.assertRaises(ValueError):self.restore(11)
    def test_expired_parent_rejected(self):
        self.export(10);self.archives['10'][1]['expired']=True
        with self.assertRaises(ValueError):self.restore(10)
    def test_retention_guard_and_explicit_checkpoint(self):
        a=self.export(10);self.archives['10'][1]['expires_at']=(self.now+timedelta(days=5)).isoformat()
        with self.assertRaisesRegex(ValueError,'RETENTION_GUARD'):self.restore(10)
        self.restore(10,checkpoint=True)
        b=self.export(11,a,checkpoint=True)
        self.assertIsNone(b['parent']);self.assertTrue(b['checkpoint'])
        del self.archives['10']
        self.restore(11,self.root/'checkpoint-restored')
        self.assertEqual(S.inventory(self.state,True),S.inventory(self.root/'checkpoint-restored',True))
    def test_unrelated_or_failed_run_rejected(self):
        self.export(10)
        for field,value in [('workflow_path','other.yml'),('conclusion','failure')]:
            old=self.archives['10'][1][field];self.archives['10'][1][field]=value
            with self.assertRaises(ValueError):self.restore(10)
            self.archives['10'][1][field]=old
    def test_zip_traversal_extra_and_duplicate_rejected(self):
        self.export(10);b=io.BytesIO(self.archives['10'][0])
        with zipfile.ZipFile(b,'a') as z:z.writestr('../escape.json','{}')
        with self.assertRaises(ValueError):S.decode_archive(b.getvalue(),True)
        for name in ('../escape.json','signals/not-a-hash.json','scans\\a.json'):
            with self.assertRaises(ValueError):S.safe_path(name)
    def test_all_existing_bytes_preserved_during_restore_replay(self):
        self.export(10);self.restore(10)
        before={p:p.read_bytes() for p in (self.root/'restored').rglob('*.json')}
        self.restore(10)
        self.assertEqual(before,{p:p.read_bytes() for p in (self.root/'restored').rglob('*.json')})
    def test_failure_summary_no_token_and_no_silent_new_root(self):
        from upbit_c.research_operations import main
        with patch('upbit_c.research_operations.github_loader',return_value=lambda i: (_ for _ in ()).throw(ValueError('missing parent'))),patch.dict('os.environ',{'GH_TOKEN':'DO_NOT_LEAK','GITHUB_REPOSITORY':'nh1018/upbit-scanner'}),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            code=main(['--state',str(self.root/'failed-state'),'--segment',str(self.root/'failed-segment'),'--summary',str(self.root/'summary.json'),'--run-id','11','--previous-run-id','10'])
        self.assertEqual(code,1)
        text=(self.root/'summary.json').read_text()
        self.assertNotIn('DO_NOT_LEAK',text)
        self.assertFalse((self.root/'failed-state').exists())
        self.assertFalse((self.root/'failed-segment').exists())
    def test_native_actions_rerun_fails_closed_without_api_or_rescan(self):
        from upbit_c.research_operations import main
        with patch('upbit_c.research_operations.github_loader') as loader,patch('upbit_c.research_operations.run_research') as run,patch.dict('os.environ',{'GH_TOKEN':'DO_NOT_LEAK','GITHUB_REPOSITORY':'nh1018/upbit-scanner','GITHUB_RUN_ATTEMPT':'2'}),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            code=main(['--state',str(self.root/'rerun-state'),'--segment',str(self.root/'rerun-segment'),'--summary',str(self.root/'rerun.json'),'--run-id','10'])
        self.assertEqual(code,1);run.assert_not_called();loader.assert_not_called()
        result=json.loads((self.root/'rerun.json').read_text())
        self.assertIn('UNSAFE_NATIVE_RERUN',result['error'])
        self.assertNotIn('DO_NOT_LEAK',json.dumps(result))
        self.assertFalse((self.root/'rerun-state').exists())
        self.assertFalse((self.root/'rerun-segment').exists())
    def test_deleted_ancestor_cannot_be_accepted_as_replay(self):
        a=self.export(10);self.export(11,a)
        self.archives['10']=(b'',{'workflow_path':S.WORKFLOW,'conclusion':'failure','expired':False})
        with self.assertRaises(ValueError):self.restore(11)
        self.assertFalse((self.root/'restored').exists())
    def test_manual_workflow_has_no_periodic_or_write_permission(self):
        text=Path('.github/workflows/upbit-c-market-research.yml').read_text(encoding='utf-8')
        self.assertNotIn('schedule:',text);self.assertNotIn('contents: write',text)
        self.assertIn('types: [labeled]',text);self.assertIn('github.actor == github.repository_owner',text)
        self.assertIn('github.event.pull_request.head.sha',text);self.assertIn('persist-credentials: false',text)
        self.assertIn('if: always()',text)
        self.assertNotIn('pull_request_target',text)
        self.assertIn("startsWith(github.event.label.name, 'c-research-scan-')",text)
        self.assertIn('--manual-label "$MANUAL_LABEL"',text)

    def test_manual_label_modes_are_explicit_and_preserve_scan_continuation(self):
        from upbit_c.research_operations import manual_mode
        self.assertEqual(manual_mode('', '12', False, True, False),('12',False,True,False))
        self.assertEqual(manual_mode('c-research-run-root','12',True,True,True),('',False,False,False))
        for mode,want in [('scan',('12',False,True,False)),('evaluate',('12',True,False,False)),('checkpoint',('12',True,False,True)),('failproof',('12',True,False,False))]:
            self.assertEqual(manual_mode('c-research-'+mode+'-12','',False,False,False),want)
    def test_malformed_manual_labels_never_start_research(self):
        from upbit_c.research_operations import manual_mode
        for label in ['c-research-scan-0','c-research-scan-01','c-research-scan-12;echo bad','c-research-scan-12\n','c-research-unknown-12','unrelated-label']:
            with self.assertRaises(ValueError):manual_mode(label,'',False,False,False)

    def test_adapter_protects_all_output_locations_before_restore(self):
        from upbit_c.research_operations import main
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        for namespace in ('data_market','output_btc_anytime','metadata_features','.git','upbit_c'):
            for target in ('state','segment','summary'):
                values={'state':self.root/'safe-state','segment':self.root/'safe-segment','summary':self.root/'safe-summary.json'}
                values[target]=self.root/namespace/target
                args=['--run-id','11','--previous-run-id','10']
                for name,path in values.items():args+=['--'+name,str(path)]
                with patch('upbit_c.research_operations.github_loader') as loader,patch('upbit_c.research_operations.run_research') as run,redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):main(args)
                loader.assert_not_called();run.assert_not_called()
                self.assertEqual(before,{p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

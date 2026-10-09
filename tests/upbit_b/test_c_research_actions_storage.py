"""Isolated Actions transport fixtures, never actual qualified market cases."""
import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock,patch
from contextlib import redirect_stdout,redirect_stderr
from datetime import datetime,timezone,timedelta

from upbit_b.feature_contracts import digest,dumps
from upbit_c import research_archive as A, research_actions_storage as T
from upbit_c import research_history as H, research_outcomes as O, research_segments as V11
from test_c_market_research import signal,path,evidence
from test_c_research_segments import zip_dir
from test_c_research_archive import envelope


class Source:
    def __init__(self):
        self.payload={};self.bytes={'index':0,'cold':0,'v11':0};self.metadata_requests=0
        self.requested=[];self.missing=set();self.expired=set();self.legacy={}
    def metadata(self,key,name,checkpoint=False,optional=False):
        self.metadata_requests+=1
        if (key,name) in self.missing or (key,name) not in self.payload:
            if optional:return None
            raise ValueError('missing parent artifact')
        if (key,name) in self.expired:raise ValueError('expired parent artifact')
        raw=self.payload[key,name]
        return {'id':key+name,'run_id':key,'name':name,'digest':'sha256:'+V11.sha(raw),'expires_at':'2099-01-01T00:00:00Z'}
    def download(self,art,kind):
        self.requested.append((art['run_id'],kind));raw=self.payload[art['run_id'],art['name']]
        self.bytes[kind]+=len(raw);return raw
    def legacy_loader(self,checkpoint=False):return lambda key:self.legacy[key]


class ActionsStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=Source();self.store=A.Archive(self.root/'initial',True)
        self.sig=signal();self.sig['test_fixture']=True
        self.name='signals/'+self.sig['signal_id']+'.json'
        self.m=self.store.commit('10',{self.name:envelope(self.sig)})
        self.export(self.store,self.m)
    def tearDown(self):self.tmp.cleanup()
    def export(self,store,manifest,parent=None,checkpoint=False):
        key=manifest['run_id'];index=self.root/(key+'-index');cold=self.root/(key+'-cold')
        result=T.export_artifacts(store,manifest,index,cold,parent,checkpoint)
        self.source.payload[key,T.INDEX_NAME]=zip_dir(index)
        self.source.payload[key,T.COLD_NAME]=zip_dir(cold)
        return result
    def restore(self,key='10',name='restored'):
        store=T.RemoteArchive(self.root/name,self.source,allow_fixtures=True)
        result=T.restore_index(store,key)
        return store,result
    def operate(self,key='11',parent='10',**kwargs):
        return T.operate(self.root/(key+'-state'),self.root/(key+'-index'),self.root/(key+'-cold'),key,
            self.source,parent=parent,allow_fixtures=True,client=Mock(requests=0),**kwargs)

    def test_restore_index_reads_no_cold(self):
        store,(m,active,result)=self.restore()
        self.assertEqual(m,self.m);self.assertEqual(len(active['signals']),1)
        self.assertEqual(self.source.bytes['cold'],0)
        self.assertFalse((store.root/'objects').exists())
        self.assertTrue(store.object_available(store.descriptor(m['inventory'][self.name])['chunks'][0]))

    def test_evaluation_only_preserves_inventory_and_skips_scanner(self):
        with patch('upbit_c.research_archive.evaluate_active',return_value={}),patch('upbit_c.research_scan.scan') as scanner:
            r=self.operate(outcomes_only=True)
        scanner.assert_not_called()
        self.assertEqual(r['new_records'],0);self.assertEqual(r['download_bytes']['cold'],0)
        m=json.loads((self.root/'11-index/runs/11.json').read_bytes())
        self.assertEqual(m['inventory'],self.m['inventory'])
        self.assertFalse(r['market_scan_executed'])

    def test_existing_signal_first_clock_not_shifted(self):
        store,(m,active,_)=self.restore()
        new=copy.deepcopy(self.sig);new['observed_at_ms']+=H.HOUR;new['proxy_anchor_open_ms']+=H.HOUR
        child=store.commit('11',{self.name:envelope(new)},('10',m['sha256']))
        self.assertEqual(child['delta_files'],[])
        self.assertEqual(store.active(child)['signals'][self.sig['signal_id']]['signal'],self.sig)
        self.assertEqual(self.source.bytes['cold'],0)

    def test_lazy_original_restore_is_byte_exact(self):
        store,(m,_,_)=self.restore()
        raw=store.read_record(self.name,m['inventory'][self.name])
        self.assertEqual(raw,envelope(self.sig));self.assertGreater(self.source.bytes['cold'],0)

    def test_missing_parent_cold_fails_before_scan(self):
        self.source.missing.add(('10',T.COLD_NAME))
        with patch('upbit_c.research_scan.scan') as scanner,self.assertRaises(ValueError):self.operate()
        scanner.assert_not_called()
        self.assertFalse((self.root/'11-state').exists())

    def test_expired_cold_fails_not_new_root(self):
        self.source.expired.add(('10',T.COLD_NAME))
        with self.assertRaises(ValueError):self.restore()

    def test_manifest_hash_failure(self):
        index=self.root/'10-index'
        p=index/'runs/10.json';m=json.loads(p.read_bytes());m['inventory']={};p.write_text(json.dumps(m))
        self.source.payload['10',T.INDEX_NAME]=zip_dir(index)
        with self.assertRaises(ValueError):self.restore()

    def test_zip_truncated_or_extra_or_duplicate_rejected(self):
        original=self.source.payload['10',T.INDEX_NAME]
        self.source.payload['10',T.INDEX_NAME]=original[:-20]
        with self.assertRaises(zipfile.BadZipFile):self.restore()
        buf=io.BytesIO(original)
        with zipfile.ZipFile(buf,'a') as z:z.writestr('../escape','bad')
        self.source.payload['10',T.INDEX_NAME]=buf.getvalue()
        with self.assertRaises(ValueError):self.restore(name='extra')

    def test_active_corruption_refuses_evaluation(self):
        index=self.root/'10-index';p=next((index/'active').glob('*'));p.write_bytes(b'bad')
        self.source.payload['10',T.INDEX_NAME]=zip_dir(index)
        with patch('upbit_c.research_archive.evaluate_active') as evaluate,self.assertRaises(ValueError):self.operate(outcomes_only=True)
        evaluate.assert_not_called()

    def test_cold_corrupt_hash_rejected_without_publication(self):
        store,(m,_,_)=self.restore()
        cold=self.root/'10-cold';next((cold/'objects').glob('*')).write_bytes(b'bad')
        self.source.payload['10',T.COLD_NAME]=zip_dir(cold)
        with self.assertRaises(ValueError):store.read_record(self.name,m['inventory'][self.name])
        self.assertFalse((store.root/'objects').exists())

    def test_new_outcomes_add_only_new_cold_payload(self):
        store,(m,_,_)=self.restore()
        rows=path(self.sig);end=rows[-1].close_ms
        out=O.evaluate(self.sig,1,rows,evidence(rows,end),end);out['test_fixture']=True
        key=digest({'signal_id':self.sig['signal_id'],'days':1,'contract':O.SCHEMA})
        name='evaluations/'+key+'.json'
        child=store.commit('11',{name:envelope(out)},('10',m['sha256']))
        self.assertEqual(self.source.bytes['cold'],0)
        result=self.export(store,child,store.transport)
        transport=json.loads((self.root/'11-index/transport.json').read_bytes())
        self.assertEqual(result['cold_new_objects'],1)
        self.assertEqual({ref['run_id'] for ref in transport['objects'].values()},{'10','11'})
        self.assertEqual(child['inventory'][self.name],m['inventory'][self.name])
        restored,(cm,active,_)=self.restore('11','child')
        self.assertEqual(active['signals'][self.sig['signal_id']]['outcomes']['1']['status'],'MATURED')
        self.assertNotIn(1,[w['days'] for w in A.pending_work(active,end)])
        self.assertEqual(restored.audit(cm)['records'],2)

    def test_checkpoint_eliminates_cold_ancestor_dependency(self):
        store,(m,_,_)=self.restore()
        child=store.commit('11',{},('10',m['sha256']))
        self.export(store,child,store.transport,checkpoint=True)
        del self.source.payload['10',T.COLD_NAME]
        new,(cm,_,_)=self.restore('11','checkpoint')
        self.assertEqual(new.audit(cm)['status'],'VERIFIED')

    def test_same_boundary_new_manual_run_is_noop(self):
        store,(m,_,_)=self.restore()
        child=store.commit('11',{},('10',m['sha256']))
        self.export(store,child,store.transport)
        nextstore,(n,_,_)=self.restore('11','second')
        second=nextstore.commit('12',{},('11',n['sha256']))
        self.assertEqual(second['inventory'],self.m['inventory'])
        self.assertEqual(second['delta_files'],[])
        self.assertEqual(self.source.bytes['cold'],0)

    def test_v11_parent_migrates_once_without_record_changes(self):
        state=self.root/'v11';H.write_once(state,'signals',self.sig['signal_id'],self.sig)
        segment=self.root/'segment';V11.export_segment(state,segment,'9',allow_fixtures=True)
        raw=zip_dir(segment)
        self.source.legacy['9']=(raw,{'workflow_path':V11.WORKFLOW,'conclusion':'success','expired':False,
            'expires_at':(datetime.now(timezone.utc)+timedelta(days=90)).isoformat(),'archive_sha256':V11.sha(raw)})
        with patch('upbit_c.research_archive.evaluate_active',return_value={}):result=self.operate('11','9',outcomes_only=True)
        self.assertIn('v11_migration',result)
        self.source.payload['11',T.INDEX_NAME]=zip_dir(self.root/'11-index')
        self.source.payload['11',T.COLD_NAME]=zip_dir(self.root/'11-cold')
        store,(m,_,_)=self.restore('11','migrated')
        self.assertEqual(store.read_record(self.name,m['inventory'][self.name]),envelope(self.sig))

    def test_workflow_remains_manual_readonly_and_separate_artifacts(self):
        text=Path('.github/workflows/upbit-c-market-research.yml').read_text(encoding='utf-8')
        self.assertIn('research_actions_storage',text)
        self.assertIn('upbit-c-research-index-',text);self.assertIn('upbit-c-research-cold-',text)
        self.assertNotIn('schedule:',text);self.assertNotIn('contents: write',text)
        self.assertNotIn('pull_request_target',text);self.assertIn('persist-credentials: false',text)
        self.assertIn('if-no-files-found: error',text)

    def test_invalid_paths_fail_before_any_api(self):
        for name in ('data_market','output','upbit_c','.github','btc_anytime'):
            with self.assertRaises(ValueError):T.operate(self.root/name,self.root/'index',self.root/'cold','11',self.source)
        self.assertEqual(self.source.metadata_requests,0)

    def test_outcomes_only_requires_parent(self):
        with self.assertRaises(ValueError):self.operate(parent='',outcomes_only=True)

    def test_run_id_and_parent_validation(self):
        for key in ('0','01','1\n','../11'):
            with self.assertRaises(ValueError):self.operate(key)
        with self.assertRaises(ValueError):self.operate('10','11')

    def test_native_rerun_guard_and_secret_nonexposure(self):
        summary=self.root/'summary.json'
        args=['--state',str(self.root/'state'),'--index',str(self.root/'index'),'--cold',str(self.root/'cold'),
            '--summary',str(summary),'--run-id','11','--previous-run-id','10']
        with patch.dict('os.environ',{'GITHUB_RUN_ATTEMPT':'2','GH_TOKEN':'NEVER_PRINT'}),patch.object(T,'GitHubArtifacts') as source,redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(T.main(args),1)
        source.assert_not_called();text=summary.read_text();self.assertIn('UNSAFE_NATIVE_RERUN',text);self.assertNotIn('NEVER_PRINT',text)

    def test_export_failure_fails_operation(self):
        with patch('upbit_c.research_archive.evaluate_active',return_value={}),patch.object(T,'export_artifacts',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.operate(outcomes_only=True)
        self.assertFalse((self.root/'11-index').exists())

    def test_new_scan_continuation_only_archives_new_record(self):
        def scanner(client,**kwargs):
            self.assertEqual(kwargs['batch_count'],1);self.assertEqual(kwargs['batch_index'],0)
            report={'activation':'RESEARCH_ONLY','test_fixture':True,'results':[],
                'started_at_ms':1000,'finished_at_ms':2000,'universe_size':0,'selected_count':0,
                'success_count':0,'failure_count':0,'failure_reasons':{},'logical_api_requests':1,'api_blocked':False}
            report['scan_id']=digest(report);return report
        result=self.operate(scanner=scanner)
        self.assertTrue(result['market_scan_executed']);self.assertEqual(result['new_records'],1)
        self.assertEqual(result['download_bytes']['cold'],0)
        manifest=json.loads((self.root/'11-index/runs/11.json').read_bytes())
        self.assertEqual(len(manifest['inventory']),2)
        self.assertEqual(manifest['inventory'][self.name],self.m['inventory'][self.name])

    def test_parent_missing_index_cannot_silently_initialize(self):
        self.source.missing.add(('10',T.INDEX_NAME))
        with patch('upbit_c.research_scan.scan') as scanner,self.assertRaises(KeyError):self.operate()
        scanner.assert_not_called();self.assertFalse((self.root/'11-index').exists())

    def test_github_metadata_expiry_and_digest_validation(self):
        source=T.GitHubArtifacts('nh1018/upbit-scanner','NEVER_PRINT')
        def fixture(expires,digest_value='sha256:'+'a'*64):
            return {'id':1,'name':T.INDEX_NAME,'expired':False,'digest':digest_value,'expires_at':expires.isoformat()}
        now=datetime.now(timezone.utc)
        source.runs['10']={'conclusion':'success'}
        source.artifacts['10']=[fixture(now+timedelta(days=5))]
        with self.assertRaisesRegex(ValueError,'RETENTION_GUARD'):source.metadata('10',T.INDEX_NAME)
        self.assertEqual(source.metadata('10',T.INDEX_NAME,checkpoint=True)['id'],1)
        source.artifacts['10']=[fixture(now-timedelta(days=1))]
        with self.assertRaisesRegex(ValueError,'expired'):source.metadata('10',T.INDEX_NAME,checkpoint=True)
        source.artifacts['10']=[fixture(now+timedelta(days=90),None)]
        with self.assertRaisesRegex(ValueError,'digest unavailable'):source.metadata('10',T.INDEX_NAME)

    def test_github_rejects_unrelated_failed_native_attempt_parents(self):
        for change in ({'path':'other.yml'},{'conclusion':'failure'},{'run_attempt':2}):
            source=T.GitHubArtifacts('nh1018/upbit-scanner','NEVER_PRINT')
            run={'path':V11.WORKFLOW,'conclusion':'success','run_attempt':1,**change}
            with patch.object(source,'api',return_value=run),self.assertRaises(ValueError):source.listing('10')

    def test_github_partial_download_digest_failure(self):
        source=T.GitHubArtifacts('nh1018/upbit-scanner','NEVER_PRINT')
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'partial'
        opener=Mock();opener.open.return_value=response
        with patch('urllib.request.build_opener',return_value=opener),self.assertRaisesRegex(ValueError,'SHA256 mismatch'):
            source.download({'id':1,'digest':'sha256:'+'a'*64},'index')

    def test_failure_summary_redacts_token(self):
        args=['--state',str(self.root/'state'),'--index',str(self.root/'index'),'--cold',str(self.root/'cold'),
            '--summary',str(self.root/'redacted.json'),'--run-id','11']
        with patch.dict('os.environ',{'GITHUB_RUN_ATTEMPT':'1','GITHUB_REPOSITORY':'nh1018/upbit-scanner','GH_TOKEN':'NEVER_PRINT'}),patch.object(T,'operate',side_effect=ValueError('error NEVER_PRINT')),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(T.main(args),1)
        text=(self.root/'redacted.json').read_text();self.assertNotIn('NEVER_PRINT',text);self.assertIn('[REDACTED]',text)


if __name__=='__main__':unittest.main()

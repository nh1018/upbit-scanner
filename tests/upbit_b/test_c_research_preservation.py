"""V1.5 transport fixtures: no remote Release or real signal publication."""
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from upbit_c import research_preservation as P, research_release as R
import test_c_research_release as fixtures


class FakeGitHub:
    def __init__(self):
        self.rows=[];self.payloads={};self.calls=[];self.fail=None;self.partial=False
    def releases(self):return self.rows
    def assets(self, rid):return [a for r in self.rows if r['id']==rid for a in r['assets']]
    def by_tag(self,tag):return next((r for r in self.rows if r['tag_name']==tag),None)
    def get(self,path,binary=False):
        if self.fail=='download':raise ValueError('network unavailable')
        return self.payloads[int(path.split('/')[-1])]
    def request(self,method,path,payload=None,upload=False):
        self.calls.append((method,path))
        if self.fail=='post':raise P.RemoteError(403)
        if path=='releases':
            row=dict(payload,id=len(self.rows)+1,assets=[]);self.rows.append(row);return row
        if self.fail=='upload':raise ValueError('network interrupted')
        rid=int(path.split('/')[1]);name=path.split('name=')[1]
        aid=len(self.payloads)+1;self.payloads[aid]=payload
        asset={'id':aid,'name':name,'size':len(payload),'state':'starter' if self.partial else 'uploaded',
               'digest':'sha256:'+R.S.sha(payload)}
        next(r for r in self.rows if r['id']==rid)['assets'].append(asset)
        return asset


class PreservationTests(unittest.TestCase):
    setUp=fixtures.ReleaseTests.setUp
    tearDown=fixtures.ReleaseTests.tearDown
    def client(self):return FakeGitHub()
    def current(self):return P.identity(self.raw,self.sha,True)
    def publish(self,client,**kw):
        return P.publish(client,self.raw,self.sha,'a'*40,'PUBLISH:'+self.sha,allow_fixtures=True,**kw)

    def test_identity_v14_compatible(self):
        c=self.current();self.assertEqual(c['run_id'],'123');self.assertEqual(c['manifest']['schema_version'],R.SCHEMA)
    def test_default_is_dry_run_zero_posts(self):
        c=self.client();r=P.publish(c,self.raw,self.sha,'a'*40,allow_fixtures=True)
        self.assertEqual(r['status'],'APPROVAL_REQUIRED');self.assertEqual(c.calls,[])
    def test_wrong_approval_no_posts(self):
        c=self.client();P.publish(c,self.raw,self.sha,'a'*40,'PUBLISH:wrong',allow_fixtures=True)
        self.assertEqual(c.calls,[])
    def test_fixture_cannot_publish_as_real(self):
        with self.assertRaises(ValueError):P.publish(self.client(),self.raw,self.sha,'a'*40,'PUBLISH:'+self.sha)
    def test_hash_fail_before_post(self):
        c=self.client()
        with self.assertRaises(ValueError):P.publish(c,self.raw,'0'*64,'a'*40,'PUBLISH:'+'0'*64,allow_fixtures=True)
        self.assertEqual(c.calls,[])
    def test_unpinned_commit_rejected(self):
        with self.assertRaises(ValueError):P.publish(self.client(),self.raw,self.sha,'main','PUBLISH:'+self.sha,allow_fixtures=True)
    def test_publication_download_verified(self):
        c=self.client();r=self.publish(c)
        self.assertEqual(r['status'],'VERIFIED_PUBLICATION');self.assertEqual(r['remote_writes'],2)
        self.assertEqual(c.rows[0]['target_commitish'],'a'*40)
        self.assertEqual(json.loads(c.rows[0]['body'])['original_ids'],[self.name])
    def test_source_actions_run_separate_from_manifest_run(self):
        c=self.client()
        P.publish(c,self.raw,self.sha,'a'*40,'PUBLISH:'+self.sha,
                  allow_fixtures=True,source_run_id='999')
        body=json.loads(c.rows[0]['body'])
        self.assertEqual(body['source_actions_run_id'],'999');self.assertEqual(body['run_id'],'123')
    def test_duplicate_no_upload(self):
        c=self.client();self.publish(c);before=list(c.calls)
        self.assertEqual(self.publish(c)['status'],'ALREADY_PRESENT');self.assertEqual(c.calls,before)
    def test_same_name_different_hash_blocked(self):
        c=self.client();self.publish(c);c.rows[0]['assets'][0]['digest']='sha256:'+'0'*64
        with self.assertRaises(ValueError):self.publish(c)
    def test_partial_upload_not_success(self):
        c=self.client();c.partial=True
        with self.assertRaises(ValueError):self.publish(c)
        with self.assertRaises(ValueError):self.publish(c)
        self.assertEqual(len(c.calls),2)
    def test_permission_failure_not_success(self):
        c=self.client();c.fail='post'
        with self.assertRaises(P.RemoteError):self.publish(c)
        self.assertFalse(c.rows)
    def test_upload_interruption_retry_existing_release(self):
        c=self.client();c.fail='upload'
        with self.assertRaises(ValueError):self.publish(c)
        self.assertEqual(len(c.rows),1);c.fail=None
        self.assertEqual(self.publish(c)['status'],'VERIFIED_PUBLICATION');self.assertEqual(len(c.rows),1)
    def test_download_failure_no_verified_receipt(self):
        c=self.client();c.fail='download'
        with self.assertRaises(ValueError):self.publish(c)
        c.fail=None;self.assertEqual(self.publish(c)['status'],'ALREADY_PRESENT')
    def test_deleted_pinned_release_not_recreated(self):
        c=self.client();self.publish(c);c.rows=[];before=list(c.calls)
        with self.assertRaises(ValueError):self.publish(c,expected_release_id=1)
        self.assertEqual(c.calls,before)
    def test_corrupt_download_fails(self):
        c=self.client();self.publish(c);c.payloads[1]=b'corrupt'
        with self.assertRaises(ValueError):self.publish(c)
    def test_extra_asset_fails(self):
        c=self.client();self.publish(c);c.rows[0]['assets'].append(dict(c.rows[0]['assets'][0]))
        with self.assertRaises(ValueError):self.publish(c)
    def test_wrong_release_tag_fails(self):
        c=self.client();self.publish(c);c.rows[0]['tag_name']=P.PREFIX+'0'*64
        with self.assertRaises(ValueError):self.publish(c)
    def test_overlap_new_active_is_not_replay(self):
        c=self.client();self.publish(c)
        m=self.store.commit('124',{},('123',self.manifest['sha256']))
        p=R.prepare(self.store,m,self.root/'new-packages')['package']
        raw=(self.root/'new-packages'/p['asset_name']).read_bytes()
        with self.assertRaisesRegex(ValueError,'overlap'):
            P.publish(c,raw,p['sha256'],'a'*40,'PUBLISH:'+p['sha256'],allow_fixtures=True)
        self.assertEqual(len(c.calls),2)
    def test_no_external_pin_download_contract(self):
        with self.assertRaises(ValueError):R.verify(self.raw,None,True)
    def test_backup_includes_zip_manifest_hashes_ids(self):
        dst=self.root/'backup'
        r=P.export_backup(self.raw,self.sha,dst,True)
        self.assertEqual((dst/self.package['asset_name']).read_bytes(),self.raw)
        self.assertEqual(r['original_ids'],[self.name]);self.assertEqual(r['run_id'],'123')
        self.assertEqual(r['independent_disaster_backup'],'NOT_VERIFIED')
        for name,ref in r['files'].items():self.assertEqual(R.ref((dst/name).read_bytes()),ref)
    def test_backup_retry_no_timestamp_rewrite(self):
        dst=self.root/'backup';a=P.export_backup(self.raw,self.sha,dst,True)
        self.assertEqual(a,P.export_backup(self.raw,self.sha,dst,True))
    def test_backup_conflict_no_overwrite(self):
        dst=self.root/'backup';dst.mkdir();(dst/'release-manifest.json').write_bytes(b'bad')
        with self.assertRaises(ValueError):P.export_backup(self.raw,self.sha,dst,True)
        self.assertFalse((dst/self.package['asset_name']).exists())
    def test_backup_protected_destination(self):
        with self.assertRaises(ValueError):P.export_backup(self.raw,self.sha,self.root/'data',True)
    def test_backup_restore_byte_identity_active_lineage(self):
        dst=self.root/'backup';P.export_backup(self.raw,self.sha,dst,True)
        r=R.restore((dst/self.package['asset_name']).read_bytes(),self.sha,self.root/'restore',True)
        restored=R.A.Archive(self.root/'restore',True)
        self.assertEqual(restored.read_record(self.name,self.manifest['inventory'][self.name]),self.original)
        self.assertEqual(restored.active(self.manifest),self.store.active(self.manifest))
        self.assertEqual(r['run_id'],'123')
    def test_locate_requires_one_identity(self):
        with self.assertRaises(ValueError):P.locate(self.client())
    def test_locate_run_and_original(self):
        c=self.client();self.publish(c)
        checked=P.checked_asset
        with patch.object(P,'checked_asset',side_effect=lambda client,asset:checked(client,asset,True)):
            self.assertEqual(P.locate(c,run_id='123')['status'],'FOUND')
            self.assertEqual(P.locate(c,original_id=self.name)['matches'][0]['asset_id'],1)
            self.assertEqual(P.locate(c,run_id='999')['status'],'NOT_FOUND')
    def test_managed_release_missing_assets_fail_closed(self):
        c=self.client();c.rows=[{'id':1,'tag_name':P.PREFIX+'0'*64,'assets':[]}]
        with self.assertRaises(ValueError):self.publish(c)
    def test_negative_asset_id_rejected(self):
        with self.assertRaises(ValueError):P.positive_id(-1)
    def test_missing_official_asset_digest_fails(self):
        c=self.client();self.publish(c);del c.rows[0]['assets'][0]['digest']
        with self.assertRaises(ValueError):self.publish(c)
    def test_backup_receipt_tampering_not_overwritten(self):
        dst=self.root/'backup';P.export_backup(self.raw,self.sha,dst,True)
        (dst/'backup-receipt.json').write_text('{}')
        with self.assertRaises(ValueError):P.export_backup(self.raw,self.sha,dst,True)
    def test_binary_get_never_forwards_secret_on_redirect(self):
        c=P.GitHub('o/r','secret-token')
        response=unittest.mock.MagicMock();response.__enter__.return_value.read.return_value=b'bytes'
        with patch('urllib.request.build_opener') as op,patch('urllib.request.urlopen',return_value=response) as redirect:
            op.return_value.open.side_effect=urllib.error.HTTPError('https://api.github.com',302,'',{'Location':'https://objects.example/a'},None)
            self.assertEqual(c.get('releases/assets/1',True),b'bytes')
            self.assertIsInstance(redirect.call_args.args[0],str)
            self.assertNotIn('secret-token',str(redirect.call_args))
    def test_no_write_credential_fails_before_network(self):
        with patch('urllib.request.build_opener') as op,self.assertRaises(ValueError):P.GitHub('o/r').request('POST','releases',{})
        op.assert_not_called()
    def test_source_failure_no_package_or_scan(self):
        with patch('upbit_c.research_actions_storage.GitHubArtifacts') as source,patch('upbit_c.research_scan.scan') as scan:
            source.return_value.metadata.side_effect=ValueError('expired source')
            with self.assertRaises(ValueError):P.prepare_source('o/r','token','123',self.root/'prepared')
            scan.assert_not_called();self.assertFalse((self.root/'prepared').exists())
    def test_summary_explicit_failure_and_no_token(self):
        output=io.StringIO();summary=self.root/'summary.json'
        with patch.object(P,'prepare_source',side_effect=P.PreservationError('incomplete asset')),patch('sys.stdout',output):
            rc=P.main(['prepare-source','--run-id','123','--destination',str(self.root/'p'),'--summary',str(summary)])
        self.assertEqual(rc,1);r=json.loads(summary.read_bytes())
        self.assertEqual(r['reason'],'incomplete asset');self.assertFalse(r['verified_publication'])
    def test_http_permission_redacted(self):
        c=P.GitHub('o/r','secret-token')
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=urllib.error.HTTPError('secret-url',403,'secret-token',{},None)
            with self.assertRaisesRegex(P.RemoteError,'GitHub HTTP 403'):c.request('POST','releases',{})
    def test_transport_error_redacted(self):
        c=P.GitHub('o/r','secret-token')
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=OSError('secret-token')
            with self.assertRaisesRegex(ValueError,'GitHub transport failed: OSError'):c.releases()
    def test_metadata_parse_error(self):
        with patch('urllib.request.build_opener') as op:
            op.return_value.open.return_value.__enter__.return_value.read.return_value=b'bad'
            with self.assertRaisesRegex(ValueError,'metadata JSON'):P.GitHub('o/r').releases()
    def test_create_missing_tag_only_404(self):
        c=P.GitHub('o/r')
        with patch.object(c,'request',side_effect=P.RemoteError(404)):self.assertIsNone(c.by_tag('tag'))
        with patch.object(c,'request',side_effect=P.RemoteError(403)):
            with self.assertRaises(P.RemoteError):c.by_tag('tag')
    def test_no_delete_or_redirect_mutation(self):
        c=P.GitHub('o/r','t')
        for method in ('PUT','PATCH','DELETE'):
            with self.assertRaises(ValueError):c.request(method,'releases/1')
    def test_cli_failure_never_prints_secrets(self):
        output=io.StringIO()
        with patch.object(P,'prepare_source',side_effect=OSError('secret-token')),patch('sys.stdout',output):
            rc=P.main(['prepare-source','--run-id','123','--destination',str(self.root/'p')])
        self.assertEqual(rc,1);self.assertNotIn('secret-token',output.getvalue())
    def test_workflow_manual_only_job_scoped_permission_and_gate(self):
        text=Path('.github/workflows/upbit-c-preservation.yml').read_text()
        self.assertIn('workflow_dispatch:',text);self.assertNotIn('schedule:',text);self.assertNotIn('pull_request:',text)
        self.assertEqual(text.count('contents: write'),1);self.assertIn('default: dry-run',text)
        self.assertIn("inputs.approval == format('PUBLISH:{0}', inputs.package_sha256)",text)
        self.assertIn('cancel-in-progress: false',text);self.assertIn('persist-credentials: false',text)


if __name__=='__main__':unittest.main()

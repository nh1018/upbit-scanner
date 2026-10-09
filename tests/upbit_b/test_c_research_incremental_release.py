"""V1.6 publication fixture tests. No actual GitHub POST requests."""
import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from upbit_c import research_incremental as I, research_incremental_release as X
from upbit_c import research_incremental_runner as CLI
import test_c_research_incremental as fixtures


class Fake:
    repository='o/r'
    def __init__(self,base,sha):
        self.items=[{'id':1,'tag_name':'c-research-'+'a'*64}]
        self.payload={1:base};self.posts=[]
        self.files={1:[{'id':1,'name':'base.zip','size':len(base),'state':'uploaded','digest':'sha256:'+sha}]}
        self.network_error=False;self.partial=False;self.download_bad=False
    def releases(self):return copy.deepcopy(self.items)
    def assets(self,key):return copy.deepcopy(self.files.get(key,[]))
    def get(self,path,binary=False):
        if self.network_error:raise OSError('SENSITIVE_URL_TOKEN')
        key=int(path.split('/')[-1])
        if binary:return b'corrupt' if self.download_bad and key!=1 else self.payload[key]
        return next(x for x in self.items if x['id']==key)
    def request(self,method,path,payload,upload=False):
        self.posts.append((method,path))
        if self.network_error:raise I.P.RemoteError(403)
        if not upload:
            item=dict(payload,id=len(self.items)+1);self.items.append(item);self.files[item['id']]=[];return item
        key=int(path.split('/')[1]);name=I.P.urllib.parse.unquote(path.split('name=')[1])
        asset={'id':key,'name':name,'size':len(payload),'state':'starter' if self.partial else 'uploaded','digest':'sha256:'+I.R.S.sha(payload)}
        self.files[key]=[asset];self.payload[key]=payload;return asset


class TransportTests(unittest.TestCase):
    setUp=fixtures.IncrementalTests.setUp
    tearDown=fixtures.IncrementalTests.tearDown
    setup_chain=fixtures.IncrementalTests.setup_chain
    child=fixtures.IncrementalTests.child
    prepare=fixtures.IncrementalTests.prepare
    def ready(self):
        self.setup_chain();self.child();result=self.prepare()
        self.zip=(self.root/'delta'/result['package']['asset_name']).read_bytes()
        self.zip_sha=result['package']['sha256'];self.client=Fake(self.raw,self.sha)
    def publish(self,approval=None):
        return X.publish(self.client,self.zip,self.zip_sha,self.catalog,self.fetch,'a'*40,
                         'PUBLISH:'+self.zip_sha if approval is None else approval,True)

    def test_default_no_write(self):
        self.ready();r=self.publish('');self.assertEqual(r['status'],'APPROVAL_REQUIRED');self.assertEqual(self.client.posts,[])
    def test_wrong_approval_no_write(self):
        self.ready();self.publish('PUBLISH:'+'0'*64);self.assertEqual(self.client.posts,[])
    def test_upload_download_verified_then_retry_no_post(self):
        self.ready();r=self.publish();self.assertEqual(r['status'],'VERIFIED_PUBLICATION')
        self.assertEqual(len(self.client.posts),2);self.assertEqual(r['catalog']['increments'][0]['asset_id'],2)
        again=self.publish();self.assertEqual(again['status'],'ALREADY_PRESENT');self.assertEqual(len(self.client.posts),2)
    def test_partial_upload_stops_and_retry_never_overwrites(self):
        self.ready();self.client.partial=True
        with self.assertRaises(ValueError):self.publish()
        with self.assertRaises(ValueError):self.publish()
        self.assertEqual(len(self.client.posts),2)
    def test_same_name_different_hash_blocks(self):
        self.ready();self.publish();self.client.files[2][0]['digest']='sha256:'+'0'*64
        with self.assertRaises(ValueError):self.publish()
        self.assertEqual(len(self.client.posts),2)
    def test_full_download_corruption(self):
        self.ready();self.client.download_bad=True
        with self.assertRaises(ValueError):self.publish()
        self.assertEqual(len(self.client.posts),2)
    def test_missing_base_remote_dependency(self):
        self.ready();self.client.files[1]=[]
        with self.assertRaises(ValueError):self.publish()
        self.assertEqual(self.client.posts,[])
    def test_unknown_increment_fork_blocks(self):
        self.ready();self.client.items.append({'id':3,'tag_name':I.PREFIX+'b'*64})
        with self.assertRaises(ValueError):self.publish()
        self.assertEqual(self.client.posts,[])
    def test_network_failure_before_mutation(self):
        self.ready();self.client.network_error=True
        with self.assertRaises(OSError):self.publish()
        self.assertEqual(self.client.posts,[])
    def test_permission_error_after_verification(self):
        self.ready()
        with patch.object(self.client,'request',side_effect=I.P.RemoteError(403)),self.assertRaises(I.P.RemoteError):self.publish()
    def test_target_commit_pinned(self):
        self.ready()
        with self.assertRaises(ValueError):X.publish(self.client,self.zip,self.zip_sha,self.catalog,self.fetch,'main','PUBLISH:'+self.zip_sha,True)
        self.assertEqual(self.client.posts,[])
    def test_resume_empty_release_after_interruption(self):
        self.ready();manifest,_=I.unpack(self.zip,self.zip_sha,True)
        self.client.items.append({'id':2,'tag_name':I.PREFIX+manifest['sha256']});self.client.files[2]=[]
        r=self.publish();self.assertEqual(r['remote_writes'],1);self.assertEqual(len(self.client.posts),1)
    def test_cli_error_redacts_sensitive_exceptions(self):
        path=self.root/'catalog.json';path.write_bytes(b'{}');out=io.StringIO()
        with patch.object(I,'read_catalog',side_effect=OSError('SENSITIVE_URL_TOKEN')),redirect_stdout(out):
            code=CLI.main(['restore','--catalog',str(path),'--catalog-sha256','0'*64,'--destination',str(self.root/'out')])
        self.assertEqual(code,1);self.assertNotIn('SENSITIVE_URL_TOKEN',out.getvalue())
    def test_workflow_manual_default_isolated_permissions(self):
        text=(fixtures.fixtures.Path(__file__).resolve().parents[2]/'.github/workflows/upbit-c-incremental-preservation.yml').read_text()
        self.assertIn('workflow_dispatch:',text);self.assertNotIn('schedule:',text)
        self.assertNotIn('workflow_run:',text);self.assertIn('default: dry-run',text)
        self.assertEqual(text.count('contents: write'),1);self.assertIn('cancel-in-progress: false',text)
        self.assertIn("if: inputs.mode == 'publish'",text);self.assertNotIn('git push',text)


if __name__=='__main__':unittest.main()

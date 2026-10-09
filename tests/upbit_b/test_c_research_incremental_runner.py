"""Actions source hydration fixtures for V1.6; no network mutations."""
import unittest
from unittest.mock import Mock,patch
from upbit_c import research_incremental as I, research_incremental_runner as CLI
import test_c_research_actions_storage as fixtures


class RunnerTests(unittest.TestCase):
    setUp=fixtures.ActionsStorageTests.setUp
    tearDown=fixtures.ActionsStorageTests.tearDown
    export=fixtures.ActionsStorageTests.export
    def baseline(self):
        r=I.R.prepare(self.store,self.m,self.root/'base')['package']
        raw=(self.root/'base'/r['asset_name']).read_bytes()
        self.catalog=I.initial_catalog('o/r',1,1,r['sha256'])
        self.fetch=lambda e:raw
        self.client=Mock(repository='o/r',token='FAKE_FIXTURE_TOKEN')
    def run_prepare(self,run):
        with patch.object(CLI.T,'GitHubArtifacts',return_value=self.source):
            return CLI.prepare_source(self.client,run,self.catalog,self.fetch,'2026-10-09T00:00:00Z',self.root/'out',True)
    def test_existing_source_noop(self):
        self.baseline();r=self.run_prepare('10')
        self.assertEqual(r['status'],'NOOP');self.assertFalse(r['market_scan_executed'])
    def test_direct_child_metadata_package(self):
        self.baseline();child=self.store.commit('11',{},('10',self.m['sha256']))
        self.export(self.store,child,checkpoint=True)
        r=self.run_prepare('11');self.assertEqual(r['package']['kind'],'METADATA_ONLY')
    def test_skipped_parent_index_hydrated(self):
        self.baseline();mid=self.store.commit('11',{},('10',self.m['sha256']))
        self.export(self.store,mid,checkpoint=True)
        child=self.store.commit('12',{},('11',mid['sha256']));self.export(self.store,child,checkpoint=True)
        r=self.run_prepare('12');self.assertEqual(r['status'],'PREPARED')
        raw=(self.root/'out'/r['package']['asset_name']).read_bytes()
        m,_=I.unpack(raw,r['package']['sha256'],True)
        self.assertEqual(m['lineage_proofs'],[{'run_id':'11','sha256':mid['sha256']}])
    def test_missing_source_never_initializes(self):
        self.baseline()
        with self.assertRaises(ValueError):self.run_prepare('999')
        self.assertFalse((self.root/'out').exists())
    def test_expired_artifact_never_uses_partial_data(self):
        self.baseline();self.source.expired.add(('10',CLI.T.INDEX_NAME))
        with self.assertRaises(ValueError):self.run_prepare('10')
        self.assertFalse((self.root/'out').exists())


if __name__=='__main__':unittest.main()

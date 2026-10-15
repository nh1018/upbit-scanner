from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import analysis_health as H
import test_snapshot_poc01 as F
from btc_anytime import analysis_snapshot as S, snapshot_poc02 as C


class ConnectionTests(unittest.TestCase):
    setUp = F.PoCTests.setUp
    tearDown = F.PoCTests.tearDown
    persist = F.PoCTests.persist
    result = F.PoCTests.result

    def health(self, s, now):
        # Isolate original Health freshness/alignment logic. Source-evidence
        # validation has its separate unmodified regression suite.
        path = self.repo/S.TARGET; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(S.text(s), encoding='utf-8')
        with patch.object(H, '_btc_active_evidence', return_value={'valid':True,'blocking_reasons':[]}):
            return H.btc_health(self.repo, now, 45)

    def run_connection(self, backend, **kw):
        return C.connect(backend, event=kw.pop('event','ENTRY_COMPLETED'),
                         now_ms=kw.pop('now_ms',self.T), health=kw.pop('health',self.health),
                         expected_code='fixture-code', **kw)

    def test_fresh_main_not_dispatch_checkout(self):
        stale = self.result(); stale['entry']['upstream_direction_decision_id']='old'
        stale['entry']['matches_snapshot_direction']=False
        stale=S.sealed({k:v for k,v in stale.items() if k!='payload_sha256'})
        b=C.MemoryBackend(stale); b.advance(self.result())
        out=self.run_connection(b)
        self.assertEqual(out['status'],'PUBLISHED_IN_MEMORY');self.assertEqual(out['source_revision'],1)

    def test_duplicate_event_is_noop(self):
        b=C.MemoryBackend(self.result())
        self.run_connection(b)
        self.assertEqual(self.run_connection(b)['status'],'NOOP');self.assertEqual(len(b.writes),1)

    def test_performance_event_does_not_read_or_publish(self):
        b=C.MemoryBackend(self.result())
        self.assertEqual(self.run_connection(b,event='PERFORMANCE_COMPLETED')['status'],'CONTEXT_ONLY_NO_PROJECTION')
        self.assertEqual((b.reads,len(b.writes)),(0,0))

    def test_mixed_degraded_diagnostic_only(self):
        self.e['upstream_direction_decision_id']='old';self.persist();b=C.MemoryBackend(self.result())
        out=self.run_connection(b)
        self.assertEqual(out['status'],'DIAGNOSTIC_ONLY');self.assertEqual(out['health']['status'],'DEGRADED')
        self.assertFalse(out['consumer_allowed']);self.assertFalse(b.writes)

    def test_missing_entry_diagnostic(self):
        for p in (self.repo/'output_entry/btc_anytime/v1/records').glob('*.json'):p.unlink()
        out=self.run_connection(C.MemoryBackend(self.result()))
        self.assertEqual(out['status'],'DIAGNOSTIC_ONLY');self.assertIn('ENTRY_UNAVAILABLE',out['reasons'])

    def test_upstream_failure_preserved(self):
        b=C.MemoryBackend(self.result());out=self.run_connection(b,upstream_success=False)
        self.assertEqual(out['status'],'DIAGNOSTIC_ONLY');self.assertIn('ENTRY_WORKFLOW_NOT_SUCCESSFUL',out['reasons'])
        self.assertFalse(b.writes)

    def test_stale_health_blocks_even_same_projection(self):
        s=self.result();b=C.MemoryBackend(s,previous=s)
        out=self.run_connection(b,now_ms=self.T+3600000)
        self.assertEqual(out['health']['status'],'STALE');self.assertFalse(b.writes)

    def test_actual_health_gate_failure_blocks_coherent(self):
        b=C.MemoryBackend(self.result())
        out=self.run_connection(b,health=lambda s,t:{'status':'DEGRADED','usable_for_current_analysis':False})
        self.assertFalse(out['consumer_allowed']);self.assertFalse(b.writes)

    def test_invalid_health_blocks(self):
        out=self.run_connection(C.MemoryBackend(self.result()),health=lambda s,t:{'status':'INVALID','usable_for_current_analysis':False})
        self.assertEqual(out['status'],'DIAGNOSTIC_ONLY')

    def test_race_discards_old_projection(self):
        b=C.MemoryBackend(self.result())
        def race(x):
            x.before_cas=None
            new=deepcopy(x.snapshot);new['entry']['matches_snapshot_direction']=False
            x.advance(S.sealed({k:v for k,v in new.items() if k!='payload_sha256'}))
        b.before_cas=race
        out=self.run_connection(b)
        self.assertEqual(out['status'],'DIAGNOSTIC_ONLY');self.assertEqual(out['attempt'],2)
        self.assertFalse(b.writes)

    def test_two_writers_publish_once(self):
        b=C.MemoryBackend(self.result())
        def other_writer(x):
            x.before_cas=None;self.run_connection(x)
        b.before_cas=other_writer
        out=self.run_connection(b)
        self.assertEqual(out['status'],'NOOP');self.assertEqual(out['attempt'],2);self.assertEqual(len(b.writes),1)

    def test_lost_ack_requeries_and_noops(self):
        b=C.MemoryBackend(self.result());b.lose_ack=True
        out=self.run_connection(b)
        self.assertEqual(out['status'],'NOOP');self.assertEqual(len(b.writes),1)

    def test_head_moves_between_snapshot_and_health(self):
        b=C.MemoryBackend(self.result())
        def newer_inputs(x):
            s=deepcopy(x.snapshot);s['entry']['matches_snapshot_direction']=False
            x.advance(S.sealed({k:v for k,v in s.items() if k!='payload_sha256'}))
        b.after_cas=newer_inputs
        out=self.run_connection(b)
        self.assertEqual(out['status'],'SUPERSEDED_AFTER_PUBLICATION')
        self.assertFalse(out['consumer_allowed'])
        self.assertEqual(out['health']['status'],'DEGRADED')

    def test_bounded_conflict_stop(self):
        b=C.MemoryBackend(self.result());b.before_cas=lambda x:x.advance(x.snapshot)
        out=self.run_connection(b)
        self.assertEqual(out['status'],'CONFLICT_EXHAUSTED');self.assertEqual(b.reads,3);self.assertFalse(b.writes)

    def test_old_publication_cannot_overwrite_new(self):
        s=self.result();old=deepcopy(s);old['generated_at_utc']='2000-01-01T00:00:00Z'
        old=S.sealed({k:v for k,v in old.items() if k!='payload_sha256'})
        self.assertEqual(self.run_connection(C.MemoryBackend(old,previous=s))['status'],'INVALID')

    def test_changed_execution_code_refused(self):
        b=C.MemoryBackend(self.result(),code='changed')
        self.assertEqual(self.run_connection(b)['status'],'INVALID');self.assertFalse(b.writes)

    def test_backend_failure_not_silent(self):
        b=C.MemoryBackend(self.result())
        with patch.object(b,'read_latest',side_effect=OSError('read failed')):
            out=self.run_connection(b)
        self.assertEqual(out['status'],'INVALID');self.assertIn('read failed',out['reason'])

    def test_rollback_original_projection_unchanged(self):
        original=self.result();b=C.MemoryBackend(original);self.run_connection(b)
        self.assertEqual(S.text(self.result()),S.text(original))
        self.assertEqual(S.text(b.writes[0]['snapshot']),S.text(original))

    def test_workflow_proposal_is_readonly_and_keeps_failure_artifacts(self):
        root=Path(__file__).resolve().parents[2]
        text=(root/'docs/poc02/btc-analysis-snapshot.connection.yml').read_text(encoding='utf-8')
        self.assertIn('workflows: [BTC Entry Timing Engine V1]',text)
        self.assertIn('contents: read',text)
        self.assertIn('if: always()',text)
        self.assertIn('ENTRY_WORKFLOW_NOT_SUCCESSFUL',text)
        self.assertNotIn('contents: write',text)
        self.assertNotIn('git push',text)
        self.assertFalse((root/'.github/workflows/btc-analysis-snapshot-poc02.yml').exists())


if __name__=='__main__':unittest.main()

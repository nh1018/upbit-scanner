"""PoC offline tests reuse the original projection fixtures/engines."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

import test_analysis_snapshot as Fixtures
from btc_anytime import analysis_snapshot as S, snapshot_poc01 as P
from btc_anytime.features.engine import digest
from btc_anytime.integrity import DURATIONS


class PoCTests(unittest.TestCase):
    def setUp(self):
        Fixtures.AnalysisTests.setUp(self)
        self.d['trigger_15m'] = {'candle_open_time_ms': self.e['trigger_time']}
        self.d.pop('decision_id'); self.d['decision_id'] = digest(self.d)
        self.e['upstream_direction_decision_id'] = self.d['decision_id']
        self.ev['trigger_time'] = self.e['trigger_time']
        self.ev['upstream_direction_decision_id'] = self.d['decision_id']
        self.ev.pop('entry_evaluation_id'); self.ev['entry_evaluation_id'] = digest(self.ev)
        self.persist()

    tearDown = Fixtures.AnalysisTests.tearDown
    persist = Fixtures.AnalysisTests.persist
    result = Fixtures.AnalysisTests.result

    def preview(self, event='ENTRY_COMPLETED', **kw):
        return P.preview(self.repo, event=event, T=kw.pop('T', self.T), **kw)

    def test_no_entry_is_valid_and_exact(self):
        original = self.result()
        out = self.preview()
        self.assertEqual(out['action'], 'EMIT_COHERENT')
        self.assertEqual(S.text(out['snapshot']), S.text(original))
        self.assertFalse(out['trade_authorization'])

    def test_wait_and_candidate_preserved(self):
        for state in ('WAIT', 'ENTRY_CANDIDATE'):
            self.ev['entry_state'] = state
            self.ev.pop('entry_evaluation_id'); self.ev['entry_evaluation_id'] = digest(self.ev)
            self.persist()
            out = self.preview()
            self.assertEqual(out['action'], 'EMIT_COHERENT')
            self.assertEqual(out['snapshot']['entry']['evaluation'], self.ev)

    def test_mixed_direction_blocked_with_original_warning(self):
        self.e['upstream_direction_decision_id'] = 'old'
        self.persist()
        out = self.preview()
        self.assertEqual(out['action'], 'BLOCKED')
        self.assertIn('ENTRY_DIRECTION_MISMATCH', out['snapshot']['warnings'])
        self.assertEqual(out['snapshot']['snapshot_status'], 'PARTIAL')

    def test_lagged_boundary_blocked(self):
        self.e['trigger_time'] -= DURATIONS['15m']; self.persist()
        self.assertIn('ENTRY_LAGS_MARKET', self.preview()['reasons'])

    def test_missing_entry_visible(self):
        for p in (self.repo/'output_entry/btc_anytime/v1/records').glob('*.json'):
            p.unlink()
        out = self.preview('AUDIT')
        self.assertEqual(out['action'], 'BLOCKED')
        self.assertIn('ENTRY_UNAVAILABLE', out['reasons'])

    def test_failed_entry_not_emitted(self):
        self.ev['execution_status'] = 'FAILED_INPUT'
        self.ev.pop('entry_evaluation_id'); self.ev['entry_evaluation_id'] = digest(self.ev)
        self.e['operational_status'] = 'FAILED_INPUT'; self.persist()
        self.assertIn('ENTRY_NOT_EVALUATED', self.preview()['reasons'])

    def test_delayed_entry_then_recovery(self):
        self.e['upstream_direction_decision_id'] = 'old'; self.persist()
        self.assertEqual(self.preview()['action'], 'BLOCKED')
        self.e['upstream_direction_decision_id'] = self.d['decision_id']; self.persist()
        self.assertEqual(self.preview()['action'], 'EMIT_COHERENT')

    def test_performance_event_never_builds(self):
        with patch.object(S, 'build', side_effect=AssertionError('projection')):
            self.assertEqual(self.preview('PERFORMANCE_COMPLETED')['action'], 'CONTEXT_ONLY_NO_PROJECTION')

    def test_duplicate_projection_noop(self):
        self.assertEqual(self.preview(previous=self.result())['action'], 'NOOP')

    def test_existing_latest_is_readonly_noop(self):
        S.atomic_write(self.repo, self.result())
        path = self.repo/S.TARGET; original = path.read_bytes()
        self.assertEqual(self.preview()['action'], 'NOOP')
        self.assertEqual(path.read_bytes(), original)

    def test_corrupt_existing_latest_blocks(self):
        path = self.repo/S.TARGET; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}', encoding='utf-8')
        self.assertEqual(self.preview()['action'], 'INVALID')

    def test_performance_only_change_not_new_signal(self):
        old = self.result(); new = deepcopy(old)
        new['performance_context'] = {'available': False, 'note': 'context refresh fixture'}
        new = S.sealed({k:v for k,v in new.items() if k != 'payload_sha256'})
        self.assertEqual(P.assess(new, now_ms=self.T, previous=old)['action'], 'NOOP')

    def test_stale_checked_before_deduplication(self):
        old = self.result()
        self.assertEqual(P.assess(old, now_ms=self.T+3600000, previous=old)['action'], 'BLOCKED')
        self.assertEqual(self.preview(T=self.T+3600000)['snapshot']['snapshot_status'], 'STALE')

    def test_corruption_is_invalid_not_missing(self):
        p = next((self.repo/'output_entry/btc_anytime/v1/records').glob('*.json'))
        p.write_text('{}', encoding='utf-8')
        self.assertEqual(self.preview()['action'], 'INVALID')

    def test_build_failure_not_hidden(self):
        with patch.object(S, 'build', side_effect=OSError('fixture failure')):
            out = self.preview()
        self.assertEqual(out['action'], 'INVALID')
        self.assertIn('fixture failure', out['reasons'][0])

    def test_readonly_no_files_changed(self):
        before = {p.relative_to(self.repo):p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}
        self.preview(); self.preview('PERFORMANCE_COMPLETED')
        after = {p.relative_to(self.repo):p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_wrong_direction_boundary_blocked(self):
        s = self.result(); s['direction']['trigger_15m']['candle_open_time_ms'] -= DURATIONS['15m']
        s = S.sealed({k:v for k,v in s.items() if k != 'payload_sha256'})
        self.assertIn('DIRECTION_LAGS_MARKET', P.assess(s, now_ms=self.T)['reasons'])

    def test_unknown_event_refused(self):
        with self.assertRaises(ValueError): self.preview('UNAPPROVED')


if __name__ == '__main__': unittest.main()

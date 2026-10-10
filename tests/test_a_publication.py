"""Synthetic publication evidence; no historical A signal creation."""
import base64
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from strategy_evaluation import a_publication as P
from strategy_evaluation.contracts import sha

HEAD = 'a' * 40
STAMP = '2026-10-10T10:00:00+09:00'
START = 1791594000000


def raw(stamp=STAMP, **changes):
    p = {'scanner_version': '1.3-cloud', 'scanned_count': 200, 'candidate_count': 1,
        'generated_at_kst': stamp, 'candidates': [{'upbit_market': 'KRW-BTC',
        'state': '관찰', 'warning': False, 'scan_time_kst': stamp,
        'upbit_timestamp_ms': START, 'upbit_price': 100, 'score': 70}]}
    p.update(changes)
    return json.dumps(p, ensure_ascii=False).encode()


def blob(b):
    return hashlib.sha1(b'blob ' + str(len(b)).encode() + b'\0' + b).hexdigest()


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.baseline = raw('2026-10-10T09:00:00+09:00')
        self.session = P.arm(self.baseline, HEAD, START - 1000)
        self.raw = raw()

    def receipt(self, **kwargs):
        args = dict(session=self.session, raw=self.raw, head=HEAD,
            blob=blob(self.raw), request_started=START + 1000, response_received=START + 2000)
        args.update(kwargs)
        return P.witness(**args)

    def test_availability_is_access_success_not_scan_start(self):
        e = self.receipt(); s = P.signals_from_receipt(self.session, e, self.raw)[0]
        self.assertEqual(s['signal_observed_at'], START + 2000)
        self.assertTrue(s['performance_eligible'])
        self.assertEqual(s['evaluation_anchor'], START + 3600000)
        self.assertIsNone(e['publisher_push_succeeded_at'])
        self.assertIsNone(e['result_generation_completed_at'])

    def test_unchanged_baseline_is_noop(self):
        self.assertIsNone(self.receipt(raw=self.baseline, blob=blob(self.baseline)))

    def test_old_changed_snapshot_cannot_be_retroactive(self):
        b = raw('2026-10-10T09:30:00+09:00')
        with self.assertRaises(ValueError): self.receipt(raw=b, blob=blob(b))

    def test_clock_regression(self):
        for changes in ({'request_started': START - 2000}, {'response_received': START}):
            with self.assertRaises(ValueError): self.receipt(**changes)

    def test_future_scan_clock(self):
        b = raw('2026-10-10T11:00:00+09:00')
        with self.assertRaises(ValueError): self.receipt(raw=b, blob=blob(b))

    def test_wrong_blob(self):
        with self.assertRaises(ValueError): self.receipt(blob='f' * 40)

    def test_incomplete_output(self):
        for change in ({'candidate_count': 2}, {'scanned_count': 0}, {'scanner_version': 'other'}):
            b = raw(**change)
            with self.assertRaises(ValueError): self.receipt(raw=b, blob=blob(b))

    def test_mixed_generation(self):
        p = json.loads(self.raw); p['candidates'][0]['scan_time_kst'] = '2026-10-10T09:00:00+09:00'
        b = json.dumps(p).encode()
        with self.assertRaises(ValueError): self.receipt(raw=b, blob=blob(b))

    def test_source_future_clock(self):
        p = json.loads(self.raw); p['candidates'][0]['upbit_timestamp_ms'] = START + 10000
        b = json.dumps(p).encode()
        with self.assertRaises(ValueError): self.receipt(raw=b, blob=blob(b))

    def test_tampered_receipt(self):
        e = self.receipt(); e['http_status'] = 500
        with self.assertRaises(ValueError): P.signals_from_receipt(self.session, e, self.raw)

    def test_resealed_false_evidence(self):
        e = self.receipt(); e.pop('event_id'); e['http_status'] = 500; e = P.sealed(e)
        with self.assertRaises(ValueError): P.signals_from_receipt(self.session, e, self.raw)

    def test_append_only_retry_first_receipt_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            P.record(root, self.session)
            e = self.receipt(); self.assertEqual(P.record(root, e, self.raw), 'CREATED')
            before = {p.name: p.read_bytes() for p in Path(root).iterdir()}
            later = self.receipt(request_started=START + 3000, response_received=START + 4000)
            self.assertEqual(P.record(root, later, self.raw), 'REPLAY_NOOP')
            self.assertEqual(before, {p.name: p.read_bytes() for p in Path(root).iterdir()})

    def test_missing_baseline(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError): P.record(root, self.receipt(), self.raw)

    def test_missing_source_fails_replay(self):
        with tempfile.TemporaryDirectory() as root:
            P.record(root, self.session); e = self.receipt(); P.record(root, e, self.raw)
            (Path(root) / (sha(self.raw) + '.source')).unlink()
            with self.assertRaises(ValueError): P.record(root, e, self.raw)

    def test_partial_event_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'damaged.json').write_bytes(b'{')
            with self.assertRaises(ValueError): P.record(root, self.session)

    def test_network_failure_no_receipt(self):
        def fail(url): raise OSError('network unavailable')
        with self.assertRaises(OSError): P.read_current(fail)

    def test_pinned_api_reads(self):
        calls = []
        def get(url):
            calls.append(url)
            if '/git/ref/' in url: return {'object': {'sha': HEAD}}
            return {'encoding': 'base64', 'path': P.PATH, 'sha': blob(self.raw),
                'content': base64.b64encode(self.raw).decode()}
        self.assertEqual(P.read_current(get)[:3], (self.raw, HEAD, blob(self.raw)))
        self.assertTrue(calls[1].endswith('?ref=' + HEAD))

    def test_input_immutable(self):
        before = copy.deepcopy((self.session, self.raw))
        e = self.receipt(); P.signals_from_receipt(self.session, e, self.raw)
        self.assertEqual(before, (self.session, self.raw))

    def test_same_scan_conflicting_publication(self):
        with tempfile.TemporaryDirectory() as root:
            P.record(root, self.session); P.record(root, self.receipt(), self.raw)
            p = json.loads(self.raw); p['candidates'][0]['score'] = 71
            b = json.dumps(p).encode()
            e = self.receipt(raw=b, blob=blob(b), request_started=START + 3000,
                response_received=START + 4000)
            with self.assertRaises(ValueError): P.record(root, e, b)

    def test_later_poll_rejects_missing_stored_source(self):
        with tempfile.TemporaryDirectory() as root:
            P.record(root, self.session); P.record(root, self.receipt(), self.raw)
            (Path(root) / (sha(self.raw) + '.source')).unlink()
            e = self.receipt(request_started=START + 3000, response_received=START + 4000)
            with self.assertRaises(ValueError): P.record(root, e, self.raw)

    def test_interrupted_writer_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / '.lock').mkdir()
            with self.assertRaises(ValueError): P.record(root, self.session)
            self.assertTrue((Path(root) / '.lock').exists())


if __name__ == '__main__': unittest.main()

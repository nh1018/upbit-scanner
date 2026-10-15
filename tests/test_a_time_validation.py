"""Offline NTP packets are fixtures, not authenticated production evidence."""
import copy
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from strategy_evaluation import time_validation as N, a_publication_v12 as V, a_publication as A
from test_a_publication import raw, blob, HEAD, START


def sample(server, start, offset=.3, delay=.02):
    nonce=N.stamp(start)
    p=bytearray(48);p[0]=0x24;p[1]=2;p[3]=(-20)&255
    p[4:8]=struct.pack('!i', int(.01*65536));p[8:12]=struct.pack('!I',int(.01*65536))
    p[24:32]=nonce;p[32:40]=N.stamp(start+delay/2+offset)
    p[40:48]=N.stamp(start+delay/2+offset)
    return dict(N.parse(bytes(p),nonce,start,start+delay,delay),server=server,
        ip='1.1.1.1' if server==N.SERVERS[0] else '2.2.2.2',
        monotonic_started=start,monotonic_ended=start+delay)


def evidence(start, offsets=(.3,.31), delay=.02):
    index=iter(range(2))
    return N.measure(lambda server: sample(server,start+next(index)*.1,offsets[N.SERVERS.index(server)],delay))


class TimeTests(unittest.TestCase):
    def test_normal_and_negative_offset(self):
        for offsets in ((.3,.31),(-.3,-.31),(0,0)):
            e=evidence(START/1000,offsets);self.assertEqual(N.verify(e)['status'],'PASS')

    def test_over_one_second(self):
        self.assertEqual(evidence(START/1000,(1.01,1.01))['status'],'FAIL')

    def test_near_limit_uncertainty_fails_not_average(self):
        self.assertEqual(evidence(START/1000,(.99,.98))['status'],'FAIL')

    def test_high_delay(self):
        self.assertEqual(evidence(START/1000,delay=.8)['status'],'FAIL')

    def test_server_disagreement(self):
        self.assertEqual(evidence(START/1000,(.3,-.3))['status'],'FAIL')

    def test_timeout(self):
        def probe(server):raise TimeoutError('fixture timeout')
        e=N.measure(probe);self.assertEqual(e['status'],'FAIL');self.assertEqual(len(e['errors']),2)

    def test_wrong_originate_and_truncated(self):
        s=sample(N.SERVERS[0],START/1000)
        for p in (bytes.fromhex(s['response_hex'])[:30],bytes(48)):
            with self.assertRaises(ValueError):N.parse(p,bytes.fromhex(s['request_transmit_hex']),s['t1_unix'],s['t4_unix'],.02)

    def test_unsynchronized_and_kiss_of_death(self):
        s=sample(N.SERVERS[0],START/1000)
        for offset,value in ((0,0xe4),(1,0),(0,0x23)):
            p=bytearray.fromhex(s['response_hex']);p[offset]=value
            with self.assertRaises(ValueError):N.parse(p,bytes.fromhex(s['request_transmit_hex']),s['t1_unix'],s['t4_unix'],.02)

    def test_clock_reverse_and_step(self):
        s=sample(N.SERVERS[0],START/1000)
        for t4 in (s['t1_unix']-1,s['t1_unix']+1):
            with self.assertRaises(ValueError):N.parse(bytes.fromhex(s['response_hex']),bytes.fromhex(s['request_transmit_hex']),s['t1_unix'],t4,.02)

    def test_resealed_calculation_corruption(self):
        e=evidence(START/1000);e['samples'][0]['offset_seconds']=0;e.pop('evidence_id');e['evidence_id']=N.digest(e)
        with self.assertRaises(ValueError):N.verify(e)

    def test_independence_ips(self):
        e=evidence(START/1000);e['samples'][1]['ip']=e['samples'][0]['ip'];e.pop('evidence_id');e['evidence_id']=N.digest(e)
        with self.assertRaises(ValueError):N.verify(e)

    def test_bracket_and_stale_evidence(self):
        pre=evidence(START/1000);post=evidence(START/1000+2)
        self.assertLess(N.bracket(pre,post,START+500,START+1000,2.12),1)
        with self.assertRaises(ValueError):N.bracket(pre,post,START+500,START+1000,200)
        with self.assertRaises(ValueError):N.bracket(pre,post,START-500,START+1000,2.12)

    def test_bracket_jump(self):
        with self.assertRaises(ValueError):N.bracket(evidence(START/1000),evidence(START/1000+2),START+500,START+1000,1.8)

    def baseline(self):
        pre=evidence(START/1000-3);post=evidence(START/1000-1)
        return V.envelope(A.arm(raw('2026-10-10T09:00:00+09:00'),HEAD,START-2000),pre,post,START-2500,START-2000,2.12)

    def receipt(self, session, shift=0):
        pre=evidence(START/1000+1+shift);post=evidence(START/1000+3+shift)
        inner=A.witness(session['publication'],raw(),HEAD,blob(raw()),START+1500+shift*1000,START+2000+shift*1000)
        e=V.envelope(inner,pre,post,START+1500+shift*1000,START+2000+shift*1000,2.12)
        e.pop('event_id');e['session_id']=session['event_id'];e['event_id']=N.digest(e)
        return e

    def test_adapter_preserves_actual_received_clock(self):
        b=self.baseline();r=self.receipt(b);s=V.signals(b,r,raw())[0]
        self.assertEqual(s['signal_observed_at'],START+2000);self.assertEqual(s['cohort'],V.VERSION)

    def test_expired_write_no_files(self):
        with tempfile.TemporaryDirectory() as root,patch.object(V.time,'time',return_value=START/1000+100):
            with self.assertRaises(ValueError):V.record(root,self.baseline())
            self.assertEqual(list(Path(root).iterdir()),[])

    def test_duplicate_observation_noop_and_legacy_untouched(self):
        b=self.baseline();r=self.receipt(b)
        with tempfile.TemporaryDirectory() as root:
            old=Path(root)/'legacy-v11.json';old.write_bytes(b'untouched')
            with patch.object(V.time,'time',return_value=START/1000):V.record(root,b)
            with patch.object(V.time,'time',return_value=START/1000+4):self.assertEqual(V.record(root,r,raw()),'CREATED')
            before={p.name:p.read_bytes() for p in (Path(root)/'v12').iterdir()}
            with patch.object(V.time,'time',return_value=START/1000+5):self.assertEqual(V.record(root,self.receipt(b,1),raw()),'REPLAY_NOOP')
            self.assertEqual(old.read_bytes(),b'untouched')
            self.assertEqual(before,{p.name:p.read_bytes() for p in (Path(root)/'v12').iterdir()})

    def test_fail_clock_never_reads_or_writes_github(self):
        from types import SimpleNamespace
        with patch.object(N,'measure',return_value={'status':'FAIL'}),patch.object(A,'read_current') as read:
            with self.assertRaises(ValueError):V.run(SimpleNamespace(mode='arm',record_root='unused',session=None))
            read.assert_not_called()

    def test_source_namespace_protected(self):
        with patch.object(V.time,'time',return_value=START/1000):
            with self.assertRaises(ValueError):V.record(Path(V.__file__).parent,self.baseline())

    def test_post_failure_creates_no_baseline(self):
        from types import SimpleNamespace
        with patch.object(N,'measure',side_effect=[evidence(START/1000-3),{'status':'FAIL'}]),patch.object(A,'read_current',return_value=(raw(),HEAD,blob(raw()),START,START+1000)),patch.object(V,'record') as write:
            with self.assertRaises(ValueError):V.run(SimpleNamespace(mode='arm',record_root='unused',session=None))
            write.assert_not_called()

    def test_expired_bracket_even_with_good_servers(self):
        with self.assertRaises(ValueError):N.bracket(evidence(START/1000),evidence(START/1000+121),START+500,START+1000,121.12)


if __name__=='__main__':unittest.main()

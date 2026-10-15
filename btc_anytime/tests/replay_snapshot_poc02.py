"""Stored evidence replay: real Health validation, socket blocked, memory CAS."""
from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import socket
import tempfile
from unittest.mock import patch

import analysis_health as H
from btc_anytime import analysis_snapshot as S, snapshot_poc02 as C
from btc_anytime.integrity import utc_ms


def replay(archive_path, evidence_root, manifest_path):
    archive=json.loads(Path(archive_path).read_text(encoding='utf-8'))
    manifest=json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    assert archive['source_pin']==manifest['source_pin']
    root=Path(evidence_root)
    for p,h in manifest['files'].items():
        assert hashlib.sha256((root/p).read_bytes()).hexdigest()==h
    actions=Counter(); healths=Counter(); unchanged=0; boundaries=set()
    with tempfile.TemporaryDirectory(prefix='btc-poc02-offline-') as temp:
        repo=Path(temp)/'evidence';shutil.copytree(root,repo)
        def health(s,T):
            p=repo/S.TARGET;p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text(S.text(s),encoding='utf-8',newline='\n')
            return H.btc_health(repo,T,45)
        backend=None
        with patch.object(socket,'socket',side_effect=AssertionError('offline network blocked')):
            for row in sorted(archive['rows'],key=lambda r:(r['commit_at'],r['commit'])):
                s=row['snapshot'];before=S.text(s)
                assert hashlib.sha256(before.encode()).hexdigest()==row['source_bytes_sha256']
                boundaries.add(s['direction']['trigger_15m']['candle_open_time_ms'])
                if backend is None:backend=C.MemoryBackend(s)
                else:backend.advance(s)
                T=utc_ms(s['generated_at_utc'])
                out=C.connect(backend,event='ENTRY_COMPLETED',now_ms=T,health=health,expected_code='fixture-code')
                actions[out['status']]+=1;healths[out['health']['status']]+=1
                assert S.text(s)==before;unchanged+=1
                writes=len(backend.writes);reads=backend.reads
                context=C.connect(backend,event='PERFORMANCE_COMPLETED',now_ms=T,health=health,expected_code='fixture-code')
                assert context['status']=='CONTEXT_ONLY_NO_PROJECTION'
                assert (len(backend.writes),backend.reads)==(writes,reads)
            writes=len(backend.writes)
            stale=C.connect(backend,event='AUDIT',now_ms=T+3600000,health=health,expected_code='fixture-code')
            assert stale['health']['status']=='STALE' and not stale['consumer_allowed']
            source=repo/s['entry']['source_reference']['file'];original=source.read_bytes()
            source.write_bytes(original+b' ')
            invalid=C.connect(backend,event='AUDIT',now_ms=T,health=health,expected_code='fixture-code')
            assert invalid['health']['status']=='INVALID' and not invalid['consumer_allowed']
            source.write_bytes(original)
            assert len(backend.writes)==writes
        for p,h in manifest['files'].items():
            assert hashlib.sha256((repo/p).read_bytes()).hexdigest()==h
    return {'source_pin':archive['source_pin'],'snapshot_versions':len(archive['rows']),
            'boundary_count':len(boundaries),'actions':dict(actions),'health_statuses':dict(healths),
            'snapshot_bytes_preserved':unchanged,'source_hashes_verified':len(manifest['files']),
            'in_memory_publications':len(backend.writes),'performance_events_no_projection':len(archive['rows']),
            'stale_blocked':True,'source_tamper_invalid':True,'network_blocked':True,
            'health_active_evidence_mocked':False,'production_writes':0,'actual_latency_measured':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',required=True);p.add_argument('--evidence-root',required=True)
    p.add_argument('--manifest',required=True);a=p.parse_args()
    print(json.dumps(replay(a.archive,a.evidence_root,a.manifest),sort_keys=True))

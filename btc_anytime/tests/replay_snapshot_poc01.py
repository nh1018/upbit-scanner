"""Replay a pre-acquired JSON snapshot archive with socket access disabled."""
import argparse
from collections import Counter
import json
import hashlib
from pathlib import Path
import socket
from unittest.mock import patch

from btc_anytime import analysis_snapshot as S, snapshot_poc01 as P
from btc_anytime.integrity import utc_ms


def replay(archive):
    previous = None
    counts = Counter(); boundaries = set(); emitted = set(); mixed = set()
    immutable = 0
    with patch.object(socket, 'socket', side_effect=AssertionError('offline replay network')):
        for row in sorted(archive['rows'], key=lambda r:(r['commit_at'], r['commit'])):
            s = row['snapshot']; before = S.text(s)
            assert hashlib.sha256(before.encode('utf-8')).hexdigest() == row['source_bytes_sha256']
            boundary = s['direction']['trigger_15m']['candle_open_time_ms']
            boundaries.add(boundary)
            if not s['entry']['matches_snapshot_direction']: mixed.add(boundary)
            out = P.assess(s, now_ms=utc_ms(s['generated_at_utc']), previous=previous)
            counts[out['action']] += 1
            if out['action'] == 'EMIT_COHERENT':
                emitted.add(boundary); previous = s
            assert S.text(s) == before
            assert S.text(out['snapshot']) == before
            immutable += 1
            assert P.preview(None, event='PERFORMANCE_COMPLETED')['action'] == 'CONTEXT_ONLY_NO_PROJECTION'
    return {'source_pin': archive['source_pin'], 'snapshot_versions': len(archive['rows']),
            'boundary_count': len(boundaries), 'mixed_boundary_count': len(mixed),
            'emitted_boundary_count': len(emitted), 'actions': dict(counts),
            'exact_snapshot_bytes_preserved': immutable, 'network_blocked': True,
            'signal_recomputed': False, 'production_latency_measured': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True)
    args = parser.parse_args()
    print(json.dumps(replay(json.loads(Path(args.archive).read_text(encoding='utf-8'))), sort_keys=True))

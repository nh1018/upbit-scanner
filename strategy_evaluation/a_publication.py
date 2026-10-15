"""Prospective, manual GitHub publication witness. Never runs the A scanner."""
import argparse
import base64
import hashlib
import json
import os
import re
import time
import urllib.request
from pathlib import Path

from upbit_b.feature_contracts import digest, dumps
from upbit_c.research_archive import isolated
from upbit_c.research_release import no_symlinks
from .adapters import a_snapshot, seal_signal
from .contracts import clock, sha, HOUR

SCHEMA = 'a-publication-evidence-1.1'
REPOSITORY = 'nh1018/upbit-scanner'
PATH = 'output/latest_scan.json'
API = 'https://api.github.com/repos/' + REPOSITORY


def now():
    return time.time_ns() // 1000000


def sealed(event):
    event = dict(event)
    event['event_id'] = digest(event)
    return event


def validate(event):
    if event.get('schema_version') != SCHEMA:
        raise ValueError('unknown publication contract')
    if event.get('event_id') != digest({k: v for k, v in event.items() if k != 'event_id'}):
        raise ValueError('publication evidence hash mismatch')
    return event


def arm(raw, head, observed_at):
    """Baseline only. This snapshot can never become an eligible signal."""
    a_snapshot(raw, sha(raw), 'baseline-only')
    if not re.fullmatch('[0-9a-f]{40}', head):
        raise ValueError('pinned Git revision required')
    return sealed({'schema_version': SCHEMA, 'kind': 'ARMED_BASELINE',
        'armed_at_utc_ms': clock(observed_at), 'baseline_sha256': sha(raw),
        'baseline_revision': head, 'repository': REPOSITORY, 'path': PATH})


def witness(session, raw, head, blob, request_started, response_received):
    validate(session)
    if session['kind'] != 'ARMED_BASELINE':
        raise ValueError('prospective baseline required')
    clock(request_started); clock(response_received)
    if not session['armed_at_utc_ms'] <= request_started <= response_received:
        raise ValueError('clock regression')
    if not re.fullmatch('[0-9a-f]{40}', head):
        raise ValueError('pinned Git revision required')
    actual_blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if blob != actual_blob:
        raise ValueError('GitHub blob content mismatch')
    if sha(raw) == session['baseline_sha256']:
        return None
    signals = a_snapshot(raw, sha(raw), API + '/contents/' + PATH + '?ref=' + head)
    payload = json.loads(raw)
    if type(payload.get('scanned_count')) is not int or not 0 < payload['scanned_count'] or len(signals) > payload['scanned_count']:
        raise ValueError('incomplete scan result')
    if any(r['scan_time_kst'] != payload['generated_at_kst'] for r in payload['candidates']):
        raise ValueError('mixed scan generation')
    from datetime import datetime
    start = datetime.fromisoformat(payload['generated_at_kst'])
    if start.utcoffset() is None:
        raise ValueError('timezone required')
    scan_start = int(start.timestamp() * 1000)
    if scan_start < session['armed_at_utc_ms'] or scan_start > request_started:
        raise ValueError('old scan or clock mismatch; no retrospective observation')
    if any(s['source_cutoff'] > response_received for s in signals):
        raise ValueError('source clock after publication witness')
    return sealed({'schema_version': SCHEMA, 'kind': 'PUBLICATION_OBSERVED',
        'session_id': session['event_id'], 'repository': REPOSITORY, 'branch': 'main',
        'path': PATH, 'revision': head, 'git_blob_sha': blob, 'output_sha256': sha(raw),
        'scanner_version': payload['scanner_version'],
        'candidate_ids': [s['signal_id'] for s in signals],
        'request_started_at_utc_ms': request_started,
        'publication_observed_at_utc_ms': response_received,
        'availability_semantics': 'SUCCESSFUL_PINNED_GITHUB_READ_CONSERVATIVE_UPPER_BOUND',
        'evidence_url': API + '/contents/' + PATH + '?ref=' + head,
        'http_status': 200, 'scan_start_utc_ms': scan_start,
        'result_generation_completed_at': None, 'publisher_attempt_at': None,
        'publisher_push_succeeded_at': None,
        'unavailable_fields': ['result_generation_completed_at', 'publisher_attempt_at',
            'publisher_push_succeeded_at'], 'historical_reconstruction': False})


def signals_from_receipt(session, receipt, raw):
    validate(receipt)
    reproduced = witness(session, raw, receipt['revision'], receipt['git_blob_sha'],
        receipt['request_started_at_utc_ms'], receipt['publication_observed_at_utc_ms'])
    if reproduced != receipt:
        raise ValueError('publication receipt does not match source/baseline')
    result = []
    for s in a_snapshot(raw, receipt['output_sha256'], receipt['evidence_url']):
        s = dict(s)
        observed = receipt['publication_observed_at_utc_ms']
        s.update(signal_observed_at=observed, evaluation_anchor=(observed // HOUR + 1) * HOUR,
            performance_eligible=True, cohort=SCHEMA, signal_kind='PUBLISHED_CANDIDATE_OBSERVATION',
            publication_event_id=receipt['event_id'], unavailable_reason=None)
        result.append(seal_signal({k: v for k, v in s.items() if k != 'signal_record_hash'}))
    return result


def exclusive(path, data):
    try:
        with path.open('xb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError('append-only byte conflict') from None


def record(root, event, raw=None):
    """One first successful witness per output/session; later polls are no-op."""
    validate(event)
    root = isolated(root)
    source = Path(__file__).resolve().parent
    if root == source or source in root.parents or root == source.parent:
        raise ValueError('source namespace protected')
    no_symlinks(root); root.mkdir(parents=True, exist_ok=True)
    lock = root / '.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError('witness writer busy; inspect interrupted write') from None
    try:
        for p in root.glob('*.json'):
            prior = validate(json.loads(p.read_bytes()))
            if prior['kind'] == 'PUBLICATION_OBSERVED':
                saved = root / (prior['output_sha256'] + '.source')
                if not saved.exists() or sha(saved.read_bytes()) != prior['output_sha256']:
                    raise ValueError('stored source missing or damaged')
                if event['kind'] == 'PUBLICATION_OBSERVED' and prior['session_id'] == event['session_id']:
                    if event['publication_observed_at_utc_ms'] < prior['publication_observed_at_utc_ms']:
                        raise ValueError('observation clock regression')
                    if prior['scan_start_utc_ms'] == event['scan_start_utc_ms'] and prior['output_sha256'] != event['output_sha256']:
                        raise ValueError('same scan generation published conflicting bytes')
            if prior['event_id'] == event['event_id']:
                if p.read_bytes() != (dumps(event) + '\n').encode():
                    raise ValueError('existing event byte conflict')
                if prior['kind'] == 'PUBLICATION_OBSERVED' and (
                        not (root / (prior['output_sha256'] + '.source')).exists() or
                        sha((root / (prior['output_sha256'] + '.source')).read_bytes()) != prior['output_sha256']):
                    raise ValueError('stored source missing or damaged')
                return 'REPLAY_NOOP'
            if event['kind'] == prior['kind'] == 'PUBLICATION_OBSERVED' and (
                    prior['session_id'], prior['output_sha256']) == (
                    event['session_id'], event['output_sha256']):
                return 'REPLAY_NOOP'
        if event['kind'] == 'PUBLICATION_OBSERVED':
            baseline = root / (event['session_id'] + '.json')
            if not baseline.exists() or raw is None:
                raise ValueError('baseline and exact output bytes required')
            signals_from_receipt(json.loads(baseline.read_bytes()), event, raw)
            exclusive(root / (sha(raw) + '.source'), raw)
        exclusive(root / (event['event_id'] + '.json'), (dumps(event) + '\n').encode())
        return 'CREATED'
    finally:
        lock.rmdir()


def read_current(get=None):
    """Pin main once, then retrieve exact Contents API bytes. No Git mutation."""
    def network(url):
        headers = {'Accept': 'application/vnd.github+json', 'User-Agent': SCHEMA}
        token = os.environ.get('GITHUB_TOKEN')
        if token:
            headers['Authorization'] = 'Bearer ' + token
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
            return json.loads(r.read())
    get = get or network
    head = get(API + '/git/ref/heads/main')['object']['sha']
    started = now()
    content = get(API + '/contents/' + PATH + '?ref=' + head)
    received = now()
    if content['encoding'] != 'base64' or content['path'] != PATH:
        raise ValueError('unexpected Contents API response')
    raw = base64.b64decode(content['content'], validate=False)
    return raw, head, content['sha'], started, received


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['inspect', 'arm', 'observe'], nargs='?', default='inspect')
    parser.add_argument('--record-root')
    parser.add_argument('--session')
    args = parser.parse_args()
    if args.mode != 'inspect' and not args.record_root:
        parser.error('explicit isolated record root required')
    if args.mode != 'inspect':
        from .a_publication_v12 import run
        return run(args)
    raw, head, blob, started, received = read_current()
    if args.mode == 'inspect':
        print(json.dumps({'revision': head, 'sha256': sha(raw), 'candidate_count':
            len(a_snapshot(raw, sha(raw), 'read-only-inventory')), 'eligible': 0,
            'reason': 'NO_PROSPECTIVE_BASELINE; inspection creates no signals'}))
        return
    if args.mode == 'arm':
        event = arm(raw, head, received)
    else:
        if not args.session:
            parser.error('session event ID required')
        if not re.fullmatch('[0-9a-f]{64}', args.session):
            parser.error('invalid session ID')
        session = json.loads((Path(args.record_root) / (args.session + '.json')).read_bytes())
        event = witness(session, raw, head, blob, started, received)
    if event is None:
        print('NO_NEW_OUTPUT'); return
    print(json.dumps({'event_id': event['event_id'], 'result': record(args.record_root, event,
        raw if args.mode == 'observe' else None)}))


if __name__ == '__main__':
    main()

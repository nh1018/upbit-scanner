"""Manual V1.2 envelope around unchanged V1.1 publication evidence."""
import json
import time
from pathlib import Path
from .time_validation import digest, dumps
from upbit_c.research_archive import isolated
from upbit_c.research_release import no_symlinks
from . import a_publication as legacy, time_validation as ntp
from .adapters import seal_signal

VERSION = 'a-publication-evidence-1.2'


def envelope(event, pre, post, started, ended, elapsed):
    bound = ntp.bracket(pre, post, started, ended, elapsed)
    result = {'schema_version': VERSION, 'kind': event['kind'], 'publication': event,
        'clock_pre': pre, 'clock_post': post, 'read_started_ms': started,
        'read_ended_ms': ended, 'bracket_elapsed_seconds': elapsed,
        'clock_error_bound_seconds': bound}
    result['event_id'] = digest(result)
    return validate(result)


def validate(e):
    if e.get('schema_version') != VERSION or e.get('event_id') != digest({k:v for k,v in e.items() if k!='event_id'}):
        raise ValueError('V1.2 envelope hash/version mismatch')
    legacy.validate(e['publication'])
    if e['kind'] != e['publication']['kind']:
        raise ValueError('envelope kind mismatch')
    bound = ntp.bracket(e['clock_pre'], e['clock_post'], e['read_started_ms'],
        e['read_ended_ms'], e['bracket_elapsed_seconds'])
    if bound != e['clock_error_bound_seconds']:
        raise ValueError('clock bound mismatch')
    inner = e['publication']
    if e['kind'] == 'ARMED_BASELINE':
        if inner['armed_at_utc_ms'] != e['read_ended_ms']: raise ValueError('baseline clock mismatch')
    elif e['kind'] == 'PUBLICATION_OBSERVED':
        if inner['request_started_at_utc_ms'] != e['read_started_ms'] or inner['publication_observed_at_utc_ms'] != e['read_ended_ms']:
            raise ValueError('receipt clock mismatch')
    else: raise ValueError('unsupported envelope kind')
    return e


def signals(session, receipt, raw):
    validate(session); validate(receipt)
    if receipt.get('session_id') != session['event_id']:
        raise ValueError('V1.2 session mismatch')
    result = legacy.signals_from_receipt(session['publication'], receipt['publication'], raw)
    return [seal_signal({**{k:v for k,v in s.items() if k!='signal_record_hash'},
        'cohort':VERSION, 'publication_event_id':receipt['event_id']}) for s in result]


def record(root, event, raw=None):
    validate(event)
    last = max(s['t4_unix'] for s in event['clock_post']['samples'])
    age = time.time()-last
    if not 0 <= age <= ntp.POLICY['fresh_write_seconds']:
        raise ValueError('clock verification expired before write')
    root = isolated(root)
    source = Path(__file__).resolve().parent
    if root == source or source in root.parents or root == source.parent:
        raise ValueError('source namespace protected')
    root = root / 'v12'
    no_symlinks(root); root.mkdir(parents=True, exist_ok=True)
    lock = root / '.lock'
    try: lock.mkdir()
    except FileExistsError: raise ValueError('V1.2 writer busy; inspect interrupted write') from None
    try:
        for p in root.glob('*.json'):
            old = validate(json.loads(p.read_bytes()))
            if old['kind'] == 'PUBLICATION_OBSERVED':
                source = root / (old['publication']['output_sha256'] + '.source')
                if not source.exists() or legacy.sha(source.read_bytes()) != old['publication']['output_sha256']:
                    raise ValueError('stored source missing/damaged')
            if old['event_id'] == event['event_id']: return 'REPLAY_NOOP'
            if old['kind'] == event['kind'] == 'PUBLICATION_OBSERVED' and old['session_id'] == event['session_id']:
                a, b = old['publication'], event['publication']
                if b['publication_observed_at_utc_ms'] < a['publication_observed_at_utc_ms']:
                    raise ValueError('observation clock regression')
                if a['scan_start_utc_ms'] == b['scan_start_utc_ms'] and a['output_sha256'] != b['output_sha256']:
                    raise ValueError('conflicting same-scan publication')
                if a['output_sha256'] == b['output_sha256']: return 'REPLAY_NOOP'
        if event['kind'] == 'PUBLICATION_OBSERVED':
            session = json.loads((root / (event['session_id'] + '.json')).read_bytes())
            if raw is None: raise ValueError('exact source required')
            signals(session, event, raw)
            legacy.exclusive(root / (legacy.sha(raw) + '.source'), raw)
        legacy.exclusive(root / (event['event_id'] + '.json'), (dumps(event)+'\n').encode())
        return 'CREATED'
    finally: lock.rmdir()


def run(args):
    # Both rounds are fresh, in this invocation. No user-supplied PASS bypass.
    pre = ntp.measure()
    if pre['status'] != 'PASS': print(json.dumps({'clock_pre_failure':pre}))
    ntp.verify(pre)
    raw, head, blob, started, received = legacy.read_current()
    actual_blob = legacy.hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    if blob != actual_blob: raise ValueError('baseline/receipt Git blob mismatch')
    post = ntp.measure()
    if post['status'] != 'PASS': print(json.dumps({'clock_post_failure':post}))
    ntp.verify(post)
    elapsed = max(s['monotonic_ended'] for s in post['samples']) - min(s['monotonic_started'] for s in pre['samples'])
    if args.mode == 'arm':
        inner = legacy.arm(raw, head, received)
    else:
        if not args.session or not legacy.re.fullmatch('[0-9a-f]{64}', args.session):
            raise ValueError('V1.2 session ID required')
        session = validate(json.loads((Path(args.record_root)/'v12'/(args.session+'.json')).read_bytes()))
        inner = legacy.witness(session['publication'], raw, head, blob, started, received)
        if inner is None:
            ntp.bracket(pre, post, started, received, elapsed)
            print('NO_NEW_OUTPUT'); return
    e = envelope(inner, pre, post, started, received, elapsed)
    if args.mode == 'observe':
        e.pop('event_id'); e['session_id'] = session['event_id']; e['event_id'] = digest(e)
    outcome = record(args.record_root, e, raw if args.mode=='observe' else None)
    print(json.dumps({'schema_version':VERSION, 'event_id':e['event_id'], 'result':outcome,
        'baseline_sha256':inner.get('baseline_sha256'), 'clock_error_bound_seconds':e['clock_error_bound_seconds'],
        'clock_samples':[{'server':s['server'], 'offset_seconds':s['offset_seconds'],
            'delay_seconds':s['delay_seconds']} for s in pre['samples']+post['samples']]}))

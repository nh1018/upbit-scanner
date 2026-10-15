"""Offline connection protocol. No Git/network transport or production writes.

A backend must pin fresh input/code revision and atomically CAS against that head.
This PoC has only an in-memory backend; no CLI can publish to GitHub.
"""
from copy import deepcopy

from . import snapshot_poc01 as P
from .integrity import utc_ms


def connect(backend, *, event, now_ms, health, expected_code, upstream_success=True, attempts=3):
    if event not in P.EVENTS:
        raise ValueError('unknown event')
    if not 1 <= attempts <= 3:
        raise ValueError('bounded CAS attempts required')
    if event == 'PERFORMANCE_COMPLETED':
        return {'status': 'CONTEXT_ONLY_NO_PROJECTION', 'consumer_allowed': False,
                'trade_authorization': False, 'diagnostics': []}
    diagnostics = []
    for attempt in range(1, attempts + 1):
        try:
            # Never use workflow_run.head_sha or the checkout selected at dispatch.
            view = backend.read_latest()
            if view['code'] != expected_code:
                raise ValueError('execution code differs from current head')
            s = backend.project(view, now_ms)
            previous = view['previous']
            result = P.assess(s, now_ms=now_ms, previous=previous)
            if previous is not None:
                if utc_ms(s['generated_at_utc']) < utc_ms(previous['generated_at_utc']):
                    raise ValueError('older snapshot cannot replace newer publication')
                old_boundary = previous['direction']['trigger_15m']['candle_open_time_ms']
                if result['boundary'] < old_boundary:
                    raise ValueError('boundary regression')
            h = health(s, now_ms)
            if h.get('status') not in ('CURRENT', 'DEGRADED', 'STALE', 'MISSING', 'INVALID'):
                raise ValueError('unknown Health status')
            allowed = h.get('usable_for_current_analysis') is True and h['status'] == 'CURRENT'
            reasons = list(result['reasons'])
            if not upstream_success:
                reasons.append('ENTRY_WORKFLOW_NOT_SUCCESSFUL')
            if result['action'] == 'BLOCKED' or not allowed or not upstream_success:
                return {'status': 'DIAGNOSTIC_ONLY', 'source_revision': view['revision'],
                        'attempt': attempt, 'reasons': reasons, 'health': h,
                        'snapshot': s, 'consumer_allowed': False,
                        'trade_authorization': False, 'diagnostics': diagnostics}
            if result['action'] == 'NOOP':
                return {'status': 'NOOP', 'source_revision': view['revision'],
                        'attempt': attempt, 'health': h, 'consumer_allowed': True,
                        'trade_authorization': False, 'diagnostics': diagnostics}
            bundle = {'snapshot': s, 'health': h, 'source_revision': view['revision'],
                      'projection_key': result['projection_key']}
            # Latest-main compare-and-swap: never rebase an old calculated commit.
            if backend.cas(view['revision'], bundle):
                # Logical read-back/Health phase. A real adapter must use the
                # unchanged fresh-main Health publisher after Snapshot CAS,
                # not claim both files were atomically published in one commit.
                current = backend.read_latest()
                if current['code'] != expected_code:
                    raise ValueError('execution code changed after publication')
                current_snapshot = backend.project(current, now_ms)
                fresh = P.assess(current_snapshot, now_ms=now_ms)
                h = health(current_snapshot, now_ms)
                if (fresh['action'] != 'EMIT_COHERENT'
                        or fresh['projection_key'] != bundle['projection_key']
                        or h.get('status') != 'CURRENT'
                        or h.get('usable_for_current_analysis') is not True):
                    return {'status': 'SUPERSEDED_AFTER_PUBLICATION', 'attempt': attempt,
                            'health': h, 'consumer_allowed': False,
                            'trade_authorization': False, 'diagnostics': diagnostics}
                return {'status': 'PUBLISHED_IN_MEMORY', 'source_revision': view['revision'],
                        'attempt': attempt, 'health': h, 'consumer_allowed': True,
                        'trade_authorization': False, 'diagnostics': diagnostics}
            diagnostics.append({'attempt': attempt, 'reason': 'HEAD_CHANGED_OR_ACK_UNCERTAIN'})
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return {'status': 'INVALID', 'attempt': attempt, 'consumer_allowed': False,
                    'trade_authorization': False, 'reason': str(exc), 'diagnostics': diagnostics}
    return {'status': 'CONFLICT_EXHAUSTED', 'consumer_allowed': False,
            'trade_authorization': False, 'diagnostics': diagnostics}


class MemoryBackend:
    """Test transport; cannot access GitHub, subprocesses, or the filesystem."""
    def __init__(self, snapshot, *, code='fixture-code', previous=None):
        self.snapshot = deepcopy(snapshot)
        self.previous = deepcopy(previous)
        self.code = code
        self.revision = 0
        self.reads = 0
        self.writes = []
        self.before_cas = None
        self.after_cas = None
        self.lose_ack = False

    def read_latest(self):
        self.reads += 1
        return {'revision': self.revision, 'snapshot': deepcopy(self.snapshot),
                'previous': deepcopy(self.previous), 'code': self.code}

    def project(self, view, now_ms):
        return deepcopy(view['snapshot'])

    def advance(self, snapshot):
        self.snapshot = deepcopy(snapshot)
        self.revision += 1

    def cas(self, expected, bundle):
        if self.before_cas is not None:
            self.before_cas(self)
        if expected != self.revision:
            return False
        self.previous = deepcopy(bundle['snapshot'])
        self.writes.append(deepcopy(bundle))
        self.revision += 1
        if self.after_cas is not None:
            self.after_cas(self)
        if self.lose_ack:
            self.lose_ack = False
            return False
        return True

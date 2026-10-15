"""Offline Entry-driven projection PoC. No writes, dispatch, or engine replay.

EMIT_COHERENT is a projection recommendation, never Health/trade authorization.
The caller must retain blocked/error diagnostics and run unchanged consumer Health.
"""
import argparse
import json
from pathlib import Path

from . import analysis_snapshot as S
from .features.engine import digest
from .integrity import DURATIONS, utc_ms

EVENTS = ('ENTRY_COMPLETED', 'AUDIT', 'PERFORMANCE_COMPLETED')


def projection_key(snapshot):
    """Performance refresh and reader clocks do not create a new signal projection."""
    return digest({
        'direction': snapshot['direction'], 'entry': snapshot['entry'],
        'feature': snapshot['feature'],
        'market': {tf: (row['latest_completed'] or {}).get('canonical_row_hash')
                   for tf, row in snapshot['market_data'].items()},
        'policy': snapshot['provenance']['snapshot_policy_hash'],
    })


def assess(snapshot, *, now_ms, previous=None):
    S.validate(snapshot)
    if previous is not None:
        S.validate(previous)
    d, e = snapshot['direction'], snapshot['entry']
    reasons = []
    if now_ms < utc_ms(snapshot['generated_at_utc']):
        reasons.append('FUTURE_SNAPSHOT')
    if not d.get('available'):
        reasons.append('DIRECTION_UNAVAILABLE')
    if not e.get('available'):
        reasons.append('ENTRY_UNAVAILABLE')
    market = snapshot['market_data']['15m']['latest_completed']
    boundary = market['time'] if market else None
    if not e.get('matches_snapshot_direction') or e.get('upstream_direction_decision_id') != d.get('decision_id'):
        reasons.append('ENTRY_DIRECTION_MISMATCH')
    ev = e.get('evaluation') or {}
    if not e.get('matches_latest_15m') or ev.get('trigger_time') != boundary:
        reasons.append('ENTRY_LAGS_MARKET')
    if d.get('trigger_15m', {}).get('candle_open_time_ms') != boundary:
        reasons.append('DIRECTION_LAGS_MARKET')
    if ev.get('upstream_direction_decision_id') != d.get('decision_id'):
        reasons.append('ENTRY_DIRECTION_MISMATCH')
    if e.get('operational_status') != 'EVALUATED' or ev.get('execution_status') != 'EVALUATED':
        reasons.append('ENTRY_NOT_EVALUATED')
    if ev.get('entry_state') not in ('ENTRY_CANDIDATE', 'WAIT', 'NO_ENTRY'):
        reasons.append('ENTRY_STATE_UNAVAILABLE')
    policy = snapshot['provenance']['snapshot_policy']
    for obj, clock in ((d, 'decision_time_utc'), (e, 'generated_at_utc')):
        stamp = obj.get(clock)
        if not stamp or not 0 <= now_ms - utc_ms(stamp) <= policy['decision_max_age_ms']:
            reasons.append('ENGINE_STALE_OR_FUTURE')
    for tf, f in snapshot['data_freshness'].items():
        if f['market_stale'] or not f['feature_matches_latest_market']:
            reasons.append(tf + ':INPUT_NOT_CURRENT')
        if f.get('feature_candle_open_utc') is None or now_ms > utc_ms(f['feature_candle_open_utc']) + 2 * DURATIONS[tf] + policy['market_publication_allowance_ms']:
            reasons.append(tf + ':CONSUMER_STALE')
    if snapshot['snapshot_status'] == 'STALE' or d.get('stale') or e.get('stale'):
        reasons.append('SNAPSHOT_STALE')
    # Preserve PARTIAL, historical warnings, and unavailable OI; Health still
    # decides whether active evidence is valid. Never relabel this snapshot READY.
    action = 'BLOCKED' if reasons else 'NOOP' if previous is not None and projection_key(previous) == projection_key(snapshot) else 'EMIT_COHERENT'
    return {'action': action, 'reasons': sorted(set(reasons)),
            'projection_key': projection_key(snapshot), 'boundary': boundary,
            'snapshot_status': snapshot['snapshot_status'],
            'warnings': list(snapshot['warnings']), 'consumer_health_required': True,
            'trade_authorization': False, 'snapshot': snapshot}


def preview(repo, *, event, T=None, previous=None):
    if event not in EVENTS:
        raise ValueError('unknown PoC event')
    if event == 'PERFORMANCE_COMPLETED':
        return {'action': 'CONTEXT_ONLY_NO_PROJECTION', 'reasons': [],
                'consumer_health_required': True, 'trade_authorization': False}
    T = S.now_ms() if T is None else T
    try:
        target = Path(repo) / S.TARGET
        if previous is None and target.exists():
            previous = json.loads(target.read_text(encoding='utf-8'))
        return assess(S.build(repo, T), now_ms=T, previous=previous)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return {'action': 'INVALID', 'reasons': [type(exc).__name__ + ':' + str(exc)],
                'consumer_health_required': True, 'trade_authorization': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', required=True)
    p.add_argument('--event', choices=EVENTS, required=True)
    a = p.parse_args()
    result = preview(a.repo, event=a.event)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 2 if result['action'] in ('BLOCKED', 'INVALID') else 0


if __name__ == '__main__':
    raise SystemExit(main())

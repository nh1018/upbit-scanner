"""Offline enrichment of existing diagnostics; never changes collection or eligibility."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from upbit_b.contracts import DURATIONS, WINDOWS
from .research_diagnostics import diagnose
from .research_history import read_record


def review(report):
    if report.get('activation') != 'RESEARCH_ONLY':
        raise ValueError('research scan required')
    rows = report['results']
    if len({r['market'] for r in rows}) != len(rows):
        raise ValueError('duplicate market')
    original = diagnose(report)
    by_market = {r['market']: r for r in rows}
    categories, markets_by_category, per_tf = Counter(), {}, Counter()
    for row in original['markets']:
        for tf, item in row['timeframes'].items():
            source = by_market[row['market']]['timeframes'][tf]
            required, duration = WINDOWS[tf], DURATIONS[tf]
            evidence = source.get('source_evidence', [])
            gaps = source.get('missing_slots', [])
            klass = item['classification']
            category, confidence, improvement = {
                'SOURCE_RESPONSES_OMIT_CALENDAR_SLOTS': ('SOURCE_OMISSION_NO_TRADE_POSSIBLE', 'MEDIUM', 'CONTRACT_CHANGE_REQUIRED_NO_IMPUTATION'),
                'OFFICIAL_ACCESSIBLE_HISTORY_EXHAUSTED': ('ACCESSIBLE_HISTORY_SHORT_LISTING_UNCONFIRMED', 'MEDIUM', 'NO_EVIDENCED_COLLECTION_FIX'),
                'BOUNDED_COLLECTION_HISTORY_SHORT': ('COLLECTION_LIMIT_OR_HISTORY_SHORT_UNRESOLVED', 'LOW', 'ADDITIONAL_SOURCE_EVIDENCE_REQUIRED'),
                'LATEST_COMPLETED_BOUNDARY_ABSENT': ('LATEST_COMPLETED_CANDLE_ABSENT', 'MEDIUM', 'SOURCE_RECHECK_REQUIRED'),
                'MANDATORY_C_FEATURE_UNAVAILABLE': ('VALID_FEATURE_HISTORY_INSUFFICIENT', 'HIGH', 'CONTRACT_OR_FUTURE_HISTORY_REQUIRED'),
                'API_OR_VALIDATION_FAILURE': ('API_VALIDATION_OR_CLOCK_UNRESOLVED', 'LOW', 'ADDITIONAL_SOURCE_EVIDENCE_REQUIRED'),
                'UNRESOLVED_GAP_OR_COLLECTION_RANGE': ('COLLECTION_RANGE_OR_SOURCE_UNRESOLVED', 'LOW', 'ADDITIONAL_SOURCE_EVIDENCE_REQUIRED'),
            }[klass]
            cs = source.get('candles', [])
            flat_range = bool(cs) and cs[-1].get('high') is not None and cs[-1].get('high') == cs[-1].get('low')
            opens = [c['open_ms'] for c in cs]
            expected_close = report['source_cutoff_ms'] // duration * duration
            # Record contradictory evidence instead of certifying the original diagnosis.
            violations = []
            if len(opens) != len(set(opens)): violations.append('DUPLICATE')
            if opens != sorted(opens): violations.append('UNSORTED')
            if any(c.get('completed') is not True or c['close_ms'] > report['source_cutoff_ms']
                   or c['close_ms'] != c['open_ms'] + duration or c['open_ms'] % duration for c in cs):
                violations.append('CANDLE_CLOCK_OR_COMPLETENESS')
            if violations:
                category, confidence, improvement = 'API_VALIDATION_OR_CLOCK_UNRESOLVED', 'HIGH', 'INVESTIGATE_RECORDED_CONTRADICTION'
            item.update(category=category, confidence=confidence, improvement=improvement,
                required_candles=required, acquired_candles=len(cs), expected_latest_close_ms=expected_close,
                full_missing_open_ms=gaps, integrity_violations=violations,
                bounded_page_limit=3, page_limit_reached=len(evidence) >= 3,
                source_evidence_sha256=hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                proven_collection_defect=False, listing_date_confirmed=False, no_trade_confirmed=False)
            item['latest_candle_zero_range'] = flat_range
            item['feature_failure_detail'] = ('ZERO_RANGE_CANDLE_RATIO_UNDEFINED' if flat_range and source['status']=='INSUFFICIENT_FEATURES' else None)
            categories[category] += 1
            per_tf[(tf, item['status'])] += 1
            markets_by_category.setdefault(category, set()).add(row['market'])
    return {**original, 'schema_version': 'upbit-c-coverage-review-1',
        'market_total': len(rows), 'evaluated_market_total': len(rows)-len(original['markets']),
        'timeframe_category_counts': dict(sorted(categories.items())),
        'market_category_counts_nonexclusive': {k: len(v) for k, v in sorted(markets_by_category.items())},
        'timeframe_status_counts': [{'timeframe': tf, 'status': status, 'count': n} for (tf, status), n in sorted(per_tf.items())],
        'proven_collection_defects': 0,
        'classification_policy': 'Recorded source absence is not independent proof of no trades or listing date; collection defect not established by short history alone.'}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('scan_record', type=Path)
    p.add_argument('--sha256', required=True, help='Externally verified original envelope hash')
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args(argv)
    raw = a.scan_record.read_bytes()
    if hashlib.sha256(raw).hexdigest() != a.sha256:
        raise ValueError('original envelope SHA256 mismatch')
    result = review(read_record(a.scan_record))
    result['original_envelope_sha256'] = a.sha256
    result['original_envelope_bytes'] = len(raw)
    with a.output.open('x', encoding='utf-8', newline='\n') as f:
        json.dump(result, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write('\n')
    return result


if __name__ == '__main__': main()

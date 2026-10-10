"""Versioned signal/measurement contracts, not execution records."""
import hashlib
import re
from decimal import Decimal, InvalidOperation
from upbit_b.feature_contracts import digest

VERSION = 'abc-gross-research-evaluation-1'
SIGNAL_SCHEMA = 'abc-recorded-observation-1'
HOUR = 3600000
HORIZONS = (1, 3, 7)
POLICY = {'anchor': 'NEXT_1H_OPEN_PROXY', 'horizons_days': HORIZONS,
    'costs': 'GROSS_NO_FEES_SLIPPAGE_SPREAD', 'sample': 'earliest_nonoverlapping_per_market_strategy_version_cohort_anchor_horizon',
    'arithmetic': 'Decimal_precision34_HALF_EVEN', 'win': 'return_pct>0', 'flat': 'return_pct=0',
    'regime': 'UNSUPPORTED_WITHOUT_RECORDED_SIGNAL_CONTEXT', 'strength_bins': ['<45','45~<65','65~<80','>=80']}
POLICY_HASH = digest(POLICY)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def external(raw, expected):
    if sha(raw) != expected: raise ValueError('external source SHA256 mismatch')


def number(value):
    if isinstance(value, bool) or value is None: raise ValueError('finite Decimal required')
    try: v = Decimal(str(value))
    except (InvalidOperation, ValueError): raise ValueError('finite Decimal required') from None
    if not v.is_finite(): raise ValueError('finite Decimal required')
    return v


def clock(value):
    if type(value) is not int or value < 0: raise ValueError('nonnegative millisecond clock required')
    return value


def validate_signal(s):
    if s.get('schema_version') != SIGNAL_SCHEMA or s.get('strategy') not in ('A','B','C'):
        raise ValueError('unknown signal contract')
    if not re.fullmatch('KRW-[A-Z0-9]+', s['market']): raise ValueError('KRW market required')
    if not all(isinstance(s[k],str) and s[k] for k in ('strategy_version','signal_id','cohort','source_reference')):
        raise ValueError('signal identity required')
    if not re.fullmatch('[0-9a-f]{64}',s['source_hash']): raise ValueError('source hash required')
    if s.get('signal_record_hash') != digest({k:v for k,v in s.items() if k!='signal_record_hash'}):
        raise ValueError('normalized observation hash mismatch')
    if type(s.get('performance_eligible')) is not bool: raise ValueError('eligibility required')
    cutoff=clock(s['source_cutoff'])
    if s['signal_observed_at'] is not None:
        observed=clock(s['signal_observed_at'])
        if observed < cutoff: raise ValueError('source cutoff after observation')
        if s['evaluation_anchor'] != (observed//HOUR+1)*HOUR: raise ValueError('invalid prospective proxy boundary')
    elif s['performance_eligible'] or s['evaluation_anchor'] is not None:
        raise ValueError('unknown availability cannot be eligible')
    if s['evaluation_anchor_type'] != POLICY['anchor']: raise ValueError('unsupported anchor')
    if s.get('score') is not None: number(s['score'])
    if s['signal_price_reference'].get('price') is not None and number(s['signal_price_reference']['price'])<=0:
        raise ValueError('invalid reference price')
    return s


def identity(s, days):
    validate_signal(s)
    if type(days) is not int or days not in HORIZONS: raise ValueError('unsupported horizon')
    return digest([VERSION,POLICY_HASH,s['strategy'],s['strategy_version'],s['cohort'],s['signal_id'],s['evaluation_anchor_type'],days])

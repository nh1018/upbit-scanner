"""Read existing records. No score recomputation or inferred historical availability."""
import json
from datetime import datetime
from upbit_b import history as B, history_contracts as BC
from upbit_c import research_history as C
from .contracts import SIGNAL_SCHEMA, HOUR, external, sha, validate_signal
from upbit_b.feature_contracts import digest


def seal_signal(s):
    s=dict(s);s['signal_record_hash']=digest({k:v for k,v in s.items() if k!='signal_record_hash'})
    return validate_signal(s)


def base(strategy, version, sid, market, observed, cutoff, raw, reference, price, cohort, score=None):
    return seal_signal({'schema_version':SIGNAL_SCHEMA,'strategy':strategy,'strategy_version':version,
        'signal_id':sid,'market':market,'signal_observed_at':observed,'source_cutoff':cutoff,
        'source_hash':sha(raw),'source_reference':reference,'cohort':cohort,'score':str(score) if score is not None else None,
        'signal_price_reference':price,'evaluation_anchor':(observed//HOUR+1)*HOUR if observed is not None else None,
        'evaluation_anchor_type':'NEXT_1H_OPEN_PROXY','performance_eligible':observed is not None,
        'signal_kind':'RECORDED_RESEARCH_OBSERVATION','regime':None,'source_cutoff_semantics':'RECORDED_DATA_CUTOFF'})


def a_snapshot(raw, expected_sha, reference):
    """Legacy candidate facts only: scan_time is not actual receipt/availability."""
    external(raw,expected_sha);p=json.loads(raw)
    if p.get('scanner_version')!='1.3-cloud' or len(p['candidates'])!=p['candidate_count']:
        raise ValueError('unsupported A snapshot')
    seen=set();result=[]
    for r in p['candidates']:
        m=r['upbit_market']
        if m in seen: raise ValueError('duplicate A market')
        seen.add(m)
        if r['state'] not in ('발사 전','관찰') or r['warning'] is not False:raise ValueError('not a recorded A candidate')
        stamp=datetime.fromisoformat(r['scan_time_kst'])
        if stamp.utcoffset() is None:raise ValueError('timezone required')
        cutoff=max(int(stamp.timestamp()*1000),r['upbit_timestamp_ms'])
        from upbit_b.feature_contracts import digest
        s=base('A',p['scanner_version'],digest(['A',m,r['scan_time_kst']]),m,None,cutoff,raw,reference,
            {'price':str(r['upbit_price']),'type':'OBSERVED_TICKER_DIAGNOSTIC','price_time':r['upbit_timestamp_ms']},
            'legacy-A-availability-unavailable',r['score'])
        s.update(signal_kind='LEGACY_CANDIDATE_OBSERVATION',unavailable_reason='SCAN_START_IS_NOT_RECEIVED_TIME')
        result.append(seal_signal(s))
    return result


def b_journals(sources):
    """Decode/validate complete supplied lineage and import verified positive transitions.

    A baseline, heartbeat, control, UNKNOWN or recovered state is never an entry.
    Missing parent terminates the import; an isolated journal cannot prove continuity.
    """
    previous=B.empty_previous();result=[]
    for raw,expected,reference in sources:
        external(raw,expected);manifest,records=B.validate_cycle(raw)
        if manifest['mode']!='PRODUCTION_PREPARED':raise ValueError('nonproduction B journal')
        next_state=B.advance(previous,raw)  # validates actual parent/cohort/transition references
        for r in records:
            if r['summary']['candidate']!='TRUE':continue
            events=[e for e in r['events'] if e['transition_verified'] and e['event_type'] in
                ('ENTER_BUILDING','ENTER_CONTINUATION','ENTER_PULLBACK_WATCH','ENTER_REACCELERATION')]
            if not events:continue
            o=r.get('observation')
            if o is None:raise ValueError('missing recorded transition detail')
            observed=BC.clock(r['engine_observation_time'])
            s=base('B',manifest['versions']['engine_algorithm'],r['observation_id'],r['instrument'],observed,
                manifest['source_cutoff'],raw,reference,o['price_anchors']['COMPLETED_1H_CLOSE_DIAGNOSTIC'] or
                {'price':None,'type':'UNAVAILABLE'},manifest['cohort_id'],o['score'])
            s.update(signal_kind='VERIFIED_POSITIVE_STATE_TRANSITION',event_references=[e['event_id'] for e in events])
            result.append(seal_signal(s))
        previous=next_state
    return result


def c_signal(signal, raw, expected_sha, reference):
    external(raw,expected_sha);C.validate_signal(signal)
    envelope=json.loads(raw)
    # An externally supplied signal must be exactly the sealed source envelope.
    from upbit_b.feature_contracts import digest
    if envelope.get('record')!=signal or envelope.get('record_sha256')!=digest(signal):raise ValueError('C envelope mismatch')
    s=base('C',signal['score_engine_version'],signal['signal_id'],signal['market'],signal['observed_at_ms'],
        signal['trigger_close_ms'],raw,reference,{'price':signal['reference_price'],'type':signal['reference_type'],
        'price_time':signal['trigger_close_ms']},signal['parameter_sha256'],str(signal['score']['score']))
    s.update(source_cutoff_semantics='TRIGGER_COMPLETED_CLOSE_BOUNDARY; exact scan cutoff unavailable in signal',original_record_hash=digest(signal))
    return seal_signal(s)


def c_outcome(s, original_signal, raw, expected_sha, reference):
    """Import a sealed existing C outcome using its recorded path/evidence only."""
    from .contracts import number
    from .engine import evaluate, seal
    from upbit_c.research_outcomes import SCHEMA
    from upbit_b.contracts import Candle
    external(raw,expected_sha);envelope=json.loads(raw);old=envelope['record']
    if envelope.get('record_sha256')!=digest(old) or old.get('schema_version')!=SCHEMA or old.get('activation')!='RESEARCH_ONLY':
        raise ValueError('C outcome envelope mismatch')
    if old['signal_id']!=s['signal_id'] or old['market']!=s['market'] or old['anchor_type']!='NEXT_1H_OPEN_PROXY':
        raise ValueError('C outcome signal/anchor mismatch')
    rows=[]
    for row in old['path']:
        row=dict(row)
        for key in ('open','high','low','close','base_volume','quote_trade_amount'):row[key]=number(row[key])
        rows.append(Candle(**row))
    new=evaluate(s,old['horizon_days'],rows,old['source_evidence'],old['as_of_ms'],original_signal)
    for key in ('status','return_pct','mfe_pct','mae_pct','anchor_price','endpoint_price'):
        if new[key]!=old[key]:raise ValueError('C outcome replay mismatch: '+key)
    if new['evaluation_anchor']!=old['anchor_open_ms'] or new['endpoint_boundary']!=old['endpoint_close_ms']:
        raise ValueError('C outcome boundary mismatch')
    new.pop('event_id');new.update(legacy_outcome_source_hash=expected_sha,legacy_outcome_reference=reference,legacy_reason=old.get('reason'))
    return seal(new)

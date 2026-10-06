"""Latest analysis projection of immutable production artifacts; no engine replay/API."""
import argparse
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .integrity import DURATIONS, iso, utc_ms
from .features.build import load_raw
from .features.engine import digest, source, known_availability, PARAMETERS
from .features.availability import load_observations, evidence_map, now_ms, git_revision
from .direction.engine import validate_snapshot, load_parameters
from .entry.engine import parameters as entry_parameters

SCHEMA = 'btc-analysis-snapshot-v1'
TARGET = 'output_btc_anytime/latest_analysis.json'
POLICY = {'market_publication_allowance_ms': 1200000, 'decision_max_age_ms': 1800000,
          'structure_rows': 8, 'feature_basis': 'stored_direction_input_snapshot_only'}


def text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n'


def sealed(value):
    value=deepcopy(value);value['payload_sha256']=digest(value);return value


def validate(value):
    if value.get('schema_version')!=SCHEMA or value.get('payload_sha256')!=digest({k:v for k,v in value.items() if k!='payload_sha256'}):
        raise ValueError('Snapshot schema/hash mismatch')
    T=utc_ms(value['generated_at_utc'])
    if value['snapshot_status'] not in ('READY','PARTIAL','STALE'):raise ValueError('Snapshot status')
    if set(value['market_data'])!=set(DURATIONS):raise ValueError('Snapshot timeframe set')
    for tf,item in value['market_data'].items():
        candle=item['latest_completed']
        if candle:
            if candle['time']%DURATIONS[tf] or candle['time']+DURATIONS[tf]>T:raise ValueError('Uncompleted/boundary')
            for r in item['recent_completed']:
                if r['time']%DURATIONS[tf] or r['time']+DURATIONS[tf]>T:raise ValueError('Future structure row')
    for tf,f in value['feature']['timeframes'].items():
        if f and (utc_ms(f['calculated_at_utc'])>T or f['candle_open_ms']+DURATIONS[tf]>T):raise ValueError('Future Feature')
    for section,clock in (('direction','decision_time_utc'),('entry','generated_at_utc')):
        if value[section].get(clock) and utc_ms(value[section][clock])>T:raise ValueError('Future result')
    text(value)
    return value


def artifact(path,key):
    obj=json.loads(path.read_text(encoding='utf-8'))
    if obj.get(key)!=digest({k:v for k,v in obj.items() if k!=key}):raise ValueError('Invalid production artifact: '+path.name)
    return obj


def latest(repo,folder,clock,key,T):
    values=[]
    for path in (repo/folder).glob('*.json'):
        # Even an older corrupt artifact must not be silently ignored.
        r=artifact(path,key)
        ident='decision_id' if key=='decision_id' else 'publication_id'
        if path.stem!=r[ident]:raise ValueError('Production artifact filename mismatch')
        if utc_ms(r[clock])<=T and utc_ms(r.get('generated_at_utc',r[clock]))<=T:values.append((r,path))
    return max(values,key=lambda x:(utc_ms(x[0][clock]),x[1].name)) if values else (None,None)


def ref(repo,path):
    return {'file':path.relative_to(repo).as_posix(),'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def numeric(row):
    values={k:Decimal(str(row[k])) for k in ('open','high','low','close','volume')}
    if not all(v.is_finite() for v in values.values()) or min(values[k] for k in ('open','high','low','close'))<=0 or values['volume']<0:
        raise ValueError('Invalid OHLCV')
    if not (values['high']>=max(values['open'],values['close'],values['low']) and values['low']<=min(values['open'],values['close'])):
        raise ValueError('OHLC logical error')


def market_row(row,tf,raw_ref,evidence,T):
    numeric(row)
    available=known_availability(row,DURATIONS[tf],evidence)
    if available is not None and available>T:raise ValueError('Future repository evidence')
    return {'time':row['time'],'candle_open_utc':iso(row['time']),
        'candle_close_exclusive_utc':iso(row['time']+DURATIONS[tf]),
        'ohlcv':{k:str(row[k]) for k in ('open','high','low','close','volume')},
        'oi_raw':None if row.get('oi') is None else str(row['oi']),
        'source':source(row),'source_schema_version':row.get('schema_version'),
        'is_closed':True,'completed_basis':'explicit_closed_flag_and_time' if 'is_closed' in row else 'legacy_no_flag_completed_time_boundary',
        'source_received_at_utc':row.get('received_at_utc'), 'retrieved_at_utc':row.get('retrieved_at_utc'),
        'oi_provenance':{k:row.get(k) for k in ('oi_status','oi_time','oi_time_basis','oi_period_start_ms','oi_period_end_ms','oi_source','oi_unit')},
        'availability':{'evidence':evidence,'available_at_utc':iso(available) if available is not None else None,
            'reader_observed_at_utc':iso(T),'historical_first_availability':'unavailable'},
        'raw_reference':deepcopy(raw_ref),'canonical_row_hash':digest(row)}


def build(repo,T=None):
    repo=Path(repo).resolve();T=now_ms() if T is None else T;revision=git_revision(repo)
    datasets,refs,files=load_raw(repo)
    d,dp=latest(repo,'output_direction/btc_anytime/v1/decisions','decision_time_utc','decision_id',T)
    e,ep=latest(repo,'output_entry/btc_anytime/v1/records','generated_at_utc','integrity_hash',T)
    events=load_observations(repo)
    if d and d.get('inline_observation_event'):events.append(d['inline_observation_event'])
    mapping=evidence_map(events,datasets)
    warnings=[];market={};features={};freshness={};versions={}
    if d:
        if d['schema_version']!='btc-direction-v1' or d['parameter_hash']!=load_parameters()['parameter_hash']:raise ValueError('Direction version mismatch')
        validate_snapshot(d['input_snapshot'])
        if d['snapshot_id']!=d['input_snapshot']['snapshot_id']:raise ValueError('Direction snapshot reference mismatch')
        versions['direction']={k:d.get(k) for k in ('schema_version','direction_engine_version','parameter_version','parameter_hash','decision_id','engine_decision_id')}
    else:warnings.append('DIRECTION_UNAVAILABLE')
    if e and e.get('evaluation'):
        ev=e['evaluation']
        if ev['entry_evaluation_id']!=digest({k:v for k,v in ev.items() if k!='entry_evaluation_id'}) or ev['parameter_hash']!=entry_parameters()['parameter_hash']:
            raise ValueError('Entry contract/hash mismatch')
        versions['entry']={k:ev.get(k) for k in ('schema_version','algorithm_version','parameter_version','parameter_hash','entry_evaluation_id')}
        ip=repo/'output_entry/btc_anytime/v1/inputs'/(e['publication_id']+'.json')
        original=artifact(ip,'integrity_hash')
        if original['manifest']['manifest_id']!=ev['input_manifest_id'] or original['manifest']['manifest_id']!=digest({k:v for k,v in original['manifest'].items() if k!='manifest_id'}):
            raise ValueError('Entry input reference mismatch')
        versions['entry']['input_reference']=ref(repo,ip)
    for tf,duration in DURATIONS.items():
        rows=sorted(datasets[tf],key=lambda r:r['time'])
        for r in rows:
            if r['time']%duration:raise ValueError('Raw boundary violation')
        closed=[r for r in rows if r.get('is_closed') is not False and r['time']+duration<=T
                and all(not r.get(k) or utc_ms(r[k])<=T for k in ('received_at_utc','retrieved_at_utc','emitted_at_utc'))]
        intervals=[b['time']-a['time'] for a,b in zip(closed,closed[1:])]
        integrity={'rows':len(closed),'duplicate':0,'missing_slots':sum(max(x//duration-1,0) for x in intervals),
                   'abnormal_intervals':sum(x!=duration for x in intervals),'ohlcv_invalid':0}
        for r in closed:numeric(r)
        if integrity['missing_slots'] or integrity['abnormal_intervals']:warnings.append(tf+':RAW_CONTINUITY_ERROR')
        projected=[market_row(r,tf,refs[tf][r['time']],mapping[tf].get(r['time']),T) for r in closed[-POLICY['structure_rows']:]]
        last=projected[-1] if projected else None
        expected=T//duration*duration-duration
        stale=last is None or (last['time']<expected and T-(expected+duration)>POLICY['market_publication_allowance_ms'])
        market[tf]={'latest_completed':last,'recent_completed':projected,'integrity':integrity,
            'in_progress_excluded':True,'rolling_24h_used':False}
        sel=d['input_snapshot']['timeframes'][tf] if d else {};r=sel.get('record')
        if r:
            original_row=next(x for x in datasets[tf] if x['time']==r['time'])
            if original_row.get('is_closed') is False:raise ValueError('Feature references an unclosed raw row')
            if digest(original_row)!=r['availability_evidence']['canonical_row_hash']:
                raise ValueError('Stored Feature/raw source conflict')
            features[tf]={'candle_open_ms':r['time'],'calculated_at_utc':iso(r['feature_generated_at_ms']),
                'values':deepcopy(r['features']),'quality':deepcopy(r['feature_quality']),
                'oi_contract':deepcopy(r['oi_metadata']),'raw_reference':deepcopy(r['raw_ref']),
                'availability':deepcopy(r['availability_evidence']),
                'versions':{k:r.get(k) for k in ('schema_version','algorithm_version','parameter_hash','dataset_manifest_id','input_feature_result_hash','generation_evidence_ref')}}
            if r['parameter_hash']!=digest(PARAMETERS):raise ValueError('Feature parameter mismatch')
        else:features[tf]=None;warnings.append(tf+':FEATURE_UNAVAILABLE')
        lag=bool(last and r and last['time']!=r['time'])
        freshness[tf]={'expected_completed_open_utc':iso(expected),'market_stale':stale,
            'publication_pending_within_allowance':bool(last and last['time']<expected and not stale),
            'market_age_ms':T-(last['time']+duration) if last else None,'feature_matches_latest_market':not lag if r else False,
            'feature_candle_open_utc':iso(r['time']) if r else None}
        if lag:warnings.append(tf+':FEATURE_LAGS_MARKET')
        if stale:warnings.append(tf+':MARKET_STALE')
        if last and last['availability']['evidence'] is None:warnings.append(tf+':AVAILABILITY_UNAVAILABLE')
    direction=({'available':False} if not d else {k:deepcopy(v) for k,v in d.items() if k in
        ('decision_id','engine_decision_id','decision_time_utc','generated_at_utc','direction_class','direction_score','regime','confidence','confidence_semantics',
         'reason_codes','primary_reason','conflicting_components','conflicting_timeframes','supporting_timeframes','missing_inputs','stale_inputs','parameter_version','parameter_hash','trigger_15m')})
    if d:
        direction.update(available=True,timeframes={tf:{k:deepcopy(v) for k,v in item.items() if k!='feature_evidence'} for tf,item in d['timeframes'].items()},
            source_reference=ref(repo,dp),stale=T-utc_ms(d['decision_time_utc'])>POLICY['decision_max_age_ms'])
        if direction['stale']:warnings.append('DIRECTION_STALE')
    entry={'available':False}
    if e:
        entry={'available':e.get('evaluation') is not None,'evaluation':deepcopy(e.get('evaluation')),
            'publication_id':e['publication_id'],'generated_at_utc':e['generated_at_utc'],'source_reference':ref(repo,ep),
            'operational_status':e['operational_status'],'upstream_direction_decision_id':e['upstream_direction_decision_id'],
            'stale':T-utc_ms(e['generated_at_utc'])>POLICY['decision_max_age_ms'],
            'matches_snapshot_direction':bool(d and e['upstream_direction_decision_id']==d['decision_id']),
            'matches_latest_15m':e.get('trigger_time')==market['15m']['latest_completed']['time'] if market['15m']['latest_completed'] else False,
            'execution_semantics':'research_candidate_not_trade_authorization'}
        if entry['stale']:warnings.append('ENTRY_STALE')
        if not entry['matches_snapshot_direction']:warnings.append('ENTRY_DIRECTION_MISMATCH')
        if not entry['matches_latest_15m']:warnings.append('ENTRY_LAGS_MARKET')
    if not entry['available']:warnings.append('ENTRY_UNAVAILABLE')
    stale=any(v['market_stale'] for v in freshness.values()) or direction.get('stale') or entry.get('stale')
    value={'schema_version':SCHEMA,'generated_at_utc':iso(T),'source_cutoff_utc':iso(max([r['latest_completed']['time']+DURATIONS[tf] for tf,r in market.items() if r['latest_completed']],default=0)),
        'snapshot_status':'STALE' if stale else 'PARTIAL' if warnings else 'READY',
        'data_freshness':freshness,'latest_available_tf':[tf for tf,v in market.items() if v['latest_completed']],
        'market_data':market,'feature':{'basis':'stored_prospective_direction_snapshot','decision_id':d['decision_id'] if d else None,'timeframes':features},
        'direction':direction,'entry':entry,'warnings':sorted(set(warnings)),
        'provenance':{'input_repository_commit':revision,'source_versions':versions,'snapshot_policy':POLICY,'snapshot_policy_hash':digest(POLICY),
            'raw_file_hashes':files,'feature_recomputed':False,'direction_recomputed':False,'entry_recomputed':False},
        'analysis_compatibility':{'ema_rsi_atr_volume_structure':'existing Feature V1 values and readiness',
            'raw_200_bar_chart_reconstruction':'unavailable; only eight recent completed rows per TF',
            'legacy_chat_scoring':'unavailable; legacy prompt/formulas not supplied','chatgpt_access':'ACCESS_UNVERIFIED',
            'stale_consumer_rule':'recompute freshness against consumer current time; generated status is as-of generation'}}
    if revision!=git_revision(repo):raise ValueError('Checkout changed during projection')
    return validate(sealed(value))


def atomic_write(repo,value):
    validate(value);repo=Path(repo).resolve();target=repo/TARGET
    if target.parent.resolve()!=repo/'output_btc_anytime':raise ValueError('Snapshot namespace symlink')
    if target.exists():
        old=validate(json.loads(target.read_text(encoding='utf-8')))
        if utc_ms(old['generated_at_utc'])>=utc_ms(value['generated_at_utc']):
            if old==value:return 'NOOP'
            raise ValueError('Snapshot time regression/conflict')
    raw=text(value).encode();target.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.analysis-',dir=target.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        validate(json.loads(Path(tmp).read_text(encoding='utf-8')))
        os.replace(tmp,target)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return 'UPDATED'


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,default=Path.cwd());p.add_argument('--write',action='store_true')
    a=p.parse_args();v=build(a.repo)
    status=atomic_write(a.repo,v) if a.write else 'READ_ONLY'
    print(text({'status':status,'snapshot_status':v['snapshot_status'],'bytes':len(text(v).encode()),'generated_at_utc':v['generated_at_utc'],
        'last_received':{tf:r['latest_completed']['source_received_at_utc'] if r['latest_completed'] else None for tf,r in v['market_data'].items()},
        'direction':v['direction'].get('direction_class'),'entry':(v['entry'].get('evaluation') or {}).get('entry_state'),'warnings':v['warnings']}),end='')


if __name__=='__main__':main()

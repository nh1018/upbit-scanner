"""V1.1 draft population-first research. No network or activation creator."""
import json
from collections import Counter
from pathlib import Path
from . import b_prospective as V1
from .contracts import clock, HOUR
from upbit_b.feature_contracts import digest, dumps

VERSION='b-prospective-research-1.1'
POLICY=dict(V1.POLICY, schema_version=VERSION,
    feature_registration='delayed permitted; immutable original completed cutoff; population first',
    source_vintage='AS_OBSERVED/HISTORICAL_AS_RETRIEVED/SOURCE_REVISION_CONFLICT/UNAVAILABLE',
    population='verified full journal lineage; independent of contexts and outcomes',
    attempts='append-only; first valid context frozen; later conflict retained')


def contract(raw):
    old=V1.contract(raw)
    body=dict(old, **POLICY);body.pop('contract_sha256')
    body['previous_contract_sha256']=old['contract_sha256']
    return dict(body,contract_sha256=digest(body))


def activation(a,c,raw):
    if c!=contract(raw):raise ValueError('V1.1 contract mismatch')
    if a is None:raise ValueError('NOT_ACTIVATED')
    if digest({k:v for k,v in a.items() if k!='event_id'})!=a.get('event_id') or a.get('contract_sha256')!=c['contract_sha256']:
        raise ValueError('activation mismatch')
    if a.get('state')!='APPROVED_ACTIVATION' or not a.get('approval_reference'):raise ValueError('approval required')
    for k in ('contract_recorded_at_ms','approved_at_ms','activation_time_ms'):clock(a[k])
    if not a['contract_recorded_at_ms']<=a['approved_at_ms']<a['activation_time_ms']:raise ValueError('retroactive activation')


def seal(body):return dict(body,event_id=digest(body))
def verify(e):
    if e.get('schema_version')!=VERSION or digest({k:v for k,v in e.items() if k!='event_id'})!=e.get('event_id'):
        raise ValueError('event hash/schema mismatch')
    return e


def verify_discovery_sources(discovery,sources):
    """Keep original discovery byte hashes; recognize ONLY exact EOL representation.

    Git LF bytes are not claimed to have the original Windows byte hash.
    Every signal field must match and the alternate bytes must hit the recorded
    external hash exactly. No raw bytes or stored provenance are rewritten.
    """
    from .adapters import b_journals
    V1.validate_discovery(discovery)
    sources=list(sources)
    current={s['signal_id']:s for s in b_journals(sources)}
    representations={ref:{V1.sha(raw),V1.sha(raw.replace(b'\r\n',b'\n')),V1.sha(raw.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'))} for raw,expected,ref in sources}
    for row in discovery['signals']:
        original=row['signal_contract'];now=current.get(original['signal_id'])
        if now==original:continue
        if now is None or original['source_hash'] not in representations.get(original['source_reference'],set()):raise ValueError('discovery byte representation mismatch')
        # signal_record_hash also incorporates source_hash; all other facts exact.
        ignored={'source_hash','signal_record_hash'}
        if {k:v for k,v in now.items() if k not in ignored}!={k:v for k,v in original.items() if k not in ignored}:raise ValueError('discovery signal mismatch')
    return True


def population(sources,c,raw,a):
    """Enumerate ALL verified eligible journals before any outcome/context selection."""
    activation(a,c,raw)
    sources=list(sources)
    from .adapters import b_journals
    verify_discovery_sources(json.loads(raw),sources)
    excluded={x['signal_contract']['signal_id'] for x in json.loads(raw)['signals']}
    result={}
    for s in b_journals(sources):
        if s['signal_id'] in excluded or s['signal_observed_at']<=a['activation_time_ms']:continue
        if s['strategy_version'] not in c['allowed_strategy_versions'] or s['cohort'] not in c['allowed_cohorts']:
            raise ValueError('strategy/cohort changed')
        if not s['performance_eligible']:continue
        e=seal(dict(schema_version=VERSION,kind='POPULATION',activation_event_id=a['event_id'],
                    contract_sha256=c['contract_sha256'],signal=s,initial_state='NOT_ATTEMPTED'))
        if s['signal_id'] in result and result[s['signal_id']]!=e:raise ValueError('population identity conflict')
        result[s['signal_id']]=e
    return sorted(result.values(),key=lambda e:(e['signal']['signal_observed_at'],e['signal']['signal_id']))


def context(s,up,ue,btc,be,completed_at_ms):
    # Reuse V1 exact indicator arithmetic; only the registration deadline differs.
    e=V1.context(s,up,ue,btc,be,completed_at_ms,_delayed=True)
    e.pop('event_id');e['schema_version']=VERSION
    conflict=any(x.get('reason')=='recorded price mismatch' for x in (e['H1'],e['H3']))
    available=any(e[h]['status'] in ('AVAILABLE','PARTIAL') for h in ('H1','H2','H3'))
    e['source_vintage']='SOURCE_REVISION_CONFLICT' if conflict else 'HISTORICAL_AS_RETRIEVED' if available else 'UNAVAILABLE'
    # AS_OBSERVED cannot be claimed from API receipt clocks alone.
    e['original_source_cutoff_ms']=s['source_cutoff']
    return seal(e)


def context_from_responses(s,upbit_raw,ue,btc_raw,be,completed_at_ms):
    """Hash-bound ingestion, not an HTTP client. No original-vintage inference."""
    from upbit_b.market_data import normalize
    from dataclasses import replace
    def decode(raw,ev,provider,instrument):
        rows=V1.verify_response(raw,ev)
        if not isinstance(rows,list):raise ValueError('candle list required')
        cs=[normalize(provider,instrument,'1h',r) for r in rows]
        received=V1._clock_ms(ev['received_at_utc'])
        return [replace(c,completed=c.close_ms<=received) for c in cs],dict(ev,source_row_open_times_ms=[c.open_ms for c in cs])
    up,ue=decode(upbit_raw,ue,'UPBIT',s['market']);btc,be=decode(btc_raw,be,'BINANCE_SPOT','BTCUSDT')
    return context(s,up,ue,btc,be,completed_at_ms)


def validate_context(e):
    verify(e)
    from upbit_b.contracts import Candle
    def rows(k):return [Candle(**{key:V1.number(v) if key in ('open','high','low','close','base_volume','quote_trade_amount') else v for key,v in r.items()}) for r in e['window_rows'][k]]
    replay=context(e['signal_contract'],rows('upbit'),e['source_inputs']['upbit'],rows('btc'),e['source_inputs']['btc'],e['registered_at_ms'])
    if digest(replay)!=digest(e):raise ValueError('context replay mismatch')
    return e


def attempt(member,prior,started,received,completed,responses,ctx=None,error=None):
    verify(member)
    if member['kind']!='POPULATION':raise ValueError('population required first')
    for t in (started,completed):clock(t)
    if received is not None:clock(received)
    if not member['signal']['signal_observed_at']<=started<=completed or received is not None and not started<=received<=completed:raise ValueError('attempt clock order')
    if received is None and (responses or ctx):raise ValueError('missing actual response clock')
    sid=member['signal']['signal_id'];prior=validate_attempts(member,prior)
    if ctx:
        validate_context(ctx)
        if ctx['signal_contract']!=member['signal'] or ctx['registered_at_ms']!=completed:raise ValueError('context identity/clock')
    for r in responses:
        import re
        if not r.get('url') or not re.fullmatch('[0-9a-f]{64}',r.get('response_sha256','')):raise ValueError('response evidence required')
        if not started<=V1._clock_ms(r['received_at_utc'])<=received:raise ValueError('response clock outside attempt')
    if ctx and any(r not in responses for r in ctx['source_evidence']):raise ValueError('context response evidence missing')
    frozen=next((x['context'] for x in prior if x['status']=='SUCCESS'),None)
    conflict=ctx and (ctx['source_vintage']=='SOURCE_REVISION_CONFLICT' or frozen and digest(ctx['window_rows'])!=digest(frozen['window_rows']))
    status='CONFLICT' if conflict else 'SUCCESS' if ctx and all(ctx[h]['status']=='AVAILABLE' for h in ('H1','H2','H3')) else 'PARTIAL' if ctx and ctx['source_vintage']!='UNAVAILABLE' else 'FAILED'
    if status=='FAILED' and not error:error='CONTEXT_UNAVAILABLE'
    return seal(dict(schema_version=VERSION,kind='ATTEMPT',population_event_id=member['event_id'],signal_id=sid,
        request_started_at_ms=started,response_received_at_ms=received,completed_at_ms=completed,
        responses=responses,data_cutoff_ms=member['signal']['source_cutoff'],retry_attempt=len(prior),
        previous_event_id=prior[-1]['event_id'] if prior else None,status=status,error=error,context=ctx))


def validate_attempts(member,events):
    result=[]
    for e in sorted(events,key=lambda e:e['retry_attempt']):
        verify(e)
        if (e['kind']!='ATTEMPT' or e['population_event_id']!=member['event_id'] or e['signal_id']!=member['signal']['signal_id']
            or e['retry_attempt']!=len(result) or e['previous_event_id']!=(result[-1]['event_id'] if result else None)):
            raise ValueError('attempt chain conflict/missing')
        if not member['signal']['signal_observed_at']<=e['request_started_at_ms']<=e['completed_at_ms']:raise ValueError('attempt clocks')
        if e['response_received_at_ms'] is None:
            if e['responses'] or e['context']:raise ValueError('missing response clock')
        elif not e['request_started_at_ms']<=e['response_received_at_ms']<=e['completed_at_ms']:raise ValueError('attempt response clocks')
        if e['data_cutoff_ms']!=member['signal']['source_cutoff']:raise ValueError('cutoff changed')
        if e['status'] not in ('SUCCESS','PARTIAL','FAILED','CONFLICT'):raise ValueError('attempt status')
        if e['context']:
            validate_context(e['context'])
            if e['context']['signal_contract']!=member['signal'] or e['context']['registered_at_ms']!=e['completed_at_ms']:raise ValueError('attempt context identity')
        rebuilt_status='FAILED'
        x=e['context'];frozen=next((r['context'] for r in result if r['status']=='SUCCESS'),None)
        if x:
            conflict=x['source_vintage']=='SOURCE_REVISION_CONFLICT' or frozen and digest(x['window_rows'])!=digest(frozen['window_rows'])
            rebuilt_status='CONFLICT' if conflict else 'SUCCESS' if all(x[h]['status']=='AVAILABLE' for h in ('H1','H2','H3')) else 'PARTIAL' if x['source_vintage']!='UNAVAILABLE' else 'FAILED'
        if e['status']!=rebuilt_status:raise ValueError('attempt status replay')
        for r in e['responses']:
            if not r.get('url') or not r.get('response_sha256') or not e['request_started_at_ms']<=V1._clock_ms(r['received_at_utc'])<=e['response_received_at_ms']:raise ValueError('attempt response clock')
        if x and any(r not in e['responses'] for r in x['source_evidence']):raise ValueError('missing response evidence')
        result.append(e)
    return result


def append(root,event,c,raw,a,member=None):
    """Exclusive content-addressed events; population key prevents duplicate admission."""
    activation(a,c,raw);verify(event)
    if event['kind']=='POPULATION':
        x=V1.validate_signal(event['signal'])
        if (x['strategy']!='B' or not x['performance_eligible'] or x['signal_observed_at']<=a['activation_time_ms']
            or x['signal_id'] in {r['signal_contract']['signal_id'] for r in json.loads(raw)['signals']}
            or x['strategy_version'] not in c['allowed_strategy_versions'] or x['cohort'] not in c['allowed_cohorts']):raise ValueError('ineligible population')
        if event['activation_event_id']!=a['event_id'] or event['contract_sha256']!=c['contract_sha256']:raise ValueError('population activation mismatch')
    elif event['kind']=='ATTEMPT':
        if member is None:raise ValueError('population member required')
        verify(member)
        if member['activation_event_id']!=a['event_id'] or event['population_event_id']!=member['event_id']:raise ValueError('attempt activation mismatch')
        parent=root if isinstance(root,Path) else Path(root)
        member_key=digest(['POPULATION',member['activation_event_id'],member['signal']['signal_id'],None])
        if not (parent/(member_key+'.json')).exists() or json.loads((parent/(member_key+'.json')).read_bytes())!=member:raise ValueError('population must be persisted before attempt')
        prior=[json.loads(p.read_bytes()) for p in parent.glob('*.json') if json.loads(p.read_bytes()).get('population_event_id')==member['event_id'] and json.loads(p.read_bytes()).get('retry_attempt',-1)<event['retry_attempt']]
        validate_attempts(member,prior+[event])
    else:raise ValueError('unknown event kind')
    from upbit_c.research_archive import isolated
    from upbit_c.research_release import no_symlinks
    import os,tempfile
    root=isolated(Path(root));no_symlinks(root)
    repo=Path(__file__).resolve().parents[1]
    if root==repo or repo in root.parents:raise ValueError('repository protected')
    root.mkdir(parents=True,exist_ok=True)
    key=digest([event['kind'],event.get('activation_event_id',event.get('population_event_id')),event.get('signal',{}).get('signal_id',event.get('signal_id')),event.get('retry_attempt')])
    p=root/(key+'.json');data=(dumps(event)+'\n').encode();tmp=None
    try:
        with tempfile.NamedTemporaryFile(dir=root,delete=False) as f:
            tmp=f.name;f.write(data);f.flush();os.fsync(f.fileno())
        try:os.link(tmp,p)
        except FileExistsError:
            if p.read_bytes()!=data:raise ValueError('append-only conflict')
            return 'REPLAY_NOOP'
    finally:
        if tmp:os.unlink(tmp)
    return 'CREATED'


def append_response(root,raw_response,evidence,c,discovery_raw,a):
    """Retain exact source bytes under V1.1 activation, without any HTTP access."""
    activation(a,c,discovery_raw);V1.verify_response(raw_response,evidence)
    from upbit_c.research_archive import isolated
    from upbit_c.research_release import no_symlinks
    import os,tempfile
    root=isolated(Path(root));no_symlinks(root)
    repo=Path(__file__).resolve().parents[1]
    if root==repo or repo in root.parents:raise ValueError('repository protected')
    root=root/'raw_responses';root.mkdir(parents=True,exist_ok=True);no_symlinks(root)
    path=root/(V1.sha(raw_response)+'.json');tmp=None
    try:
        with tempfile.NamedTemporaryFile(dir=root,delete=False) as f:
            tmp=f.name;f.write(raw_response);f.flush();os.fsync(f.fileno())
        try:os.link(tmp,path)
        except FileExistsError:
            if path.read_bytes()!=raw_response:raise ValueError('raw response conflict')
            return 'REPLAY_NOOP'
    finally:
        if tmp:os.unlink(tmp)
    return 'CREATED'


def report(members,attempts,outcomes,c,raw,a,*,sources):
    activation(a,c,raw)
    if members!=population(sources,c,raw,a):raise ValueError("incomplete or reordered population ledger")
    roster={}
    for m in members:
        verify(m);s=V1.validate_signal(m['signal'])
        if (s['strategy']!='B' or not s['performance_eligible'] or s['signal_observed_at']<=a['activation_time_ms']
            or s['signal_id'] in {x['signal_contract']['signal_id'] for x in json.loads(raw)['signals']}
            or s['strategy_version'] not in c['allowed_strategy_versions'] or s['cohort'] not in c['allowed_cohorts']):raise ValueError('ineligible population')
        if m['kind']!='POPULATION' or m['activation_event_id']!=a['event_id'] or m['contract_sha256']!=c['contract_sha256']:raise ValueError('population contract')
        if s['signal_id'] in roster:raise ValueError('duplicate population')
        roster[s['signal_id']]=m
    if any(e['signal_id'] not in roster for e in attempts):raise ValueError('attempt outside population')
    selected_context={};states=Counter();reasons=Counter();vintage=Counter();delays=[]
    for sid,m in sorted(roster.items()):
        chain=validate_attempts(m,[e for e in attempts if e['signal_id']==sid])
        state=chain[-1]['status'] if chain else 'NOT_ATTEMPTED';states[state]+=1
        for e in chain:
            if e['error']:reasons[e['error']]+=1
        frozen=next((e['context'] for e in chain if e['status']=='SUCCESS'),None)
        ctx=frozen or next((e['context'] for e in chain if e['status']=='PARTIAL'),None)
        if ctx:selected_context[sid]=ctx
        vintage[ctx['source_vintage'] if ctx else 'UNAVAILABLE']+=1
        if chain:delays.append(chain[0]['request_started_at_ms']-m['signal']['signal_observed_at'])
    from .aggregate import latest,aggregate,stats
    ev=latest(outcomes)
    for e in ev:
        if e['signal_id'] not in roster or e['signal_contract']!=roster[e['signal_id']]['signal']:raise ValueError('outcome outside population')
    horizons=[]
    for days in (1,3,7):
        end={};chosen=[];excluded=[]
        for m in sorted(roster.values(),key=lambda m:(m['signal']['signal_observed_at'],m['signal']['signal_id'])):
            s=m['signal'];group=(s['strategy_version'],s['cohort'],s['market'])
            if s['evaluation_anchor']<end.get(group,-1):excluded.append(s['signal_id']);continue
            chosen.append(s['signal_id']);end[group]=s['evaluation_anchor']+days*24*HOUR
        rows=[e for e in ev if e['horizon']['days']==days and e['signal_id'] in chosen]
        counts=Counter(e['status'] for e in rows);counts['NOT_EVALUATED']=len(chosen)-len(rows)
        vintage_groups={}
        for grade in ('AS_OBSERVED','HISTORICAL_AS_RETRIEVED','SOURCE_REVISION_CONFLICT','UNAVAILABLE'):
            ids=[sid for sid in chosen if (selected_context[sid]['source_vintage'] if sid in selected_context else 'UNAVAILABLE')==grade]
            grade_rows=[e for e in rows if e['signal_id'] in ids]
            vintage_groups[grade]={'population':len(ids),'evaluated':len(grade_rows),'statistics':stats(grade_rows),
                'hypothesis_groups':{(h+'_'+n if n else h):{key:stats([e for e in grade_rows if e['signal_id'] in selected_context and selected_context[e['signal_id']][h].get('group' if h=='H1' else 'group'+n)==key])
                    for key in (('INCREASE','DECREASE','EQUAL') if h=='H1' else ('POSITIVE','NEGATIVE','ZERO'))}
                    for h,n in (('H1',''),('H2','6'),('H2','24'))}}
        associations={}
        for feature in ('return6_pct','return24_pct','high_distance_pct','range_position_pct'):
            pairs=[(V1.number(selected_context[e['signal_id']]['H3'][feature]),V1.number(e['return_pct']),-V1.number(e['mae_pct'])) for e in rows
                   if e['status']=='MATURED' and e['signal_id'] in selected_context and selected_context[e['signal_id']]['H3'].get(feature) is not None]
            associations[feature]={'n':len(pairs),'return_spearman':V1._rank_corr([p[0] for p in pairs],[p[1] for p in pairs]),
                'adverse_magnitude_spearman':V1._rank_corr([p[0] for p in pairs],[p[2] for p in pairs]),'inference':'DESCRIPTIVE_ONLY'}
        horizons.append(dict(H3_associations=associations,days=days,eligible=len(roster),overlap_excluded=excluded,selected=len(chosen),outcomes=dict(counts),
            hypothesis_available={(h+'_'+n if n else h):sum(sid in selected_context and selected_context[sid][h].get('status'+n)=='AVAILABLE' for sid in chosen) for h,n in (('H1',''),('H2','6'),('H2','24'),('H3',''))},
            source_vintage_groups=vintage_groups,statistics=aggregate(rows),outcome_coverage=str(V1.D(len(rows))/len(chosen)) if chosen else None))
    import statistics
    delay_summary={'count':len(delays),'min':min(delays) if delays else None,'median':str(statistics.median(delays)) if delays else None,'max':max(delays) if delays else None}
    return dict(collection_delay_summary_ms=delay_summary,
        attempt_vintage_counts=dict(Counter(e['context']['source_vintage'] if e['context'] else 'UNAVAILABLE' for e in attempts)),
        completion_delay_ms=sorted(e['completed_at_ms']-roster[e['signal_id']]['signal']['signal_observed_at'] for e in attempts),
        context_success=sum(e['H1']['status']==e['H2']['status']==e['H3']['status']=='AVAILABLE' for e in selected_context.values()),
        context_partial=sum(not(e['H1']['status']==e['H2']['status']==e['H3']['status']=='AVAILABLE') for e in selected_context.values()),
        schema_version=VERSION,eligible=len(roster),context_states=dict(states),failure_reasons=dict(reasons),
        vintage_counts=dict(vintage),vintage_coverage={k:str(V1.D(v)/len(roster)) if roster else None for k,v in vintage.items()},distinct_start_times=len({m['signal']['evaluation_anchor'] for m in members}),
        attempt_count=len(attempts),retry_count=sum(e['retry_attempt']>0 for e in attempts),collection_delay_ms=sorted(delays),context_missing=len(roster)-len(selected_context),horizons=horizons)

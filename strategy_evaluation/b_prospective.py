"""Manual B hypothesis preparation. No network, clock, workflow or activation writes.

The existing engine owns outcomes; this module only validates/fixes research
context and projects existing outcomes into preregistered descriptive groups.
"""
import json
from decimal import Decimal as D, localcontext, ROUND_HALF_EVEN
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from upbit_b.feature_contracts import digest, dumps
from upbit_b.market_data import iso
from upbit_b.features import _clock_ms
from .contracts import HOUR, POLICY_HASH, sha, clock, number, validate_signal
from .engine import evaluate, validate_evaluation, valid_evidence
from .aggregate import aggregate, latest

VERSION = 'b-prospective-research-1'
POLICY = {
    'schema_version': VERSION, 'state': 'DRAFT_NOT_ACTIVATED',
    'activation_time_utc': None, 'activation_approval': None,
    'evaluation_policy_sha256': POLICY_HASH,
    'horizons_days': [1, 3, 7], 'path_hours': [1, 3, 6, 12, 24],
    'H1': 'recent6_quote/prior6_quote; >1 INCREASE, <1 DECREASE, =1 EQUAL; zero denominator UNAVAILABLE',
    'H2': 'BINANCE_SPOT BTCUSDT completed1h close-to-close returns6/24; POSITIVE/NEGATIVE/ZERO',
    'H3': 'completed UPBIT1h returns6/24; (close/high24-1)*100; (close-low24)/(high24-low24)*100',
    'cutoff': 'floor(recorded source_cutoff/HOUR)*HOUR; never after recorded observation',
    'price_reference': 'last completed1h close; exact stored reference match required',
    'pre_window': '25 consecutive completed1h candles ending at cutoff',
    'feature_registration': 'after signal observation, before NEXT_1H_OPEN_PROXY; no late replacement',
    'source_vintage': 'source_received_at <= feature_registered_at; separately record if received after signal observation',
    'classification': 'STABLECOIN/GENERAL_ALT/UNKNOWN; positive classification needs pinned evidence, clock and approval reference',
    'discovery': 'excluded forever by signal_id; all pre-activation observations excluded',
    'cluster': 'evaluation_anchor UTC millisecond; cross-market dependence remains',
    'selection': 'existing aggregate earliest nonoverlap, separately for each horizon; selection before grouping',
    'H3_statistics': 'continuous descriptive rank associations only; no fitted thresholds',
    'costs': 'GROSS_NO_FEES_SLIPPAGE_SPREAD_NOT_ACTUAL_ENTRY',
    'future_BTC': 'outcome-only; never changes pre-signal BTC groups',
    'conclusions': 'descriptive only; no fixed N implies significance; show time clusters and coverage',
}


def contract(discovery_raw):
    discovery = json.loads(discovery_raw)
    validate_discovery(discovery)
    versions=sorted({r['signal_contract']['strategy_version'] for r in discovery['signals']})
    cohorts=sorted({r['signal_contract']['cohort'] for r in discovery['signals']})
    body = dict(POLICY, discovery_file_sha256=sha(discovery_raw), allowed_strategy_versions=versions,allowed_cohorts=cohorts)
    return dict(body, contract_sha256=digest(body))


def validate_contract(c):
    body = {k: v for k, v in c.items() if k != 'contract_sha256'}
    if digest(body) != c.get('contract_sha256'):
        raise ValueError('contract hash mismatch')
    if any(c.get(k) != v for k, v in POLICY.items()):
        raise ValueError('unapproved policy change')
    return c


def validate_discovery(d):
    if d.get('schema_version') != 'b-discovery-cohort-1' or len(d.get('signals', [])) != 16:
        raise ValueError('exact discovery16 required')
    seen = set()
    for r in d['signals']:
        s = validate_signal(r['signal_contract'])
        if s['strategy'] != 'B' or s['signal_id'] in seen:
            raise ValueError('discovery identity conflict')
        seen.add(s['signal_id'])
        e = r['evaluation_24h']
        validate_evaluation(e)
        if e['signal_contract'] != s or e['status'] != 'MATURED':
            raise ValueError('discovery outcome mismatch')
        if any(number(e[k]) != number(r['prior_evaluation'][k]) for k in ('return_pct', 'mfe_pct', 'mae_pct')):
            raise ValueError('prior discovery result mismatch')
    return d


def verify_discovery_sources(d, sources):
    """Requires full original lineage, not just an unverified list of IDs."""
    from .adapters import b_journals
    validate_discovery(d)
    current = {s['signal_id']: s for s in b_journals(sources)}
    for r in d['signals']:
        s = r['signal_contract']
        if current.get(s['signal_id']) != s:
            raise ValueError('discovery journal/observation mismatch')
    return True


def validate_activation(a, c, discovery_raw):
    """Read-only validator for a future separately approved, externally sealed event.

    No activation creator/CLI is provided in this preparation release.
    """
    validate_contract(c)
    if c['discovery_file_sha256'] != sha(discovery_raw):
        raise ValueError('discovery hash mismatch')
    if c != contract(discovery_raw):raise ValueError('derived contract mismatch')
    if a is None:
        raise ValueError('NOT_ACTIVATED')
    body = {k: v for k, v in a.items() if k != 'event_id'}
    if digest(body) != a.get('event_id') or a.get('contract_sha256') != c['contract_sha256']:
        raise ValueError('activation identity mismatch')
    if a.get('state') != 'APPROVED_ACTIVATION' or not a.get('approval_reference'):
        raise ValueError('separate human approval required')
    for k in ('contract_recorded_at_ms', 'approved_at_ms', 'activation_time_ms'):
        clock(a[k])
    if not a['contract_recorded_at_ms'] <= a['approved_at_ms'] < a['activation_time_ms']:
        raise ValueError('activation cannot be retroactive')
    return a


def eligibility(s, c, discovery_raw, activation):
    validate_signal(s)
    validate_activation(activation, c, discovery_raw)
    if s['strategy'] != 'B' or not s['performance_eligible']:
        return 'INELIGIBLE_SIGNAL'
    d = validate_discovery(json.loads(discovery_raw))
    if s['signal_id'] in {r['signal_contract']['signal_id'] for r in d['signals']}:
        return 'DISCOVERY_EXCLUDED'
    if s['signal_observed_at'] <= activation['activation_time_ms']:
        return 'PRE_ACTIVATION_EXCLUDED'
    if s['strategy_version'] not in c['allowed_strategy_versions'] or s['cohort'] not in c['allowed_cohorts']:
        return 'STRATEGY_COHORT_CHANGED'
    return 'ELIGIBLE'


def eligible_from_journals(sources, c, discovery_raw, activation):
    """Only the existing full-lineage adapter can originate real registrations."""
    from .adapters import b_journals
    verify_discovery_sources(json.loads(discovery_raw),sources)
    return [{'signal':s,'eligibility':eligibility(s,c,discovery_raw,activation)} for s in b_journals(sources)]


def _window(candles, evidence, provider, instrument, cutoff, registered, count=25):
    """All clocks and evidence identity are enforced; missing data is not repaired."""
    cs = sorted((c for c in candles if cutoff-count*HOUR <= c.open_ms < cutoff), key=lambda c: c.open_ms)
    if [c.open_ms for c in cs] != list(range(cutoff-count*HOUR, cutoff, HOUR)):
        raise ValueError('MISSING_OR_DUPLICATE_PRE_PATH')
    url = urlparse(evidence['url']); query = parse_qs(url.query)
    if provider == 'UPBIT':
        if not valid_evidence(evidence, instrument):
            raise ValueError('invalid UPBIT evidence')
    elif not (provider == 'BINANCE_SPOT' and instrument == 'BTCUSDT' and url.scheme == 'https'
              and url.netloc == 'data-api.binance.vision' and url.path == '/api/v3/klines'
              and query.get('symbol') == ['BTCUSDT'] and query.get('interval') == ['1h']):
        raise ValueError('invalid Binance spot evidence')
    import re
    if not re.fullmatch('[0-9a-f]{64}', evidence.get('response_sha256', '')):
        raise ValueError('source hash missing')
    received = _clock_ms(evidence['received_at_utc'])
    if received > registered:
        raise ValueError('evidence received after registration')
    for c in cs:
        if (c.provider, c.instrument, c.timeframe, c.completed) != (provider, instrument, '1h', True):
            raise ValueError('forming/mixed candle')
        if c.close_ms != c.open_ms+HOUR or c.close_ms > min(cutoff, received):
            raise ValueError('future candle')
        o,h,l,cl,v,q = map(number, (c.open,c.high,c.low,c.close,c.base_volume,c.quote_trade_amount))
        if min(o,h,l,cl)<=0 or min(v,q)<0 or h<max(o,cl,l) or l>min(o,cl):
            raise ValueError('invalid OHLCV')
        if c.open_ms not in evidence.get('source_row_open_times_ms', []):
            raise ValueError('row evidence missing')
    return cs


def _pct(a, b):
    return (number(a)/number(b)-1)*100


def _sign(v):
    return 'POSITIVE' if v>0 else 'NEGATIVE' if v<0 else 'ZERO'


def context(s, upbit, upbit_evidence, btc, btc_evidence, registered_at_ms, classification=None, *, _delayed=False):
    """Pure context builder; registration gate excludes late hindsight features.

    The supplied source bytes must separately be verified using verify_response.
    """
    validate_signal(s); clock(registered_at_ms)
    if s['strategy'] != 'B' or s['signal_observed_at'] is None:
        raise ValueError('verified B observation required')
    if not (s['signal_observed_at'] <= registered_at_ms and (_delayed or registered_at_ms < s['evaluation_anchor'])):
        raise ValueError('LATE_OR_PRE_SIGNAL_REGISTRATION')
    cutoff=s['source_cutoff']//HOUR*HOUR
    if cutoff>s['signal_observed_at']:
        raise ValueError('future cutoff')
    from dataclasses import asdict
    import copy
    out={'schema_version':VERSION, 'signal_id':s['signal_id'], 'signal_contract':copy.deepcopy(s),
         'signal_record_hash':s['signal_record_hash'], 'registered_at_ms':registered_at_ms,
         'data_cutoff_ms':cutoff, 'time_cluster':s['evaluation_anchor'], 'classification':'UNKNOWN',
         'classification_evidence':None, 'H1':{'status':'UNAVAILABLE'}, 'H2':{'status':'UNAVAILABLE'},
         'H3':{'status':'UNAVAILABLE'}, 'source_evidence':[],
         'window_rows':{'upbit':[asdict(x) for x in upbit if cutoff-25*HOUR<=x.open_ms<cutoff],
                        'btc':[asdict(x) for x in btc if cutoff-25*HOUR<=x.open_ms<cutoff]},
         'source_inputs':copy.deepcopy({'upbit':upbit_evidence,'btc':btc_evidence})}
    if classification is not None:
        required=('category','instrument','evidence_sha256','source_url','classified_at_ms','approval_reference')
        if any(k not in classification for k in required): raise ValueError('classification evidence missing')
        import re
        if (classification['category'] not in ('STABLECOIN','GENERAL_ALT') or classification['instrument']!=s['market']
            or not re.fullmatch('[0-9a-f]{64}',classification['evidence_sha256'])
            or urlparse(classification['source_url']).scheme!='https' or not classification['approval_reference']
            or clock(classification['classified_at_ms'])>registered_at_ms):
            raise ValueError('classification evidence invalid')
        out.update(classification=classification['category'],classification_evidence=classification)
    with localcontext() as ctx:
        ctx.prec,ctx.rounding=34,ROUND_HALF_EVEN
        try:
            cs=_window(upbit,upbit_evidence,'UPBIT',s['market'],cutoff,registered_at_ms,count=12)
            price=number(cs[-1].close)
            if price!=number(s['signal_price_reference']['price']):raise ValueError('recorded price mismatch')
            recent=sum((number(c.quote_trade_amount) for c in cs[-6:]),D(0))
            prior=sum((number(c.quote_trade_amount) for c in cs[-12:-6]),D(0))
            out['H1']={'status':'AVAILABLE' if prior>0 else 'UNAVAILABLE','recent6_quote':str(recent),
                       'prior6_quote':str(prior),'ratio':str(recent/prior) if prior>0 else None,
                       'group':('INCREASE' if recent>prior else 'DECREASE' if recent<prior else 'EQUAL') if prior>0 else 'UNAVAILABLE'}
        except (ValueError,KeyError,TypeError) as exc:out['H1']={'status':'UNAVAILABLE','reason':str(exc)}
        try:
            cs=_window(upbit,upbit_evidence,'UPBIT',s['market'],cutoff,registered_at_ms)
            price=number(cs[-1].close)
            if price!=number(s['signal_price_reference']['price']):raise ValueError('recorded price mismatch')
            hi=max(number(c.high) for c in cs[-24:]);lo=min(number(c.low) for c in cs[-24:])
            out['H3']={'status':'AVAILABLE','reference_price':str(price),'return6_pct':str(_pct(price,cs[-7].close)),
                       'return24_pct':str(_pct(price,cs[-25].close)), 'high_distance_pct':str(_pct(price,hi)),
                       'range_position_pct':str((price-lo)/(hi-lo)*100) if hi>lo else None,
                       'range_status':'AVAILABLE' if hi>lo else 'UNAVAILABLE_ZERO_RANGE'}
        except (ValueError,KeyError,TypeError) as exc:
            out['H3']={'status':'UNAVAILABLE','reason':str(exc)}
        if out['H1']['status']=='AVAILABLE' or out['H3']['status']=='AVAILABLE':out['source_evidence'].append(upbit_evidence)
        h2={};available=0
        for n in (6,24):
            try:
                cs=_window(btc,btc_evidence,'BINANCE_SPOT','BTCUSDT',cutoff,registered_at_ms,count=n+1)
                r=_pct(cs[-1].close,cs[0].close);available+=1
                h2.update({f'return{n}_pct':str(r),f'group{n}':_sign(r),f'status{n}':'AVAILABLE'})
            except (ValueError,KeyError,TypeError) as exc:
                h2.update({f'return{n}_pct':None,f'group{n}':'UNAVAILABLE',f'status{n}':'UNAVAILABLE',f'reason{n}':str(exc)})
        h2['status']='AVAILABLE' if available==2 else 'PARTIAL' if available else 'UNAVAILABLE';out['H2']=h2
        if available:out['source_evidence'].append(btc_evidence)
    out['source_vintage']='RECEIVED_BY_REGISTRATION; not proof of bytes available at original signal time'
    return dict(out,event_id=digest(out))


def verify_response(raw, evidence):
    if sha(raw)!=evidence.get('response_sha256'):raise ValueError('API response hash mismatch')
    return json.loads(raw,parse_float=D)


def context_from_responses(s, upbit_raw, upbit_evidence, btc_raw, btc_evidence, registered_at_ms, classification=None):
    """Public ingestion boundary: hashes, normalization, clock checks; no HTTP calls."""
    from dataclasses import replace
    from upbit_b.market_data import normalize
    def decode(raw, ev, provider, instrument):
        rows=verify_response(raw,ev)
        if not isinstance(rows,list):raise ValueError('response must be candle list')
        cs=[normalize(provider,instrument,'1h',row) for row in rows]
        received=_clock_ms(ev['received_at_utc'])
        return [replace(c,completed=c.close_ms<=received) for c in cs],dict(ev,source_row_open_times_ms=[c.open_ms for c in cs])
    up,ue=decode(upbit_raw,upbit_evidence,'UPBIT',s['market'])
    bc,be=decode(btc_raw,btc_evidence,'BINANCE_SPOT','BTCUSDT')
    return context(s,up,ue,bc,be,registered_at_ms,classification)


def path_diagnostics(s, candles, evidence, as_of_ms):
    """Prefix diagnostics only after the shared +1d engine validates the full path."""
    e=evaluate(s,1,candles,evidence,as_of_ms)
    if e['status']!='MATURED':return {'status':e['status'],'reason':e['reason'],'hours':None}
    cs=sorted((c for c in candles if s['evaluation_anchor']<=c.open_ms<e['endpoint_boundary']),key=lambda c:c.open_ms)
    with localcontext() as ctx:
        ctx.prec,ctx.rounding=34,ROUND_HALF_EVEN
        price=number(e['anchor_price']);hours={}
        for h in POLICY['path_hours']:
            path=cs[:h]
            hours[str(h)]={'return_pct':str(_pct(path[-1].close,price)),
                'mfe_pct':str(max(D(0),_pct(max(number(c.high) for c in path),price))),
                'mae_pct':str(min(D(0),_pct(min(number(c.low) for c in path),price)))}
    return {'status':'MATURED','path_sha256':e['path_sha256'],'hours':hours}


def btc_outcome(s, days, candles, evidence, as_of_ms):
    """Official spot benchmark only, never an input to pre-signal context groups."""
    if days not in POLICY['horizons_days']:raise ValueError('unsupported horizon')
    validate_signal(s);clock(as_of_ms);end=s['evaluation_anchor']+days*24*HOUR
    if as_of_ms<end:return {'status':'PENDING','return_pct':None}
    try:cs=_window(candles,evidence,'BINANCE_SPOT','BTCUSDT',end,as_of_ms,count=days*24)
    except (ValueError,KeyError,TypeError) as exc:return {'status':'UNVERIFIABLE','return_pct':None,'reason':str(exc)}
    with localcontext() as ctx:
        ctx.prec,ctx.rounding=34,ROUND_HALF_EVEN
        r=str(_pct(cs[-1].close,cs[0].open))
    body={'status':'MATURED','return_pct':r,'signal_id':s['signal_id'],'days':days,
          'start_ms':s['evaluation_anchor'],'end_ms':end,'as_of_ms':as_of_ms,'evidence':evidence,
          'use':'OUTCOME_ONLY_NOT_HEDGE_PNL'}
    from dataclasses import asdict
    body['path_rows']=[asdict(c) for c in cs]
    return dict(body,event_id=digest(body))


def validate_context(e):
    if e.get('schema_version')!=VERSION or digest({k:v for k,v in e.items() if k!='event_id'})!=e.get('event_id'):
        raise ValueError('context hash mismatch')
    validate_signal(e['signal_contract'])
    if e['signal_id']!=e['signal_contract']['signal_id'] or e['signal_record_hash']!=e['signal_contract']['signal_record_hash']:
        raise ValueError('context signal mismatch')
    if not e['signal_contract']['signal_observed_at']<=e['registered_at_ms']<e['signal_contract']['evaluation_anchor']:
        raise ValueError('late context')
    from upbit_b.contracts import Candle
    def rows(key):
        out=[]
        for row in e['window_rows'][key]:
            row=dict(row)
            for k in ('open','high','low','close','base_volume','quote_trade_amount'):row[k]=number(row[k])
            out.append(Candle(**row))
        return out
    replay=context(e['signal_contract'],rows('upbit'),e['source_inputs']['upbit'],rows('btc'),e['source_inputs']['btc'],
                   e['registered_at_ms'],e['classification_evidence'])
    if digest(replay)!=digest(e):raise ValueError('context calculation replay mismatch')
    return e


def append_context(root, e, c, discovery_raw, activation):
    """Explicit isolated append-only writer; unavailable until approved activation."""
    validate_context(e)
    if eligibility(e['signal_contract'],c,discovery_raw,activation)!='ELIGIBLE':raise ValueError('ineligible registration')
    from upbit_c.research_archive import isolated
    from upbit_c.research_release import no_symlinks
    root=isolated(Path(root));no_symlinks(root)
    repo=Path(__file__).resolve().parents[1]
    if root==repo or repo in root.parents:raise ValueError('repository output protected')
    root.mkdir(parents=True,exist_ok=True)
    path=root/(digest([activation['event_id'],e['signal_id']])+'.json')
    envelope={'context':e,'contract_sha256':c['contract_sha256'],'activation_event_id':activation['event_id']}
    data=(dumps(envelope)+'\n').encode()
    # Atomic exclusive publication; interrupted temporary files never become records.
    import os
    import tempfile
    temp=None
    try:
        with tempfile.NamedTemporaryFile(dir=root,delete=False) as f:
            temp=f.name;f.write(data);f.flush();os.fsync(f.fileno())
        try:os.link(temp,path)
        except FileExistsError:
            if path.read_bytes()!=data:raise ValueError('append-only conflict')
            return 'REPLAY_NOOP'
    finally:
        if temp is not None:os.unlink(temp)
    return 'CREATED'


def append_response(root, raw, evidence, c, discovery_raw, activation):
    """Future explicit raw sidecar retention, isolated and content-addressed.

    This never fetches data and cannot run while this study is unapproved.
    """
    validate_activation(activation,c,discovery_raw);verify_response(raw,evidence)
    from upbit_c.research_archive import isolated
    from upbit_c.research_release import no_symlinks
    import os
    import tempfile
    root=isolated(Path(root));no_symlinks(root)
    repo=Path(__file__).resolve().parents[1]
    if root==repo or repo in root.parents:raise ValueError('repository output protected')
    directory=root/'raw_responses';directory.mkdir(parents=True,exist_ok=True);no_symlinks(directory)
    path=directory/(sha(raw)+'.json');temp=None
    try:
        with tempfile.NamedTemporaryFile(dir=directory,delete=False) as f:
            temp=f.name;f.write(raw);f.flush();os.fsync(f.fileno())
        try:os.link(temp,path)
        except FileExistsError:
            if path.read_bytes()!=raw:raise ValueError('raw response conflict')
            return 'REPLAY_NOOP'
    finally:
        if temp is not None:os.unlink(temp)
    return 'CREATED'


def report(events, contexts, c, discovery_raw, activation, benchmarks=()):
    """Existing outcome selection BEFORE hypothesis grouping; unavailable stays counted."""
    validate_activation(activation,c,discovery_raw)
    contexts_by_id={}
    for e in contexts:
        validate_context(e)
        if e['signal_id'] in contexts_by_id and contexts_by_id[e['signal_id']]!=e:raise ValueError('context conflict')
        contexts_by_id[e['signal_id']]=e
    eligible=[]
    for e in latest(events):
        s=e['signal_contract']
        if eligibility(s,c,discovery_raw,activation)=='ELIGIBLE':eligible.append(e)
    benchmark_map={}
    for b in benchmarks:
        if b.get('status')!='MATURED':continue
        if digest({k:v for k,v in b.items() if k!='event_id'})!=b.get('event_id'):raise ValueError('benchmark hash mismatch')
        key=(b['signal_id'],b['days'])
        if key in benchmark_map and benchmark_map[key]!=b:raise ValueError('benchmark conflict')
        benchmark_map[key]=b
    output=[]
    for group in aggregate(eligible):
        rows=[e for e in eligible if (e['strategy_version'],e['cohort'],e['horizon']['days'])==
              (group['strategy_version'],group['cohort'],group['horizon_days'])]
        selected=[e for e in rows if e['signal_id'] not in group['overlap_excluded']]
        groups={}
        for name in ('H1','H2_6','H2_24','CLASSIFICATION'):
            buckets={}
            for e in selected:
                x=contexts_by_id.get(e['signal_id'])
                if x and x['signal_contract']!=e['signal_contract']:raise ValueError('context/outcome mismatch')
                key='UNAVAILABLE'
                if x:
                    if name=='H1':key=x['H1'].get('group','UNAVAILABLE')
                    elif name.startswith('H2'):key=x['H2'].get('group'+name.split('_')[1],'UNAVAILABLE')
                    else:key=x['classification']
                buckets.setdefault(key,[]).append(e)
            from .aggregate import stats
            import statistics
            with localcontext() as ctx:
                ctx.prec,ctx.rounding=34,ROUND_HALF_EVEN
                groups[name]={}
                for k,v in buckets.items():
                    matured=[e for e in v if e['status']=='MATURED'];excess=[]
                    for e in matured:
                        b=benchmark_map.get((e['signal_id'],e['horizon']['days']))
                        if b:
                            if b['start_ms']!=e['evaluation_anchor'] or b['end_ms']!=e['endpoint_boundary']:raise ValueError('benchmark boundary mismatch')
                            from upbit_b.contracts import Candle
                            path=[]
                            for row in b['path_rows']:
                                row=dict(row)
                                for key in ('open','high','low','close','base_volume','quote_trade_amount'):row[key]=number(row[key])
                                path.append(Candle(**row))
                            replay=btc_outcome(e['signal_contract'],b['days'],path,b['evidence'],b['as_of_ms'])
                            if digest(replay)!=digest(b):raise ValueError('benchmark calculation mismatch')
                            excess.append(number(e['return_pct'])-number(b['return_pct']))
                    groups[name][k]=dict(stats(v),median_return_pct=str(statistics.median([number(e['return_pct']) for e in matured])) if matured else None,
                        distinct_start_times=len({e['evaluation_anchor'] for e in v}),
                        matured_start_times=len({e['evaluation_anchor'] for e in matured}),
                        context_available=sum(e['signal_id'] in contexts_by_id and (
                            contexts_by_id[e['signal_id']]['H1']['status']=='AVAILABLE' if name=='H1' else
                            contexts_by_id[e['signal_id']]['H2'].get('status'+name.split('_')[1])=='AVAILABLE' if name.startswith('H2') else
                            contexts_by_id[e['signal_id']]['classification']!='UNKNOWN') for e in v),
                        btc_relative_samples=len(excess),mean_simple_excess_pp=str(sum(excess,D(0))/len(excess)) if excess else None)
        associations={}
        for feature in ('return6_pct','return24_pct','high_distance_pct','range_position_pct'):
            pairs=[(number(contexts_by_id[e['signal_id']]['H3'][feature]),number(e['return_pct']),-number(e['mae_pct']))
                   for e in selected if e['status']=='MATURED' and e['signal_id'] in contexts_by_id
                   and contexts_by_id[e['signal_id']]['H3'].get(feature) is not None]
            associations[feature]={'n':len(pairs),'return_spearman':_rank_corr([x[0] for x in pairs],[x[1] for x in pairs]),
                'adverse_magnitude_spearman':_rank_corr([x[0] for x in pairs],[x[2] for x in pairs]),'inference':'DESCRIPTIVE_NOT_SIGNIFICANCE'}
        inc=groups['H1'].get('INCREASE',{}).get('average_return_pct');dec=groups['H1'].get('DECREASE',{}).get('average_return_pct')
        with localcontext() as ctx:
            ctx.prec,ctx.rounding=34,ROUND_HALF_EVEN
            contrast=str(number(inc)-number(dec)) if inc is not None and dec is not None else None
        output.append(dict(group,time_clusters=len({e['evaluation_anchor'] for e in selected}),hypothesis_groups=groups,
                           H1_mean_increase_minus_decrease_pp=contrast,H3_associations=associations))
    return output


def _rank_corr(x,y):
    if len(x)<3:return None
    def ranks(vs):return [D(sum(v<x for v in vs))+D(sum(v==x for v in vs)-1)/2 for x in vs]
    with localcontext() as ctx:
        ctx.prec,ctx.rounding=34,ROUND_HALF_EVEN
        x,y=ranks(x),ranks(y);mx=sum(x)/len(x);my=sum(y)/len(y)
        den=sum((v-mx)**2 for v in x)*sum((v-my)**2 for v in y)
        return str(sum((a-mx)*(b-my) for a,b in zip(x,y))/den.sqrt()) if den>0 else None


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description='Read-only draft validation; no activation/collection command')
    p.add_argument('--contract',type=Path,required=True);p.add_argument('--discovery',type=Path,required=True)
    args=p.parse_args();raw=args.discovery.read_bytes();c=json.loads(args.contract.read_bytes())
    validate_contract(c);validate_discovery(json.loads(raw))
    if c!=contract(raw):raise ValueError('discovery/contract mismatch')
    print(json.dumps({'state':'DRAFT_NOT_ACTIVATED','contract_sha256':c['contract_sha256'],
                      'discovery16':16,'validation_start':None,'production_writes':0}))

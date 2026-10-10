"""Pure gross outcome evaluation. No fetching and no execution-price claims."""
from decimal import Decimal as D, localcontext, ROUND_HALF_EVEN
from upbit_b.feature_contracts import digest
from upbit_b.market_data import UPBIT
from upbit_b.features import _clock_ms
from upbit_c import research_outcomes as C
from .contracts import VERSION, POLICY_HASH, HOUR, number, clock, identity, validate_signal
import re
from urllib.parse import urlparse, parse_qs


def valid_evidence(ev, market):
    parsed=urlparse(ev.get('url',''))
    return (parsed.scheme=='https' and parsed.netloc=='api.upbit.com' and parsed.path=='/v1/candles/minutes/60'
        and parse_qs(parsed.query).get('market')==[market]
        and bool(re.fullmatch('[0-9a-f]{64}',str(ev.get('response_sha256','')))))


def result(s, days, as_of):
    key=identity(s,days);clock(as_of)
    if s['signal_observed_at'] is not None and as_of<s['signal_observed_at']:raise ValueError('evaluation precedes observation')
    return {'evaluation_version':VERSION,'policy_sha256':POLICY_HASH,'evaluation_id':key,
        **{k:s[k] for k in ('strategy','strategy_version','cohort','signal_id','market','signal_observed_at','source_cutoff',
            'signal_price_reference','evaluation_anchor','evaluation_anchor_type','source_hash','source_reference','score','regime','signal_kind','source_cutoff_semantics')},
        'signal_contract':s,
        'horizon':{'days':days},'as_of_ms':as_of,'status':'PENDING','return_pct':None,'mfe_pct':None,'mae_pct':None,
        'data_evidence':[],'path_sha256':None,'anchor_price':None,'endpoint_price':None,
        'endpoint_boundary':s['evaluation_anchor']+days*24*HOUR if s['evaluation_anchor'] is not None else None,
        'fees_included':False,'slippage_included':False,'spread_included':False,'return_basis':'GROSS_LONG_RESEARCH_PROXY',
        'actual_entry':False,'reason':'FUTURE_HORIZON_NOT_COMPLETE'}


def seal(r):
    r=dict(r);r['event_id']=digest(r);return r


def evaluate(s, days, candles, evidence, as_of, original_c_signal=None):
    validate_signal(s)
    r=result(s,days,as_of)
    if not s['performance_eligible']:
        r.update(status='UNVERIFIABLE',reason='RECORDED_AVAILABILITY_UNAVAILABLE');return seal(r)
    if s['strategy']=='C':
        # Reuse the approved C contract without synthesizing a C signal for A/B.
        if original_c_signal is None:raise ValueError('actual sealed C signal required')
        # Identity and every shared clock must agree; no Direction/score re-run.
        from upbit_c.research_history import validate_signal as validate_c
        validate_c(original_c_signal)
        if s.get('original_record_hash')!=digest(original_c_signal):raise ValueError('C original hash mismatch')
        for key,original in [('signal_id','signal_id'),('market','market'),('signal_observed_at','observed_at_ms'),
            ('source_cutoff','trigger_close_ms'),('evaluation_anchor','proxy_anchor_open_ms'),
            ('strategy_version','score_engine_version'),('cohort','parameter_sha256')]:
            if s[key]!=original_c_signal[original]:raise ValueError('C adapter/source mismatch')
        if as_of>=r['endpoint_boundary'] and any(not valid_evidence(e,s['market']) for e in evidence):
            r.update(status='UNVERIFIABLE',reason='INVALID_EVIDENCE_IDENTITY');return seal(r)
        with localcontext() as ctx:
            ctx.prec,ctx.rounding=34,ROUND_HALF_EVEN
            out=C.evaluate(original_c_signal,days,candles,evidence,as_of)
        r.update({k:out[k] for k in ('status','return_pct','mfe_pct','mae_pct','anchor_price','endpoint_price','reason')})
        r.update(data_evidence=out['source_evidence'],path_sha256=out.get('path_sha256'),
                 hmax=out.get('hmax'),lmin=out.get('lmin'))
        return seal(r)
    if as_of<r['endpoint_boundary']:return seal(r)
    anchor,end=s['evaluation_anchor'],r['endpoint_boundary']
    selected=sorted((c for c in candles if anchor<=c.open_ms<end),key=lambda c:c.open_ms)
    r['data_evidence']=list(evidence)
    try:
        seen=[c.open_ms for c in selected]
        if len(set(seen))!=len(seen):raise ValueError('DUPLICATE_OR_CONFLICT')
        if seen!=list(range(anchor,end,HOUR)):raise ValueError('MISSING_PATH')
        for c in selected:
            if c.provider!='UPBIT' or c.instrument!=s['market'] or c.timeframe!='1h' or c.completed is not True or c.close_ms!=c.open_ms+HOUR or c.close_ms>as_of:
                raise ValueError('INVALID_OR_FORMING_CANDLE')
            o,h,l,cl,v,q=[number(x) for x in (c.open,c.high,c.low,c.close,c.base_volume,c.quote_trade_amount)]
            if min(o,h,l,cl)<=0 or min(v,q)<0 or h<max(o,cl,l) or l>min(o,cl):raise ValueError('INVALID_OHLCV')
            if not any(valid_evidence(ev,s['market'])
                       and c.open_ms in ev.get('source_row_open_times_ms',[]) and c.close_ms<=_clock_ms(ev['received_at_utc'])<=as_of for ev in evidence):
                raise ValueError('MISSING_OBSERVATION_EVIDENCE')
    except (ValueError,KeyError,TypeError) as exc:
        r.update(status='UNVERIFIABLE',reason=str(exc));return seal(r)
    with localcontext() as ctx:
        ctx.prec,ctx.rounding=34,ROUND_HALF_EVEN
        price,endpoint=number(selected[0].open),number(selected[-1].close)
        hi,lo=max(number(c.high) for c in selected),min(number(c.low) for c in selected)
        from upbit_c.research_scan import candle_record
        r.update(status='MATURED',reason=None,anchor_price=str(price),endpoint_price=str(endpoint),hmax=str(hi),lmin=str(lo),
            return_pct=str((endpoint/price-1)*100),mfe_pct=str(max(D(0),(hi/price-1)*100)),
            mae_pct=str(min(D(0),(lo/price-1)*100)),path_sha256=digest([candle_record(c) for c in selected]))
    return seal(r)


def validate_evaluation(r):
    if r.get('event_id')!=digest({k:v for k,v in r.items() if k!='event_id'}):raise ValueError('evaluation event hash mismatch')
    if r.get('evaluation_version')!=VERSION or r.get('policy_sha256')!=POLICY_HASH:raise ValueError('unknown evaluation version')
    if r.get('status') not in ('MATURED','PENDING','UNVERIFIABLE'):raise ValueError('invalid status')
    s=r['signal_contract'];validate_signal(s)
    clock(r['as_of_ms'])
    if s['signal_observed_at'] is not None and r['as_of_ms']<s['signal_observed_at']:
        raise ValueError('evaluation precedes observation')
    if any(r[k]!=s[k] for k in ('strategy','strategy_version','cohort','signal_id','market','signal_observed_at','source_cutoff',
                               'evaluation_anchor','evaluation_anchor_type','source_hash','source_reference','score','signal_kind','regime')):
        raise ValueError('event/signal mismatch')
    if identity(s,r['horizon']['days'])!=r['evaluation_id']:raise ValueError('evaluation identity mismatch')
    expected_end=s['evaluation_anchor']+r['horizon']['days']*24*HOUR if s['evaluation_anchor'] is not None else None
    if r['endpoint_boundary']!=expected_end:raise ValueError('endpoint boundary mismatch')
    if r.get('actual_entry') is not False or any(r.get(k) is not False for k in ('fees_included','slippage_included','spread_included')):
        raise ValueError('gross proxy contract required')
    if r['status']=='PENDING' and (expected_end is None or r['as_of_ms']>=expected_end):raise ValueError('invalid pending horizon')
    if r['status']=='MATURED':
        if not s['performance_eligible'] or r['signal_observed_at'] is None or r['as_of_ms']<r['endpoint_boundary'] or not r['path_sha256'] or not r['data_evidence']:
            raise ValueError('invalid maturity/evidence')
        for k in ('return_pct','mfe_pct','mae_pct'):number(r[k])
        if number(r['mfe_pct'])<0 or number(r['mae_pct'])>0:raise ValueError('invalid excursion signs')
        with localcontext() as ctx:
            ctx.prec,ctx.rounding=34,ROUND_HALF_EVEN
            price,endpoint,hi,lo=[number(r[k]) for k in ('anchor_price','endpoint_price','hmax','lmin')]
            if min(price,endpoint,hi,lo)<=0 or hi<max(price,endpoint) or lo>min(price,endpoint):raise ValueError('invalid measured range')
            expected=((endpoint/price-1)*100,max(D(0),(hi/price-1)*100),min(D(0),(lo/price-1)*100))
            if tuple(number(r[k]) for k in ('return_pct','mfe_pct','mae_pct'))!=expected:raise ValueError('metric/price mismatch')
    elif any(r[k] is not None for k in ('return_pct','mfe_pct','mae_pct')):raise ValueError('unmatured metrics prohibited')
    return r

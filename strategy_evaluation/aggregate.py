"""Nonoverlapping descriptive gross statistics; no profitability inference."""
from collections import Counter, defaultdict
from decimal import Decimal as D, localcontext, ROUND_HALF_EVEN
from .contracts import number
from .engine import validate_evaluation


def latest(events):
    by=defaultdict(list);seen={}
    for e in events:
        validate_evaluation(e)
        if e['event_id'] in seen:continue
        seen[e['event_id']]=e;by[e['evaluation_id']].append(e)
    result=[]
    for group in by.values():
        if len({x['signal_contract']['signal_record_hash'] for x in group})!=1:
            raise ValueError('same signal identity with different original observation')
        group.sort(key=lambda x:(x['as_of_ms'],x['event_id']))
        terminal=[x for x in group if x['status']=='MATURED']
        if terminal:
            keys=('return_pct','mfe_pct','mae_pct','anchor_price','endpoint_price','path_sha256')
            if any(any(x[k]!=terminal[0][k] for k in keys) for x in terminal):raise ValueError('conflicting matured result')
            if any(x['status']!='MATURED' and x['as_of_ms']>=terminal[0]['as_of_ms'] for x in group):raise ValueError('terminal regression')
            result.append(terminal[0])
        else:result.append(group[-1])
    return result


def stats(rows):
    counts=Counter(r['status'] for r in rows)
    mature=[r for r in rows if r['status']=='MATURED']
    returns=[number(r['return_pct']) for r in mature]
    wins=[x for x in returns if x>0];loss=[x for x in returns if x<0]
    def mean(xs):return sum(xs,D(0))/len(xs) if xs else None
    def text(x):return str(x) if x is not None else None
    def distribution(key):
        xs=sorted(number(r[key]) for r in mature)
        return {'mean':text(mean(xs)),**{p:text(xs[max(0,(len(xs)*q+99)//100-1)]) if xs else None for p,q in [('p25',25),('p50',50),('p75',75),('p95',95)]}}
    win,lose=mean(wins),mean(loss)
    return {'signals':len(rows),'MATURED':len(mature),'PENDING':counts['PENDING'],'UNVERIFIABLE':counts['UNVERIFIABLE'],
        'evaluation_coverage':text(D(len(mature))/len(rows)) if rows else None,
        'wins':len(wins),'losses':len(loss),'flats':len(returns)-len(wins)-len(loss),
        'win_rate':text(D(len(wins))/len(returns)) if returns else None,
        'average_return_pct':text(mean(returns)),'average_profit_pct':text(win),'average_loss_pct':text(lose),
        'payoff_ratio':text(win/abs(lose)) if win is not None and lose is not None else None,
        'expectancy_pct':text(mean(returns)), 'mfe_pct':distribution('mfe_pct'),'mae_pct':distribution('mae_pct'),
        'denominators':{'win_rate_and_expectancy':'MATURED_NONOVERLAPPING_INCLUDING_FLATS','coverage':'ALL_SELECTED_SIGNALS',
                        'profit':'POSITIVE_MATURED_ONLY','loss':'NEGATIVE_MATURED_ONLY'},
        'return_basis':'GROSS_LONG_PROXY_NOT_REALIZED_PNL'}


def aggregate(events):
    with localcontext() as ctx:
        ctx.prec,ctx.rounding=34,ROUND_HALF_EVEN
        rows=latest(events);groups=defaultdict(list)
        for r in rows:
            key=(r['strategy'],r['strategy_version'],r['cohort'],r['evaluation_anchor_type'],r['horizon']['days'])
            groups[key].append(r)
        output=[]
        for key,group in sorted(groups.items()):
            chosen=[];excluded=[];end_by_market={}
            # Eligibility/selection do not depend on eventual returns or maturity.
            for r in sorted(group,key=lambda x:(x['signal_observed_at'] if x['signal_observed_at'] is not None else -1,x['signal_id'])):
                start=r['evaluation_anchor'];market=r['market']
                if start is not None and start<end_by_market.get(market,-1):excluded.append(r['signal_id']);continue
                chosen.append(r)
                if start is not None:end_by_market[market]=r['endpoint_boundary']
            strength=defaultdict(list)
            for r in chosen:
                score=number(r['score']) if r['score'] is not None else None
                # C's research score is 0..1; never mix units across strategies.
                if score is not None and r['strategy']=='C':score*=100
                band='UNAVAILABLE' if score is None else '<45' if score<45 else '45~<65' if score<65 else '65~<80' if score<80 else '>=80'
                strength[band].append(r)
            output.append({'strategy':key[0],'strategy_version':key[1],'cohort':key[2],'anchor':key[3],'horizon_days':key[4],
                'input_signals':len(group),'overlap_excluded':excluded,'statistics':stats(chosen),
                'strength_bands':{k:stats(v) for k,v in sorted(strength.items())},
                'market_regime':{'status':'UNSUPPORTED','reason':'NO_VALIDATED_REGIME_CONTRACT'},
                'dependence_warning':'Nonoverlap only within each market/group/horizon; cross-market and cross-horizon dependence remains.'})
        return output

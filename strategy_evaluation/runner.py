"""Manual read-only inventory and explicit bounded outcome API helper. No workflow."""
import argparse
import json
from pathlib import Path
from dataclasses import replace
from upbit_b.market_data import UPBIT, normalize, iso
from upbit_b.feature_contracts import dumps
from upbit_c.research_history import read_record
from . import adapters as A
from .contracts import sha, HOUR, clock, HORIZONS, VERSION
from .engine import evaluate


def fetch_evaluation(client, signal, days, as_of, original_c_signal=None):
    """Operator-invoked read-only price retrieval, never a strategy rescan."""
    if days not in HORIZONS or type(days) is not int:raise ValueError('unsupported horizon')
    clock(as_of)
    if signal['evaluation_anchor'] is None or as_of<signal['evaluation_anchor']+days*24*HOUR:
        return evaluate(signal,days,[],[],as_of,original_c_signal)
    end=signal['evaluation_anchor']+days*24*HOUR
    try:
        rows,evidence=client.get(UPBIT,'/v1/candles/minutes/60',{'market':signal['market'],'count':days*24,'to':iso(end)})
        if not isinstance(rows,list):raise ValueError('invalid candle response')
        candles=[replace(normalize('UPBIT',signal['market'],'1h',r),completed=True) for r in rows]
        evidence=dict(evidence,source_row_open_times_ms=[c.open_ms for c in candles])
        from upbit_b.features import _clock_ms
        observed=max(as_of,_clock_ms(evidence['received_at_utc']))
        return evaluate(signal,days,candles,[evidence],observed,original_c_signal)
    except (ValueError,TypeError,OSError,KeyError) as exc:
        r=evaluate(signal,days,[],[],as_of,original_c_signal)
        r.update(status='UNVERIFIABLE',reason='API_OR_SOURCE_ERROR:'+type(exc).__name__)
        r.pop('event_id',None)
        from .engine import seal
        return seal(r)


def inventory(repo, c_scan=None, c_sha=None):
    repo=Path(repo)
    a_path=repo/'output/latest_scan.json';a_raw=a_path.read_bytes()
    a=A.a_snapshot(a_raw,sha(a_raw),'output/latest_scan.json')
    sources=[]
    for p in sorted((repo/'output_upbit_b/v1/history').glob('*/*.jsonl')):
        raw=p.read_bytes();sources.append((raw,sha(raw),p.relative_to(repo).as_posix()))
    try:b=A.b_journals(sources);b_error=None
    except ValueError as e:b=[];b_error=str(e)
    result={'schema_version':'abc-evaluation-read-only-inventory-1','evaluation_version':VERSION,
        'A':{'source_hash':sha(a_raw),'candidate_observations':len(a),'performance_eligible':sum(s['performance_eligible'] for s in a),
             'limitation':'Legacy scan start is not receipt; no historical availability invented'},
        'B':{'journal_files':len(sources),'verified_positive_transitions':len(b),'chain_error':b_error},
        'C':{'signals':None,'reason':'verified scan not supplied'},'production_writes':0,'api_calls':0}
    if c_scan is not None:
        from .contracts import external
        raw=Path(c_scan).read_bytes();external(raw,c_sha);report=read_record(c_scan)
        # Count stored PASS conditions only; never emit/reconstruct signals from an old scan.
        count=sum(r.get('score') is not None and r['score'].get('research_setup')=='PASS' for r in report['results'])
        result['C']={'scan_id':report['scan_id'],'scan_sha256':c_sha,'recorded_PASS_conditions':count,
            'signals_created':0,'win_rate':None,'reason':'No stored prospective C signal supplied'}
    return result,a,b


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path);p.add_argument('--c-scan',type=Path);p.add_argument('--c-sha256')
    p.add_argument('--bundle',type=Path,help='Externally pinned manual evaluation input, no API call')
    p.add_argument('--bundle-sha256');p.add_argument('--record-root',type=Path,help='Explicit isolated append-only destination; default stdout only')
    args=p.parse_args(argv)
    if args.bundle:
        from .contracts import external, number
        from upbit_b.contracts import Candle
        raw=args.bundle.read_bytes();external(raw,args.bundle_sha256);bundle=json.loads(raw)
        cs=[]
        for row in bundle['candles']:
            row=dict(row)
            for key in ('open','high','low','close','base_volume','quote_trade_amount'):row[key]=number(row[key])
            cs.append(Candle(**row))
        result=evaluate(bundle['signal'],bundle['days'],cs,bundle['evidence'],bundle['as_of_ms'],bundle.get('original_c_signal'))
        if args.record_root:
            from .storage import append
            publication=append(args.record_root,result)
            print(dumps({'publication':publication,'evaluation':result}));return result
    else:
        if args.repo is None or args.record_root is not None:p.error('--repo required for inventory; --record-root requires --bundle')
        result,_,_=inventory(args.repo,args.c_scan,args.c_sha256)
    print(dumps(result));return result


if __name__=='__main__':main()

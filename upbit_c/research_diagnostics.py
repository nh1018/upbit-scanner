"""Evidence-based coverage diagnosis; never fills candles or changes scoring eligibility."""
import argparse
import json
import hashlib
from collections import Counter
from pathlib import Path
from .research_history import read_record
from .evidence import FIELDS


def probe_sources(audit, client):
    """Repeat narrowly scoped official source queries, retaining new evidence separately."""
    from upbit_b.contracts import DURATIONS
    from upbit_b.market_data import UPBIT, iso, normalize
    result=[]
    for row in audit['markets']:
        for tf,item in row['timeframes'].items():
            if item['status'] not in ('MISSING_CANDLES','INSUFFICIENT_DATA','STALE_DATA'):
                continue
            duration=DURATIONS[tf]
            if item['status']=='MISSING_CANDLES':
                target=item['example_missing_open_ms'][0]
                end=target+duration
            elif item['status']=='STALE_DATA':
                end=audit['source_cutoff_ms']//duration*duration
                target=end-duration
            else:
                end=item['first_open_ms'];target=None
            endpoint='/v1/candles/days' if tf=='1d' else '/v1/candles/minutes/'+str(duration//60000)
            observation={'market':row['market'],'timeframe':tf,'original_status':item['status'],
                'target_open_ms':target,'query_to_ms':end,'independent_trade_history':'unavailable',
                'official_listing_date':'unavailable'}
            try:
                data,ev=client.get(UPBIT,endpoint,{'market':row['market'],'count':2,'to':iso(end)})
                if not isinstance(data,list):raise ValueError('invalid candle response')
                parsed=[normalize('UPBIT',row['market'],tf,c) for c in data]
                if any(c.open_ms>=end for c in parsed):raise ValueError('source range violation')
                opens=[c.open_ms for c in parsed]
                observation.update(source_evidence=ev,returned_open_times_ms=opens,
                    result=('NO_EARLIER_CANDLES_RETURNED' if not opens else 'EARLIER_CANDLES_NOW_AVAILABLE') if target is None else ('ORIGINAL_MISSING_SLOT_STILL_ABSENT' if target not in opens else 'ORIGINAL_MISSING_SLOT_NOW_PRESENT'))
            except (ValueError,TypeError,OSError) as exc:
                observation.update(result='PROBE_UNAVAILABLE',reason=str(exc))
            result.append(observation)
    return {'schema_version':'upbit-c-source-probes-11','activation':'RESEARCH_ONLY',
        'source_scan_id':audit['source_scan_id'],'logical_queries':len(result),
        'result_counts':dict(Counter(x['result'] for x in result)),'observations':result,
        'original_records_modified':0,'eligibility_changed':False}


def diagnose(report):
    failures=[]
    status_counts=Counter()
    classifications=Counter()
    for row in report['results']:
        if row.get('score_status')=='EVALUATED':continue
        item={'market':row['market'],'score_status':row['score_status'],'timeframes':{}}
        for tf,source in row['timeframes'].items():
            status=source['status']
            if status=='EVALUATED':continue
            status_counts[status]+=1
            cs=source.get('candles',[])
            ev=source.get('source_evidence',[])
            gaps=source.get('missing_slots',[])
            opens={t for e in ev for t in e.get('source_row_open_times_ms',[])}
            feature=source.get('feature_snapshot',{})
            ready=feature.get('readiness',{})
            meta=feature.get('metadata',{})
            empty_sha=hashlib.sha256(b'[]').hexdigest()
            empty_response=any(e.get('source_row_open_times_ms')==[] or e.get('response_sha256')==empty_sha for e in ev)
            if status=='MISSING_CANDLES':
                classification='SOURCE_RESPONSES_OMIT_CALENDAR_SLOTS' if gaps and all(t not in opens for t in gaps) else 'UNRESOLVED_GAP_OR_COLLECTION_RANGE'
            elif status=='INSUFFICIENT_DATA':
                classification='OFFICIAL_ACCESSIBLE_HISTORY_EXHAUSTED' if empty_response else 'BOUNDED_COLLECTION_HISTORY_SHORT'
            elif status=='STALE_DATA':classification='LATEST_COMPLETED_BOUNDARY_ABSENT'
            elif status=='INSUFFICIENT_FEATURES':classification='MANDATORY_C_FEATURE_UNAVAILABLE'
            else:classification='API_OR_VALIDATION_FAILURE'
            classifications[classification]+=1
            item['timeframes'][tf]={'status':status,'classification':classification,
                'row_count':len(cs),'page_count':len(ev),'empty_response_observed':empty_response,
                'exact_empty_array_response_hash_matched':any(e.get('response_sha256')==empty_sha for e in ev),
                'first_open_ms':cs[0]['open_ms'] if cs else None,
                'last_close_ms':cs[-1]['close_ms'] if cs else None,
                'source_input_sha256':source.get('source_input_sha256'),
                'response_sha256s':[e.get('response_sha256') for e in ev],
                'missing_slot_count':len(gaps),'example_missing_open_ms':gaps[:3],
                'all_gaps_absent_from_received_source_rows':bool(gaps) and all(t not in opens for t in gaps),
                'latest_contiguous_segment_rows':meta.get('available_history'),
                'c_fields_not_ready':{f:ready.get(f,'MISSING') for f in FIELDS if ready.get(f)!='READY'},
                'latest_segment_has_all_C_fields':all(ready.get(f)=='READY' for f in FIELDS),
                'official_listing_date':'unavailable',
                'confirmed_zero_trades':'unavailable',
                'reason':source.get('reason')}
        failures.append(item)
    return {'schema_version':'upbit-c-coverage-diagnosis-11','activation':'RESEARCH_ONLY',
        'source_scan_id':report['scan_id'],'source_cutoff_ms':report['source_cutoff_ms'],
        'failed_market_count':len(failures),'failed_timeframe_counts':dict(status_counts),
        'classification_timeframe_counts':dict(classifications),
        'eligibility_changed':False,'candles_imputed':0,'score_parameters_changed':False,
        'limitations':['Official candle absence is consistent with no trades, but independent trade history/listing dates were not obtained.',
            'C requires AVAILABLE complete windows and all 15 fields; a ready trailing segment alone does not override that contract.'],
        'markets':failures}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('scan_record',type=Path)
    p.add_argument('--output',type=Path)
    a=p.parse_args(argv)
    result=diagnose(read_record(a.scan_record))
    text=json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    if a.output:
        a.output.parent.mkdir(parents=True,exist_ok=True)
        with a.output.open('x',encoding='utf-8') as f:f.write(text)
    else:print(text)
    return result


if __name__=='__main__':main()

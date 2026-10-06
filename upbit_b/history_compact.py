"""Lossless cycle-local JSONL dictionaries. No deleted fields or binary compression."""
from collections import Counter
from copy import deepcopy
import hashlib
import json

from . import feature_contracts as F
from . import history_contracts as C

SCHEMA = 'upbit-b-history-compact-1'
CODEC = 'named-record-layout-and-value-dictionaries-1'
STORAGE_CONTRACT = {'schema':SCHEMA,'codec':CODEC,'references':'cycle_local_only','lossless':True}
STORAGE_HASH = F.digest(STORAGE_CONTRACT)


def versions():
    # Existing selection/calculation policy stays identical; storage identity alone
    # creates a new cohort. No migration claims a verified cross-cohort transition.
    return {**C.versions(),'history_storage_schema':SCHEMA,'history_storage_hash':STORAGE_HASH}


def _sha(raw):return hashlib.sha256(raw).hexdigest()


def pack_values(manifest,records):
    """Internal encoder also used on explicitly nonpublishable size projections."""
    roots=[manifest,*records];strings=Counter();containers=Counter();objects={};layouts=set()
    def inventory(value):
        if isinstance(value,str):strings[value]+=1
        elif isinstance(value,(dict,list)):
            key=F.dumps(value)
            if len(key)>=100:containers[key]+=1;objects[key]=value
            if isinstance(value,dict):
                layouts.add(tuple(sorted(value)))
                for child in value.values():inventory(child)
            else:
                for child in value:inventory(child)
    for root in roots:inventory(root)
    fields=sorted(layouts);field_index={keys:i for i,keys in enumerate(fields)}
    # Only strings whose gross repeat savings exceed table+reference overhead.
    string_pool=sorted(s for s,n in strings.items() if n>1 and (n-1)*len(F.dumps(s))>n*16+4)
    string_index={s:i for i,s in enumerate(string_pool)}
    pool_keys=sorted((k for k,n in containers.items() if n>1 and (n-1)*len(k)>n*24+8),key=lambda k:(len(k),k))
    pool_index={k:i for i,k in enumerate(pool_keys)}
    def encode(value,limit=None):
        if isinstance(value,str):return [-2,string_index[value]] if value in string_index else value
        if isinstance(value,(dict,list)):
            key=F.dumps(value);idx=pool_index.get(key)
            if idx is not None and (limit is None or idx<limit):return [-3,idx]
            if isinstance(value,list):return [-1,*[encode(x,limit) for x in value]]
            keys=tuple(sorted(value));return [field_index[keys],*[encode(value[k],limit) for k in keys]]
        return value
    # Pooling an entire repeated subtree can make its nested tables redundant.
    # Count actual reachable references, not repetition hidden behind a parent.
    # Shrink only; all retained/removed dictionary entries still reconstruct the
    # identical tree. This reduces dictionary overhead without dropping evidence.
    while True:
        shared=[encode(objects[k],i) for i,k in enumerate(pool_keys)]
        encoded_roots=[encode(r) for r in roots]
        string_uses=Counter();shared_uses=Counter();visited=set();used_layouts=set()
        def usage(v):
            if not isinstance(v,list):return
            tag=v[0]
            if tag==-2:string_uses[string_pool[v[1]]]+=1;return
            if tag==-3:
                idx=v[1];shared_uses[pool_keys[idx]]+=1
                if idx not in visited:visited.add(idx);usage(shared[idx])
                return
            if tag>=0:used_layouts.add(tag)
            for child in v[1:]:usage(child)
        for r in encoded_roots:usage(r)
        next_strings=[s for s in string_pool if string_uses[s]>1 and (string_uses[s]-1)*len(F.dumps(s))>string_uses[s]*16+4]
        next_pool=[k for k in pool_keys if shared_uses[k]>1]
        if next_strings==string_pool and next_pool==pool_keys:break
        string_pool=next_strings;string_index={s:i for i,s in enumerate(string_pool)}
        pool_keys=next_pool;pool_index={k:i for i,k in enumerate(pool_keys)}
    # Remove unused field layouts left behind by exact shared subtrees.
    selected_fields=[fields[i] for i in sorted(used_layouts)]
    layout_remap={old:new for new,old in enumerate(sorted(used_layouts))}
    def remap(v):
        if not isinstance(v,list):return v
        if v[0] in (-2,-3):return v
        return [layout_remap[v[0]] if v[0]>=0 else v[0],*[remap(x) for x in v[1:]]]
    shared=[remap(x) for x in shared];fields=selected_fields
    encoded_records=[{'instrument':r['instrument'],'record_kind':r['record_kind'],
        'candidate':r['summary']['candidate'],'state':r['summary']['state'],'data':remap(encode(r))} for r in records]
    raw_records=b''.join((F.dumps(r)+'\n').encode() for r in encoded_records)
    logical=(F.dumps(manifest)+'\n').encode()+b''.join((F.dumps(r)+'\n').encode() for r in records)
    header={'record_kind':'MANIFEST','schema_version':SCHEMA,'codec':CODEC,
        'storage_contract':STORAGE_CONTRACT,'storage_contract_sha256':STORAGE_HASH,
        'cycle_id':manifest.get('cycle_id'),'source_cutoff':manifest.get('source_cutoff'),
        'cohort_id':manifest.get('cohort_id'),'mode':manifest.get('mode'),
        'scan_counts':{k:manifest.get(k) for k in ('universe_count','candidate_count','unknown_count','failed','unattempted')},
        'field_layouts':[list(keys) for keys in fields],'strings':string_pool,'shared_values':shared,
        'logical_manifest':remap(encode(manifest)),'record_count':len(records),
        'logical_payload_sha256':_sha(logical),'encoded_records_sha256':_sha(raw_records)}
    header['header_sha256']=F.digest(header)
    return (F.dumps(header)+'\n').encode()+raw_records


def pack(payload):
    from .history import validate_cycle
    manifest,records=validate_cycle(payload)
    return pack_values(manifest,records)


def unpack(payload):
    """Strict deterministic decoder; returns exact original canonical logical bytes."""
    if not isinstance(payload,bytes) or not payload.endswith(b'\n'):raise ValueError('compact canonical newline required')
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:raise ValueError('duplicate compact JSON key')
            result[k]=v
        return result
    lines=payload.decode('utf-8').splitlines()
    values=[json.loads(line,object_pairs_hook=unique) for line in lines]
    if any(F.dumps(value)!=line for value,line in zip(values,lines)):raise ValueError('noncanonical compact JSONL')
    h=values[0]
    if h.get('schema_version')!=SCHEMA or h.get('codec')!=CODEC:raise ValueError('unsupported compact schema/codec')
    if h.get('storage_contract')!=STORAGE_CONTRACT or h.get('storage_contract_sha256')!=STORAGE_HASH:raise ValueError('compact storage contract')
    if h.get('header_sha256')!=F.digest({k:v for k,v in h.items() if k!='header_sha256'}):raise ValueError('compact header hash')
    if h['record_count']!=len(values)-1 or h['encoded_records_sha256']!=_sha(b''.join((line+'\n').encode() for line in lines[1:])):
        raise ValueError('compact records count/hash')
    fields=h['field_layouts'];strings=h['strings'];shared=h['shared_values']
    if not isinstance(fields,list) or any(not isinstance(keys,list) or not all(isinstance(k,str) for k in keys) or keys!=sorted(set(keys)) for keys in fields):
        raise ValueError('invalid field layouts')
    if len({tuple(keys) for keys in fields})!=len(fields) or not isinstance(strings,list) or not all(isinstance(s,str) for s in strings):
        raise ValueError('invalid dictionary')
    resolved=[]
    def index(value,length):
        if isinstance(value,bool) or not isinstance(value,int) or not 0<=value<length:raise ValueError('invalid compact reference')
        return value
    def decode(value,depth=0):
        if depth>100:raise ValueError('compact nesting limit')
        if not isinstance(value,list):
            if isinstance(value,dict):raise ValueError('encoded object must use a layout')
            return value
        if not value or isinstance(value[0],bool) or not isinstance(value[0],int):raise ValueError('invalid compact tag')
        tag=value[0]
        if tag==-1:return [decode(x,depth+1) for x in value[1:]]
        if tag in (-2,-3):
            if len(value)!=2:raise ValueError('reference arity')
            pool=strings if tag==-2 else resolved
            return deepcopy(pool[index(value[1],len(pool))])
        keys=fields[index(tag,len(fields))]
        if len(value)!=len(keys)+1:raise ValueError('layout arity')
        return {k:decode(x,depth+1) for k,x in zip(keys,value[1:])}
    for value in shared:resolved.append(decode(value))  # backward references only: cycles rejected
    manifest=decode(h['logical_manifest']);records=[]
    for v in values[1:]:
        if not isinstance(v,dict) or set(v)!={'instrument','record_kind','candidate','state','data'}:raise ValueError('compact record view schema')
        r=decode(v['data'])
        if (v['instrument'],v['record_kind'],v['candidate'],v['state'])!=(r['instrument'],r['record_kind'],r['summary']['candidate'],r['summary']['state']):
            raise ValueError('compact human-readable view mismatch')
        records.append(r)
    logical=(F.dumps(manifest)+'\n').encode()+b''.join((F.dumps(r)+'\n').encode() for r in records)
    if _sha(logical)!=h['logical_payload_sha256'] or manifest.get('cycle_id')!=h['cycle_id'] or manifest.get('source_cutoff')!=h['source_cutoff']:
        raise ValueError('compact logical identity/hash')
    if manifest.get('cohort_id')!=h['cohort_id'] or manifest.get('mode')!=h['mode'] or h['scan_counts']!={k:manifest.get(k) for k in h['scan_counts']}:
        raise ValueError('compact manifest view mismatch')
    from .history import validate_cycle
    validate_cycle(logical)
    return logical


def storage_report(payload,entries):
    """Same actual projection, plus explicit size-only normal-cycle scenarios."""
    from .history import validate_cycle,_compact
    from .history_runner import storage_report as legacy_report
    from decimal import Decimal as D,localcontext
    m,records=validate_cycle(payload);encoded=pack(payload)
    legacy=legacy_report(payload,entries)
    # Keep record attribution and dictionary overhead separate, avoiding a false
    # claim that encoded line lengths already include their shared evidence.
    h=json.loads(encoded.split(b'\n',1)[0]);encoded_lines=encoded.splitlines()[1:]
    by_type={}
    for r,line in zip(records,encoded_lines):
        kind=('FAILURE' if r.get('failure_reason') else 'UNKNOWN' if r['summary']['candidate']=='UNKNOWN' else
              'CANDIDATE_DETAIL' if r['summary']['candidate']=='TRUE' else 'CONTROL' if r['selection_reason']=='CONTROL' else r['record_kind'])
        info=by_type.setdefault(kind,{'count':0,'legacy_record_bytes':0,'compact_record_line_bytes':0})
        info['count']+=1;info['legacy_record_bytes']+=len((F.dumps(r)+'\n').encode());info['compact_record_line_bytes']+=len(line)+1
    candidates=[r for r in records if r['summary']['candidate']=='TRUE']
    controls=[r for r in records if r['selection_reason']=='CONTROL']
    state_pool=[r for r in records if r['summary']['candidate']=='FALSE' and r not in controls]
    def shape(r,kind,detail=False):
        item=deepcopy(r);item.update(record_kind=kind,events=deepcopy(r['events']) if kind=='STATE_DELTA' else [],selection_reason=kind,
            previous_state_reference=r['observation_id'],previous_effective_cycle_id=m['cycle_id'])
        if detail:return item
        item.pop('evidence',None)
        if kind in ('HEARTBEAT','CONTROL'):
            item.update(evidence_level='COMPACT',observation=_compact(entries[r['instrument']]),previous_detail_reference=r['observation_id'])
        else:item.pop('observation',None);item.pop('previous_detail_reference',None);item['evidence_level']='STATE_ONLY'
        return item
    scenarios=[]
    # Four hourly phases, two detail-rate cases (2 or 3/hour => 60/day).
    # Each projection selects distinct current observations; these are NOT future
    # signals, candidate transitions or usable production journals.
    for phase in range(4):
        for detail_count in (2,3):
            details=candidates[:detail_count]
            rs=[shape(r,'STATE_DELTA',True) for r in details]+[shape(r,'CONTROL') for r in controls]+[shape(r,'STATE_DELTA') for r in state_pool[:24]]
            if phase==0:rs.extend(shape(r,'HEARTBEAT') for r in candidates[detail_count:])
            sm=deepcopy(m);sm.update(mode='SIZE_ONLY_NONPUBLISHABLE',membership={'baseline':None,'added':[],'removed':[]})
            # Same metadata is retained conservatively; scenario rates are not a
            # forecast of observed future manifests or market transitions.
            old=(F.dumps(sm)+'\n').encode()+b''.join((F.dumps(r)+'\n').encode() for r in rs)
            new=pack_values(sm,rs)
            groups={}
            for row,line in zip(rs,new.splitlines()[1:]):
                kind='CANDIDATE_DETAIL' if row['evidence_level']=='DETAIL' else row['record_kind']
                group=groups.setdefault(kind,{'count':0,'legacy_record_bytes':0,'compact_record_line_bytes':0})
                group['count']+=1;group['legacy_record_bytes']+=len((F.dumps(row)+'\n').encode());group['compact_record_line_bytes']+=len(line)+1
            scenarios.append({'phase':phase,'details':len(details),'heartbeats':max(len(candidates)-detail_count,0) if phase==0 else 0,
                'controls':len(controls),'state_changes':min(len(state_pool),24),'legacy_bytes':len(old),'compact_bytes':len(new),
                'dictionary_header_bytes':len(new.splitlines()[0])+1,'groups':groups})
    with localcontext() as ctx:
        ctx.prec=34
        old_normal=D(sum(s['legacy_bytes'] for s in scenarios))/len(scenarios)
        new_normal=D(sum(s['compact_bytes'] for s in scenarios))/len(scenarios)
        daily=new_normal*24
        import gzip,subprocess,tempfile
        with tempfile.TemporaryDirectory(prefix='b-compact-git-') as folder:
            subprocess.run(['git','init','--bare','--quiet',folder],check=True,capture_output=True)
            git_sizes={}
            for name,raw in (('legacy',payload),('compact',encoded)):
                result=subprocess.run(['git','--git-dir='+folder,'hash-object','-w','--stdin'],input=raw,check=True,capture_output=True)
                oid=result.stdout.decode().strip()
                from pathlib import Path
                git_sizes[name]=(Path(folder)/'objects'/oid[:2]/oid[2:]).stat().st_size
        return F.canonical({'legacy_existing_estimator':legacy,'cold_start':{'legacy_bytes':len(payload),'compact_bytes':len(encoded),
            'saving_pct':100*(1-D(len(encoded))/len(payload)),'dictionary_header_bytes':len(encoded.splitlines()[0])+1,
            'layouts':len(h['field_layouts']),'strings':len(h['strings']),'shared_values':len(h['shared_values']),'by_type':by_type},
            'normal_cycle_sizing_scenarios':scenarios,'estimated_normal_cycle_legacy_bytes':old_normal,
            'estimated_normal_cycle_compact_bytes':new_normal,'normal_saving_pct':100*(1-new_normal/old_normal),
            'daily_estimated_bytes':daily,'30_days_estimated_bytes':daily*30,'180_days_estimated_bytes':daily*180,'365_days_estimated_bytes':daily*365,
            'legacy_same_scenarios_365_days':old_normal*24*365,
            'storage_format_comparison':{'jsonl_legacy':len(payload),'jsonl_compact':len(encoded),
                'gzip_legacy_bytes':len(gzip.compress(payload,mtime=0)),'gzip_compact_bytes':len(gzip.compress(encoded,mtime=0)),
                'gzip_adopted':False,'reason':'retain text JSONL and git/browser access; gzip needs decompression and impairs text diff',
                'temporary_git_loose_blob_bytes':git_sizes,'git_annual_growth_measured':False,
                'git_risks':['8760 cycle files/commits per year if hourly; tree/history overhead',
                             'Git compression/delta/pack behavior differs from JSONL size; no guaranteed annual ratio',
                             'clones and Actions checkout still carry immutable history; no pruning or rewriting']},
            'assumptions':{'candidates':len(candidates),'controls':len(controls),'details_per_day':min(D(len(candidates)),D('2.5'))*24,
                'state_changes_per_hour':min(len(state_pool),24),'heartbeat_hours':4,'heartbeat_overridden_by_detail':True,
                'future_event_rate_measured':False,'normal_projections_are_not_signals':True},
            'production_files_created':0,'logical_roundtrip_bytes_equal':unpack(encoded)==payload})


def main():
    import argparse
    from pathlib import Path
    from .history import validate_cycle
    parser=argparse.ArgumentParser(description='Read-only logical JSONL viewer for legacy/Compact files')
    parser.add_argument('file',type=Path);args=parser.parse_args()
    raw=args.file.read_bytes();m,records=validate_cycle(raw)
    print(F.dumps(m))
    for r in records:print(F.dumps(r))


if __name__=='__main__':main()

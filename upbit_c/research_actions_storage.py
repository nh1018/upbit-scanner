"""V1.3 manual Actions transport: small index + immutable cold deltas.

Artifact retention is bounded. Receipts prove referenced bytes were published;
metadata preflight proves current existence, not a fresh full cold-byte audit.
"""
import io
import json
import os
import re
import sys
import time
import zipfile
import argparse
import tempfile
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

from upbit_b.feature_contracts import dumps, digest
from . import research_archive as A
from . import research_segments as V11
from .research_operations import NoRedirect, manual_mode, github_loader

SCHEMA = 'upbit-c-actions-storage-1.3'
COLD_SCHEMA = 'upbit-c-actions-cold-1.3'
INDEX_NAME = 'upbit-c-research-index-1'
COLD_NAME = 'upbit-c-research-cold-1'


def run_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[1-9][0-9]*',value):
        raise ValueError('invalid Actions run ID')
    return value


def index_path(name):
    if not re.fullmatch(r'(runs/[1-9][0-9]*|(?:active|descriptors|imports)/[0-9a-f]{64})\.json',name):
        raise ValueError('unsafe index path')
    return name


def object_path(name):
    if not re.fullmatch(r'objects/[0-9a-f]{64}\.gz',name):
        raise ValueError('unsafe cold object path')
    return name


def file_ref(raw):
    return {'sha256':V11.sha(raw),'bytes':len(raw)}


def zip_files(raw, expected, extra_name, limit):
    """Validate the full ZIP before any publication. Never extract paths."""
    with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
        infos=zipped.infolist()
        if len(infos)>100000 or len({i.filename for i in infos})!=len(infos):
            raise ValueError('duplicate/excessive ZIP entries')
        files={i.filename for i in infos if not i.is_dir()}
        if files != set(expected)|{extra_name} or sum(i.file_size for i in infos)>limit:
            raise ValueError('missing/extra/oversized Artifact content')
        result={name:zipped.read(name) for name in sorted(files)}
    for name, ref in expected.items():
        if file_ref(result[name])!=ref:
            raise ValueError('Artifact file hash/size mismatch')
    return result


class GitHubArtifacts:
    """Read-only authenticated source; never forwards auth on signed redirects."""
    def __init__(self, repository, token):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository):
            raise ValueError('invalid repository')
        self.repository,self.token=repository,token
        self.runs={};self.artifacts={};self.downloads={}
        self.bytes={'index':0,'cold':0,'v11':0}
        self.metadata_requests=0

    def api(self,path):
        self.metadata_requests+=1
        req=urllib.request.Request('https://api.github.com/repos/'+self.repository+'/'+path,
            headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json'})
        try:
            with urllib.request.urlopen(req,timeout=60) as response:return json.load(response)
        except urllib.error.HTTPError as exc:
            raise ValueError('GitHub metadata HTTP '+str(exc.code)) from None

    def listing(self,key):
        key=run_id(key)
        if key not in self.runs:
            run=self.api('actions/runs/'+key)
            if run['path']!=V11.WORKFLOW or run['conclusion']!='success' or run.get('run_attempt')!=1:
                raise ValueError('parent must be successful same-workflow original attempt')
            self.runs[key]=run
            artifacts=[]
            for page in range(1,101):
                rows=self.api(f'actions/runs/{key}/artifacts?per_page=100&page={page}')['artifacts']
                artifacts+=rows
                if len(rows)<100:break
            else:raise ValueError('artifact pagination limit')
            self.artifacts[key]=artifacts
        return self.artifacts[key]

    def metadata(self,key,name,checkpoint=False,optional=False):
        matches=[a for a in self.listing(key) if a['name']==name]
        if not matches and optional:return None
        if len(matches)!=1:raise ValueError('missing/duplicate parent artifact')
        art=matches[0]
        expires=datetime.fromisoformat(art['expires_at'].replace('Z','+00:00'))
        now=datetime.now(timezone.utc)
        if art['expired'] or expires<=now:raise ValueError('expired parent artifact')
        if not checkpoint and expires-now<timedelta(days=14):raise ValueError('RETENTION_GUARD: explicit checkpoint required')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}',art.get('digest') or ''):
            raise ValueError('official artifact digest unavailable')
        return art

    def download(self,art,kind):
        key=str(art['id'])
        if key not in self.downloads:
            req=urllib.request.Request(f'https://api.github.com/repos/{self.repository}/actions/artifacts/{key}/zip',
                headers={'Authorization':'Bearer '+self.token})
            try:
                with urllib.request.build_opener(NoRedirect).open(req,timeout=60) as response:raw=response.read()
            except urllib.error.HTTPError as exc:
                if exc.code not in (301,302,303,307,308):raise ValueError('Artifact download HTTP '+str(exc.code)) from None
                location=exc.headers.get('Location','')
                if not location.startswith('https://'):raise ValueError('unsafe Artifact redirect')
                with urllib.request.urlopen(location,timeout=120) as response:raw=response.read()
            if 'sha256:'+V11.sha(raw)!=art['digest']:raise ValueError('official Artifact SHA256 mismatch')
            self.downloads[key]=raw;self.bytes[kind]+=len(raw)
        return self.downloads[key]

    def legacy_loader(self,checkpoint=False):
        def load(key):
            art=self.metadata(key,'upbit-c-research-increment-1',checkpoint)
            raw=self.download(art,'v11')
            return raw,{'workflow_path':V11.WORKFLOW,'conclusion':'success','expired':False,
                'expires_at':art['expires_at'],'archive_sha256':art['digest'][7:]}
        return load


class RemoteArchive(A.Archive):
    def __init__(self,root,source,transport=None,allow_fixtures=False):
        super().__init__(root,allow_fixtures)
        self.source=source;self.transport=transport

    def object_available(self,item):
        key='objects/'+item['sha256']+'.gz'
        ref=(self.transport or {}).get('objects',{}).get(key)
        if ref:
            return ref['sha256']==item['compressed_sha256'] and ref['bytes']==item['compressed_bytes']
        return super().object_available(item)

    def object_path(self,key):
        path=super().object_path(key)
        name='objects/'+key+'.gz'
        ref=(self.transport or {}).get('objects',{}).get(name)
        if ref and not path.exists():self.hydrate_run(ref['run_id'])
        return path

    def hydrate_run(self,key):
        art=self.source.metadata(key,COLD_NAME,checkpoint=True)
        raw=self.source.download(art,'cold')
        expected={p:{k:v for k,v in ref.items() if k!='run_id'} for p,ref in self.transport['objects'].items() if ref['run_id']==key}
        files=zip_files(raw,expected,'cold-manifest.json',V11.MAX_BYTES)
        manifest=A.unseal(json.loads(files.pop('cold-manifest.json')))
        if (manifest['schema_version']!=COLD_SCHEMA or manifest['run_id']!=key or manifest['files']!=expected
                or manifest['sha256']!=self.transport['cold_manifests'][key]):
            raise ValueError('wrong cold manifest receipt')
        for name, data in files.items():
            object_path(name)
            A.publish(self.root/name,data)


def restore_index(store,key,checkpoint=False):
    art=store.source.metadata(key,INDEX_NAME,checkpoint,optional=True)
    if art is None:return None
    raw=store.source.download(art,'index')
    with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
        infos=zipped.infolist()
        if len(infos)>100000 or sum(i.file_size for i in infos)>V11.MAX_BYTES:
            raise ValueError('index Artifact resource limit')
        transport=A.unseal(json.loads(zipped.read('transport.json')))
    if transport['schema_version']!=SCHEMA or transport['run_id']!=key or transport.get('test_fixture_namespace')!=store.allow_fixtures:
        raise ValueError('wrong transport identity/schema')
    for name in transport['index_files']:index_path(name)
    for name,ref in transport['objects'].items():
        object_path(name);run_id(ref['run_id'])
    files=zip_files(raw,transport['index_files'],'transport.json',V11.MAX_BYTES)
    # Preflight ALL cold dependencies before accepting an active-only operation.
    origins={ref['run_id'] for ref in transport['objects'].values()}
    expiries=[]
    for origin in sorted(origins):
        metadata=store.source.metadata(origin,COLD_NAME,checkpoint)
        if origin not in transport['cold_manifests']:raise ValueError('missing cold receipt')
        expiries.append(metadata['expires_at'])
    for name,data in files.items():
        if name!='transport.json':A.publish(store.root/name,data)
    store.transport=transport
    manifest=store.manifest(key,transport['manifest_sha256'])
    active=store.active(manifest)
    if manifest['inventory_sha256']!=transport['inventory_sha256']:
        raise ValueError('wrong transport inventory')
    # Validate descriptor links and account for every referenced compressed object.
    expected={}
    for ref in manifest['inventory'].values():
        for item in store.descriptor(ref)['chunks']:
            name='objects/'+item['sha256']+'.gz'
            if name not in transport['objects'] or not store.object_available(item):
                raise ValueError('missing/conflicting object receipt')
            expected[name]=transport['objects'][name]
    if expected!=transport['objects']:raise ValueError('extra/unlinked object receipt')
    return manifest,active,{'index_zip_bytes':len(raw),'cold_dependencies':len(origins),
        'earliest_cold_expiry':min(expiries) if expiries else None,
        'verification':'INDEX_HASHES_AND_COLD_EXISTENCE_RECEIPTS; NOT_FULL_COLD_BYTE_REAUDIT'}


def export_artifacts(store,manifest,index_dir,cold_dir,parent_transport=None,checkpoint=False,source_revision=None):
    index_dir,cold_dir=A.isolated(index_dir),A.isolated(cold_dir)
    if any(p.exists() and any(p.iterdir()) for p in (index_dir,cold_dir)):
        raise ValueError('export destinations must be empty')
    prior=parent_transport or {}
    objects={} if checkpoint else dict(prior.get('objects',{}))
    cold_files={}
    for ref in manifest['inventory'].values():
        for item in store.descriptor(ref)['chunks']:
            name='objects/'+item['sha256']+'.gz'
            expected={'sha256':item['compressed_sha256'],'bytes':item['compressed_bytes']}
            if name in objects:
                if {k:v for k,v in objects[name].items() if k!='run_id'}!=expected:raise ValueError('prior object receipt changed')
            else:
                raw=store.object_path(item['sha256']).read_bytes()
                if file_ref(raw)!=expected:raise ValueError('cold export hash mismatch')
                A.publish(cold_dir/name,raw)
                cold_files[name]=expected
                objects[name]=dict(expected,run_id=manifest['run_id'])
    cold=A.seal({'schema_version':COLD_SCHEMA,'run_id':manifest['run_id'],'files':cold_files})
    cold_raw=(dumps(cold)+'\n').encode()
    if len(cold_files)+1>100000 or sum(ref['bytes'] for ref in cold_files.values())+len(cold_raw)>V11.MAX_BYTES:
        raise ValueError('cold Artifact resource limit; explicit pack sharding required')
    A.publish(cold_dir/'cold-manifest.json',cold_raw)
    paths={'runs/'+manifest['run_id']+'.json','active/'+manifest['active_sha256']+'.json'}
    paths|={'descriptors/'+ref['descriptor_sha256']+'.json' for ref in manifest['inventory'].values()}
    for migration in manifest['imports'].values():
        paths|={'imports/'+ref['sha256']+'.json' for ref in [migration['original_manifest']]+migration.get('chain_manifests',[])}
    index={}
    for name in sorted(paths):
        raw=(store.root/name).read_bytes();index[name]=file_ref(raw);A.publish(index_dir/name,raw)
    origins={ref['run_id'] for ref in objects.values()}
    receipts={key:value for key,value in prior.get('cold_manifests',{}).items() if key in origins}
    if cold_files:receipts[manifest['run_id']]=cold['sha256']
    transport=A.seal({'schema_version':SCHEMA,'run_id':manifest['run_id'],
        'manifest_sha256':manifest['sha256'],'inventory_sha256':manifest['inventory_sha256'],
        'source_revision':source_revision,'test_fixture_namespace':store.allow_fixtures,
        'parent_transport_sha256':None if checkpoint else prior.get('sha256'),
        'checkpoint':checkpoint,'index_files':index,'objects':objects,'cold_manifests':receipts})
    transport_raw=(dumps(transport)+'\n').encode()
    if len(index)+1>100000 or sum(ref['bytes'] for ref in index.values())+len(transport_raw)>V11.MAX_BYTES:
        raise ValueError('index Artifact resource limit; explicit metadata sharding required')
    A.publish(index_dir/'transport.json',transport_raw)
    return {'transport_sha256':transport['sha256'],'index_uncompressed_bytes':sum(p.stat().st_size for p in index_dir.rglob('*') if p.is_file()),
        'cold_compressed_object_bytes':sum(ref['bytes'] for ref in cold_files.values()),
        'cold_new_objects':len(cold_files),'cold_manifest_sha256':cold['sha256']}


def operate(state,index_dir,cold_dir,key,source,parent='',outcomes_only=False,evaluate_history=False,
            checkpoint=False,batch_count=1,batch_index=0,scanner=None,client=None,allow_fixtures=False):
    from .research_scan import scan, ResearchClient
    from .research_history import record_scan
    key=run_id(key)
    for p in (state,index_dir,cold_dir):A.isolated(p)
    if outcomes_only and not parent:raise ValueError('outcomes-only requires explicit parent')
    if (Path(state).exists() and any(Path(state).iterdir())):raise ValueError('state destination must be empty')
    if not 1<=batch_count<=100 or not 0<=batch_index<batch_count:raise ValueError('invalid batch')
    store=RemoteArchive(state,source,allow_fixtures=allow_fixtures)
    before=None;parent_manifest=None;parent_transport=None
    result={'activation':'RESEARCH_ONLY','run_id':key,'previous_run_id':parent or None,
        'production_files_created':0,'market_scan_executed':not outcomes_only}
    started=time.perf_counter()
    if parent:
        run_id(parent)
        if int(parent)>=int(key):raise ValueError('parent must precede child')
        restored=restore_index(store,parent,checkpoint)
        if restored:
            parent_manifest,before,verification=restored;parent_transport=store.transport
            result['parent_verification']=verification
        else:
            # One-time V1.1 conversion. Its byte-exact original records remain cold.
            parent_manifest,verification=A.import_remote_v11(store,parent,parent,source.legacy_loader(checkpoint),checkpoint=checkpoint)
            before=store.active(parent_manifest)
            result['v11_migration']=verification
    records={}
    client=client or ResearchClient()
    if not outcomes_only:
        report=(scanner or scan)(client,batch_index=batch_index,batch_count=batch_count,
            progress=lambda i,n,m,s:print(f'C research {i}/{n} {m} {s}',file=sys.stderr) if i%20==0 or i==n else None)
        with tempfile.TemporaryDirectory(prefix='c-new-records-') as temp:
            record_scan(Path(temp),report)
            records={p.relative_to(temp).as_posix():p.read_bytes() for p in Path(temp).rglob('*.json')}
        result['scan']={k:report[k] for k in ('scan_id','universe_size','selected_count','success_count','failure_count',
            'failure_reasons','logical_api_requests','started_at_ms','finished_at_ms','api_blocked')}
        result['scan']['original_record_bytes']=len(records['scans/'+report['scan_id']+'.json'])
    if outcomes_only or evaluate_history:
        if before is None:
            before=A.seal({'schema_version':A.ACTIVE_SCHEMA,'activation':'RESEARCH_ONLY','inventory_sha256':digest({}),'signals':{}})
        # Do not retrospectively evaluate newly created signals before publication.
        records.update(A.evaluate_active(before,client,time.time_ns()//1000000))
    # Preserve completed new observations before compression/publication. This
    # directory is uploaded ONLY on a failed run and is never an eligible parent.
    # A killed runner before this point still cannot guarantee preservation.
    for name,raw in records.items():A.publish(store.root/'pending-records'/name,raw)
    if checkpoint and parent_transport:
        for origin in sorted({ref['run_id'] for ref in parent_transport['objects'].values()}):store.hydrate_run(origin)
    parent_ref=(parent_manifest['run_id'],parent_manifest['sha256']) if parent_manifest else None
    manifest=store.commit(key,records,parent_ref)
    # Every new record is fully hashed/validated. Prior cold payloads rely on
    # previous full verification + current authenticated existence receipts.
    for name in manifest['delta_files']:store.read_record(name,manifest['inventory'][name])
    active=store.active(manifest)
    if checkpoint:result['full_audit']=store.audit(manifest)
    result['export']=export_artifacts(store,manifest,index_dir,cold_dir,parent_transport,checkpoint,
                                     os.environ.get('SOURCE_REVISION'))
    result.update(status='SUCCESS',manifest_sha256=manifest['sha256'],
        inventory_sha256=manifest['inventory_sha256'],active_sha256=active['sha256'],
        active_bytes=len((dumps(active)+'\n').encode()),signal_count=len(active['signals']),
        new_records=len(manifest['delta_files']),download_bytes=dict(source.bytes),
        metadata_api_requests=source.metadata_requests,
        unresolved_horizons=len(A.pending_work(active,time.time_ns()//1000000)),
        logical_api_requests=getattr(client,'requests',0),elapsed_seconds=str(time.perf_counter()-started))
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('state','index','cold','summary'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--run-id',required=True);p.add_argument('--previous-run-id',default='')
    p.add_argument('--manual-label',default='')
    p.add_argument('--outcomes-only',action='store_true');p.add_argument('--evaluate-history',action='store_true')
    p.add_argument('--checkpoint',action='store_true')
    p.add_argument('--batch-count',type=int,default=1);p.add_argument('--batch-index',type=int,default=0)
    args=p.parse_args(argv)
    for path in (args.state,args.index,args.cold,args.summary):A.isolated(path)
    summary={'schema_version':SCHEMA,'activation':'RESEARCH_ONLY','run_id':args.run_id,
             'status':'FAILED','production_files_created':0}
    try:
        if os.environ.get('GITHUB_RUN_ATTEMPT','1')!='1':raise ValueError('UNSAFE_NATIVE_RERUN: use a new manual run')
        parent,outcomes,evaluate,checkpoint=manual_mode(args.manual_label,args.previous_run_id,
            args.outcomes_only,args.evaluate_history,args.checkpoint)
        source=GitHubArtifacts(os.environ['GITHUB_REPOSITORY'],os.environ['GH_TOKEN'])
        summary.update(operate(args.state,args.index,args.cold,args.run_id,source,parent,outcomes,evaluate,
            checkpoint,args.batch_count,args.batch_index))
    except Exception as exc:
        # No exception strings from remote URLs/clients/credentials in artifacts.
        error=str(exc) if isinstance(exc,ValueError) else 'operation failed; inspect error class'
        token=os.environ.get('GH_TOKEN')
        if token:error=error.replace(token,'[REDACTED]')
        summary.update(error_type=type(exc).__name__,error=error)
        if 'source' in locals():summary['download_bytes']=dict(source.bytes)
        print('C storage operation failed: '+type(exc).__name__,file=sys.stderr)
    A.publish(args.summary,(dumps(summary)+'\n').encode())
    print(dumps(summary))
    return 0 if summary['status']=='SUCCESS' else 1


if __name__=='__main__':raise SystemExit(main())

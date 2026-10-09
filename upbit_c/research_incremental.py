"""V1.6 immutable incremental transport over a pinned V1.5 baseline."""
import copy
import io
import json
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from upbit_b.feature_contracts import dumps
from . import research_archive as A, research_release as R, research_preservation as P

SCHEMA = 'upbit-c-incremental-package-1.6'
CATALOG_SCHEMA = 'upbit-c-preservation-catalog-1.6'
MAX_CHAIN = 128
MAX_RESTORED_BYTES = 5 * 1024**3
PREFIX = 'c-incremental-research-'


def encoded(value):
    return (dumps(value)+'\n').encode()


def raw_identity(ref):
    return {key:ref[key] for key in ('sha256','bytes')}


def utc(value):
    parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds()!=0:
        raise ValueError('explicit UTC generation time required')
    return parsed.isoformat().replace('+00:00','Z')


def initial_catalog(repository='nh1018/upbit-scanner', release_id=407878231,
                    asset_id=624870458,
                    sha256='5bb5dcd1b385f51b7baa52d3f37c67a141f55e704fa50490fa854bc3ce9e6796'):
    P.GitHub(repository);P.positive_id(release_id);P.positive_id(asset_id);A.identifier(sha256)
    return A.seal({'schema_version':CATALOG_SCHEMA,'activation':'RESEARCH_ONLY',
                   'base':{'repository':repository,'release_id':release_id,
                           'asset_id':asset_id,'sha256':sha256},'increments':[]})


def check_catalog(catalog):
    A.unseal(catalog)
    if catalog['schema_version']!=CATALOG_SCHEMA or catalog['activation']!='RESEARCH_ONLY':
        raise ValueError('wrong catalog contract')
    base=catalog['base'];P.GitHub(base['repository']);A.identifier(base['sha256'])
    P.positive_id(base['asset_id']);P.positive_id(base['release_id'])
    if not isinstance(catalog['increments'],list) or len(catalog['increments'])>MAX_CHAIN:
        raise ValueError('catalog chain limit')
    seen={base['sha256']}
    for seq,entry in enumerate(catalog['increments'],1):
        A.identifier(entry['sha256']);A.identifier(entry['package_id'])
        if entry['sha256'] in seen or entry['sequence']!=seq:
            raise ValueError('duplicate/reordered catalog package')
        seen.add(entry['sha256'])
        for field in ('asset_id','release_id'):
            if entry.get(field) is not None:P.positive_id(entry[field])
    return catalog


def read_catalog(raw, expected_sha):
    A.identifier(expected_sha)
    if len(raw)>16*1024**2 or R.S.sha(raw)!=expected_sha:
        raise ValueError('external catalog hash/size mismatch')
    return check_catalog(json.loads(raw))


class PackageCache:
    """Hash-checked immutable ZIP cache; never a substitute for inner audit."""
    def __init__(self, fetch, directory=None):
        self.fetch=fetch;self.root=None
        self.metrics={'network_bytes':0,'cache_bytes':0,'fetches':0,'cache_hits':0}
        if directory is not None:
            R.no_symlinks(Path(directory));self.root=A.isolated(directory)

    def __call__(self, entry):
        sha=entry['sha256'];A.identifier(sha)
        path=self.root/(sha+'.zip') if self.root else None
        if path is not None:R.no_symlinks(path)
        if path is not None and path.exists():
            if path.stat().st_size>R.MAX_BYTES:raise ValueError('cache size limit')
            raw=path.read_bytes();self.metrics['cache_hits']+=1;self.metrics['cache_bytes']+=len(raw)
        else:
            raw=self.fetch(entry);self.metrics['fetches']+=1;self.metrics['network_bytes']+=len(raw)
        if len(raw)>R.MAX_BYTES or R.S.sha(raw)!=sha:
            raise ValueError('package/cache SHA256 mismatch')
        if path is not None:A.publish(path,raw)
        return raw


def github_fetch(client):
    def fetch(entry):
        asset=P.positive_id(entry.get('asset_id'))
        return client.get('releases/assets/'+str(asset),binary=True)
    return fetch


def inventory_delta(previous, current):
    if not set(previous)<=set(current):
        raise ValueError('previous original missing from checkpoint')
    for name in previous:
        if raw_identity(previous[name])!=raw_identity(current[name]):
            raise ValueError('same original ID different raw hash/size')
    return sorted(set(current)-set(previous))


def ancestry(store, target, previous):
    """Verify source parent SHA links; retain original intermediate manifests."""
    if target['sha256']==previous['sha256']:
        return []
    if target['run_id']==previous['run_id']:
        raise ValueError('same checkpoint ID different manifest')
    proofs=[];cursor=target;seen=set()
    for _ in range(MAX_CHAIN):
        if cursor['lineage_id']!=previous['lineage_id']:
            raise ValueError('source lineage mismatch')
        inventory_delta(previous['inventory'],cursor['inventory'])
        parent=cursor.get('parent')
        if parent=={'run_id':previous['run_id'],'sha256':previous['sha256']}:
            return proofs
        if not parent or parent['sha256'] in seen:
            raise ValueError('missing/cyclic source ancestry')
        seen.add(parent['sha256'])
        prior=store.manifest(parent['run_id'],parent['sha256'])
        inventory_delta(prior['inventory'],cursor['inventory'])
        proofs.append({'run_id':prior['run_id'],'sha256':prior['sha256']})
        cursor=prior
    raise ValueError('source ancestry limit')


def pack(receipt, files):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_STORED) as zipped:
        for name in sorted(files)+['increment-manifest.json']:
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.create_system=0
            zipped.writestr(info,encoded(receipt) if name=='increment-manifest.json' else files[name])
    raw=buffer.getvalue()
    if len(raw)>R.MAX_BYTES:raise ValueError('increment ZIP size limit')
    return raw


def unpack(raw, expected_sha, allow_fixtures=False):
    A.identifier(expected_sha)
    if len(raw)>R.MAX_BYTES or R.S.sha(raw)!=expected_sha:
        raise ValueError('increment external ZIP SHA/size mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        infos=z.infolist();names=[x.filename for x in infos]
        if (len(infos)>R.MAX_ENTRIES or len(set(names))!=len(names) or
                sum(x.file_size for x in infos)>R.MAX_BYTES or
                any(x.is_dir() or x.compress_type!=zipfile.ZIP_STORED for x in infos)):
            raise ValueError('unsafe increment ZIP structure')
        if 'increment-manifest.json' not in names or z.getinfo('increment-manifest.json').file_size>16*1024**2:
            raise ValueError('missing/oversized increment manifest')
        receipt=A.unseal(json.loads(z.read('increment-manifest.json')))
        if not P.re.fullmatch(r'[1-9][0-9]*',receipt['source_actions_run_id']):
            raise ValueError('invalid source Actions run ID')
        if receipt['schema_version']!=SCHEMA or receipt['activation']!='RESEARCH_ONLY':
            raise ValueError('wrong increment schema')
        if receipt['fixture_namespace'] and not allow_fixtures:raise ValueError('fixture increment prohibited')
        if utc(receipt['generation_time_utc'])!=receipt['generation_time_utc']:
            raise ValueError('noncanonical generation clock')
        if set(names)!=set(receipt['new_files'])|{'increment-manifest.json'}:
            raise ValueError('extra/missing increment payload')
        files={}
        for name,ref in receipt['new_files'].items():
            R.safe_name(name)
            if z.getinfo(name).file_size!=ref['bytes']:raise ValueError('increment payload size mismatch')
            value=z.read(name)
            if R.ref(value)!=ref:raise ValueError('increment payload hash mismatch')
            files[name]=value
    return receipt,files


class Chain:
    def __init__(self, catalog, raw, root, allow_fixtures=False):
        self.catalog=check_catalog(catalog);self.base=catalog['base'];self.allow_fixtures=allow_fixtures
        self.store,self.manifest,receipt=R.unpack_verified(raw,self.base['sha256'],root,allow_fixtures)
        self.active=self.store.active(self.manifest)
        self.files=dict(receipt['files']);self.owners={n:self.base['sha256'] for n in self.manifest['inventory']}
        self.tip_sha=self.base['sha256'];self.sequence=0;self.entries=[]

    def apply(self, raw, expected_sha, entry=None):
        receipt,files=unpack(raw,expected_sha,self.allow_fixtures)
        if (receipt['base']!=self.base or receipt['parent_zip_sha256']!=self.tip_sha or
                type(receipt['sequence']) is not int or receipt['sequence']!=self.sequence+1):
            raise ValueError('wrong base/predecessor/order or missing intermediate')
        if receipt['fixture_namespace']!=self.allow_fixtures:
            raise ValueError('fixture namespace mismatch')
        if entry is not None and (entry['package_id']!=receipt['sha256'] or entry['sequence']!=receipt['sequence']):
            raise ValueError('catalog package identity mismatch')
        if any(n in self.files for n in files):raise ValueError('duplicate stored file payload')
        if len(self.files)+len(files)>R.MAX_ENTRIES or sum(x['bytes'] for x in self.files.values())+sum(len(v) for v in files.values())>MAX_RESTORED_BYTES:
            raise ValueError('restored chain resource limit')
        for name,value in files.items():A.publish(self.store.root/name,value)
        target_ref=receipt['target_manifest']
        if target_ref['run_id']!=receipt['source_actions_run_id']:
            raise ValueError('source Actions/checkpoint ID mismatch')
        target=self.store.manifest(target_ref['run_id'],target_ref['sha256'])
        if target.get('activation')!='RESEARCH_ONLY':raise ValueError('target activation contract')
        if target['sha256']==self.manifest['sha256']:raise ValueError('identical checkpoint must be NOOP')
        if target['lineage_id']!=target_ref['lineage_id'] or target['active_sha256']!=target_ref['active_sha256']:
            raise ValueError('target manifest linkage mismatch')
        added=inventory_delta(self.manifest['inventory'],target['inventory'])
        expected_new={n:raw_identity(target['inventory'][n]) for n in added}
        referenced={n:dict(raw_identity(self.manifest['inventory'][n]),owner_zip_sha256=self.owners[n])
                    for n in self.manifest['inventory']}
        if receipt['new_originals']!=expected_new or receipt['referenced_originals']!=referenced:
            raise ValueError('original reference coverage/hash/owner conflict')
        proofs=ancestry(self.store,target,self.manifest)
        if proofs!=receipt['lineage_proofs']:raise ValueError('source ancestry proof mismatch')
        needed=R.required_files(self.store,target)|{'runs/'+x['run_id']+'.json' for x in proofs}
        reused={n:self.files[n] for n in needed if n in self.files}
        if receipt['reused_files']!=reused or set(files)!=needed-set(reused):
            raise ValueError('file dependency coverage mismatch')
        for name,ref in reused.items():
            if R.ref((self.store.root/name).read_bytes())!=ref:raise ValueError('reused file corrupt')
        for name in added:self.store.read_record(name,target['inventory'][name])
        # Rare changed descriptor encodings require full byte validation too.
        for name in self.manifest['inventory']:
            if target['inventory'][name]!=self.manifest['inventory'][name]:
                self.store.read_record(name,target['inventory'][name])
        active=self.store.active(target)
        if active!=self.store.build_active(target['inventory'],self.active,added):
            raise ValueError('Active State not derived from original delta')
        changes={'hash_changed':active['sha256']!=self.active['sha256'],
                 'signals_changed':active['signals']!=self.active['signals']}
        if receipt['active_change']!=changes or receipt['kind']!=('DATA' if added else 'METADATA_ONLY'):
            raise ValueError('incorrect Active/data classification')
        if entry is not None and entry['target_manifest_sha256']!=target['sha256']:
            raise ValueError('catalog target mismatch')
        self.files.update(receipt['new_files']);self.owners.update({n:expected_sha for n in added})
        self.manifest=target;self.active=active;self.tip_sha=expected_sha;self.sequence+=1
        descriptor={'sha256':expected_sha,'package_id':receipt['sha256'],'sequence':self.sequence,
                    'kind':receipt['kind'],'target_manifest_sha256':target['sha256'],
                    'source_actions_run_id':receipt['source_actions_run_id'],
                    'generation_time_utc':receipt['generation_time_utc'],'bytes':len(raw),
                    'asset_id':None,'release_id':None}
        if entry is not None and any(entry.get(k)!=v for k,v in descriptor.items() if k not in ('asset_id','release_id')):
            raise ValueError('catalog descriptor metadata mismatch')
        self.entries.append(descriptor)
        return receipt,descriptor


@contextmanager
def verified_chain(catalog, fetch, allow_fixtures=False):
    check_catalog(catalog)
    with tempfile.TemporaryDirectory(prefix='c-incremental-chain-') as tmp:
        chain=Chain(catalog,fetch(catalog['base']),Path(tmp),allow_fixtures)
        for entry in catalog['increments']:chain.apply(fetch(entry),entry['sha256'],entry)
        yield chain


def prepare(store, manifest, catalog, fetch, generation_time_utc, source_actions_run_id,
            destination=None, allow_fixtures=False):
    from .research_actions_storage import run_id
    run_id(source_actions_run_id);clock=utc(generation_time_utc)
    if manifest['run_id']!=source_actions_run_id:raise ValueError('source Actions/checkpoint ID mismatch')
    store.manifest(manifest['run_id'],manifest['sha256']);store.audit(manifest)
    with verified_chain(catalog,fetch,allow_fixtures) as chain:
        previous=chain.manifest;added=inventory_delta(previous['inventory'],manifest['inventory'])
        current_active=store.active(manifest)
        if manifest['sha256']==previous['sha256']:
            return {'status':'NOOP','new_original_count':0,'active_changed':False,'package_bytes':0,'remote_writes':0}
        if chain.sequence>=MAX_CHAIN:raise ValueError('catalog chain limit')
        proofs=ancestry(store,manifest,previous)
        needed=R.required_files(store,manifest)|{'runs/'+x['run_id']+'.json' for x in proofs}
        new_files={};reused={}
        for name in sorted(needed):
            R.safe_name(name);raw=(store.root/name).read_bytes();ref=R.ref(raw)
            if name in chain.files:
                if chain.files[name]!=ref:raise ValueError('immutable archive path encoding conflict')
                reused[name]=ref
            else:new_files[name]=raw
        receipt=A.seal({'schema_version':SCHEMA,'activation':'RESEARCH_ONLY',
            'fixture_namespace':allow_fixtures,'kind':'DATA' if added else 'METADATA_ONLY',
            'base':copy.deepcopy(catalog['base']),'parent_zip_sha256':chain.tip_sha,
            'sequence':chain.sequence+1,'generation_time_utc':clock,
            'source_actions_run_id':source_actions_run_id,
            'target_manifest':{k:manifest[k] for k in ('run_id','sha256','lineage_id','active_sha256')},
            'new_originals':{n:raw_identity(manifest['inventory'][n]) for n in added},
            'referenced_originals':{n:dict(raw_identity(previous['inventory'][n]),owner_zip_sha256=chain.owners[n]) for n in previous['inventory']},
            'lineage_proofs':proofs,'active_change':{'hash_changed':current_active['sha256']!=chain.active['sha256'],
                                                  'signals_changed':current_active['signals']!=chain.active['signals']},
            'reused_files':reused,'new_files':{n:R.ref(v) for n,v in new_files.items()}})
        raw=pack(receipt,new_files);sha=R.S.sha(raw)
        _,entry=chain.apply(raw,sha);chain.store.audit(chain.manifest)
        next_catalog=A.seal({'schema_version':CATALOG_SCHEMA,'activation':'RESEARCH_ONLY',
                            'base':copy.deepcopy(catalog['base']),
                            'increments':copy.deepcopy(catalog['increments'])+[entry]})
        name=PREFIX+receipt['sha256']+'.zip'
        external={'schema_version':SCHEMA,'asset_name':name,'sha256':sha,'bytes':len(raw),
                  'package_id':receipt['sha256'],'sequence':entry['sequence'],
                  'parent_zip_sha256':receipt['parent_zip_sha256'],'source_actions_run_id':source_actions_run_id,
                  'generation_time_utc':clock,'kind':receipt['kind']}
        result={'status':'PREPARED','package':external,'new_original_count':len(added),
                'referenced_original_count':len(previous['inventory']),
                'active_change':receipt['active_change'],'payload_bytes':sum(len(x) for x in new_files.values()),
                'catalog':next_catalog,'catalog_file_sha256':R.S.sha(encoded(next_catalog)),
                'remote_writes':0}
        if destination is not None:
            R.no_symlinks(Path(destination));destination=A.isolated(destination)
            outputs={name:raw,name+'.receipt.json':encoded(external),
                     'catalog-'+next_catalog['sha256']+'.json':encoded(next_catalog)}
            for key,value in outputs.items():
                path=destination/key;R.no_symlinks(path)
                if path.exists() and path.read_bytes()!=value:raise ValueError('immutable prepared output conflict')
            for key,value in outputs.items():A.publish(destination/key,value)
        return result


def restore(catalog,fetch,destination,allow_fixtures=False):
    R.no_symlinks(Path(destination));destination=A.isolated(destination)
    with verified_chain(catalog,fetch,allow_fixtures) as chain:
        chain.store.audit(chain.manifest)
        # All prior checkpoint metadata also survives; preflight before writes.
        for name in chain.files:
            path=destination/name;R.no_symlinks(path)
            if path.exists() and path.read_bytes()!=(chain.store.root/name).read_bytes():
                raise ValueError('restore overwrite prohibited')
        for name in sorted(chain.files):A.publish(destination/name,(chain.store.root/name).read_bytes())
        restored=A.Archive(destination,allow_fixtures)
        manifest=restored.manifest(chain.manifest['run_id'],chain.manifest['sha256'])
        return dict(restored.audit(manifest),run_id=manifest['run_id'],
                    manifest_sha256=manifest['sha256'],active_sha256=manifest['active_sha256'],
                    lineage_id=manifest['lineage_id'],increments=chain.sequence,
                    catalog_sha256=catalog['sha256'],remote_writes=0)

"""V1.5 explicit Release transport. No writes without per-package approval.

V1.4 ZIP bytes and original contracts are unchanged. No delete/update APIs.
"""
import argparse
import io
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from upbit_b.feature_contracts import dumps
from . import research_release as R, research_archive as A
from .research_operations import NoRedirect

SCHEMA = 'upbit-c-preservation-1.5'
PREFIX = 'c-research-'


class PreservationError(ValueError):
    """Safe operator-facing message constructed only by this adapter."""


class RemoteError(PreservationError):
    def __init__(self, status):
        self.status = status
        super().__init__('GitHub HTTP ' + str(status))


class GitHub(R.ReleaseReader):
    """Bounded API requests, redacted failures, no automatic mutation retries."""
    def request(self, method, path, payload=None, upload=False):
        if method not in ('GET', 'POST') or not path.startswith('releases') or '..' in path:
            raise PreservationError('unsupported Release operation')
        if method == 'POST' and not self.token:
            raise PreservationError('write credential required')
        origin = 'https://uploads.github.com' if upload else 'https://api.github.com'
        headers = {'Accept':'application/vnd.github+json', 'X-GitHub-Api-Version':'2022-11-28'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if payload is not None:
            headers['Content-Type'] = 'application/zip' if upload else 'application/json'
        data = payload if upload else json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(origin+'/repos/'+self.repository+'/'+path,
                                     data=data, headers=headers, method=method)
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=120) as response:
                raw = response.read(16*1024**2+1)
        except urllib.error.HTTPError as exc:
            raise RemoteError(exc.code) from None
        except (OSError, TimeoutError) as exc:
            raise PreservationError('GitHub transport failed: '+type(exc).__name__) from None
        if len(raw)>16*1024**2:
            raise PreservationError('GitHub metadata size limit')
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise PreservationError('invalid GitHub metadata JSON') from None

    def get(self, path, binary=False):
        if not binary:
            return self.request('GET', path)
        try:
            return super().get(path, binary=True)
        except (OSError, TimeoutError) as exc:
            raise PreservationError('GitHub download failed: '+type(exc).__name__) from None

    def releases(self):
        rows = []
        for page in range(1, 101):
            part = self.request('GET', f'releases?per_page=100&page={page}')
            if not isinstance(part, list):
                raise PreservationError('invalid Release listing')
            rows.extend(part)
            if len(part)<100:
                return rows
        raise PreservationError('Release pagination limit')

    def by_tag(self, tag):
        try:
            return self.request('GET', 'releases/tags/'+urllib.parse.quote(tag, safe=''))
        except RemoteError as exc:
            if exc.status == 404:
                return None
            raise


def inspect(raw, expected_sha, allow_fixtures=False):
    """Full V1.4 original/Active/lineage audit before trusting manifest metadata."""
    audit = R.verify(raw, expected_sha, allow_fixtures)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        manifest = A.unseal(json.loads(z.read('release-manifest.json')))
    return manifest, audit


def identity(raw, expected_sha, allow_fixtures=False):
    manifest, audit = inspect(raw, expected_sha, allow_fixtures)
    return {'schema_version':SCHEMA, 'activation':'RESEARCH_ONLY',
            'tag':PREFIX+manifest['sha256'], 'asset_name':PREFIX+manifest['sha256']+'.zip',
            'sha256':expected_sha, 'bytes':len(raw), 'run_id':manifest['run_id'],
            'package_id':manifest['sha256'], 'manifest':manifest, 'audit':audit}


def checked_asset(client, asset, allow_fixtures=False):
    digest = asset.get('digest', '')
    if asset.get('state')!='uploaded' or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise PreservationError('incomplete asset or official digest unavailable')
    raw = client.get('releases/assets/'+str(positive_id(asset['id'])), binary=True)
    current = identity(raw, digest[7:], allow_fixtures)
    if asset.get('size')!=len(raw) or asset.get('name')!=current['asset_name']:
        raise PreservationError('asset name/size identity mismatch')
    return current, raw


def positive_id(value):
    if type(value) is not int or value<=0:
        raise PreservationError('invalid GitHub ID')
    return value


def plan(client, current, allow_fixtures=False):
    """Scan managed Releases, full verify prior originals, fail closed on overlap.

    Remote digests identify observed catalog bytes, NOT a substitute for the
    externally retained SHA required for recovery. No writes in this function.
    """
    previous = []
    present = None
    seen = set()
    for release in client.releases():
        tag = release.get('tag_name', '')
        if not tag.startswith(PREFIX):
            continue
        if tag in seen or not re.fullmatch(PREFIX+r'[0-9a-f]{64}', tag):
            raise PreservationError('duplicate/invalid managed Release tag')
        seen.add(tag)
        assets = client.assets(positive_id(release['id']))
        if not assets and tag==current['tag']:
            # Retry a Release creation that completed before upload interruption.
            continue
        if len(assets)!=1:
            raise PreservationError('managed Release must contain exactly one verified ZIP')
        item, _ = checked_asset(client, assets[0], allow_fixtures)
        if tag!=item['tag']:
            raise PreservationError('Release/package identity conflict')
        if tag==current['tag']:
            if item['sha256']!=current['sha256']:
                raise PreservationError('same name different ZIP hash')
            present={'release_id':release['id'], 'asset_id':assets[0]['id']}
        previous.append(item['manifest'])
    comparison = R.compare_originals(current['manifest'], previous)
    if comparison['duplicate_originals'] and not present:
        # Never silently lose changed Active State by calling overlap a replay.
        raise PreservationError('originals overlap another package; explicit disjoint export required')
    return {'schema_version':SCHEMA, 'status':'ALREADY_PRESENT' if present else 'APPROVAL_REQUIRED',
            'remote_writes':0, 'package_sha256':current['sha256'], 'tag':current['tag'],
            'run_id':current['run_id'], 'originals':comparison, **(present or {})}


def publish(client, raw, expected_sha, target_commit, approval='', expected_release_id=None,
            allow_fixtures=False, source_run_id=None):
    """Explicit approval bound to ZIP hash. Retry always re-lists remote state.

    On uncertain POST outcome, stop; a later invocation can safely reverify.
    Never deletes a starter asset, replaces bytes, or marks success before GET.
    """
    current = identity(raw, expected_sha, allow_fixtures)
    if approval!='PUBLISH:'+expected_sha:
        return plan(client, current, allow_fixtures)
    if not re.fullmatch(r'[0-9a-f]{40}', target_commit):
        raise PreservationError('target must be pinned commit SHA')
    if source_run_id is not None and not re.fullmatch(r'[1-9][0-9]*',source_run_id):
        raise PreservationError('invalid source Actions run ID')
    release = client.by_tag(current['tag'])
    if expected_release_id is not None:
        positive_id(expected_release_id)
        if not release or release['id']!=expected_release_id:
            raise PreservationError('previously pinned Release missing or replaced')
    result = plan(client, current, allow_fixtures)
    if result['status']=='ALREADY_PRESENT':
        return result
    writes = 0
    if release is None:
        release = client.request('POST', 'releases', {
            'tag_name':current['tag'], 'target_commitish':target_commit,
            'name':'C research archive '+current['package_id'], 'draft':False,
            'prerelease':True, 'make_latest':'false',
            'body':dumps({'schema_version':SCHEMA, 'activation':'RESEARCH_ONLY',
                          'run_id':current['run_id'], 'package_id':current['package_id'],
                          'source_actions_run_id':source_run_id,
                          'sha256':expected_sha,
                          'original_manifest_sha256':current['manifest']['original_manifest_sha256'],
                          'original_ids':sorted(current['manifest']['original_records'])})})
        writes+=1
    if release.get('tag_name')!=current['tag']:
        raise PreservationError('Release creation identity mismatch')
    release_id = positive_id(release['id'])
    # Recheck immediately before POST, including externally created starter assets.
    assets = client.assets(release_id)
    if assets:
        result = R.publication_plan(current, assets)
        asset_id = result['asset_id']
    else:
        uploaded = client.request('POST', f'releases/{release_id}/assets?name='+
                                  urllib.parse.quote(current['asset_name'], safe=''), raw, upload=True)
        writes+=1
        asset_id = positive_id(uploaded['id'])
    assets = client.assets(release_id)
    if len(assets)!=1:
        raise PreservationError('post-upload asset catalog conflict')
    result = R.publication_plan(current, assets)
    if result.get('asset_id')!=asset_id:
        raise PreservationError('uploaded asset ID mismatch')
    item, downloaded = checked_asset(client, assets[0], allow_fixtures)
    if item['sha256']!=expected_sha or downloaded!=raw:
        raise PreservationError('post-upload download mismatch')
    return {'schema_version':SCHEMA, 'status':'VERIFIED_PUBLICATION',
            'remote_writes':writes, 'release_id':release_id, 'asset_id':asset_id,
            'tag':current['tag'], 'package_sha256':expected_sha, 'run_id':current['run_id']}


def locate(client, run_id=None, original_id=None):
    """Read-only discovery; recovery still requires an external SHA pin."""
    if (run_id is None)==(original_id is None):
        raise PreservationError('select exactly one run ID or original ID')
    matches=[]
    for release in client.releases():
        if not release.get('tag_name','').startswith(PREFIX):
            continue
        assets=client.assets(positive_id(release['id']))
        if len(assets)!=1:
            raise PreservationError('incomplete managed Release')
        item,_=checked_asset(client,assets[0])
        if item['tag']!=release['tag_name']:
            raise PreservationError('Release/package mismatch')
        if (run_id is not None and item['run_id']==run_id or
                original_id is not None and original_id in item['manifest']['original_records']):
            matches.append({'release_id':release['id'], 'asset_id':assets[0]['id'],
                            'observed_sha256':item['sha256'], 'tag':item['tag']})
    return {'status':'FOUND' if matches else 'NOT_FOUND', 'matches':matches,
            'external_sha_required_for_restore':True, 'remote_writes':0}


def export_backup(raw, expected_sha, destination, allow_fixtures=False):
    current=identity(raw,expected_sha,allow_fixtures)
    R.no_symlinks(Path(destination))
    destination=A.isolated(destination)
    # Deterministic payload files; timestamp receipt is written last, once.
    files={current['asset_name']:raw,
           'release-manifest.json':(dumps(current['manifest'])+'\n').encode()}
    hashes=''.join(R.S.sha(value)+'  '+name+'\n' for name,value in sorted(files.items()))
    files['SHA256SUMS']=hashes.encode()
    receipt={'schema_version':SCHEMA, 'package_sha256':expected_sha,
             'run_id':current['run_id'], 'original_ids':sorted(current['manifest']['original_records']),
             'files':{name:R.ref(value) for name,value in files.items()},
             'independent_disaster_backup':'NOT_VERIFIED',
             'restore_command':'python -B -m upbit_c.research_release restore --package '+
                 current['asset_name']+' --sha256 '+expected_sha+' --destination <isolated-restored-archive>'}
    receipt_path=destination/'backup-receipt.json'
    if receipt_path.exists():
        existing=json.loads(receipt_path.read_bytes())
        if {k:v for k,v in existing.items() if k!='backup_created_at_utc'}!=receipt:
            raise PreservationError('backup receipt conflict')
        receipt=existing
    else:
        receipt['backup_created_at_utc']=datetime.now(timezone.utc).isoformat()
    files['backup-receipt.json']=(dumps(receipt)+'\n').encode()
    for name,value in files.items():
        target=destination/name
        R.no_symlinks(target)
        if target.exists() and target.read_bytes()!=value:
            raise PreservationError('backup overwrite prohibited')
    for name,value in files.items():
        A.publish(destination/name,value)
    return receipt


def prepare_source(repository, token, run_id, destination):
    """GET-only hydrate of an explicit successful V1.3/V1.1 source; no scan."""
    from . import research_actions_storage as T
    R.no_symlinks(Path(destination));destination=A.isolated(destination)
    source=T.GitHubArtifacts(repository,token)
    with tempfile.TemporaryDirectory(prefix='c-preservation-') as tmp:
        store=T.RemoteArchive(Path(tmp),source)
        restored=T.restore_index(store,T.run_id(run_id),checkpoint=True)
        if restored:
            manifest,_,_=restored
            for origin in sorted({x['run_id'] for x in store.transport['objects'].values()}):
                store.hydrate_run(origin)
        else:
            manifest,_=A.import_remote_v11(store,run_id,run_id,source.legacy_loader(True),checkpoint=True)
        result=R.prepare(store,manifest,destination)
    return dict(result,download_bytes=source.bytes,market_scan_executed=False,
                source_actions_run_id=run_id, source_repository=repository,
                source_revision=source.runs.get(run_id,{}).get('head_sha'))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['prepare-source','plan','publish','download','locate','backup'])
    p.add_argument('--repository',default=os.environ.get('GITHUB_REPOSITORY','nh1018/upbit-scanner'))
    p.add_argument('--package',type=Path);p.add_argument('--sha256')
    p.add_argument('--destination',type=Path);p.add_argument('--run-id');p.add_argument('--original-id')
    p.add_argument('--asset-id',type=int);p.add_argument('--expected-release-id',type=int)
    p.add_argument('--target-commit',default=os.environ.get('GITHUB_SHA',''))
    p.add_argument('--approval',default='');p.add_argument('--summary',type=Path)
    a=p.parse_args(argv)
    if a.summary:
        R.no_symlinks(a.summary);A.isolated(a.summary)
    try:
        client=GitHub(a.repository,os.environ.get('GH_TOKEN'))
        if a.command=='prepare-source':
            result=prepare_source(a.repository,client.token,a.run_id,a.destination)
        elif a.command=='locate':
            result=locate(client,a.run_id,a.original_id)
        elif a.command=='download':
            raw=client.download(a.asset_id,a.sha256)
            result=export_backup(raw,a.sha256,a.destination)
        else:
            if not a.package or a.package.stat().st_size>R.MAX_BYTES:
                raise PreservationError('missing/oversized package')
            raw=a.package.read_bytes()
            if a.command=='backup':
                result=export_backup(raw,a.sha256,a.destination)
            elif a.command=='plan':
                result=plan(client,identity(raw,a.sha256))
            else:
                if a.approval!='PUBLISH:'+str(a.sha256):
                    raise PreservationError('explicit PUBLISH:<sha256> approval required')
                result=publish(client,raw,a.sha256,a.target_commit,a.approval,a.expected_release_id,
                               source_run_id=a.run_id)
        result=dict(result,operation_status='SUCCESS')
        status=0
    except Exception as exc:
        # Deliberately omit exception text, URLs, signed query strings and tokens.
        result={'schema_version':SCHEMA,'operation_status':'FAILED','error_type':type(exc).__name__,
                'http_status':getattr(exc,'status',None),'verified_publication':False,
                'stage':a.command, 'reason':str(exc) if isinstance(exc,PreservationError) else 'source/package verification or IO failed'}
        status=1
    if a.summary:A.publish(a.summary,(dumps(result)+'\n').encode())
    print(dumps(result))
    return status


if __name__=='__main__':raise SystemExit(main())

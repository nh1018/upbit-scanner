"""V1.6 Release transport. GET-only by default; no delete/update operations."""
import copy
import re
import urllib.parse
from . import research_incremental as I, research_archive as A, research_preservation as P


def dependency_metadata(client,catalog):
    """Publication must not hide deleted remote dependencies behind a cache."""
    for item in [catalog['base']]+catalog['increments']:
        release=client.get('releases/'+str(P.positive_id(item.get('release_id'))))
        if release.get('id')!=item['release_id']:raise P.PreservationError('dependency Release identity mismatch')
        assets=client.assets(release['id'])
        matching=[x for x in assets if x['id']==P.positive_id(item.get('asset_id'))]
        if len(matching)!=1 or matching[0].get('state')!='uploaded' or matching[0].get('digest')!='sha256:'+item['sha256']:
            raise P.PreservationError('remote dependency missing/changed/incomplete')


def publish(client,raw,sha,catalog,fetch,target_commit='',approval='',allow_fixtures=False):
    """Validate complete chain before mutation; uncertain POST stops for re-list."""
    if client.repository!=catalog['base']['repository']:
        raise P.PreservationError('repository/catalog mismatch')
    with I.verified_chain(catalog,fetch,allow_fixtures) as chain:
        manifest,entry=chain.apply(raw,sha);chain.store.audit(chain.manifest)
    dependency_metadata(client,catalog)
    tag=I.PREFIX+manifest['sha256'];name=tag+'.zip'
    known={I.PREFIX+x['package_id']:x['release_id'] for x in catalog['increments']}
    seen=set();present=None
    for release in client.releases():
        current=release.get('tag_name','')
        if not current.startswith(I.PREFIX):continue
        if current in seen or current not in set(known)|{tag}:
            raise P.PreservationError('untracked/forked/duplicate incremental Release')
        seen.add(current)
        if current in known and release['id']!=known[current]:
            raise P.PreservationError('pinned Release replaced')
        if current==tag:present=release
    if not set(known)<=seen:raise P.PreservationError('catalog Release missing')
    assets=client.assets(present['id']) if present else []

    def verify_assets(items):
        if len(items)!=1:raise P.PreservationError('partial/conflicting asset catalog')
        asset=items[0]
        if (asset.get('name')!=name or asset.get('size')!=len(raw) or
                asset.get('state')!='uploaded' or asset.get('digest')!='sha256:'+sha):
            raise P.PreservationError('asset identity/hash/state conflict')
        downloaded=client.get('releases/assets/'+str(P.positive_id(asset['id'])),binary=True)
        if downloaded!=raw:raise P.PreservationError('full download mismatch')
        I.unpack(downloaded,sha,allow_fixtures)
        return asset

    writes=0
    if assets:
        asset=verify_assets(assets);status='ALREADY_PRESENT'
    elif approval!='PUBLISH:'+sha:
        return {'status':'APPROVAL_REQUIRED','package_sha256':sha,'tag':tag,'remote_writes':0}
    else:
        if not re.fullmatch(r'[0-9a-f]{40}',target_commit):raise P.PreservationError('pinned target commit required')
        if present is None:
            present=client.request('POST','releases',{
                'tag_name':tag,'target_commitish':target_commit,'name':'C incremental research '+manifest['sha256'],
                'draft':False,'prerelease':True,'make_latest':'false',
                'body':I.dumps({'schema_version':I.SCHEMA,'activation':'RESEARCH_ONLY','package_sha256':sha,
                                'base':catalog['base'],'parent_zip_sha256':manifest['parent_zip_sha256'],
                                'new_originals':manifest['new_originals'],'target_manifest':manifest['target_manifest'],
                                'source_actions_run_id':manifest['source_actions_run_id']})})
            writes+=1
        if present.get('tag_name')!=tag:raise P.PreservationError('Release identity conflict')
        release_id=P.positive_id(present['id'])
        assets=client.assets(release_id)
        uploaded_id=None
        if not assets:
            uploaded=client.request('POST','releases/'+str(release_id)+'/assets?name='+urllib.parse.quote(name,safe=''),raw,upload=True)
            writes+=1;uploaded_id=P.positive_id(uploaded['id'])
        asset=verify_assets(client.assets(release_id));status='VERIFIED_PUBLICATION'
        if uploaded_id is not None and asset['id']!=uploaded_id:
            raise P.PreservationError('uploaded Asset ID mismatch')
    entry.update(release_id=P.positive_id(present['id']),asset_id=P.positive_id(asset['id']))
    next_catalog=A.seal({'schema_version':I.CATALOG_SCHEMA,'activation':'RESEARCH_ONLY',
                        'base':copy.deepcopy(catalog['base']),'increments':copy.deepcopy(catalog['increments'])+[entry]})
    return {'status':status,'release_id':entry['release_id'],'asset_id':entry['asset_id'],
            'package_sha256':sha,'tag':tag,'catalog':next_catalog,
            'catalog_file_sha256':I.R.S.sha(I.encoded(next_catalog)),'remote_writes':writes}

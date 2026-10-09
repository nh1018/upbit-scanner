"""Manual V1.6 prepare/restore/publication CLI. No scan or implicit publication."""
import argparse
import json
import os
import tempfile
from pathlib import Path
from . import research_incremental as I, research_incremental_release as X
from . import research_actions_storage as T


def prepare_source(client,source_run,catalog,fetch,clock,destination,allow_fixtures=False):
    """GET-only source hydration. Missing ancestry is an error, not a reset."""
    source=T.GitHubArtifacts(client.repository,client.token)
    with tempfile.TemporaryDirectory(prefix='c-incremental-source-') as tmp:
        store=T.RemoteArchive(Path(tmp),source,allow_fixtures=allow_fixtures)
        restored=T.restore_index(store,T.run_id(source_run),checkpoint=True)
        if not restored:raise ValueError('V1.2+ checkpoint required; migrate V1.1 explicitly first')
        manifest,_,_=restored
        for origin in sorted({x['run_id'] for x in store.transport['objects'].values()}):store.hydrate_run(origin)
        # Latest independent checkpoint excludes ancestor manifests. Read only
        # their small verified indexes, retaining the original sealed bytes.
        with I.verified_chain(catalog,fetch,allow_fixtures) as prior:anchor=prior.manifest
        cursor=manifest;seen=set()
        for _ in range(I.MAX_CHAIN):
            if cursor['sha256']==anchor['sha256'] or cursor.get('parent')=={'run_id':anchor['run_id'],'sha256':anchor['sha256']}:break
            parent=cursor.get('parent')
            if not parent or parent['sha256'] in seen:raise ValueError('source ancestry unavailable')
            seen.add(parent['sha256'])
            with tempfile.TemporaryDirectory(prefix='c-incremental-index-') as index:
                intermediate=T.RemoteArchive(Path(index),source,allow_fixtures=allow_fixtures)
                result=T.restore_index(intermediate,T.run_id(parent['run_id']),checkpoint=True)
                if not result or result[0]['sha256']!=parent['sha256']:raise ValueError('source parent index mismatch')
                cursor=result[0]
                I.A.publish(store.root/'runs'/(cursor['run_id']+'.json'),I.encoded(cursor))
        else:raise ValueError('source ancestry limit')
        result=I.prepare(store,manifest,catalog,fetch,clock,source_run,destination,allow_fixtures)
    return dict(result,source_download_bytes=source.bytes,market_scan_executed=False)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['init-catalog','prepare-local','prepare-source','restore','publish'])
    p.add_argument('--repository',default=os.environ.get('GITHUB_REPOSITORY','nh1018/upbit-scanner'))
    p.add_argument('--catalog',type=Path);p.add_argument('--catalog-sha256')
    p.add_argument('--archive',type=Path);p.add_argument('--run-id');p.add_argument('--manifest-sha256')
    p.add_argument('--generation-time');p.add_argument('--destination',type=Path)
    p.add_argument('--cache',type=Path);p.add_argument('--summary',type=Path)
    p.add_argument('--package',type=Path);p.add_argument('--sha256');p.add_argument('--approval',default='')
    p.add_argument('--target-commit',default=os.environ.get('GITHUB_SHA',''))
    args=p.parse_args(argv)
    try:
        if args.command=='init-catalog':
            catalog=I.initial_catalog(repository=args.repository)
            result={'status':'UNVERIFIED_TEMPLATE','catalog':catalog,'catalog_file_sha256':I.R.S.sha(I.encoded(catalog)),'remote_writes':0}
        else:
            catalog=I.read_catalog(args.catalog.read_bytes(),args.catalog_sha256)
            client=I.P.GitHub(args.repository,os.environ.get('GH_TOKEN'))
            if client.repository!=catalog['base']['repository']:raise ValueError('repository mismatch')
            fetch=I.PackageCache(I.github_fetch(client),args.cache)
            if args.command=='restore':result=I.restore(catalog,fetch,args.destination)
            elif args.command=='prepare-local':
                store=I.A.Archive(args.archive);manifest=store.manifest(args.run_id,args.manifest_sha256)
                result=I.prepare(store,manifest,catalog,fetch,args.generation_time,args.run_id,args.destination)
            elif args.command=='prepare-source':
                result=prepare_source(client,args.run_id,catalog,fetch,args.generation_time,args.destination)
            else:
                result=X.publish(client,args.package.read_bytes(),args.sha256,catalog,fetch,args.target_commit,args.approval)
            result['package_io']=fetch.metrics
        if args.summary:
            I.R.no_symlinks(args.summary);I.A.isolated(args.summary)
            I.A.publish(args.summary,I.encoded(result))
        print(I.dumps(result));return 0
    except Exception as exc:
        # Exception messages from transports/files may contain signed URLs or
        # operator input. Emit class only; never headers/credentials/raw errors.
        result={'status':'FAILED','error_type':type(exc).__name__,'remote_success_unconfirmed':True}
        if args.summary:
            I.R.no_symlinks(args.summary);I.A.isolated(args.summary)
            I.A.publish(args.summary,I.encoded(result))
        print(I.dumps(result));return 1


if __name__=='__main__':raise SystemExit(main())

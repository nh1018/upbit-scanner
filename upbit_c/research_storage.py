"""Explicit local V1.2 manual tooling; never starts Actions or publishes Releases."""
import argparse
import json
import tempfile
import time
from pathlib import Path

from upbit_b.feature_contracts import dumps
from .research_archive import Archive, import_v11, import_remote_v11, pending_work, evaluate_active


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=('import-v11','import-remote-v11','audit','restore','prepare','evaluate','scan','checkpoint','recover-checkpoint'))
    p.add_argument('--archive', required=True, type=Path)
    p.add_argument('--run', required=True)
    p.add_argument('--manifest-sha', help='required original trusted V1.2 manifest SHA')
    p.add_argument('--state', type=Path)
    p.add_argument('--v11-manifest', type=Path)
    p.add_argument('--destination', type=Path)
    p.add_argument('--record', action='append', help='restore only this exact original record (repeatable)')
    p.add_argument('--next-run')
    p.add_argument('--parent-run', help='explicit parent for additional V1.1 import')
    p.add_argument('--source-run', help='successful V1.1 Actions run to migrate once')
    p.add_argument('--outcome-limit', type=int, default=30)
    args = p.parse_args(argv)
    store = Archive(args.archive)
    if args.operation in ('import-v11','import-remote-v11'):
        if bool(args.parent_run) != bool(args.manifest_sha):
            p.error('import parent-run and manifest-sha must be supplied together')
        parent = (args.parent_run,args.manifest_sha) if args.parent_run else None
        if args.operation == 'import-remote-v11':
            import os
            from .research_operations import github_loader
            token = os.environ.get('GH_TOKEN')
            if not token or not args.source_run:
                p.error('explicit source-run and existing GH_TOKEN required')
            manifest, verification = import_remote_v11(store,args.run,args.source_run,
                github_loader('nh1018/upbit-scanner',token),parent)
            result = {'manifest':manifest,'source_verification':verification}
            print(dumps(result))
            return result
        if not args.state or not args.v11_manifest:
            p.error('explicit verified V1.1 state and manifest required')
        raw = args.v11_manifest.read_bytes()
        result = import_v11(store, args.run, args.state, json.loads(raw), raw, parent)
    else:
        if not args.manifest_sha:
            p.error('trusted manifest SHA required; no automatic latest selection')
        manifest = store.manifest(args.run, args.manifest_sha)
        if args.operation == 'audit':
            result = store.audit(manifest)
        elif args.operation == 'restore':
            if not args.destination:
                p.error('isolated destination required')
            result = {'restored_records':store.restore(manifest, args.destination, args.record)}
        elif args.operation in ('checkpoint','recover-checkpoint'):
            if not args.destination:
                p.error('independent destination required')
            result = store.checkpoint(manifest, args.destination, recover_active=args.operation=='recover-checkpoint')
        elif args.operation == 'prepare':
            active = store.active(manifest)
            result = {'work':pending_work(active, time.time_ns()//1000000),
                      'cold_payload_bytes_read':0, 'signals':len(active['signals'])}
        else:
            if not args.next_run or args.next_run == args.run:
                p.error('new explicit next-run required')
            if args.operation == 'evaluate':
                from .research_scan import ResearchClient
                delta = evaluate_active(store.active(manifest), ResearchClient(),
                                        time.time_ns()//1000000, args.outcome_limit)
            else:
                from .research_scan import scan, ResearchClient
                from .research_history import record_scan
                # Only this new full-universe scan is materialized; never old scans.
                with tempfile.TemporaryDirectory(prefix='c-storage-scan-') as temp:
                    report = scan(ResearchClient())
                    record_scan(Path(temp), report)
                    delta = {f.relative_to(temp).as_posix():f.read_bytes()
                             for f in Path(temp).rglob('*.json')}
            result = store.commit(args.next_run, delta, (args.run, args.manifest_sha))
    print(dumps(result))
    return result


if __name__ == '__main__':
    main()

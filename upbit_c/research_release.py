"""V1.4 local Release-ready checkpoint transport. No remote mutation API.

Default prepare is audit/plan only. Explicit local writes are immutable. Package
SHA256 must be pinned outside the downloaded package before restore.
"""
import argparse
import io
import json
import os
import re
import tempfile
import urllib.request
import urllib.error
import zipfile
from pathlib import Path

from upbit_b.feature_contracts import dumps
from . import research_archive as A
from . import research_segments as S
from .research_operations import NoRedirect

SCHEMA = 'upbit-c-release-package-1.4'
MAX_BYTES = 2 * 1024**3 - 1
MAX_ENTRIES = 100000


def safe_name(name):
    if not re.fullmatch(r'(?:objects/[0-9a-f]{64}\.gz|(?:active|descriptors|imports)/[0-9a-f]{64}\.json|runs/[A-Za-z0-9_-]{1,100}\.json)', name):
        raise ValueError('unsafe package path')
    return name


def ref(raw):
    return {'sha256': S.sha(raw), 'bytes': len(raw)}


def no_symlinks(path):
    if any(part.is_symlink() or getattr(part, 'is_junction', lambda: False)()
           for part in [path, *path.parents]):
        raise ValueError('symlink destination prohibited')


def required_files(store, manifest):
    names = {'runs/' + manifest['run_id'] + '.json',
             'active/' + manifest['active_sha256'] + '.json'}
    for record in manifest['inventory'].values():
        names.add('descriptors/' + record['descriptor_sha256'] + '.json')
        for chunk in store.descriptor(record)['chunks']:
            names.add('objects/' + chunk['sha256'] + '.gz')
    for migration in manifest['imports'].values():
        for item in [migration['original_manifest']] + migration.get('chain_manifests', []):
            names.add('imports/' + item['sha256'] + '.json')
    return names


def prepare(store, manifest, destination=None):
    """Full audit, deterministic self-contained package; no upload or recompute."""
    store.manifest(manifest['run_id'], manifest['sha256'])
    audit = store.audit(manifest)
    names = sorted(required_files(store, manifest))
    if len(names) + 1 > MAX_ENTRIES:
        raise ValueError('package entry limit; sharding required')
    files = {n: ref((store.root / safe_name(n)).read_bytes()) for n in names}
    receipt = A.seal({'schema_version': SCHEMA, 'activation': 'RESEARCH_ONLY',
        'run_id': manifest['run_id'], 'lineage_id': manifest['lineage_id'],
        'original_manifest_sha256': manifest['sha256'],
        'inventory_sha256': manifest['inventory_sha256'],
        'active_sha256': manifest['active_sha256'],
        'original_records': manifest['inventory'],
        'fixture_namespace': store.allow_fixtures, 'files': files})
    result = {'schema_version': SCHEMA, 'mode': 'DRY_RUN', 'audit': audit,
        'package_id': receipt['sha256'], 'asset_name': 'c-research-' + receipt['sha256'] + '.zip',
        'receipt': receipt, 'payload_bytes': sum(x['bytes'] for x in files.values()),
        'remote_writes': 0}
    if result['payload_bytes'] > MAX_BYTES:
        raise ValueError('Release asset size limit; sharding required')
    if destination is None:
        return result
    no_symlinks(Path(destination))
    destination = A.isolated(destination)
    no_symlinks(destination)
    with tempfile.TemporaryDirectory(prefix='c-release-') as tmp:
        staged = Path(tmp) / result['asset_name']
        with zipfile.ZipFile(staged, 'w', compression=zipfile.ZIP_STORED) as zipped:
            for name in names + ['release-manifest.json']:
                raw = (dumps(receipt) + '\n').encode() if name == 'release-manifest.json' else (store.root / name).read_bytes()
                if name != 'release-manifest.json' and ref(raw) != files[name]:
                    raise ValueError('source changed during packaging')
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                # Preserve the approved V1.4 Windows ZIP bytes on every host.
                # ZipInfo otherwise defaults to 0 on Windows and 3 on Unix.
                info.create_system = 0
                zipped.writestr(info, raw)
        raw = staged.read_bytes()
        if len(raw) > MAX_BYTES:
            raise ValueError('Release asset size limit; sharding required')
        # Verify complete package before making it visible to operators.
        verify(raw, S.sha(raw), store.allow_fixtures)
        external = {'schema_version': SCHEMA, 'asset_name': result['asset_name'],
                    'package_id': receipt['sha256'], **ref(raw)}
        A.publish(destination / result['asset_name'], raw)
        A.publish(destination / (result['asset_name'] + '.receipt.json'), (dumps(external) + '\n').encode())
    return dict(result, mode='LOCAL_PACKAGE', package=external)


def unpack_verified(raw, expected_sha, root, allow_fixtures=False):
    A.identifier(expected_sha)
    if len(raw) > MAX_BYTES or S.sha(raw) != expected_sha:
        raise ValueError('package external hash/size mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
        infos = zipped.infolist()
        if (len(infos) > MAX_ENTRIES or len({x.filename for x in infos}) != len(infos)
                or sum(x.file_size for x in infos) > MAX_BYTES):
            raise ValueError('duplicate/oversized package')
        if any(x.is_dir() or x.compress_type != zipfile.ZIP_STORED for x in infos):
            raise ValueError('unsupported package encoding')
        if 'release-manifest.json' not in {x.filename for x in infos}:
            raise ValueError('missing package manifest')
        meta = zipped.getinfo('release-manifest.json')
        if meta.file_size > 16 * 1024**2:
            raise ValueError('package manifest too large')
        receipt = A.unseal(json.loads(zipped.read(meta)))
        if receipt['schema_version'] != SCHEMA or receipt['activation'] != 'RESEARCH_ONLY':
            raise ValueError('wrong package contract')
        if receipt['fixture_namespace'] and not allow_fixtures:
            raise ValueError('fixture package prohibited')
        expected = receipt['files']
        if {x.filename for x in infos} != set(expected) | {'release-manifest.json'}:
            raise ValueError('missing/extra package files')
        # Validate all names and sizes before reading any payload.
        for name, item in expected.items():
            safe_name(name)
            if zipped.getinfo(name).file_size != item['bytes']:
                raise ValueError('package file size mismatch')
        for name, item in expected.items():
            value = zipped.read(name)
            if ref(value) != item:
                raise ValueError('package file hash mismatch')
            A.publish(root / name, value)
    store = A.Archive(root, allow_fixtures)
    manifest = store.manifest(receipt['run_id'], receipt['original_manifest_sha256'])
    if (manifest['lineage_id'] != receipt['lineage_id'] or
            manifest['inventory_sha256'] != receipt['inventory_sha256'] or
            manifest['active_sha256'] != receipt['active_sha256'] or
            manifest['inventory'] != receipt['original_records'] or
            required_files(store, manifest) != set(expected)):
        raise ValueError('package original/index linkage mismatch')
    store.audit(manifest)
    return store, manifest, receipt


def verify(raw, expected_sha, allow_fixtures=False):
    with tempfile.TemporaryDirectory(prefix='c-release-verify-') as tmp:
        store, manifest, receipt = unpack_verified(raw, expected_sha, Path(tmp), allow_fixtures)
        return {'status': 'VERIFIED', 'package_id': receipt['sha256'],
                'manifest_sha256': manifest['sha256'], 'lineage_id': manifest['lineage_id'],
                **store.audit(manifest)}


def restore(raw, expected_sha, destination, allow_fixtures=False):
    no_symlinks(Path(destination))
    destination = A.isolated(destination)
    no_symlinks(destination)
    with tempfile.TemporaryDirectory(prefix='c-release-restore-') as tmp:
        store, manifest, receipt = unpack_verified(raw, expected_sha, Path(tmp), allow_fixtures)
        # No destination file written before all existing overlaps are checked.
        for name in required_files(store, manifest):
            target = destination / name
            no_symlinks(target)
            if target.exists() and target.read_bytes() != (store.root / name).read_bytes():
                raise ValueError('restore overwrite prohibited')
        audit = store.checkpoint(manifest, destination)
        return {'package_id': receipt['sha256'], 'run_id': manifest['run_id'],
                'manifest_sha256': manifest['sha256'], **audit}


def publication_plan(package, assets):
    """Read-only retry/dedup check. Never delete/replace an uploaded asset."""
    matches = [x for x in assets if x.get('name') == package['asset_name']]
    if len(matches) > 1:
        raise ValueError('duplicate Release asset names')
    if not matches:
        return {'action': 'APPROVAL_REQUIRED_UPLOAD', 'remote_writes': 0}
    item = matches[0]
    if (item.get('state') != 'uploaded' or item.get('size') != package['bytes'] or
            item.get('digest') != 'sha256:' + package['sha256']):
        raise ValueError('existing Release asset conflict or unverified upload')
    return {'action': 'ALREADY_PRESENT_REVERIFY_DOWNLOAD', 'asset_id': item['id'], 'remote_writes': 0}


def compare_originals(current, verified_manifests):
    """Compare fully verified package manifests, not untrusted asset labels.

    Shared originals can legitimately occur in distinct checkpoint versions.
    Report them explicitly; never skip the new active state merely for overlap.
    """
    A.unseal(current)
    known = {}
    for previous in verified_manifests:
        A.unseal(previous)
        if previous['schema_version'] != SCHEMA:
            raise ValueError('wrong catalog contract')
        for name, item in previous['original_records'].items():
            identity = {key: item[key] for key in ('sha256', 'bytes')}
            if name in known and known[name] != identity:
                raise ValueError('existing original identity conflict')
            known[name] = identity
    duplicates, new = [], []
    for name, item in current['original_records'].items():
        if name in known:
            if known[name] != {key: item[key] for key in ('sha256', 'bytes')}:
                raise ValueError('original identity conflict')
            duplicates.append(name)
        else:
            new.append(name)
    return {'duplicate_originals': sorted(duplicates), 'new_originals': sorted(new),
            'same_package_present': any(x['sha256'] == current['sha256'] for x in verified_manifests),
            'remote_writes': 0}


class ReleaseReader:
    """GET-only API. Token never forwarded to download redirect hosts."""
    def __init__(self, repository, token=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('invalid repository')
        self.repository, self.token = repository, token

    def get(self, path, binary=False):
        headers = {'Accept': 'application/octet-stream' if binary else 'application/vnd.github+json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request('https://api.github.com/repos/' + self.repository + '/' + path, headers=headers)
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response:
                raw = response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if binary and exc.code == 302:
                location = exc.headers.get('Location', '')
                if not location.startswith('https://'):
                    raise ValueError('unsafe download redirect') from None
                with urllib.request.urlopen(location, timeout=120) as response:
                    raw = response.read(MAX_BYTES + 1)
            else:
                raise ValueError('Release read HTTP ' + str(exc.code)) from None
        if len(raw) > MAX_BYTES:
            raise ValueError('Release download size limit')
        return raw if binary else json.loads(raw)

    def assets(self, release_id):
        if type(release_id) is not int or release_id <= 0:
            raise ValueError('invalid Release ID')
        result = []
        for page in range(1, 12):
            rows = self.get(f'releases/{release_id}/assets?per_page=100&page={page}')
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise ValueError('Release asset pagination limit')

    def download(self, asset_id, expected_sha):
        if type(asset_id) is not int or asset_id <= 0:
            raise ValueError('invalid asset ID')
        raw = self.get('releases/assets/' + str(asset_id), binary=True)
        verify(raw, expected_sha)
        return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--manifest-sha', required=True)
    p.add_argument('--write-package', type=Path)
    for mode in ('verify', 'restore'):
        p = sub.add_parser(mode)
        p.add_argument('--package', type=Path, required=True)
        p.add_argument('--sha256', required=True)
        if mode == 'restore':
            p.add_argument('--destination', type=Path, required=True)
    p = sub.add_parser('plan-publication')
    p.add_argument('--package-receipt', type=Path, required=True)
    p.add_argument('--repository', required=True)
    p.add_argument('--release-id', type=int, required=True)
    p = sub.add_parser('download')
    p.add_argument('--repository', required=True)
    p.add_argument('--asset-id', type=int, required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        store = A.Archive(args.archive)
        result = prepare(store, store.manifest(args.run_id, args.manifest_sha), args.write_package)
    elif args.command == 'plan-publication':
        package = json.loads(args.package_receipt.read_bytes())
        result = publication_plan(package, ReleaseReader(args.repository, os.environ.get('GH_TOKEN')).assets(args.release_id))
    elif args.command == 'download':
        no_symlinks(args.destination)
        destination = A.isolated(args.destination)
        raw = ReleaseReader(args.repository, os.environ.get('GH_TOKEN')).download(args.asset_id, args.sha256)
        name = 'c-research-download-' + args.sha256 + '.zip'
        A.publish(destination / name, raw)
        result = {'status': 'VERIFIED_DOWNLOAD', **ref(raw), 'file': name, 'remote_writes': 0}
    else:
        if args.package.stat().st_size > MAX_BYTES:
            raise ValueError('package size limit')
        raw = args.package.read_bytes()
        result = verify(raw, args.sha256) if args.command == 'verify' else restore(raw, args.sha256, args.destination)
    print(dumps(result))


if __name__ == '__main__':
    main()

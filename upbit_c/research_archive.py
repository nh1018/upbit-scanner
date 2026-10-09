"""V1.2 lossless content-addressed archive and disposable active index.

Local filesystem transport only. No production writes, automatic retention or
claim of permanent durability. Manifests are the atomic commit boundary.
"""
import gzip
import copy
import json
import os
import re
import tempfile
from pathlib import Path

from upbit_b.feature_contracts import digest, dumps
from . import research_segments as legacy
from .research_history import validate_signal
from .research_outcomes import DAY_HORIZONS, SCHEMA as OUTCOME_SCHEMA

SCHEMA = 'upbit-c-cold-archive-1.2'
ACTIVE_SCHEMA = 'upbit-c-active-state-1.2'
CHUNK_BYTES = 1024 * 1024
HEX = re.compile(r'[0-9a-f]{64}\Z')


def isolated(root):
    root = Path(root).resolve()
    forbidden = {'.git', '.github', 'data', 'data_market', 'metadata_features',
                 'market_data_v1', 'btc_anytime', 'upbit_b', 'upbit_c', 'tests',
                 'chat_analysis', 'diagnostics'}
    if any(p.lower() in forbidden or p.lower().startswith('output') for p in root.parts):
        raise ValueError('protected namespace')
    return root


def seal(value):
    return dict(value, sha256=digest(value))


def unseal(value):
    if not isinstance(value, dict) or value.get('sha256') != digest({k:v for k,v in value.items() if k != 'sha256'}):
        raise ValueError('manifest/index hash mismatch')
    return value


def publish(path, raw):
    """Never replace bytes, including after interruption or concurrent replay."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('immutable path conflict')
        return
    fd, name = tempfile.mkstemp(prefix='.stage-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise ValueError('concurrent immutable path conflict') from None
    finally:
        os.unlink(name)


def identifier(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError('invalid content identifier')
    return value


class Archive:
    def __init__(self, root, allow_fixtures=False):
        self.root = isolated(root)
        self.allow_fixtures = allow_fixtures

    def object_path(self, key):
        return self.root / 'objects' / (identifier(key) + '.gz')

    def object_available(self, item):
        """Local check; authenticated remote transport can supply pinned receipts."""
        obj = self.object_path(item['sha256'])
        return obj.is_file() and obj.stat().st_size == item['compressed_bytes']

    def put_record(self, name, raw):
        legacy.verify_record(name, raw, self.allow_fixtures)
        chunks = []
        for offset in range(0, len(raw), CHUNK_BYTES):
            part = raw[offset:offset + CHUNK_BYTES]
            key = legacy.sha(part)
            compressed = gzip.compress(part, compresslevel=6, mtime=0)
            if self.object_path(key).exists():
                # Different Python/zlib encoders may produce different gzip
                # containers for identical bytes. Reuse a verified original.
                import io
                compressed = self.object_path(key).read_bytes()
                with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
                    if stream.read(CHUNK_BYTES+1) != part:
                        raise ValueError('existing content-addressed object corrupt')
            else:
                publish(self.object_path(key), compressed)
            chunks.append({'sha256':key, 'bytes':len(part),
                           'compressed_sha256':legacy.sha(compressed), 'compressed_bytes':len(compressed)})
        descriptor = {'sha256':legacy.sha(raw), 'bytes':len(raw), 'chunks':chunks}
        encoded = (dumps(descriptor)+'\n').encode()
        key = legacy.sha(encoded)
        publish(self.root/'descriptors'/(key+'.json'),encoded)
        # Do not repeat every chunk descriptor in every cumulative manifest.
        return {'sha256':descriptor['sha256'], 'bytes':len(raw),
                'descriptor_sha256':key, 'descriptor_bytes':len(encoded)}

    def descriptor(self, ref):
        raw = (self.root/'descriptors'/(identifier(ref['descriptor_sha256'])+'.json')).read_bytes()
        if legacy.sha(raw) != ref['descriptor_sha256'] or len(raw) != ref['descriptor_bytes']:
            raise ValueError('record descriptor hash mismatch')
        value = json.loads(raw)
        if value['sha256'] != ref['sha256'] or value['bytes'] != ref['bytes']:
            raise ValueError('descriptor record identity mismatch')
        return value

    def read_record(self, name, ref):
        legacy.safe_path(name)
        parts = []
        for item in self.descriptor(ref)['chunks']:
            raw = self.object_path(item['sha256']).read_bytes()
            if len(raw) != item['compressed_bytes'] or legacy.sha(raw) != item['compressed_sha256']:
                raise ValueError('compressed object hash mismatch')
            # Bounded decompression, never trust a gzip expansion declaration.
            import io
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                part = stream.read(CHUNK_BYTES + 1)
            if len(part) != item['bytes'] or len(part) > CHUNK_BYTES or legacy.sha(part) != item['sha256']:
                raise ValueError('uncompressed object hash mismatch')
            parts.append(part)
        raw = b''.join(parts)
        if len(raw) != ref['bytes'] or legacy.sha(raw) != ref['sha256']:
            raise ValueError('record hash mismatch')
        legacy.verify_record(name, raw, self.allow_fixtures)
        return raw

    def manifest(self, run, expected_sha):
        identifier(expected_sha)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', run):
            raise ValueError('unsafe run identifier')
        value = unseal(json.loads((self.root / 'runs' / (run + '.json')).read_bytes()))
        if value['sha256'] != expected_sha or value['run_id'] != run or value['schema_version'] != SCHEMA:
            raise ValueError('wrong pinned manifest')
        if value.get('test_fixture_namespace') and not self.allow_fixtures:
            raise ValueError('fixture archive prohibited in real research')
        for name in value['inventory']:
            legacy.safe_path(name)
        if value['inventory_sha256'] != digest(value['inventory']):
            raise ValueError('inventory hash mismatch')
        return value

    def active(self, manifest):
        unseal(manifest)
        raw = (self.root / 'active' / (identifier(manifest['active_sha256']) + '.json')).read_bytes()
        value = unseal(json.loads(raw))
        if value['sha256'] != manifest['active_sha256'] or value['schema_version'] != ACTIVE_SCHEMA:
            raise ValueError('wrong active index')
        if value['inventory_sha256'] != digest(manifest['inventory']):
            raise ValueError('active inventory mismatch')
        # Validate index coverage against manifest, without downloading scan blobs.
        expected = {p.split('/')[1][:-5] for p in manifest['inventory'] if p.startswith('signals/')}
        if set(value['signals']) != expected:
            raise ValueError('missing/extra active signal')
        for key, item in value['signals'].items():
            if item['signal'].get('test_fixture') and not self.allow_fixtures:
                raise ValueError('fixture active signal prohibited')
            validate_signal(item['signal'])
            if item['signal']['signal_id'] != key or item['original_reference'] != 'signals/' + key + '.json':
                raise ValueError('active signal identity mismatch')
            if digest(item['signal']) != item['signal_record_sha256']:
                raise ValueError('active signal payload mismatch')
            for days, result in item['outcomes'].items():
                if days not in ('1','3','7') or result['status'] not in ('PENDING','UNVERIFIABLE','MATURED'):
                    raise ValueError('invalid active outcome')
                if result['reference'] not in manifest['inventory'] or not result['reference'].startswith('evaluations/'):
                    raise ValueError('missing active outcome reference')
        return value

    def build_active(self, inventory, previous=None, delta=None):
        """Full recovery path; reads signals/evaluations, never old scan payloads."""
        signals = copy.deepcopy(previous['signals']) if previous else {}
        evaluations = []
        selected = inventory if delta is None else {name:inventory[name] for name in delta}
        for name, ref in sorted(selected.items()):
            if name.startswith('scans/'):
                continue
            record = json.loads(self.read_record(name, ref))['record']
            if name.startswith('signals/'):
                key = record['signal_id']
                signals[key] = {'signal':record, 'signal_record_sha256':digest(record),
                    'original_reference':name, 'outcomes':{}, 'last_evaluation_ms':None}
            else:
                evaluations.append((name, record))
        for name, record in sorted(evaluations, key=lambda x:(x[1]['as_of_ms'], x[0])):
            key, days = record['signal_id'], record['horizon_days']
            if key not in signals or days not in DAY_HORIZONS or record['schema_version'] != OUTCOME_SCHEMA:
                raise ValueError('orphan/invalid evaluation')
            if record['status'] not in ('PENDING','UNVERIFIABLE','MATURED'):
                raise ValueError('invalid outcome status')
            item = signals[key]
            horizon = str(days)
            previous = item['outcomes'].get(horizon)
            item['last_evaluation_ms'] = max(item['last_evaluation_ms'] or 0, record['as_of_ms'])
            if previous and previous['status'] == 'MATURED':
                if record['status'] == 'MATURED' and previous['record_sha256'] != digest(record):
                    raise ValueError('conflicting matured outcome')
                continue
            if record['anchor_type'] != 'NEXT_1H_OPEN_PROXY' or record['anchor_open_ms'] != item['signal']['proxy_anchor_open_ms']:
                raise ValueError('outcome anchor mismatch')
            end = item['signal']['proxy_anchor_open_ms'] + days * 86400000
            if record['endpoint_close_ms'] != end or record['activation'] != 'RESEARCH_ONLY':
                raise ValueError('invalid outcome endpoint/activation')
            if record['status'] == 'MATURED' and (record['as_of_ms'] < end or not record['path'] or record.get('path_sha256') != digest(record['path'])):
                raise ValueError('invalid matured path/cutoff')
            if previous and record['status'] != 'MATURED' and (record['as_of_ms'], name) < (previous['as_of_ms'], previous['reference']):
                continue
            item['outcomes'][horizon] = {'status':record['status'], 'reference':name,
                'record_sha256':digest(record), 'as_of_ms':record['as_of_ms']}
        return seal({'schema_version':ACTIVE_SCHEMA, 'activation':'RESEARCH_ONLY',
                     'inventory_sha256':digest(inventory), 'signals':signals})

    def commit(self, run, records, parent=None, migration=None):
        """records is only the new delta. Explicit parent must be hash-pinned.

        Objects/index first; final exclusive run manifest last. Retry identical
        inputs after a partial write is safe. No implicit missing-parent fallback.
        """
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', run):
            raise ValueError('unsafe run identifier')
        previous = self.manifest(*parent) if parent else None
        if not parent and any((self.root / 'runs').glob('*.json')):
            # Only byte-identical root replay is allowed in an existing lineage.
            existing = self.root / 'runs' / (run + '.json')
            if not existing.exists():
                raise ValueError('explicit parent required; no implicit fresh start')
        previous_active = None
        if previous:
            previous_active = self.active(previous)  # Corruption is not silently reset.
        inventory = dict(previous['inventory']) if previous else {}
        added = []
        for name, raw in sorted(records.items()):
            legacy.safe_path(name)
            if name in inventory:
                if legacy.sha(raw) != inventory[name]['sha256']:
                    # V1.1 repeated retrieval of the same signal preserves its
                    # original first observation, never a shifted proxy anchor.
                    legacy.verify_record(name, raw, self.allow_fixtures)
                    new = json.loads(raw)['record']
                    old = previous_active['signals'][new['signal_id']]['signal'] if previous_active and name.startswith('signals/') else None
                    comparable = lambda s:{k:v for k,v in s['score'].items() if k != 'sources'}
                    if old is None or comparable(old) != comparable(new):
                        raise ValueError('existing raw overwrite prohibited')
                continue
            inventory[name] = self.put_record(name, raw)
            added.append(name)
        # A supplied scan must have every actually emitted signal present. Do
        # not invent absent past signals during migration or continuation.
        from .research_history import signals as scan_signals
        scan_summaries = []
        for name, raw in records.items():
            if name.startswith('scans/'):
                report = json.loads(raw)['record']
                emitted = list(scan_signals(report))
                for signal in emitted:
                    if 'signals/' + signal['signal_id'] + '.json' not in inventory:
                        raise ValueError('missing scan signal; historical reconstruction prohibited')
                if name in added:
                    scan_summaries.append({'reference':name, 'qualified_count':len(emitted),
                        'started_at_ms':report['started_at_ms'], 'finished_at_ms':report['finished_at_ms'],
                        'success_count':report['success_count'], 'failure_count':report['failure_count']})
        for ref in inventory.values():
            for item in self.descriptor(ref)['chunks']:
                if not self.object_available(item):
                    raise ValueError('missing/truncated cold object; no new checkpoint')
        active = self.build_active(inventory, previous_active, added)
        publish(self.root / 'active' / (active['sha256'] + '.json'), (dumps(active)+'\n').encode())
        imports = dict(previous['imports']) if previous else {}
        if migration:
            original = migration['original_manifest']
            imports[original['sha256']] = migration
        manifest = seal({'schema_version':SCHEMA, 'activation':'RESEARCH_ONLY', 'run_id':run,
            'test_fixture_namespace':self.allow_fixtures,
            'parent':{'run_id':parent[0], 'sha256':parent[1]} if parent else None,
            'lineage_id':previous['lineage_id'] if previous else run,
            'migration':migration, 'imports':imports,
            'inventory':inventory, 'inventory_sha256':digest(inventory),
            'delta_files':added, 'active_sha256':active['sha256'],
            'scan_summaries':scan_summaries,
            'no_signal_scan_count':(previous['no_signal_scan_count'] if previous else 0)+sum(x['qualified_count']==0 for x in scan_summaries)})
        publish(self.root / 'runs' / (run + '.json'), (dumps(manifest)+'\n').encode())
        return manifest

    def audit_cold(self, manifest):
        unseal(manifest)
        if manifest['inventory_sha256'] != digest(manifest['inventory']):
            raise ValueError('inventory hash mismatch')
        for name, ref in manifest['inventory'].items():
            self.read_record(name, ref)
        for migration in manifest['imports'].values():
            for ref in [migration['original_manifest']] + migration.get('chain_manifests',[]):
                raw = (self.root/'imports'/(identifier(ref['sha256'])+'.json')).read_bytes()
                if legacy.sha(raw) != ref['sha256'] or len(raw) != ref['bytes']:
                    raise ValueError('original import manifest changed')
        return len(manifest['inventory'])

    def audit(self, manifest):
        self.audit_cold(manifest)
        active = self.active(manifest)
        if active != self.build_active(manifest['inventory']):
            raise ValueError('active not derived from originals')
        return {'records':len(manifest['inventory']), 'signals':len(active['signals']), 'status':'VERIFIED'}

    def checkpoint(self, manifest, destination, recover_active=False):
        """Independent verified backup; copy each unique compressed shard once."""
        if recover_active:
            self.audit_cold(manifest)
            active = self.rebuild_active(manifest)
        else:
            self.audit(manifest)
            active = self.active(manifest)
        dest = Archive(destination, self.allow_fixtures)
        if dest.root == self.root:
            raise ValueError('checkpoint must be an independent destination')
        for ref in manifest['inventory'].values():
            descriptor = self.descriptor(ref)
            descriptor_key = ref['descriptor_sha256']
            publish(dest.root/'descriptors'/(descriptor_key+'.json'),
                    (self.root/'descriptors'/(descriptor_key+'.json')).read_bytes())
            for item in descriptor['chunks']:
                publish(dest.object_path(item['sha256']), self.object_path(item['sha256']).read_bytes())
        for migration in manifest['imports'].values():
            for ref in [migration['original_manifest']] + migration.get('chain_manifests',[]):
                key = ref['sha256']
                publish(dest.root/'imports'/(key+'.json'), (self.root/'imports'/(key+'.json')).read_bytes())
        publish(dest.root/'active'/(active['sha256']+'.json'), (dumps(active)+'\n').encode())
        # Final commit boundary, no ancestor manifest dependency.
        publish(dest.root/'runs'/(manifest['run_id']+'.json'), (dumps(manifest)+'\n').encode())
        return dest.audit(dest.manifest(manifest['run_id'],manifest['sha256']))

    def restore(self, manifest, destination, names=None):
        unseal(manifest)
        destination = isolated(destination)
        names = sorted(manifest['inventory'] if names is None else names)
        # Verify everything requested before publishing anything.
        with tempfile.TemporaryDirectory(prefix='c-archive-restore-') as temp:
            stage = Path(temp)
            for name in names:
                raw = self.read_record(name, manifest['inventory'][name])
                target = destination / name
                if target.exists() and target.read_bytes() != raw:
                    raise ValueError('restore overwrite prohibited')
                staged = stage/name
                staged.parent.mkdir(parents=True,exist_ok=True)
                staged.write_bytes(raw)
            for name in names:
                publish(destination / name, (stage/name).read_bytes())
        return len(names)

    def rebuild_active(self, manifest):
        """Return verified recovery bytes; do not overwrite a corrupt original."""
        unseal(manifest)
        rebuilt = self.build_active(manifest['inventory'])
        if rebuilt['sha256'] != manifest['active_sha256']:
            raise ValueError('recovered active differs from pinned original')
        return rebuilt


def pending_work(active, as_of_ms):
    """No archive access. Full original signal contracts remain in the index."""
    unseal(active)
    work = []
    for key, item in active['signals'].items():
        for days in DAY_HORIZONS:
            prior = item['outcomes'].get(str(days), {})
            if prior.get('status') == 'MATURED':
                continue
            end = item['signal']['proxy_anchor_open_ms'] + days*86400000
            work.append({'signal_id':key, 'days':days, 'due':as_of_ms >= end,
                         'endpoint_ms':end, 'previous_status':prior.get('status','PENDING')})
    return sorted(work, key=lambda x:(not x['due'], x['endpoint_ms'], x['signal_id'], x['days']))


def evaluate_active(active, client, as_of_ms, limit=30):
    """Use unchanged outcome calculator; no scan/source archive download."""
    from .research_outcomes import fetch_outcome
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('invalid outcome limit')
    records = {}
    for work in pending_work(active, as_of_ms)[:limit]:
        result = fetch_outcome(client, active['signals'][work['signal_id']]['signal'], work['days'], as_of_ms)
        key = digest({'signal_id':work['signal_id'], 'days':work['days'], 'contract':OUTCOME_SCHEMA}) if result['status'] == 'MATURED' else digest(result)
        records['evaluations/' + key + '.json'] = (dumps({'record':result, 'record_sha256':digest(result)})+'\n').encode()
    return records


def import_v11(archive, run, state, source_manifest, source_manifest_raw=None, parent=None, chain=None):
    """Explicit verified migration, not regeneration of historical observations."""
    legacy.verify_manifest(source_manifest)
    actual = legacy.inventory(state, archive.allow_fixtures)
    if actual != source_manifest['inventory']:
        raise ValueError('missing/changed V1.1 inventory')
    original = source_manifest_raw if source_manifest_raw is not None else (dumps(source_manifest)+'\n').encode()
    if json.loads(original) != source_manifest:
        raise ValueError('original manifest bytes do not match validated manifest')
    key = legacy.sha(original)
    publish(archive.root/'imports'/(key+'.json'),original)
    chain_refs = []
    for raw, metadata in chain or []:
        legacy.verify_manifest(json.loads(raw))
        sha = legacy.sha(raw)
        publish(archive.root/'imports'/(sha+'.json'),raw)
        chain_refs.append({'sha256':sha,'bytes':len(raw),'transport_evidence':metadata})
    return archive.commit(run, {name:(Path(state)/name).read_bytes() for name in actual}, parent=parent,
        migration={'schema':legacy.SCHEMA, 'run_id':source_manifest['run_id'],
                   'manifest_sha256':source_manifest['manifest_sha256'],
                   'lineage_id':source_manifest['lineage_id'], 'original_bytes_preserved':True,
                   'chain_manifests':chain_refs,
                   'original_manifest':{'sha256':key,'bytes':len(original),
                                        'original_bytes_available':source_manifest_raw is not None}})


def import_remote_v11(archive, run, source_run, loader, parent=None, checkpoint=False):
    """One-time API migration; retain V1.1 success/expiry/chain checks unchanged.

    The caller supplies the existing authenticated loader; no credential creation
    or remote writes. All original chain manifests are preserved as import evidence.
    """
    cache = {}
    def cached(key):
        if key not in cache:
            cache[key] = loader(key)
        return cache[key]
    with tempfile.TemporaryDirectory(prefix='c-v11-migration-') as temp:
        manifest, verification = legacy.restore_chain(str(source_run), cached, temp,
                                                      allow_fixtures=archive.allow_fixtures,checkpoint=checkpoint)
        # Do not turn an expired/deleted remote lineage into an offline fresh start.
        import io
        import zipfile
        chain = []
        for key, (raw, metadata) in sorted(cache.items()):
            with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
                chain.append((zipped.read('manifest.json'),metadata))
        original = next(raw for raw, _ in chain if json.loads(raw)['run_id']==str(source_run))
        migrated = import_v11(archive, run, temp, manifest, original, parent,chain)
    return migrated, verification

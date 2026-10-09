"""Verified immutable incremental artifacts. No implicit lineage reconstruction."""
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from upbit_b.feature_contracts import digest, dumps
from .research_history import read_record, validate_signal

SCHEMA = 'upbit-c-research-segment-1'
WORKFLOW = '.github/workflows/upbit-c-market-research.yml'
CATEGORIES = ('scans', 'signals', 'evaluations')
MAX_BYTES = 5 * 1024 ** 3


def safe_path(name):
    if not isinstance(name, str) or not re.fullmatch(r'(scans|signals|evaluations)/[0-9a-f]{64}\.json', name):
        raise ValueError('unsafe research record path')
    return name


def sha(data):
    return hashlib.sha256(data).hexdigest()


def verify_record(name, data, allow_fixtures=False):
    safe_path(name)
    obj = json.loads(data)
    record = obj['record']
    if digest(record) != obj['record_sha256']:
        raise ValueError('record envelope hash mismatch')
    if record.get('test_fixture') and not allow_fixtures:
        raise ValueError('fixture prohibited in real research lineage')
    if name.startswith('signals/'):
        validate_signal(record)
        if name.split('/')[-1] != record['signal_id'] + '.json':
            raise ValueError('signal filename mismatch')
    if name.startswith('scans/'):
        if record.get('activation') != 'RESEARCH_ONLY':
            raise ValueError('nonresearch scan')
        if digest({k:v for k,v in record.items() if k != 'scan_id'}) != record['scan_id']:
            raise ValueError('scan identity mismatch')
        if name.split('/')[-1] != record['scan_id'] + '.json':
            raise ValueError('scan filename mismatch')
        from decimal import Decimal
        from upbit_b.contracts import Candle
        from upbit_b.features import _input_hash
        for row in record['results']:
            for tf, item in row['timeframes'].items():
                if 'candles' not in item:
                    continue
                cs = [Candle(**{k:Decimal(v) if k in ('open','high','low','close','base_volume','quote_trade_amount') else v for k,v in c.items()}) for c in item['candles']]
                if _input_hash(cs) != item['source_input_sha256']:
                    raise ValueError('stored source input hash mismatch')
                if any(c.provider != 'UPBIT' or c.instrument != row['market'] or c.timeframe != tf or not c.completed or c.close_ms > record['source_cutoff_ms'] for c in cs):
                    raise ValueError('invalid stored source identity/cutoff')
    return record


def inventory(root, allow_fixtures=False):
    result = {}
    for category in CATEGORIES:
        for path in sorted((Path(root) / category).glob('*.json')):
            name = safe_path(category + '/' + path.name)
            raw = path.read_bytes()
            verify_record(name, raw, allow_fixtures)
            result[name] = sha(raw)
    return result


def seal(manifest):
    return {**manifest, 'manifest_sha256':digest(manifest)}


def verify_manifest(manifest):
    if manifest.get('schema_version') != SCHEMA or manifest.get('activation') != 'RESEARCH_ONLY':
        raise ValueError('invalid segment schema')
    if digest({k:v for k,v in manifest.items() if k != 'manifest_sha256'}) != manifest.get('manifest_sha256'):
        raise ValueError('manifest hash mismatch')
    if not re.fullmatch(r'[1-9][0-9]*', str(manifest.get('run_id'))):
        raise ValueError('invalid source run ID')
    for field in ('inventory', 'delta_files'):
        if not isinstance(manifest.get(field), dict):
            raise ValueError('missing record inventory')
        for name, value in manifest[field].items():
            safe_path(name)
            if not re.fullmatch('[0-9a-f]{64}', value):
                raise ValueError('invalid file hash')
    if manifest.get('inventory_sha256') != digest(manifest['inventory']):
        raise ValueError('inventory hash mismatch')
    if any(manifest['inventory'].get(k) != v for k,v in manifest['delta_files'].items()):
        raise ValueError('delta/inventory mismatch')
    if manifest.get('checkpoint') and manifest.get('parent'):
        raise ValueError('checkpoint must be self-contained')
    if manifest.get('parent'):
        parent = manifest['parent']
        if (not re.fullmatch(r'[1-9][0-9]*', str(parent.get('run_id')))
                or int(parent['run_id']) >= int(manifest['run_id'])
                or not re.fullmatch('[0-9a-f]{64}', parent.get('manifest_sha256',''))):
            raise ValueError('invalid/cyclic parent reference')
    elif manifest['delta_files'] != manifest['inventory']:
        raise ValueError('root/checkpoint lacks records')
    return manifest


def export_segment(state, dest, run_id, parent=None, checkpoint=False, allow_fixtures=False, source_revision=None):
    current = inventory(state, allow_fixtures)
    previous = verify_manifest(parent)['inventory'] if parent else {}
    for name, value in previous.items():
        if current.get(name) != value:
            raise ValueError('previous record missing/overwritten: ' + name)
    delta = current if checkpoint else {k:v for k,v in current.items() if k not in previous}
    manifest = seal({'schema_version':SCHEMA, 'activation':'RESEARCH_ONLY',
        'run_id':str(run_id), 'checkpoint':bool(checkpoint), 'source_revision':source_revision,
        'lineage_id':parent['lineage_id'] if parent else digest({'root_run_id':str(run_id),'schema':SCHEMA}),
        'parent':({'run_id':parent['run_id'],'manifest_sha256':parent['manifest_sha256']} if parent and not checkpoint else None),
        'inventory':current, 'inventory_sha256':digest(current), 'delta_files':delta,
        'new_records':len(current)-len(previous), 'exported_records':len(delta),
        'exported_record_bytes':sum((Path(state)/k).stat().st_size for k in delta),
        'verification':'ALL_RECORD_ENVELOPES_AND_SOURCE_INPUT_HASHES_PASS'})
    verify_manifest(manifest)
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise ValueError('segment export destination must be empty')
    dest.mkdir(parents=True, exist_ok=True)
    for name in delta:
        target = dest / 'records' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(state)/name, target)
    (dest/'manifest.json').write_text(dumps(manifest)+'\n',encoding='utf-8')
    return manifest


def decode_archive(raw, allow_fixtures=False):
    """Never extract untrusted zip paths. Validate every byte before publication."""
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        entries = z.infolist()
        if len({i.filename for i in entries}) != len(entries):
            raise ValueError('duplicate archive entries')
        if len(entries) > 100000 or sum(i.file_size for i in entries) > MAX_BYTES:
            raise ValueError('archive resource limit')
        m = verify_manifest(json.loads(z.read('manifest.json')))
        expected = {'manifest.json'} | {'records/'+k for k in m['delta_files']}
        actual = {i.filename for i in entries if not i.is_dir()}
        if expected != actual:
            raise ValueError('missing/extra archive records')
        records = {}
        for name, value in m['delta_files'].items():
            data = z.read('records/'+name)
            if sha(data) != value:
                raise ValueError('archive file hash mismatch')
            verify_record(name,data,allow_fixtures)
            records[name] = data
        return m, records


def restore_chain(tip_run_id, loader, state, now=None, checkpoint=False, allow_fixtures=False):
    """loader(run_id) -> (zip bytes, metadata); missing ancestor is fatal."""
    now = now or datetime.now(timezone.utc)
    chain, seen, run_id = [], set(), str(tip_run_id)
    expiries = []
    while run_id:
        if run_id in seen or len(seen) >= 1000:
            raise ValueError('cyclic/oversized artifact chain')
        seen.add(run_id)
        raw, metadata = loader(run_id)
        if metadata.get('expired') or metadata.get('workflow_path') != WORKFLOW or metadata.get('conclusion') != 'success':
            raise ValueError('missing/expired/unsuccessful/unrelated parent artifact')
        if metadata.get('archive_sha256') and sha(raw) != metadata['archive_sha256']:
            raise ValueError('GitHub artifact digest mismatch')
        if not metadata.get('expires_at'):
            raise ValueError('parent expiry evidence unavailable')
        expiry = datetime.fromisoformat(metadata['expires_at'].replace('Z','+00:00'))
        if expiry <= now:
            raise ValueError('parent artifact expired')
        expiries.append(expiry)
        m, records = decode_archive(raw,allow_fixtures)
        if m['run_id'] != run_id:
            raise ValueError('artifact source run mismatch')
        if chain and chain[-1][0]['parent']['manifest_sha256'] != m['manifest_sha256']:
            raise ValueError('parent manifest reference mismatch')
        chain.append((m, records))
        run_id = m['parent']['run_id'] if m['parent'] else None
    earliest = min(expiries)
    if (earliest-now).total_seconds() < 14*86400 and not checkpoint:
        raise ValueError('RETENTION_GUARD: export/download a checkpoint before ancestor expiry')
    combined = {}
    for m, records in reversed(chain):
        prior = {k:sha(v) for k,v in combined.items()}
        if m['parent']:
            if any(k in prior for k in records):
                raise ValueError('delta reuploads existing record')
            if m['lineage_id'] != chain[-1][0]['lineage_id']:
                raise ValueError('lineage identity mismatch')
        combined.update(records)
        if sum(len(v) for v in combined.values()) > MAX_BYTES:
            raise ValueError('restored chain resource limit')
        if {k:sha(v) for k,v in combined.items()} != m['inventory']:
            raise ValueError('missing/changed previous record inventory')
    state=Path(state)
    # Stage only after all ancestors, hashes and expiration have passed validation.
    existing = inventory(state, allow_fixtures)
    if any(k not in combined or sha(combined[k]) != value for k,value in existing.items()):
        raise ValueError('restore conflicts with existing state')
    for name, data in combined.items():
        dest=state/name
        if name in existing:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        temp=None
        try:
            with tempfile.NamedTemporaryFile(dir=dest.parent,delete=False) as f:
                temp=f.name; f.write(data); f.flush(); os.fsync(f.fileno())
            os.link(temp,dest)
        finally:
            if temp:os.unlink(temp)
    return chain[0][0], {'verified_segments':len(chain),'restored_records':len(combined),
        'earliest_ancestor_expiry':earliest.isoformat(),'checkpoint_required_within_14_days':False}

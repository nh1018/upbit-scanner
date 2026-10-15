"""Isolated CI: reconstruct public evidence from Git, offline replay, optional GET.

No publisher, workflow dispatch, exchange access, order transport, or Git writes.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request

from btc_anytime import analysis_snapshot as S, snapshot_poc01 as P
from btc_anytime.integrity import utc_ms
from btc_anytime.tests.replay_snapshot_poc02 import replay

BRANCH = 'test/btc-snapshot-poc02-isolated-20261015'
REPO = 'nh1018/upbit-scanner'


def git_bytes(sha, path):
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('invalid revision')
    return subprocess.check_output(['git', 'show', sha + ':' + path])


def verify(manifest):
    with tempfile.TemporaryDirectory(prefix='btc-poc02-ci-') as td:
        root = Path(td)
        rows = []
        try:
            for row in manifest['rows']:
                raw = git_bytes(row['commit'], S.TARGET)
                if hashlib.sha256(raw).hexdigest() != row['source_bytes_sha256']:
                    raise ValueError('Snapshot original bytes mismatch')
                s = json.loads(raw); S.validate(s)
                rows.append({**row, 'snapshot': s})
            for path, expected in manifest['files'].items():
                if (not path.startswith(('output_direction/btc_anytime/v1/decisions/',
                                         'output_entry/btc_anytime/v1/records/',
                                         'output_entry/btc_anytime/v1/inputs/'))
                        or '..' in Path(path).parts):
                    raise ValueError('unexpected evidence namespace')
                raw = git_bytes(manifest['source_pin'], path)
                if hashlib.sha256(raw).hexdigest() != expected:
                    raise ValueError('source bytes mismatch: ' + path)
                p = root/'source'/path; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(raw)
        except subprocess.CalledProcessError:
            return {'status': 'NOT_EXECUTED_MISSING_GIT_EVIDENCE'}
        archive = root/'archive.json'; archive.write_text(json.dumps(
            {'source_pin': manifest['source_pin'], 'rows': rows}), encoding='utf-8')
        index = root/'manifest.json'; index.write_text(json.dumps(manifest), encoding='utf-8')
        result = replay(archive, root/'source', index)
        if result['boundary_count'] != 12 or result['snapshot_versions'] != 40 or result['in_memory_publications'] != 12:
            raise ValueError('fixed cohort replay mismatch')
        return {'status': 'PASS', **result}


def github_get(path):
    url = 'https://api.github.com/repos/' + REPO + '/' + path
    headers = {'User-Agent': 'btc-poc02-isolated-readonly', 'Accept': 'application/vnd.github+json'}
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(url, headers=headers, method='GET')
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def live_read():
    if os.environ.get('GITHUB_REF') != 'refs/heads/' + BRANCH or os.environ.get('GITHUB_REPOSITORY') != REPO:
        raise ValueError('isolated test branch required')
    branch = github_get('git/ref/heads/' + BRANCH)['object']['sha']
    if branch != os.environ.get('GITHUB_SHA'):
        raise ValueError('test branch advanced; validate latest run instead')
    main = github_get('git/ref/heads/main')['object']['sha']
    # Pin the read: never substitute a later main response in the same observation.
    f = github_get('contents/' + S.TARGET + '?ref=' + main)
    if f.get('encoding') != 'base64':
        raise ValueError('unsupported Snapshot response encoding')
    raw = base64.b64decode(f['content'], validate=False)
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if blob != f['sha']:
        raise ValueError('Git blob mismatch')
    s = json.loads(raw); S.validate(s)
    result = P.assess(s, now_ms=S.now_ms())
    # BLOCKED/stale is a legitimate observation, not grounds for inventing data.
    return {'test_branch_sha': branch, 'read_only_main_revision': main,
            'snapshot_sha256': hashlib.sha256(raw).hexdigest(),
            'snapshot_generated_at_utc': s['generated_at_utc'],
            'projection_action': result['action'], 'reasons': result['reasons'],
            'methods': ['GET'], 'production_writes': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', default='btc_anytime/tests/fixtures/poc02_replay_manifest.json')
    p.add_argument('--live-github-read', action='store_true')
    p.add_argument('--output')
    a = p.parse_args()
    result = {'replay': verify(json.loads(Path(a.manifest).read_text(encoding='utf-8')))}
    if a.live_github_read:
        result['github_get'] = live_read()
    if a.output:
        Path(a.output).write_text(json.dumps(result, sort_keys=True, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

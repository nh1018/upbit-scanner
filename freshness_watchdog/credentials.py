"""Read an existing Git credential without interactive login or token creation."""
import os
import subprocess


def existing_git_token():
    result = subprocess.run(
        ['git', '-c', 'credential.interactive=never', 'credential', 'fill'],
        input='protocol=https\nhost=github.com\n\n', text=True,
        capture_output=True, timeout=20,
        env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never'})
    fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    token = fields.get('password') if result.returncode == 0 else None
    if not token:
        raise ValueError('existing Git credential unavailable')
    return token

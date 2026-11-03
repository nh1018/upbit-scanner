"""Publish the already committed A output; never run or alter the scanner.

Bounded fast-forward retries only. A rebase conflict fails immediately.
"""
import subprocess
import time
from pathlib import Path


def git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=repo, check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def publish(repo, *, sleep=time.sleep, before_push=None):
    repo = Path(repo)
    if git(repo, "status", "--porcelain").stdout.strip():
        raise ValueError("publication requires a clean committed checkout")
    for attempt in range(1, 4):
        # The remote may have advanced while scanning or during backoff.
        git(repo, "fetch", "origin", "main")
        git(repo, "rebase", "origin/main")
        if before_push is not None:
            before_push(attempt)
        pushed = git(repo, "push", "origin", "HEAD:main", check=False)
        if pushed.returncode == 0:
            return {"status": "PUSHED", "attempt": attempt}
        if attempt < 3:
            sleep(5 * attempt)
    raise RuntimeError("A output publication failed after 3 attempts")


if __name__ == "__main__":
    import json
    try:
        print(json.dumps(publish(Path.cwd())))
    except (subprocess.SubprocessError, ValueError, RuntimeError):
        # Transport output may contain credentials; never print it.
        raise SystemExit("A publication failed; no force push or scanner retry performed")

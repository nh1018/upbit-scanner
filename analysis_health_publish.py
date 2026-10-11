"""Publish Health from current remote main with bounded compare-and-swap retries.

No rebase of an already calculated result. No force push or engine replay.
The caller's checkout and all production inputs remain unchanged.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

import analysis_health as H

TARGET = "output_system_health/latest.json"


def git(repo, *args, check=True):
    return subprocess.run(["git", "-c", "safe.directory=" + str(Path(repo).resolve()),
                           "-c", "core.autocrlf=false", "-C", str(repo), *args],
                          check=check, capture_output=True, text=True)


def publish(repo, attempts=3, *, builder=H.build, before_push=None):
    repo = Path(repo).resolve()
    if not 1 <= attempts <= 3:
        raise ValueError("bounded publication attempts required")
    for attempt in range(1, attempts + 1):
        git(repo, "fetch", "origin", "main")
        revision = git(repo, "rev-parse", "refs/remotes/origin/main").stdout.strip()
        with tempfile.TemporaryDirectory(prefix="analysis-health-publication-") as tmp:
            checkout = Path(tmp) / "checkout"
            git(repo, "worktree", "add", "--detach", str(checkout), revision)
            try:
                # A queued old publisher must not publish with replaced gate code.
                for name in ("analysis_health.py", "analysis_health_publish.py"):
                    local = Path(__file__).with_name(name)
                    if (checkout / name).read_bytes() != local.read_bytes():
                        raise ValueError("Health execution code differs from current main")
                value = H.validate(builder(checkout))
                target = checkout / TARGET
                if target.exists():
                    previous = H.validate(json.loads(target.read_text(encoding="utf-8")))
                    if H._parse_iso_ms(value["generated_at_utc"]) <= H._parse_iso_ms(previous["generated_at_utc"]):
                        return {"status": "NOOP_NEWER_HEALTH", "source_revision": revision, "attempt": attempt}
                value["input_repository_commit"] = revision
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                                  encoding="utf-8", newline="\n")
                git(checkout, "add", "--", TARGET)
                if git(checkout, "diff", "--cached", "--name-only").stdout.splitlines() != [TARGET]:
                    raise ValueError("Unexpected Health publication files")
                git(checkout, "-c", "user.name=github-actions[bot]", "-c",
                    "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                    "commit", "-m", "Update analysis input health")
                if before_push is not None:
                    before_push(attempt, revision)
                result = git(checkout, "push", "origin", "HEAD:refs/heads/main", check=False)
                if result.returncode == 0:
                    return {"status": "PUSHED", "source_revision": revision, "attempt": attempt,
                            "publication_commit": git(checkout, "rev-parse", "HEAD").stdout.strip(),
                            "generated_at_utc": value["generated_at_utc"]}
                # Whether rejected or an uncertain response, fetch/recompute from
                # the actual remote state. Never replay the old derived commit.
            finally:
                git(repo, "worktree", "remove", "--force", str(checkout))
    raise RuntimeError("Health publication failed after bounded fresh-state retries")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path.cwd())
    args = p.parse_args()
    print(json.dumps(publish(args.repo), sort_keys=True))


if __name__ == "__main__":
    main()

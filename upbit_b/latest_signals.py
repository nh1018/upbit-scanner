"""Publish a reproducible read-only view of the newest validated B journal.

No market API, no scan replay, no journal mutation. The publisher runs after B
history publication and never substitutes an older journal for a newer bad one.
"""
import argparse
import json
from pathlib import Path

from . import feature_contracts as F
from .signal_summary import summarize


def latest_summary(repo):
    base = Path(repo) / "output_upbit_b" / "v1" / "history"
    paths = sorted(base.glob("????-??-??/??.jsonl"))
    if not paths:
        raise FileNotFoundError("NO_B_HISTORY")
    source = paths[-1]
    result = summarize(source.read_bytes())
    result["source_path"] = source.relative_to(repo).as_posix()
    result["source_journal_sha256"] = __import__("hashlib").sha256(source.read_bytes()).hexdigest()
    return result


def publish(repo):
    repo = Path(repo).resolve()
    summary = latest_summary(repo)
    target = repo / "output_upbit_b" / "v1" / "latest_signals.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = F.dumps(summary) + "\n"
    if target.exists() and target.read_text(encoding="utf-8") == encoded:
        return {"status": "UNCHANGED", "path": target.relative_to(repo).as_posix()}
    target.write_text(encoded, encoding="utf-8")
    return {"status": "UPDATED", "path": target.relative_to(repo).as_posix()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    print(json.dumps(publish(args.repo), sort_keys=True))


if __name__ == "__main__":
    main()

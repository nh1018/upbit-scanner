"""Print read-only recovery plans. There is intentionally no --dispatch flag."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from .core import ms, plan
from .reader import GitHubReadOnly, ObservationError, observe
from .credentials import existing_git_token
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--evidence", type=Path, help="offline evidence JSON; no network")
    p.add_argument("--now", help="offline replay UTC only")
    p.add_argument("--token-env", default="FRESHNESS_WATCHDOG_READ_TOKEN")
    p.add_argument("--git-credential", action="store_true", help="use existing Git credential; no interactive login")
    p.add_argument("--include-evidence", action="store_true", help="print GET receipts and evidence; never credentials")
    args = p.parse_args()
    if args.now and not args.evidence:
        p.error("--now requires offline evidence")
    try:
        e = json.loads(args.evidence.read_text()) if args.evidence else observe(
            GitHubReadOnly(existing_git_token() if args.git_credential else os.environ.get(args.token_env)))
        now = ms(args.now) if args.now else ms(datetime.now(timezone.utc).isoformat())
        result = plan(e, now)
        result["actual_dispatch_count"] = 0
        if args.include_evidence:
            result["evidence"] = e
        print(json.dumps(result, sort_keys=True))
        return 2 if result["action"] == "BLOCKED" else 0
    except (ObservationError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({"mode": "READ_ONLY", "action": "BLOCKED", "reason": "OBSERVATION_FAILED",
                          "detail": str(error) if isinstance(error, ObservationError) else "EVIDENCE_OR_CREDENTIAL_UNAVAILABLE",
                          "actual_dispatch_count": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Print read-only recovery plans. There is intentionally no --dispatch flag."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from .core import ms, plan
from .reader import GitHubReadOnly, ObservationError, observe


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--evidence", type=Path, help="offline evidence JSON; no network")
    p.add_argument("--now", help="offline replay UTC only")
    p.add_argument("--token-env", default="FRESHNESS_WATCHDOG_READ_TOKEN")
    args = p.parse_args()
    if args.now and not args.evidence:
        p.error("--now requires offline evidence")
    try:
        e = json.loads(args.evidence.read_text()) if args.evidence else observe(
            GitHubReadOnly(os.environ.get(args.token_env)))
        now = ms(args.now) if args.now else ms(datetime.now(timezone.utc).isoformat())
        result = plan(e, now)
        result["actual_dispatch_count"] = 0
        print(json.dumps(result, sort_keys=True))
        return 2 if result["action"] == "BLOCKED" else 0
    except (ObservationError, OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"mode": "READ_ONLY", "action": "BLOCKED", "reason": "OBSERVATION_FAILED",
                          "actual_dispatch_count": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Deterministic A/B recovery planning; never dispatch or change production."""
import hashlib
import json
import re
from datetime import datetime, timezone

HOUR = 3_600_000
FRESHNESS_MS = 120 * 60_000
ACTIVE = {"queued", "in_progress", "waiting", "requested", "pending"}
TARGETS = {
    "A": {"workflow": "upbit-scanner.yml", "id": 368188656,
          "publish_step": "Commit scan results"},
    "B": {"workflow": "upbit-b-history.yml", "id": 375462203,
          "publish_step": "Verify published cycle on origin/main"},
}


def ms(value):
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.utcoffset() is None:
        raise ValueError("timezone required")
    return int(d.timestamp() * 1000)


def iso(value):
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def source(strategy, payload, path, now):
    """Validate published bytes, reusing B's exact existing immutable contract."""
    if not isinstance(payload, bytes) or not payload or strategy not in TARGETS:
        raise ValueError("missing publication bytes or invalid strategy")
    if strategy == "A":
        def unique(pairs):
            d = {}
            for k, v in pairs:
                if k in d:
                    raise ValueError("duplicate JSON field")
                d[k] = v
            return d
        o = json.loads(payload, object_pairs_hook=unique)
        if (path != "output/latest_scan.json" or o.get("scanner_version") != "1.3-cloud"
                or type(o.get("scanned_count")) is not int or o["scanned_count"] <= 0
                or type(o.get("candidate_count")) is not int or o["candidate_count"] < 0
                or o["candidate_count"] > o["scanned_count"]):
            raise ValueError("invalid A publication")
        cutoff = ms(o["generated_at_kst"])
        complete = True
    else:
        from upbit_b.history import validate_cycle
        from upbit_b.history_compact import unpack
        from upbit_b.history_contracts import cycle_path
        header = json.loads(payload.splitlines()[0])
        logical = unpack(payload) if header.get("schema_version") == "upbit-b-history-compact-1" else payload
        m, _ = validate_cycle(logical)
        cutoff = m["source_cutoff"]
        if path != cycle_path(cutoff) or ms(m["completed_at"]) > now:
            raise ValueError("invalid B publication boundary/clock")
        complete = m["completeness"] == "COMPLETE"
    if cutoff > now:
        raise ValueError("future source")
    return {"path": path, "cutoff_ms": cutoff, "complete": complete,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "freshness_status": "CURRENT" if now - cutoff <= FRESHNESS_MS else "STALE"}


def plan(evidence, now):
    """Evidence is a single, bounded read session; incomplete queries fail closed.

    Proposed policy, not activated: A recover at age>=90m; B missing current
    journal at minute>=35. Health remains120m. At most one A/B proposal per tick.
    """
    blocked = lambda reason: {"mode": "READ_ONLY", "action": "BLOCKED", "reason": reason}
    try:
        if evidence["repository"] != "nh1018/upbit-scanner" or evidence["default_branch"] != "main":
            raise ValueError("repository/default branch mismatch")
        if not re.fullmatch("[a-f0-9]{40}", evidence["revision"]):
            raise ValueError("invalid revision")
        if evidence["queries_complete"] is not True or not 0 <= now - evidence["observed_at_ms"] <= 90_000:
            raise ValueError("incomplete or expired evidence")
        if not 0 <= evidence["observed_at_ms"] - evidence["started_at_ms"] <= 120_000:
            raise ValueError("observation clock/duration invalid")
        for strategy, target in TARGETS.items():
            w = evidence["workflows"][strategy]
            if w["state"] != "active" or w["id"] != target["id"] or w["config_verified"] is not True:
                raise ValueError("workflow inactive or configuration changed")
        for r in evidence["active_runs"]:
            if r["workflow_id"] in {x["id"] for x in TARGETS.values()} and r["status"] != "completed":
                return blocked("A_OR_B_RUN_ACTIVE")
        for r in evidence["latest_runs"].values():
            if r["status"] not in ACTIVE | {"completed"}:
                raise ValueError("unknown latest execution state")
            if r["status"] in ACTIVE:
                return blocked("A_OR_B_RUN_ACTIVE")
        proposals = []
        states = {}
        for strategy, target in TARGETS.items():
            s = evidence["sources"][strategy]
            if s.get("error"):
                states[strategy] = "SOURCE_INVALID_OR_MISSING"
                continue
            if (not re.fullmatch("[a-f0-9]{64}", s["sha256"]) or s["cutoff_ms"] > now
                    or type(s["complete"]) is not bool):
                raise ValueError("source evidence invalid")
            states[strategy] = "CURRENT" if now - s["cutoff_ms"] <= FRESHNESS_MS else "STALE"
            latest = evidence["latest_runs"][strategy]
            if (latest["status"] != "completed" or latest["conclusion"] != "success"
                    or latest["publication_verified"] is not True):
                states[strategy] += ":LATEST_RUN_NOT_VERIFIED"
                continue
            if not s["complete"]:
                states[strategy] += ":PARTIAL_PUBLICATION"
                continue
            boundary = now // HOUR * HOUR
            if strategy == "A":
                due = now - s["cutoff_ms"] >= 90 * 60_000
            else:
                current = evidence["b_current"]
                from upbit_b.history_contracts import cycle_path
                if current["path"] != cycle_path(boundary) or current["revision"] != evidence["revision"]:
                    raise ValueError("current journal proof mismatch")
                if current.get("invalid"):
                    states[strategy] += ":CURRENT_JOURNAL_INVALID"
                    continue
                if type(current["exists"]) is not bool:
                    raise ValueError("current journal proof invalid")
                due = not current["exists"] and now - boundary >= 35 * 60_000
            if due:
                proposals.append({"strategy": strategy, "workflow": target["workflow"], "ref": "main",
                                  "boundary_ms": boundary, "key": strategy + ":" + str(boundary),
                                  "reason": "SOURCE_AGE_90M" if strategy == "A" else "NO_CURRENT_JOURNAL",
                                  "source_sha256": s["sha256"], "revision": evidence["revision"]})
        # B coverage expires with the live window; deterministic priority and
        # cross-target serialization avoid deliberately creating A/B writers together.
        proposals.sort(key=lambda p: (p["strategy"] != "B", p["key"]))
        result = {"mode": "READ_ONLY", "action": "WOULD_DISPATCH" if proposals else "NO_ACTION",
                  "states": states, "proposal": proposals[0] if proposals else None,
                  "deferred": [p["strategy"] for p in proposals[1:]]}
        result["plan_sha256"] = digest(result)
        return result
    except (KeyError, ValueError, TypeError, OverflowError):
        return blocked("EVIDENCE_UNVERIFIED")

"""GET-only GitHub evidence observer. No dispatch, mutations or market API calls."""
import base64
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

from .core import ACTIVE, TARGETS, HOUR, iso, ms, source


class ObservationError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ObservationError("redirect rejected")


class GitHubReadOnly:
    def __init__(self, token=None, opener=None):
        self.token = token
        self.open = opener or urllib.request.build_opener(NoRedirect()).open
        self.requests = []

    def get(self, path, missing=False):
        if len(self.requests) >= 40:
            raise ObservationError("read budget exceeded")
        if not path.startswith("/") or ".." in path or path.startswith("//"):
            raise ObservationError("invalid repository API path")
        headers = {"Accept": "application/vnd.github+json", "Cache-Control": "no-cache",
                   "User-Agent": "upbit-freshness-watchdog-read-only"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        url = "https://api.github.com/repos/nh1018/upbit-scanner" + ("" if path == "/" else path)
        url += ("&" if "?" in path else "?") + "watchdog_nonce=" + str(time.time_ns())
        req = urllib.request.Request(url, headers=headers, method="GET")
        # Record the path only; never retain request headers or credentials.
        receipt = {"path": path, "method": "GET"}
        self.requests.append(receipt)
        try:
            with self.open(req, timeout=15) as response:
                raw = response.read(4_000_001)
                if len(raw) > 4_000_000:
                    raise ObservationError("response size limit")
                receipt.update(status=response.status, sha256=hashlib.sha256(raw).hexdigest(),
                               received_at_utc=datetime.now(timezone.utc).isoformat())
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            receipt["status"] = error.code
            if error.code == 404 and missing:
                return None
            raise ObservationError("GitHub read rejected: HTTP " + str(error.code)) from None
        except (OSError, ValueError) as error:
            # Do not leak exception text that could contain transport headers.
            raise ObservationError("GitHub read unavailable or invalid") from None


def pages(api, path, key):
    result = []
    for page in range(1, 11):
        value = api.get(path + ("&" if "?" in path else "?") + "per_page=100&page=" + str(page))
        rows = value[key]
        if not isinstance(rows, list):
            raise ObservationError("invalid pagination")
        result.extend(rows)
        if len(rows) < 100:
            return result
    raise ObservationError("pagination incomplete")


def observe(api, now=None):
    now = now or datetime.now(timezone.utc)
    start = ms(now.isoformat())
    monotonic_start = time.monotonic()
    repo = api.get("/")
    if repo["default_branch"] != "main" or repo.get("archived") or repo.get("disabled"):
        raise ObservationError("repository unavailable/default branch changed")
    revision = api.get("/branches/main")["commit"]["sha"]
    pins = json.loads(Path(__file__).with_name("workflow_pins.json").read_text())

    def contents(path, missing=False):
        return api.get("/contents/" + urllib.parse.quote(path, safe="/") + "?ref=" + revision, missing)

    def raw_file(path, missing=False):
        obj = contents(path, missing)
        if obj is None:
            return None
        if obj["type"] != "file" or obj["encoding"] != "base64":
            raise ObservationError("file unavailable or oversized")
        raw = base64.b64decode(obj["content"], validate=False)
        git_hash = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if git_hash != obj["sha"]:
            raise ObservationError("Git blob mismatch")
        return raw

    workflows = {}
    latest = {}
    for strategy, target in TARGETS.items():
        w = api.get("/actions/workflows/" + str(target["id"]))
        expected_path = ".github/workflows/" + target["workflow"]
        config = raw_file(expected_path)
        workflows[strategy] = {"state": w["state"], "id": w["id"],
                               "config_verified": w["path"] == expected_path and
                               hashlib.sha256(config).hexdigest() == pins[target["workflow"]]}
        rows = api.get("/actions/workflows/" + str(target["id"]) + "/runs?branch=main&per_page=1")["workflow_runs"]
        if not rows:
            latest[strategy] = {"status": "missing", "conclusion": None, "publication_verified": False}
            continue
        run = rows[0]
        if run["workflow_id"] != target["id"] or ms(run["created_at"]) > start:
            raise ObservationError("unexpected run/clock")
        jobs = pages(api, "/actions/runs/" + str(run["id"]) + "/jobs", "jobs")
        verified = any(step["name"] == target["publish_step"] and step["conclusion"] == "success"
                       for job in jobs for step in (job.get("steps") or []))
        latest[strategy] = {"id": run["id"], "status": run["status"],
                            "conclusion": run["conclusion"], "publication_verified": verified}
    # No created-time filter: an old queued run must also suppress recovery.
    active = []
    for status in sorted(ACTIVE):
        active.extend(pages(api, "/actions/runs?status=" + status, "workflow_runs"))
    sources = {}
    a = raw_file("output/latest_scan.json", missing=True)
    try:
        sources["A"] = source("A", a, "output/latest_scan.json", start)
    except (ValueError, TypeError, KeyError):
        sources["A"] = {"error": "INVALID_OR_MISSING_A"}
    from upbit_b.history_contracts import cycle_path
    current_path = cycle_path(start // HOUR * HOUR)
    current_bytes = raw_file(current_path, missing=True)
    b_current = {"path": current_path, "revision": revision, "exists": current_bytes is not None}
    paths = []
    for day in (now.date(), (now - timedelta(days=1)).date()):
        directory = "output_upbit_b/v1/history/" + day.isoformat()
        entries = contents(directory, missing=True) or []
        if not isinstance(entries, list):
            raise ObservationError("invalid journal directory")
        paths.extend(x["path"] for x in entries if x["type"] == "file" and x["name"].endswith(".jsonl"))
    try:
        path = max(paths)
        sources["B"] = source("B", current_bytes if path == current_path else raw_file(path), path, start)
    except (ValueError, KeyError, TypeError, IndexError):
        sources["B"] = {"error": "INVALID_OR_MISSING_B"}
        b_current["invalid"] = current_bytes is not None
    end = ms(datetime.now(timezone.utc).isoformat()) if now.tzinfo else start
    # An inspection that crossed an hour cannot propose a past B boundary.
    if (end // HOUR != start // HOUR or end < start or
            abs((end - start) / 1000 - (time.monotonic() - monotonic_start)) > 2):
        raise ObservationError("observation crossed boundary or clock changed")
    if api.get("/branches/main")["commit"]["sha"] != revision:
        raise ObservationError("main changed during inspection; inspect again next tick")
    return {"repository": "nh1018/upbit-scanner", "default_branch": "main", "revision": revision,
            "started_at_ms": start, "observed_at_ms": end, "queries_complete": True,
            "workflows": workflows, "latest_runs": latest, "active_runs": active,
            "sources": sources, "b_current": b_current, "read_receipts": api.requests}

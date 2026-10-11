import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import analysis_health as H
import analysis_health_publish as P


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bare = self.root / "remote.git"
        self.repo = self.root / "writer"
        self.bare.mkdir(); self.repo.mkdir()
        P.git(self.bare, "init", "--bare", "--initial-branch=main")
        P.git(self.repo, "init", "--initial-branch=main")
        P.git(self.repo, "config", "user.name", "fixture")
        P.git(self.repo, "config", "user.email", "fixture@example.invalid")
        for name in ("analysis_health.py", "analysis_health_publish.py"):
            shutil.copyfile(Path(P.__file__).with_name(name), self.repo / name)
        (self.repo / "state.json").write_text('{"status":"CURRENT"}')
        (self.repo / "raw.txt").write_bytes(b"immutable raw\n")
        P.git(self.repo, "add", ".")
        P.git(self.repo, "commit", "-m", "fixture")
        P.git(self.repo, "remote", "add", "origin", str(self.bare))
        P.git(self.repo, "push", "-u", "origin", "main")
        self.calls = []

    def builder(self, checkout):
        status = json.loads((checkout / "state.json").read_text())["status"]
        self.calls.append(status)
        return {"schema_version": H.SCHEMA, "generated_at_utc": "2026-10-11T01:00:0%dZ" % len(self.calls),
                "overall_status": "READY" if status == "CURRENT" else "BLOCKED",
                "systems": {k: {"status": status, "usable_for_current_analysis": status == "CURRENT"}
                            for k in ("btc", "upbit_a", "upbit_b")}}

    def remote_health(self):
        return json.loads(P.git(self.bare, "show", "main:" + P.TARGET).stdout)

    def change(self, status):
        P.git(self.repo, "pull", "--ff-only", "origin", "main")
        (self.repo / "state.json").write_text(json.dumps({"status": status}))
        P.git(self.repo, "add", "state.json"); P.git(self.repo, "commit", "-m", status)
        P.git(self.repo, "push", "origin", "main")

    def test_normal_degraded_current_recovery_and_only_health_written(self):
        for status in ("CURRENT", "DEGRADED", "CURRENT"):
            if self.calls: self.change(status)
            result = P.publish(self.repo, builder=self.builder)
            self.assertEqual(result["status"], "PUSHED")
            self.assertEqual(self.remote_health()["systems"]["btc"]["status"], status)
            self.assertEqual(P.git(self.bare, "diff", "main^", "main", "--name-only").stdout.strip(), P.TARGET)
            self.assertEqual(P.git(self.bare, "show", "main:raw.txt").stdout, "immutable raw\n")

    def test_remote_moves_then_old_calculation_is_discarded(self):
        def race(attempt, revision):
            if attempt == 1: self.change("DEGRADED")
        result = P.publish(self.repo, builder=self.builder, before_push=race)
        self.assertEqual(result["attempt"], 2)
        self.assertEqual(self.calls, ["CURRENT", "DEGRADED"])
        self.assertEqual(self.remote_health()["systems"]["btc"]["status"], "DEGRADED")

    def test_old_event_rebuilds_latest_recovered_snapshot(self):
        self.change("DEGRADED")
        P.publish(self.repo, builder=self.builder)
        self.change("CURRENT")
        # Even a caller whose local checkout still predates the remote update
        # fetches the current source, never its event's old source revision.
        P.publish(self.repo, builder=self.builder)
        self.assertEqual(self.remote_health()["systems"]["btc"]["status"], "CURRENT")

    def test_newer_health_cannot_be_overwritten_by_older_clock(self):
        P.publish(self.repo, builder=self.builder)
        previous = self.remote_health()
        self.calls.clear()
        self.assertEqual(P.publish(self.repo, builder=self.builder)["status"], "NOOP_NEWER_HEALTH")
        self.assertEqual(self.remote_health(), previous)

    def test_builder_failure_keeps_remote_and_caller_unchanged(self):
        before = P.git(self.bare, "rev-parse", "main").stdout
        with self.assertRaises(ValueError):
            P.publish(self.repo, builder=lambda _: (_ for _ in ()).throw(ValueError("invalid source")))
        self.assertEqual(P.git(self.bare, "rev-parse", "main").stdout, before)
        self.assertEqual(P.git(self.repo, "status", "--porcelain").stdout, "")
        self.assertEqual(len(P.git(self.repo, "worktree", "list", "--porcelain").stdout.split("worktree ")) - 1, 1)

    def test_changed_execution_code_is_rejected(self):
        (self.repo / "analysis_health.py").write_bytes(b"different gate\n")
        P.git(self.repo, "add", "analysis_health.py"); P.git(self.repo, "commit", "-m", "different code")
        P.git(self.repo, "push", "origin", "main")
        with self.assertRaisesRegex(ValueError, "execution code"):
            P.publish(self.repo, builder=self.builder)

    def test_persistent_push_failure_is_bounded_and_does_not_publish(self):
        original = P.git
        def reject(repo, *args, **kwargs):
            if args and args[0] == "push":
                import subprocess
                return subprocess.CompletedProcess([], 1, "", "fixture push failure")
            return original(repo, *args, **kwargs)
        before = P.git(self.bare, "rev-parse", "main").stdout
        with patch.object(P, "git", side_effect=reject), self.assertRaises(RuntimeError):
            P.publish(self.repo, builder=self.builder)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(P.git(self.bare, "rev-parse", "main").stdout, before)

    def test_workflow_refreshes_in_snapshot_job_without_new_chain(self):
        root = Path(__file__).parents[1]
        snapshot = (root / ".github/workflows/btc-analysis-snapshot.yml").read_text()
        self.assertLess(snapshot.index("Publish only the complete latest"), snapshot.index("Refresh Health after"))
        self.assertIn("python -B -m analysis_health_publish --repo .", snapshot)
        health = (root / ".github/workflows/analysis-health.yml").read_text()
        self.assertNotIn("git rebase", health)
        self.assertIn("cancel-in-progress: false", health)
        self.assertIn("cron: '10,25,40,55 * * * *'", health)

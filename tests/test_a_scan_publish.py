import subprocess
import tempfile
import unittest
from pathlib import Path

import a_scan_publish as P


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote = self.root / "remote.git"
        self.writer = self.root / "writer"
        self.other = self.root / "other"
        self.remote.mkdir()
        P.git(self.remote, "init", "--bare", "--initial-branch=main")
        self.writer.mkdir()
        P.git(self.writer, "init", "--initial-branch=main")
        P.git(self.writer, "remote", "add", "origin", str(self.remote))
        self.configure(self.writer)
        self.commit(self.writer, "output.json", b"baseline\n")
        P.git(self.writer, "push", "-u", "origin", "main")
        P.git(self.root, "clone", str(self.remote), str(self.other))
        self.configure(self.other)
        self.commit(self.writer, "output.json", b"new scanner bytes\n")
        self.waits = []

    def configure(self, repo):
        P.git(repo, "config", "user.name", "fixture")
        P.git(repo, "config", "user.email", "fixture@example.invalid")

    def commit(self, repo, name, payload):
        (repo / name).write_bytes(payload)
        P.git(repo, "add", name)
        P.git(repo, "commit", "-m", "fixture")

    def advance(self, attempt):
        self.commit(self.other, "unrelated.txt", str(attempt).encode())
        P.git(self.other, "push", "origin", "main")

    def test_latest_remote_is_integrated_before_first_push(self):
        self.advance(1)
        self.assertEqual(P.publish(self.writer, sleep=self.waits.append)["attempt"], 1)
        self.assertEqual(P.git(self.remote, "show", "main:output.json").stdout,
                         b"new scanner bytes\n")
        self.assertEqual(P.git(self.remote, "show", "main:unrelated.txt").stdout, b"1")
        self.assertEqual(self.waits, [])

    def test_two_publication_races_then_success_preserve_exact_output(self):
        result = P.publish(self.writer, sleep=self.waits.append,
                           before_push=lambda n: self.advance(n) if n < 3 else None)
        self.assertEqual(result, {"status": "PUSHED", "attempt": 3})
        self.assertEqual(self.waits, [5, 10])
        self.assertEqual(P.git(self.remote, "show", "main:output.json").stdout,
                         b"new scanner bytes\n")

    def test_continuous_race_fails_without_overwriting_remote(self):
        with self.assertRaises(RuntimeError):
            P.publish(self.writer, sleep=self.waits.append, before_push=self.advance)
        self.assertEqual(self.waits, [5, 10])
        self.assertEqual(P.git(self.remote, "show", "main:output.json").stdout, b"baseline\n")

    def test_conflicting_output_fails_closed(self):
        self.commit(self.other, "output.json", b"different publication\n")
        P.git(self.other, "push", "origin", "main")
        with self.assertRaises(subprocess.CalledProcessError):
            P.publish(self.writer, sleep=self.waits.append)
        self.assertEqual(self.waits, [])
        self.assertEqual(P.git(self.remote, "show", "main:output.json").stdout,
                         b"different publication\n")

    def test_uncommitted_output_rejected(self):
        (self.writer / "output.json").write_bytes(b"dirty")
        with self.assertRaises(ValueError):
            P.publish(self.writer, sleep=self.waits.append)

    def test_workflow_uses_publisher_without_changing_schedule(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/upbit-scanner.yml").read_text()
        self.assertIn('cron: "37 * * * *"', workflow)
        self.assertIn('python -B -m a_scan_publish', workflow)
        self.assertNotIn('git push --force', workflow)

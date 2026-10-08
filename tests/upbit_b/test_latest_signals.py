"""Latest B summary must not fall back to stale cycles or fabricate signals."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from upbit_b.latest_signals import latest_summary, publish


class LatestSignalsTests(unittest.TestCase):
    def test_latest_only_and_source_digest(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root) / "output_upbit_b/v1/history/2026-10-08"
            base.mkdir(parents=True)
            (base / "01.jsonl").write_bytes(b"older\n")
            (base / "02.jsonl").write_bytes(b"newer\n")
            with patch("upbit_b.latest_signals.summarize", return_value={"cycle_id": "new"}) as mock:
                result = latest_summary(Path(root))
            mock.assert_called_once_with(b"newer\n")
            self.assertEqual(result["source_path"], "output_upbit_b/v1/history/2026-10-08/02.jsonl")
            self.assertEqual(len(result["source_journal_sha256"]), 64)

    def test_newest_invalid_does_not_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root) / "output_upbit_b/v1/history/2026-10-08"
            base.mkdir(parents=True)
            (base / "01.jsonl").write_bytes(b"older\n")
            (base / "02.jsonl").write_bytes(b"bad")
            with self.assertRaises(ValueError):
                latest_summary(Path(root))

    def test_publish_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            with patch("upbit_b.latest_signals.latest_summary", return_value={"cycle_id": "same"}):
                self.assertEqual(publish(root)["status"], "UPDATED")
                self.assertEqual(publish(root)["status"], "UNCHANGED")


if __name__ == "__main__":
    unittest.main()

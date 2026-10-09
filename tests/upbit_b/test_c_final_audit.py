"""Final audit regression: legacy CLI must share archive namespace protection."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from upbit_c import research_runner as R


class FinalAuditTests(unittest.TestCase):
    def test_nested_protected_parent_fails_before_api(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(R,'ResearchClient') as client:
            target=Path(tmp)/'.github'/'nested'/'research'
            with self.assertRaises(SystemExit):R.main(['--output',str(target)])
            client.assert_not_called()
            self.assertFalse(target.exists())

    def test_protected_output_fails_before_api_or_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('.git','.github','upbit_c','tests','output_custom','OUTPUT_DIAGNOSTICS',
                         'data','data_market','btc_anytime','upbit_b','metadata_features','chat_analysis'):
                with self.subTest(name=name),patch.object(R,'ResearchClient') as client,patch.object(R,'scan') as scan:
                    with self.assertRaises(SystemExit):R.main(['--output',str(Path(tmp)/name)])
                    client.assert_not_called();scan.assert_not_called()
                    self.assertFalse((Path(tmp)/name).exists())

    def test_isolated_research_destination_remains_allowed(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(R,'ResearchClient'),patch('builtins.print'):
            result=R.main(['--output',str(Path(tmp)/'isolated-research'),'--outcomes-only'])
            self.assertEqual(result['activation'],'RESEARCH_ONLY')
            self.assertEqual(result['production_files_created'],0)


if __name__=='__main__':unittest.main()

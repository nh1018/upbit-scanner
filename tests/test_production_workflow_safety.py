"""Verify pipeline failure propagation without executing any collector."""
import os,shutil,subprocess,tempfile,unittest
from pathlib import Path

class WorkflowSafetyTests(unittest.TestCase):
    def test_b_collector_pipeline_failure_is_not_hidden_by_tee(self):
        root=Path(__file__).parents[1]
        text=(root/'.github/workflows/upbit-b-history.yml').read_text()
        step=text.split('      - name: Publish one validated prospective cycle; never modify collectors',1)[1].split('      - name:',1)[0]
        self.assertIn('shell: bash',step)
        self.assertIn('set -euo pipefail',step)
        bash=shutil.which('bash')
        if bash is None and Path('C:/Program Files/Git/bin/bash.exe').exists():bash='C:/Program Files/Git/bin/bash.exe'
        if bash is None:
            # Windows MinGit has no bash; Linux PR CI exercises the pipeline.
            self.assertEqual(os.name,'nt','Linux Actions runner must provide bash')
            return
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([bash,'-c','set -euo pipefail; false | tee log; touch should_not_run'],cwd=folder,capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((Path(folder)/'should_not_run').exists())
            result=subprocess.run([bash,'-c','set -euo pipefail; printf fixture | tee success'],cwd=folder,capture_output=True)
            self.assertEqual(result.returncode,0)
            self.assertEqual((Path(folder)/'success').read_text(),'fixture')

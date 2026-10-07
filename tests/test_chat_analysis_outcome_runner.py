import json,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from chat_analysis.history import build,write_once
from chat_analysis.outcome_runner import run

class RunnerTests(unittest.TestCase):
 def test_btc_matures_only_from_completed_repo_bars(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);p=root/"data_market/btc_anytime/15m";p.mkdir(parents=True)
   # Decision at 12:00; this 12:45-open bar closes exactly at +1h.
   p.joinpath("btc_15m_20261007.jsonl").write_text(json.dumps({"time":1791377100000,"open":100,"high":106,"low":99,"close":105})+"\n")
   r=build("BTC","LONG","100","2026-10-07T12:00:00Z","x",[{"path":"snapshot","sha256":"x"}],horizons=["1h"])
   write_once(root,r)
   s=run(root,datetime(2026,10,7,13,1,tzinfo=timezone.utc));self.assertEqual(s["written"],1)
   out=json.loads(next((root/"output_chat_analysis/v1/outcomes").glob("*/*.json")).read_text())
   self.assertEqual(out["horizons"]["1h"]["status"],"MATURED")
 def test_upbit_fails_closed_without_finalized_source(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);r=build("UPBIT","WATCH","100","2026-10-07T12:00:00Z","x",[{"path":"scan","sha256":"x"}],instruments=["KRW-X"],horizons=["1d"])
   write_once(root,r);run(root,datetime(2026,10,8,13,tzinfo=timezone.utc))
   out=json.loads(next((root/"output_chat_analysis/v1/outcomes").glob("*/*.json")).read_text())
   self.assertEqual(out["status"],"PENDING_SOURCE")

if __name__=="__main__":unittest.main()

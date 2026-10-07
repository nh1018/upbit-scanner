import tempfile,unittest
from pathlib import Path
from chat_analysis.history import build,write_once,validate
from chat_analysis.outcome import evaluate

class ChatHistoryTests(unittest.TestCase):
 def base(self):
  return build("BTC","LONG","100","2026-10-07T12:00:00Z","long bias",
    [{"path":"output_btc_anytime/latest_analysis.json","sha256":"abc"}])
 def test_deterministic_and_write_once(self):
  a=self.base();b=self.base();self.assertEqual(a["decision_id"],b["decision_id"]);validate(a)
  with tempfile.TemporaryDirectory() as d:
   s,p=write_once(d,a);self.assertEqual(s,"WRITTEN")
   s,_=write_once(d,a);self.assertEqual(s,"NOOP")
 def test_requires_source_refs(self):
  with self.assertRaises(ValueError):build("BTC","LONG","100","2026-10-07T12:00:00Z","x",[])
 def test_outcome_long(self):
  r=self.base();bars=[{"close_time_ms":1791378000000,"close":"105","high":"106","low":"99"}]
  o=evaluate(r,bars,1791378000000);x=o["horizons"]["1h"]
  self.assertEqual(x["status"],"MATURED");self.assertEqual(x["directional_return_pct"],"5.00")
 def test_no_retro_decision_from_outcome(self):
  r=self.base();o=evaluate(r,[],1791381600000)
  self.assertEqual(o["horizons"]["1h"]["status"],"UNAVAILABLE")

if __name__=="__main__":unittest.main()

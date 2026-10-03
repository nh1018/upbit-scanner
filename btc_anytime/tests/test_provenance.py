import copy,hashlib,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from btc_anytime.provenance import evaluate,append_event,digest,verify_recorded
from btc_anytime.backfill_htf import daily_archive
from btc_anytime.integrity import Entry
from btc_anytime.tests.test_tools import archive,NOW

class ProvenanceTests(unittest.TestCase):
    def bundle(self,changed=False):
        fetch=archive("1h");url="https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1h/BTCUSDT-1h-2026-09-30.zip"
        content=fetch(url);data=daily_archive("1h","2026-09-30",fetch)
        if changed:
            for r in data:r["source_sha256"]="0"*64
        entries=[Entry("original.jsonl",n,r) for n,r in enumerate(data,1)]
        refs=[dict(commit="original",file="original.jsonl",line=e.line,row_sha256=digest(e.data),time=e.data["time"]) for e in entries]
        import csv
        from zipfile import ZipFile
        with ZipFile(io.BytesIO(content)) as z:rows=list(csv.reader(io.StringIO(z.read(z.namelist()[0]).decode())))[1:]
        rest=json.dumps(rows).encode();checksum=(hashlib.sha256(content).hexdigest()+"  BTCUSDT-1h-2026-09-30.zip").encode()
        return entries,content,checksum,rest,refs
    def result(self,changed=False):return evaluate(*self.bundle(changed),now_ms=NOW)
    def test_unchanged_pass(self):self.assertEqual(self.result()["classification"],"UNCHANGED");self.assertEqual(self.result()["status"],"PASS")
    def test_equivalent_republish(self):
        r=self.result(True);self.assertEqual(r["status"],"PASS");self.assertEqual(r["classification"],"ARCHIVE_REPUBLISHED_WITH_EQUIVALENT_USED_DATA");self.assertIn("original_csv_bytes",r["unavailable_fields"])
    def test_used_row_change_fails(self):
        args=list(self.bundle(True));args[0][0].data["volume"]="1";args[4][0]["row_sha256"]=digest(args[0][0].data)
        r=evaluate(*args,now_ms=NOW);self.assertEqual(r["status"],"FAIL");self.assertEqual(r["classification"],"MARKET_DATA_CHANGED")
    def test_bad_checksum_fails(self):
        args=list(self.bundle(True));args[2]=b"bad";self.assertEqual(evaluate(*args,now_ms=NOW)["status"],"FAIL")
    def test_wrong_checksum_filename_fails(self):
        args=list(self.bundle(True));args[2]=args[2].replace(b"BTCUSDT",b"OTHER");self.assertEqual(evaluate(*args,now_ms=NOW)["status"],"FAIL")
    def test_rest_mismatch_fails(self):
        args=list(self.bundle(True));rest=json.loads(args[3]);rest[0][5]="999";args[3]=json.dumps(rest).encode();self.assertEqual(evaluate(*args,now_ms=NOW)["status"],"FAIL")
    def test_missing_evidence_unresolved(self):
        args=list(self.bundle(True));args[4]=[];r=evaluate(*args,now_ms=NOW);self.assertEqual(r["status"],"FAIL");self.assertEqual(r["classification"],"UNRESOLVED_PROVENANCE_CHANGE")
    def test_original_preserved_and_not_mutated(self):
        args=self.bundle(True);before=copy.deepcopy(args[0]);evaluate(*args,now_ms=NOW);self.assertEqual(before,args[0]);args[0][0].data["oi"]="modified";self.assertEqual(evaluate(*args,now_ms=NOW)["status"],"FAIL")
    def test_append_only_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            e=self.result(True);p=append_event(e,d);before=p.read_bytes()
            with self.assertRaises(FileExistsError):append_event(e,d)
            newer=copy.deepcopy(e);newer["verification_time_utc"]+="new";append_event(newer,d)
            self.assertEqual(before,p.read_bytes());self.assertEqual(len(list(Path(d).glob("*.json"))),2)
    def test_no_event_fails(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):verify_recorded(self.bundle(True)[0],directory=d)
    def test_tampered_event_fails(self):
        with tempfile.TemporaryDirectory() as d:
            p=append_event(self.result(True),d);p.write_text(p.read_text().replace('"PASS"','"FAIL"'))
            with self.assertRaises(ValueError):verify_recorded(self.bundle(True)[0],directory=d)
    def test_recorded_event_requires_fresh_checks(self):
        args=self.bundle(True);entries,content,checksum,rest,refs=args
        with tempfile.TemporaryDirectory() as d:
            append_event(evaluate(*args,now_ms=NOW),d)
            def fetch(url):return checksum if url.endswith("CHECKSUM") else rest if "klines?" in url else content
            with patch("btc_anytime.provenance.observation",return_value=refs):
                self.assertEqual(verify_recorded(entries,fetch,d)["status"],"PASS")
                with self.assertRaises(ValueError):verify_recorded(entries,lambda url:b"bad",d)
    def test_uncompleted_boundary_fails(self):
        args=self.bundle(True);self.assertEqual(evaluate(*args,now_ms=args[0][0].data["time"])["status"],"FAIL")
    def test_missing_schema_fails(self):
        args=self.bundle(True);event=evaluate(*args,now_ms=NOW);del event["header"]
        with tempfile.TemporaryDirectory() as d:
            append_event(event,d)
            with self.assertRaises(ValueError):verify_recorded(args[0],directory=d)
    def test_git_original_change_fails(self):
        args=self.bundle(True)
        with tempfile.TemporaryDirectory() as d:
            append_event(evaluate(*args,now_ms=NOW),d)
            with patch("btc_anytime.provenance.observation",side_effect=ValueError("original observation modified")):
                with self.assertRaises(ValueError):verify_recorded(args[0],directory=d)
    def test_missing_recorded_evidence_fails(self):
        args=self.bundle(True);event=evaluate(*args,now_ms=NOW);del event["evidence"]["rest_pass"]
        with tempfile.TemporaryDirectory() as d:
            append_event(event,d)
            def fetch(url):return args[2] if url.endswith("CHECKSUM") else args[3] if "klines?" in url else args[1]
            with patch("btc_anytime.provenance.observation",return_value=args[4]):
                with self.assertRaises(ValueError):verify_recorded(args[0],fetch,d)
if __name__=="__main__":unittest.main()

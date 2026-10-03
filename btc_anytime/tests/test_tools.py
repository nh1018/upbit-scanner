import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile,ZIP_DEFLATED
from btc_anytime.integrity import Entry,DURATIONS,analyze,cross_check,iso,utc_ms,load_entries,source_cross_check,decimal
from btc_anytime.backfill_htf import rest_day, daily_archive,historical_oi,plan_backfill,write_plan

START=utc_ms("2026-09-30T00:00:00Z")
NOW=utc_ms("2026-10-04T00:00:00Z")
def row(tf="1h",time=START,oi=10):
    d={"symbol":"BTCUSDT","market":"BINANCE_USDT_M_FUTURES","timeframe":tf,"time":time,"candle_time_utc":iso(time),"close_time_ms":time+DURATIONS[tf]-1,"open":"100.1","high":"110","low":"90","close":"105","volume":"0.3","source":"fixture"}
    if oi is not None:d["oi"]=oi
    return d

def entry(d,line=1,file="fixture.jsonl"):return Entry(file,line,d)

def archive(tf,day="2026-09-30",bad=False):
    duration=DURATIONS[tf];start=utc_ms(day+"T00:00:00Z")
    lines=["open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore"]
    for t in range(start,start+86400000,duration):lines.append(f"{t},100.1,110,90,105,0.3,{t+duration-1},31.5,10,0.1,10.5,0")
    out=io.BytesIO()
    with ZipFile(out,"w",ZIP_DEFLATED) as z:z.writestr(f"BTCUSDT-{tf}-{day}.csv","\n".join(lines)+"\n")
    data=out.getvalue()
    def fetch(url):
        if "openInterestHist" in url:
            from urllib.parse import parse_qs,urlparse
            args=parse_qs(urlparse(url).query);a=int(args["startTime"][0]);b=int(args["endTime"][0]);limit=int(args["limit"][0]);
            return json.dumps([{"symbol":"BTCUSDT","timestamp":t,"sumOpenInterest":"900.123"} for t in list(range(a,b+1,duration))[:limit]]).encode()
        if url.endswith(".CHECKSUM"):return (("0"*64 if bad else hashlib.sha256(data).hexdigest())+"  archive.zip").encode()
        return data
    return fetch

class IntegrityTests(unittest.TestCase):
    def test_continuous_ohlcv_and_oi(self):
        rs=[entry(row(time=START+i*3600000),i+1) for i in range(3)]
        r=analyze(rs,"1h",now_ms=NOW);self.assertEqual(r["status"],"PASS");self.assertEqual(r["row_count"],3)
    def test_gap_all_slots(self):
        rs=[entry(row()),entry(row(time=START+3*3600000),2)]
        r=analyze(rs,"1h",now_ms=NOW);self.assertEqual(r["missing_slot_count"],2);self.assertEqual([x["time"] for x in r["missing_slots"]],[START+3600000,START+7200000])
    def test_conflicting_duplicate(self):
        b=row();b["close"]="104";r=analyze([entry(row()),entry(b,2)],"1h",now_ms=NOW)
        self.assertEqual(r["duplicate_count"],1);self.assertEqual(len(r["conflicts"]),1)
    def test_equivalent_utc_formats_duplicate(self):
        a=row();b=row();b["candle_time_utc"]="2026-09-30T00:00:00.000000Z"
        self.assertEqual(analyze([entry(a),entry(b,2)],"1h",now_ms=NOW)["utc_duplicate_count"],1)
    def test_invalid_values_timestamp_and_in_progress(self):
        for patch in [{"volume":-1},{"high":99},{"oi":float("nan")},{"oi":-1},{"time":START+1},{"candle_time_utc":"bad"},{"is_closed":False},{"close_time_ms":START}]:
            with self.subTest(patch=patch):self.assertEqual(analyze([entry({**row(),**patch})],"1h",now_ms=NOW)["status"],"FAIL")
    def test_unavailable_and_legacy_missing_oi_are_warnings(self):
        a=row(oi=None);a.update(oi=None,oi_status="unavailable")
        b=row(time=START+3600000,oi=None)
        r=analyze([entry(a),entry(b,2)],"1h",now_ms=NOW)
        self.assertEqual(r["status"],"WARNING");self.assertEqual(r["oi_coverage"],{"unavailable":1,"missing_unlabelled":1})
    def test_source_transition_boundary_and_unsorted(self):
        a=row();b=row(time=START+3600000);b["source"]="live"
        r=analyze([entry(a,file="history"),entry(b,file="daily")],"1h",now_ms=NOW)
        self.assertEqual(r["source_transitions"][0]["delta_ms"],3600000)
        r=analyze([entry(b),entry(a,2)],"1h",now_ms=NOW);self.assertEqual(len(r["file_order_errors"]),1)
    def test_exact_decimal_crosscheck(self):
        lower=[entry(row("15m",START+i*900000)) for i in range(4)];upper=row();upper["volume"]="1.2"
        r=cross_check(lower,[entry(upper)],"1h");self.assertEqual(r["compared"],1);self.assertFalse(r["mismatches"])
        upper["volume"]="1.20001";self.assertEqual(cross_check(lower,[entry(upper)],"1h")["mismatches"][0]["field"],"volume")
    def test_crosscheck_rejects_incomplete_and_duplicate_children(self):
        children=[entry(row("15m",START+i*900000)) for i in range(4)]
        self.assertEqual(len(cross_check(children[:-1],[entry(row())],"1h")["incomplete_lower_buckets"]),1)
        self.assertEqual(len(cross_check(children+[children[0]],[entry(row())],"1h")["duplicate_lower_buckets"]),1)
    def test_parse_failure_has_location(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"1h";p.mkdir();(p/"btc_1h_history.jsonl").write_text("bad\n")
            entries,errors=load_entries(Path(d),"1h");self.assertEqual(errors[0]["line"],1)

class SourcePolicyTests(unittest.TestCase):
    def test_raw_mismatch_is_audit_but_official_mismatch_fails(self):
        lower=[Entry("fixture",n,row("15m",START+n*900000)) for n in range(4)]
        upper=[Entry("fixture",1,{**row("1h",START),"volume":"1.3"})]
        raw=source_cross_check(lower,upper,"1h","raw_to_official")
        official=source_cross_check(lower,upper,"1h","official_to_official")
        self.assertEqual(raw["status"],"SOURCE_MISMATCH");self.assertEqual(raw["structural_status"],"PASS");self.assertEqual(official["status"],"FAIL")
    def test_official_missing_coverage_fails(self):
        upper=[Entry("fixture",1,row("1h",START))]
        self.assertEqual(source_cross_check([],upper,"1h","official_to_official")["status"],"FAIL")

class SourceAuditTests(unittest.TestCase):
    def bundle(self):
        from btc_anytime.backfill_htf import ARCHIVE
        bundle={}
        for tf in ("1h","4h","1d"):
            d=row(tf,START,oi=None);d.update(volume=str(decimal("0.3")*(DURATIONS[tf]//900000)),source="binance_usdm_public_data_daily_klines_backfill",source_url=f"{ARCHIVE}/{tf}/BTCUSDT-{tf}-2026-09-30.zip",source_sha256="a"*64,source_entry=f"BTCUSDT-{tf}-2026-09-30.csv",source_row=1,oi_status="unavailable",oi_unavailable_reason="fixture")
            bundle[tf]=[Entry("fixture",1,d)]
        bundle["15m"]=[Entry("fixture",n,{**row("15m",START+n*900000),"received_at_utc":"2026-09-30T01:00:00Z"}) for n in range(4)]
        return bundle
    def reference(self,tf,day,fetch):
        b=self.bundle()
        if tf!="15m":return [b[tf][0].data]
        return [row("15m",START+n*900000,oi=None) for n in range(96)]
    def test_source_mismatch_does_not_fail_mandatory_official_audit(self):
        from unittest.mock import patch
        from btc_anytime.source_audit import audit
        b=self.bundle();b["15m"][0].data["volume"]="0.1"
        with patch("btc_anytime.source_audit.daily_archive",side_effect=self.reference):r=audit(b,official=True,now_ms=NOW)
        self.assertEqual(r["status"],"PASS");self.assertTrue(r["mandatory_validation_complete"]);self.assertEqual(r["raw_to_official"][0]["status"],"SOURCE_MISMATCH")
    def test_bad_recorded_checksum_fails(self):
        from unittest.mock import patch
        from btc_anytime.source_audit import audit
        b=self.bundle();b["1h"][0].data["source_sha256"]="b"*64
        with patch("btc_anytime.source_audit.daily_archive",side_effect=self.reference):r=audit(b,official=True,now_ms=NOW)
        self.assertEqual(r["status"],"FAIL");self.assertTrue(r["source_errors"])
    def test_missing_explicit_oi_provenance_fails(self):
        from unittest.mock import patch
        from btc_anytime.source_audit import audit
        b=self.bundle();b["1h"][0].data["oi_status"]="missing"
        with patch("btc_anytime.source_audit.daily_archive",side_effect=self.reference):r=audit(b,official=True,now_ms=NOW)
        self.assertEqual(r["status"],"FAIL")

class BackfillTests(unittest.TestCase):
    def prepare(self,root,tf="1h"):
        p=root/tf;p.mkdir();history=p/f"btc_{tf}_history.jsonl";history.write_text(json.dumps(row(tf,START-DURATIONS[tf],oi=None))+"\n");return history
    def test_checksum_and_archive_metadata(self):
        rows=daily_archive("1h","2026-09-30",archive("1h"));self.assertEqual(len(rows),24);self.assertEqual(rows[0]["source_row"],2);self.assertIsNone(rows[0]["oi"])
        with self.assertRaises(ValueError):daily_archive("1h","2026-09-30",archive("1h",bad=True))
    def test_plan_dry_run_does_not_touch_history_or_create_data(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);history=self.prepare(root);before=history.read_bytes()
            plan=plan_backfill(root,"1h",START+2*3600000,NOW,archive("1h"))
            self.assertEqual(len(plan["rows"]),2);self.assertEqual(history.read_bytes(),before);self.assertEqual(len(list((root/"1h").iterdir())),1)
    def test_write_then_rerun_is_idempotent_history_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);history=self.prepare(root);before=history.read_bytes()
            plan=plan_backfill(root,"1h",START+2*3600000,NOW,archive("1h"),True)
            self.assertTrue(all(r["oi_timestamp_ms"]==r["time"] for r in plan["rows"]));write_plan(root,plan)
            self.assertEqual(history.read_bytes(),before)
            rerun=plan_backfill(root,"1h",START+2*3600000,NOW,archive("1h"),True)
            self.assertEqual(len(rerun["rows"]),0);self.assertEqual(write_plan(root,rerun),[])
    def test_all_three_timeframe_boundaries(self):
        for tf in ("1h","4h","1d"):
            with self.subTest(tf=tf),tempfile.TemporaryDirectory() as d:
                root=Path(d);self.prepare(root,tf);plan=plan_backfill(root,tf,START+DURATIONS[tf],NOW,archive(tf))
                self.assertEqual(plan["rows"][0]["time"],START);self.assertEqual(len(plan["rows"]),1)
    def test_15m_and_in_progress_end_forbidden(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.prepare(root)
            with self.assertRaises(ValueError):plan_backfill(root,"15m",START,NOW,archive("1h"))
            with self.assertRaises(ValueError):plan_backfill(root,"1h",NOW+3600000,NOW,archive("1h"))
            with self.assertRaises(ValueError):plan_backfill(root,"1h",START+1,NOW,archive("1h"))
    def test_conflicting_existing_values_abort(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.prepare(root);bad=row();bad["close"]="104"
            (root/"1h"/"btc_1h_20260930.jsonl").write_text(json.dumps(bad)+"\n")
            with self.assertRaises(ValueError):plan_backfill(root,"1h",START+3600000,NOW,archive("1h"))
    def test_missing_official_candle_aborts_before_write(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.prepare(root)
            with self.assertRaises(ValueError):plan_backfill(root,"1h",START+86400000+3600000,NOW,archive("1h"))
    def test_historical_oi_exact_timestamp_only(self):
        def sparse(url):return json.dumps([{"symbol":"BTCUSDT","timestamp":START,"sumOpenInterest":"123.45"}]).encode()
        # Explicit empty response after first page avoids any carry-forward.
        calls=0
        def fetch(url):
            nonlocal calls;calls+=1;return sparse(url) if calls==1 else b"[]"
        data,warnings=historical_oi("1h",START,START+7200000,fetch)
        self.assertEqual(list(data),[START]);self.assertEqual(data[START]["oi_timestamp_ms"],START)
    def test_cutover_cannot_pass_first_live(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.prepare(root);live=row();live["received_at_utc"]="2026-09-30T01:00:00Z"
            (root/"1h"/"btc_1h_20260930.jsonl").write_text(json.dumps(live)+"\n")
            with self.assertRaisesRegex(ValueError,"first live"):plan_backfill(root,"1h",START+3600000,NOW,archive("1h"))
    def test_write_rejects_tampered_plan_before_creation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.prepare(root);plan=plan_backfill(root,"1h",START+3600000,NOW,archive("1h"));plan["rows"][0]["is_closed"]=False
            with self.assertRaises(ValueError):write_plan(root,plan)
            self.assertFalse((root/"1h"/"btc_1h_20260930.jsonl").exists())
    def test_official_rest_complete_day_and_missing_rejected(self):
        values=[[t,"100.1","110","90","105","0.3",t+3600000-1,"0",1,"0","0","0"] for t in range(START,START+86400000,3600000)]
        rows=rest_day("1h","2026-09-30",NOW,lambda _:json.dumps(values).encode())
        self.assertEqual(len(rows),24);self.assertNotIn("source_sha256",rows[0]);self.assertEqual(rows[0]["source_verification"],"official_https_rest_response_hash_no_published_checksum")
        with self.assertRaises(ValueError):rest_day("1h","2026-09-30",NOW,lambda _:json.dumps(values[:-1]).encode())
    def test_rest_fallback_is_explicit_and_only_zip_404(self):
        from urllib.error import HTTPError
        def missing(url):raise HTTPError(url,404,"missing",None,None)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.prepare(root)
            with self.assertRaises(HTTPError):plan_backfill(root,"1h",START+3600000,NOW,missing)
        def checksum_missing(url):
            if url.endswith(".CHECKSUM"):raise HTTPError(url,404,"missing",None,None)
            return archive("1h")(url)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.prepare(root)
            with self.assertRaises(HTTPError):plan_backfill(root,"1h",START+3600000,NOW,checksum_missing,allow_rest_fallback=True)
    def test_historical_oi_wrong_boundary_is_rejected(self):
        with self.assertRaises(ValueError):historical_oi("1h",START,START+3600000,lambda _:json.dumps([{"symbol":"BTCUSDT","timestamp":START+1,"sumOpenInterest":"1"}]).encode())
if __name__=="__main__":unittest.main()

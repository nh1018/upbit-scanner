"""Explicit memory dry-run or activated prospective publication; never reconstruct."""
import argparse
import json
import subprocess
import time
import os
import urllib.request
import urllib.error
from pathlib import Path
from collections import Counter
from decimal import Decimal as D

from . import feature_contracts as F
from . import history_contracts as C
from .history import build_cycle,validate_cycle,load_previous,check_existing,write_once,Conflict
from .market_data import HTTPClient,MarketData,universe,tickers,exchange_info,mapping,DataError
from .features import bundle,_clock_ms
from .trend_state import evaluate
from .contracts import DURATIONS

def activated(): return os.environ.get("UPBIT_B_HISTORY_ACTIVATED", "false")=="true"

def collect(client,boundary,started,clock_ms,progress=None,approved=None):
    """Raw windows never leave memory; fresh tickers collected after Features."""
    members,universe_ev=universe(client)
    markets=[m["market"] for m in members]
    info,exchange_ev=exchange_info(client)
    md=MarketData(client,boundary)
    staged={};failures={}
    for i,market in enumerate(markets):
        counterpart=mapping(market,info,approved)
        up={tf:md.window("UPBIT",market,tf) for tf in DURATIONS}
        spot=({tf:md.window("BINANCE_SPOT",counterpart["symbol"],tf) for tf in DURATIONS}
              if counterpart["status"] in ("VERIFIED","UNVERIFIED") else {})
        if any(w.reason and "418" in w.reason for w in (*up.values(),*spot.values())):raise DataError("API blocked (418); stop this run")
        failed={tf:w.reason or w.status for tf,w in up.items() if w.status in ("API_ERROR","INVALID_DATA")}
        if failed:failures[market]=F.dumps(failed)
        generated=clock_ms();features=bundle(up,spot,counterpart,generated)
        w=up["1h"]
        close=None
        if w.candles and w.candles[-1].close_ms==boundary:
            c=w.candles[-1]
            close={"provider":"UPBIT","instrument":market,"price":F.canonical(c.close),
                "candle_open_ms":c.open_ms,"candle_close_ms":c.close_ms,
                "received_at_ms":max(_clock_ms(ev["received_at_utc"]) for ev in w.evidence),
                "source_reference":{"input_sha256":w.input_sha256,"source_row_open_ms":c.open_ms,
                                    "responses":[{k:ev[k] for k in ("url","response_sha256","received_at_utc")} for ev in w.evidence]}}
        staged[market]={"bundle":features,"close_1h":close}
        md.cache.clear()
        if progress and ((i+1)%20==0 or i+1==len(markets)):progress(i+1,len(markets))
    # Independent batches preserve other markets on a ticker batch API failure.
    prices={};price_ev={}
    for i in range(0,len(markets),80):
        requested=markets[i:i+80]
        try:
            rows,ev=tickers(client,requested)
            prices.update(rows)
            for market in requested:price_ev[market]=ev[0]
        except DataError as exc:
            if "418" in str(exc):raise
            for market in requested:failures[market]="TICKER_API_ERROR:"+str(exc)
    entries={}
    for market,item in staged.items():
        actual=None
        if market in prices:
            ev=price_ev[market];ticker=prices[market]
            actual={"instrument":market,"provider":"UPBIT","price":ticker["trade_price"],
                "source_time_ms":ticker["timestamp"],"received_at_ms":_clock_ms(ev["received_at_utc"]),
                "source_reference":ev}
        engine=evaluate(item["bundle"],market,clock_ms(),actual)
        entries[market]={**item,"engine":engine,"status":"API_ERROR" if market in failures else "OK",
                         "reason":failures.get(market)}
    return markets,entries,{"universe":universe_ev,"exchange_info":exchange_ev,"ticker_responses":list({ev["response_sha256"]:ev for ev in price_ev.values()}.values())}

def _git(repo,*args,check=True):
    result=subprocess.run(["git","-c","safe.directory="+str(Path(repo).resolve()).replace("\\","/"),
        "-c","user.name=github-actions[bot]","-c","user.email=41898282+github-actions[bot]@users.noreply.github.com",
        *args],cwd=repo,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
    if check and result.returncode:raise RuntimeError("Git operation failed: "+args[0])
    return result

def _remote_bytes(repo,path):
    exists=_git(repo,"cat-file","-e","origin/main:"+path,check=False)
    return _git(repo,"show","origin/main:"+path).stdout if exists.returncode==0 else None

def publish(repo,payload,clock_ms):
    """Bounded normal push; reuse frozen payload; never force or recollect."""
    if not activated():raise ValueError("ACTIVATION_OFF")
    m,_=validate_cycle(payload);path=C.cycle_path(m["source_cutoff"])
    expected=Path(repo)/path
    if expected.read_bytes()!=payload:raise Conflict("local prepared bytes changed")
    status=_git(repo,"status","--porcelain","--untracked-files=all").stdout.decode().splitlines()
    if any(line[3:]!=path or line[:2]!="??" for line in status):raise ValueError("unrelated local/staged modifications")
    if status:
        _git(repo,"add","--",path)
        staged=_git(repo,"diff","--cached","--name-status").stdout.decode().strip()
        if staged!="A\t"+path:raise ValueError("only one new cycle journal may be staged")
        _git(repo,"commit","-m","Record prospective Upbit B observations")
    else:
        # A previous attempt may have committed locally but failed to push.
        _git(repo,"fetch","origin","main")
        remote=_remote_bytes(repo,path)
        if remote is not None:
            if remote!=payload:raise Conflict("remote cycle exists with different bytes")
            return {"status":"REMOTE_IDENTICAL_NOOP","cycle_id":m["cycle_id"],"path":path,
                    "repository_observed_at":C.iso(clock_ms())}
        base=_git(repo,"merge-base","HEAD","origin/main").stdout.decode().strip()
        own=_git(repo,"diff","--name-status",base+"..HEAD").stdout.decode().strip()
        if own!="A\t"+path:raise ValueError("no isolated unpublished cycle commit to retry")
        _git(repo,"rebase","origin/main")
    for attempt in range(C.POLICY["git_push_attempts"]):
        C.check_window(m["source_cutoff"],C.clock(m["started_at"]),clock_ms())
        result=_git(repo,"push","origin","HEAD:main",check=False)
        if result.returncode==0:
            return {"status":"PUSHED","cycle_id":m["cycle_id"],"path":path,
                    "commit_sha":_git(repo,"rev-parse","HEAD").stdout.decode().strip(),
                    "push_acknowledged_at":C.iso(clock_ms()),"attempt":attempt+1}
        _git(repo,"fetch","origin","main")
        remote=_remote_bytes(repo,path)
        if remote is not None:
            if remote!=payload:raise Conflict("remote cycle exists with different bytes")
            return {"status":"REMOTE_IDENTICAL_NOOP","cycle_id":m["cycle_id"],"path":path,
                    "repository_observed_at":C.iso(clock_ms())}
        _git(repo,"rebase","origin/main")
        if expected.read_bytes()!=payload:raise Conflict("rebase changed frozen prepared bytes")
    raise Conflict("push retries exhausted; no force push")

def storage_report(payload,entries):
    m,records=validate_cycle(payload)
    sizes={}
    for kind in ("DETAIL","COMPACT","STATE_ONLY"):
        rows=[r for r in records if r["evidence_level"]==kind]
        total=sum(len((F.dumps(r)+"\n").encode()) for r in rows)
        sizes[kind]={"count":len(rows),"bytes":total,"average_bytes":F.canonical(D(total)/len(rows)) if rows else None}
    compact=[r for r in records if r["selection_reason"]=="CONTROL"]
    # First controls are detailed baseline records; measure a compact projection
    # with the actual current data rather than pretending baseline size is steady state.
    from .history import _compact
    def shape(r,kind):
        # Size-only projection using actual values; no invented normal cycle is published.
        item={k:v for k,v in r.items() if k not in ("evidence","events","observation")}
        item.update(record_kind=kind,evidence_level="COMPACT",selection_reason=kind,events=[],
            previous_state_reference=r["observation_id"],previous_effective_cycle_id=m["cycle_id"],
            previous_detail_reference=r["observation_id"],observation=_compact(entries[r["instrument"]]))
        return item
    compact_control=[shape(r,"CONTROL") for r in compact]
    compact_heartbeats=[shape(r,"HEARTBEAT") for r in records if r["summary"]["candidate"]=="TRUE"]
    def average(rows):return D(sum(len((F.dumps(r)+"\n").encode()) for r in rows))/len(rows) if rows else D(0)
    compact_avg=average(compact_control);heartbeat_avg=average(compact_heartbeats)
    detail_avg=D(sizes["DETAIL"]["average_bytes"] or 0)
    state_avg=D(sizes["STATE_ONLY"]["average_bytes"] or 0)
    manifest_bytes=len((F.dumps(m)+"\n").encode())
    # Scenarios are explicit assumptions, not fabricated state trajectories.
    candidates=m["candidate_count"]
    daily=candidates*6*heartbeat_avg+24*len(compact_control)*compact_avg+60*detail_avg+24*24*state_avg+24*manifest_bytes
    normal=daily/24
    return F.canonical({"cold_start_payload_bytes":len(payload),"manifest_bytes":manifest_bytes,"record_sizes":sizes,
        "compact_control_average_bytes":compact_avg,"compact_heartbeat_average_bytes":heartbeat_avg,
        "candidate_detail_average_bytes":average([r for r in records if r["evidence_level"]=="DETAIL" and r["summary"]["candidate"]=="TRUE"]),
        "normal_cycle_estimated_bytes":normal,"daily_estimated_bytes":daily,
        "30_days_estimated_bytes":daily*30,"180_days_estimated_bytes":daily*180,"365_days_estimated_bytes":daily*365,
        "assumptions":{"candidates":candidates,"candidate_heartbeats_per_day":6,"controls_per_hour":len(compact_control),
                       "detailed_events_per_day":60,"state_changes_per_hour":24},
        "baseline_only":True,"future_event_rate_measured":False})

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",default=".")
    choice=parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--dry-run",action="store_true")
    choice.add_argument("--record",action="store_true")
    args=parser.parse_args(argv);repo=Path(args.repo).resolve()
    if args.record and not activated():
        print(F.dumps({"status":"ACTIVATION_OFF","production_files_created":0}));return
    clock_ms=lambda:time.time_ns()//1000000
    started=clock_ms();boundary=started//C.HOUR*C.HOUR
    existing=check_existing(repo,boundary)
    if existing!="ABSENT":
        if args.record:
            frozen=(repo/C.cycle_path(boundary)).read_bytes()
            result=publish(repo,frozen,clock_ms)
            print(F.dumps({"status":existing,"publication":result,"production_files_created":0}));return
        print(F.dumps({"status":existing,"production_files_created":0}));return
    if args.record:C.check_window(boundary,started)
    previous=load_previous(repo,boundary)
    if previous["cycle_id"]:
        previous["receipt"]={"cycle_id":previous["cycle_id"],"path":C.cycle_path(previous["boundary"]),
            "journal_sha256":__import__("hashlib").sha256((repo/C.cycle_path(previous["boundary"])).read_bytes()).hexdigest(),
            "repository_observed_at":C.iso(started),"semantics":"observed_in_checkout_at_scan_start_not_first_commit_time"}
    counts,errors=Counter(),Counter()
    def opener(request,**kw):
        counts[request.full_url.split("/")[2]]+=1
        try:return urllib.request.urlopen(request,**kw)
        except urllib.error.HTTPError as exc:errors[str(exc.code)]+=1;raise
        except OSError:errors["NETWORK"]+=1;raise
    def progress(done,total):print(F.dumps({"progress":done,"universe":total,"elapsed_ms":clock_ms()-started}),flush=True)
    markets,entries,scan_evidence=collect(HTTPClient(opener=opener),boundary,started,clock_ms,progress)
    completed=clock_ms();prepared=clock_ms()
    revision=_git(repo,"rev-parse","HEAD").stdout.decode().strip()
    scan_evidence["code_artifact_sha256"]=F.digest({path.name:__import__("hashlib").sha256(path.read_bytes()).hexdigest()
                                                  for path in sorted((repo/"upbit_b").glob("*.py"))})
    scan_evidence["clean_checkout"]=_git(repo,"status","--porcelain").stdout==b""
    payload=build_cycle(markets,entries,boundary,started,completed,prepared,revision,previous,
                        publishable=args.record,scan_evidence=scan_evidence)
    m,records=validate_cycle(payload)
    with __import__("decimal").localcontext() as context:
        context.prec=34;context.rounding=__import__("decimal").ROUND_HALF_EVEN
        storage=storage_report(payload,entries)
    report={"status":"DRY_RUN_PASS" if args.dry_run else "PREPARED","manifest":m,"storage":storage,
        "requests":dict(counts),"transport_errors":dict(errors),"runtime_ms":clock_ms()-started,
        "production_files_created":0,"raw_files_created":0,
        "detail_example":next((r for r in records if r["evidence_level"]=="DETAIL"),None)}
    if args.record:
        write_once(repo,payload,clock_ms());report["publication"]=publish(repo,payload,clock_ms());report["production_files_created"]=1
    print(F.dumps(report),flush=True)
    return report

if __name__=="__main__":main()

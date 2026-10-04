"""Read-only production dry-run; opt-in exclusive derived namespace writer."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import re
import subprocess
from pathlib import Path
from .engine import build_timeframe, digest, PARAMETERS, MANIFEST_VERSION, encode
from .snapshot import synchronize
from btc_anytime.integrity import DURATIONS, iso


def protected_hashes(repo):
    roots=("data_market", "data", "output", "market_data_v1", ".github", "btc_anytime/cloudflare", "btc_anytime/tradingview", "btc_anytime/provenance_events")
    files=[]
    for name in roots:
        root=repo/name
        if root.exists():files.extend(p for p in root.rglob("*") if p.is_file())
    for name in ("upbit_binance_scanner.py", "btc_anytime/backfill_htf.py", "btc_anytime/integrity.py", "btc_anytime/validate_history.py", "btc_anytime/provenance.py", "btc_anytime/HTF_BACKFILL_REPORT.json"):
        if (repo/name).exists():files.append(repo/name)
    return {str(p.relative_to(repo)).replace("\\","/"):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}


def load_raw(repo):
    datasets={};references={};file_hashes={}
    for tf in DURATIONS:
        rows=[];refs={}
        for path in sorted((repo/"data_market/btc_anytime"/tf).glob(f"btc_{tf}_*.jsonl")):
            content=path.read_bytes()
            name=path.relative_to(repo).as_posix()
            file_hashes[name]=hashlib.sha256(content).hexdigest()
            for number,line in enumerate(content.decode("utf-8-sig").splitlines(),1):
                if not line.strip():raise ValueError(f"blank raw row: {name}:{number}")
                # Retain the original decimal text; do not route it through binary float.
                row=json.loads(line,parse_float=str)
                if not isinstance(row,dict):raise ValueError("raw row must be object")
                rows.append(row)
                if row["time"] in refs:raise ValueError(f"duplicate raw key: {tf}:{row['time']}")
                refs[row["time"]]={"file":name,"line":number,"time":row["time"],
                                   "row_sha256":hashlib.sha256(line.encode()).hexdigest()}
        if not rows:raise ValueError(f"empty raw timeframe: {tf}")
        datasets[tf]=rows;references[tf]=refs
    return datasets,references,file_hashes


def build_dataset(datasets, observed_at_ms, file_hashes=None, references=None, unit_registry=(), git_revision=None, availability_evidence=None):
    manifest={"manifest_version":MANIFEST_VERSION,"parameter_hash":digest(PARAMETERS),
              "file_hashes":file_hashes or {},"row_hashes":{tf:[digest(r) for r in sorted(rows,key=lambda x:x["time"])] for tf,rows in datasets.items()},
              "observation":{"kind":"consumer_first_observed","observed_at_ms":observed_at_ms},
              "unit_registry":list(unit_registry)}
    if availability_evidence is not None:manifest["availability_evidence"]=availability_evidence
    if git_revision is not None:manifest["input_git_commit"]=git_revision
    manifest_id=digest(manifest)
    manifest["manifest_id"]=manifest_id
    series={}
    for tf,rows in datasets.items():
        availability={r["time"]:{"kind":"consumer_first_observed","observed_at_ms":observed_at_ms,
                                 "ref":"manifest:"+manifest_id} for r in rows}
        if availability_evidence is not None:availability=availability_evidence.get(tf,{})
        records=build_timeframe(rows,tf,availability,unit_registry,(references or {}).get(tf))
        for record in records:
            record["dataset_manifest_id"]=manifest_id
            record["result_hash"]=digest(record)
        series[tf]=records
    return manifest,series


def write_derived(repo, manifest, series, snapshot):
    """No latest overwrite: a new manifest namespace, exclusive files only."""
    identity=manifest.get("manifest_id","")
    content={k:v for k,v in manifest.items() if k!="manifest_id"}
    if not re.fullmatch(r"[0-9a-f]{64}",identity) or identity!=digest(content):
        raise ValueError("invalid manifest identity")
    if any(r.get("dataset_manifest_id")!=identity for records in series.values() for r in records):
        raise ValueError("feature manifest mismatch")
    if any(r.get("result_hash")!=digest({k:v for k,v in r.items() if k!="result_hash"})
           for records in series.values() for r in records):
        raise ValueError("feature result hash mismatch")
    if any(tf not in DURATIONS or any(r.get("timeframe")!=tf for r in records) for tf,records in series.items()):
        raise ValueError("invalid output timeframe")
    namespace=(repo/"data_features/btc_anytime/v1").resolve()
    expected=repo.resolve()/"data_features/btc_anytime/v1"
    if namespace!=expected:
        raise ValueError("derived namespace symlink refused")
    root=namespace/identity
    root.mkdir(parents=True,exist_ok=False)
    (root/"manifest.json").write_text(json.dumps(manifest,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    for tf,records in series.items():
        directory=root/tf;directory.mkdir()
        with (directory/"features.jsonl").open("x",encoding="utf-8") as handle:
            for record in records:handle.write(json.dumps(record,sort_keys=True)+"\n")
    with (root/"snapshot.json").open("x",encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot,sort_keys=True,indent=2)+"\n")
    return root


def dry_run(repo):
    before=protected_hashes(repo)
    datasets,refs,files=load_raw(repo)
    revision=subprocess.run(["git","--no-optional-locks","-c","safe.directory="+str(repo.resolve()),
                             "rev-parse","HEAD"],cwd=repo,capture_output=True,text=True,check=True).stdout.strip()
    for refs_by_tf in refs.values():
        for ref in refs_by_tf.values():ref["git_commit"]=revision
    observed=int(datetime.now(timezone.utc).timestamp()*1000)
    from .registry import load_registry
    registry,units=load_registry(repo)
    manifest,series=build_dataset(datasets,observed,files,refs,units,git_revision=revision)
    snapshot=synchronize(series,observed)
    summaries={};problems=[]
    for tf,records in series.items():
        times=[r["time"] for r in records];duration=DURATIONS[tf]
        gaps=sum(max((b-a)//duration-1,0) for a,b in zip(times,times[1:]))
        abnormal=sum(b-a!=duration for a,b in zip(times,times[1:]))
        invalid=sum(not all(r["input_validity"].values()) or r["time"]+duration>observed for r in records)
        duplicates=len(times)-len(set(times))
        latest=records[-1]
        ready=[k for k,q in latest["feature_quality"].items() if q["ready"]]
        nulls={k:q["null_reason"] for k,q in latest["feature_quality"].items() if not q["ready"]}
        summaries[tf]={"rows":len(records),"first":iso(times[0]),"last":iso(times[-1]),
            "duplicates":duplicates,"missing_slots":gaps,"abnormal_intervals":abnormal,"invalid_rows":invalid,
            "latest_ready":ready,"latest_null":nulls,"oi_absolute_available":sum(r["features"]["oi_absolute"] is not None for r in records),
            "validated_oi_segments":len({r["oi_metadata"]["segment_id"] for r in records if r["oi_metadata"]["segment_id"]}),
            "oi_change_ready":sum(r["features"]["oi_change"] is not None for r in records),
            "oi_change_pct_ready":sum(r["features"]["oi_change_pct"] is not None for r in records),
            "price_oi_state_ready":sum(r["features"]["price_oi_state"] is not None for r in records),
            "first_live_change":next((r["candle_time_utc"] for r in records if r["source"]=="tradingview_binance_usdm_htf" and r["features"]["oi_change"] is not None),None),
            "oi_reasons":dict(Counter(r["oi_metadata"]["null_reason"] or "validated" for r in records)),
            "sample":latest["features"]}
        if gaps or abnormal or invalid or duplicates:problems.append(tf+":raw_integrity")
    unchanged=before==protected_hashes(repo)
    if not unchanged:problems.append("protected_files_changed")
    return {"status":"FAIL" if problems else "PASS","observed_at_utc":iso(observed),
            "manifest":manifest,"registry_id":registry["registry_id"],"timeframes":summaries,"snapshot":snapshot,
            "protected_file_count":len(before),"protected_files_unchanged":unchanged,"problems":problems}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,default=Path.cwd())
    args=parser.parse_args()
    result=dry_run(args.repo.resolve())
    print(json.dumps(encode(result),sort_keys=True,indent=2))
    return 0 if result["status"]=="PASS" else 1


if __name__=="__main__":
    raise SystemExit(main())

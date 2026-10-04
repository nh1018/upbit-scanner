"""Versioned, evidence-backed OI contracts. No physical unit inference."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlparse,parse_qs
from btc_anytime.integrity import DURATIONS,utc_ms

DEFAULT_REGISTRY = Path(__file__).parent/"registries/oi_registry_v1.json"
REQUIRED = ("contract_id","contract_schema_version","provider","instrument","symbol","market_type",
            "timeframe","oi_field","source_field","basis","observation_timestamp",
            "observation_timestamp_semantics","source_period_start_semantics","source_period_end_semantics",
            "source_schema_version","validation_status","unit_status","evidence","code_anchors")


def identity(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()


def text_hash(raw):
    """Explicit UTF-8 text evidence identity survives Git CRLF/LF checkout conversion."""
    return sha256(raw.replace(b"\r\n",b"\n")).hexdigest()


def _evidence(repo,ref):
    path=(repo/ref["file"]).resolve()
    if not path.is_relative_to(repo.resolve()):raise ValueError("evidence outside repository")
    raw=path.read_bytes()
    if ref.get("hash_basis")!="utf8_text_lf" or text_hash(raw)!=ref["sha256"]:raise ValueError("evidence hash mismatch")
    return json.loads(raw)


def load_registry(repo, path=None):
    registry=json.loads((path or DEFAULT_REGISTRY).read_text(encoding="utf-8"))
    if registry.get("registry_schema_version")!="oi-registry-v1":raise ValueError("registry schema")
    if registry.get("registry_id")!=identity({k:v for k,v in registry.items() if k!="registry_id"}):
        raise ValueError("registry identity mismatch")
    registered_at=utc_ms(registry["recorded_at_utc"])
    active=[];seen=set()
    for original in registry["contracts"]:
        c=deepcopy(original)
        if any(not c.get(k) for k in REQUIRED):raise ValueError("incomplete OI contract")
        if c["contract_id"] in seen:raise ValueError("duplicate contract ID")
        seen.add(c["contract_id"])
        if c["timeframe"] not in DURATIONS:raise ValueError("unsupported OI timeframe")
        for anchor in c["code_anchors"]:
            path=(repo/anchor["file"]).resolve()
            if not path.is_relative_to(repo.resolve()) or anchor.get("hash_basis")!="utf8_text_lf" or text_hash(path.read_bytes())!=anchor["sha256"]:
                raise ValueError("contract code anchor changed")
        if c["validation_status"]=="UNRESOLVED_UNIT":
            if c.get("unit") is not None or c["unit_status"]!="unavailable":raise ValueError("unresolved unit must stay unavailable")
            continue
        if c["validation_status"]!="VALIDATED" or c["unit_status"]!="validated" or not c.get("unit"):
            raise ValueError("invalid contract validation state")
        evidence=[_evidence(repo,r) for r in c["evidence"]]
        if c["provider"]=="tradingview":
            if c["basis"]!="confirmed_oi_bar_close_boundary" or c["source_schema_version"]!="btc-anytime-htf-v1":raise ValueError("unsupported TV scope")
            if not any(e.get("source_url")=="https://www.tradingview.com/symbols/BTCUSDT.P_OI/?exchange=BINANCE" and
                       e.get("symbol_info",{}).get("resolved_symbol")==c["service_symbol"] and
                       e["symbol_info"].get("provider_id")=="binance" and
                       e["symbol_info"].get("currency_code")==e["symbol_info"].get("currency")==c["unit"] for e in evidence):
                raise ValueError("exact TV symbol unit evidence missing")
        elif c["provider"]=="binance":
            if not any(e.get("claim_type")=="official_field_unit_declaration" and
                       urlparse(e.get("source_url","")).hostname in ("developers.binance.com","fapi.binance.com") and
                       e.get("field")=="sumOpenInterest" and e.get("symbol")==c["symbol"] and e.get("unit")==c["unit"] for e in evidence):
                raise ValueError("Binance field-specific unit evidence missing")
        else:raise ValueError("unverified provider")
        c["validation_available_at_ms"]=registered_at
        active.append(c)
    scopes=[(c["provider"],c["instrument"],c["timeframe"],c["basis"]) for c in active]
    if len(scopes)!=len(set(scopes)):raise ValueError("ambiguous OI scopes")
    return registry,active


def contract_matches(row,tf,contract):
    """Legacy unit-test contracts keep their existing API; versioned contracts are strict."""
    if not contract.get("contract_schema_version"):return True
    c=contract
    if (c.get("contract_schema_version")!="oi-contract-v1" or c.get("validation_status")!="VALIDATED" or
        c.get("unit_status")!="validated" or c.get("timeframe")!=tf or c.get("oi_field")!="oi" or
        row.get("symbol")!=c["symbol"] or row.get("market")!=c["market_type"]):return False
    if c["provider"]=="tradingview":
        return (row.get("source")==c["required_source"] and row.get("schema_version")==c["source_schema_version"] and
                row.get("oi_time_basis")==c["basis"] and bool(row.get("received_at_utc")))
    if c["provider"]=="binance":
        url=urlparse(row.get("oi_source_url",""));query=parse_qs(url.query)
        return (url.scheme=="https" and url.hostname=="fapi.binance.com" and url.path=="/futures/data/openInterestHist" and
                query.get("symbol")==[c["symbol"]] and query.get("period")==[tf] and
                bool(re.fullmatch(r"[0-9a-f]{64}",row.get("oi_source_response_sha256",""))))
    return False

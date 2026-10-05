"""Pure Feature V1 interpretation. No fetching, persistence, orders or history."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal as D, InvalidOperation, localcontext, ROUND_HALF_EVEN

from .contracts import DURATIONS, MAPPING_STATES
from . import feature_contracts as F
from .trend_contracts import ALGORITHM_VERSION, SCHEMA_VERSION, PARAMETER_VERSION, PARAMETER_SHA256, PARAMETERS as P

NUMERIC = set(F.FEATURES) - {"confirmed_pivot_high", "confirmed_pivot_low", "high_structure", "low_structure",
                           "breakout20", "breakdown20", "ema20_upward_recross"}
BOOLEANS = {"breakout20", "breakdown20", "ema20_upward_recross"}

def _ms(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("nonnegative integer clock required")
    return value

def _iso(ms):
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms)).isoformat().replace("+00:00", "Z")

def _number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, D)):
        raise ValueError("finite decimal required")
    try:
        result = D(value)
    except (InvalidOperation, ValueError):
        raise ValueError("finite decimal required") from None
    if not result.is_finite():
        raise ValueError("finite decimal required")
    return result

def _measurements(value):
    if isinstance(value, dict):
        return {k: _measurements(v) for k, v in value.items() if k != "observed_at_ms"}
    return value

def feature_hash(snapshot):
    """Recheck the existing Feature measurement contract without changing it."""
    m = snapshot["metadata"]
    return F.digest({"features": _measurements(snapshot["features"]), "readiness": snapshot["readiness"],
                     "algorithm": F.ALGORITHM_VERSION, "parameter": F.PARAMETER_SHA256,
                     "input": m["input_sha256"], "cutoff": m["source_cutoff_ms"]})

def _reference(ref, close, now):
    # Geometry is confirmed at p+2 close, not at the pivot's historical open.
    return (isinstance(ref, dict) and isinstance(ref.get("pivot_open_ms"), int)
            and isinstance(ref.get("confirmation_boundary_ms"), int)
            and ref["pivot_open_ms"] < ref["confirmation_boundary_ms"] <= close
            and _ms(ref.get("observed_at_ms")) <= now)

def _view(snapshot, provider, instrument, tf, cutoff, now):
    result = {"values": {}, "metadata": (snapshot or {}).get("metadata", {}), "reasons": [], "contract_error": False}
    if not snapshot or "metadata" not in snapshot:
        result["reasons"].append("NO_FEATURE_SNAPSHOT")
        return result
    def fail(reason, contract=False):
        result["reasons"].append(reason)
        result["contract_error"] = contract
        return result
    if any(snapshot.get(k) != v for k, v in {
        "schema_version": F.SCHEMA_VERSION, "algorithm_version": F.ALGORITHM_VERSION,
        "parameter_version": F.PARAMETER_VERSION, "parameter_sha256": F.PARAMETER_SHA256}.items()):
        return fail("FEATURE_VERSION_MISMATCH", True)
    try:
        if snapshot.get("measurement_sha256") != feature_hash(snapshot):
            return fail("FEATURE_HASH_MISMATCH", True)
        m = snapshot["metadata"]
        if not any(status == "READY" for status in snapshot["readiness"].values()):
            return fail("SOURCE_UNAVAILABLE:"+",".join(sorted(set(snapshot["readiness"].values()))))
        if m.get("source_status") not in ("AVAILABLE", "INSUFFICIENT_DATA", "INCOMPLETE_COVERAGE"):
            return fail("SOURCE_UNAVAILABLE")
        if (m.get("provider"), m.get("instrument"), m.get("timeframe")) != (provider, instrument, tf):
            return fail("SOURCE_IDENTITY_MISMATCH", True)
        if m.get("source_cutoff_ms") != cutoff:
            return fail("SOURCE_CUTOFF_MISMATCH")
        duration = DURATIONS[tf]
        close = _ms(m["source_candle_close_ms"])
        if close != cutoff // duration * duration or _ms(m["source_candle_open_ms"]) != close-duration:
            return fail("STALE_OR_INVALID_BOUNDARY")
        received, generated = _ms(m["source_received_at_ms"]), _ms(m["feature_generated_at_ms"])
        if not cutoff <= generated <= now or not received <= generated:
            return fail("FUTURE_OR_INVALID_AVAILABILITY")
        if not m.get("evidence") or not m.get("input_sha256"):
            return fail("MISSING_SOURCE_EVIDENCE")
        clocks = []
        for ev in m["evidence"]:
            dt = datetime.fromisoformat(ev["received_at_utc"].replace("Z", "+00:00"))
            if dt.tzinfo is None or dt.utcoffset() != timedelta(0) or not ev.get("url") or len(ev.get("response_sha256", "")) != 64:
                return fail("INVALID_SOURCE_EVIDENCE")
            delta = dt - datetime(1970, 1, 1, tzinfo=timezone.utc)
            clocks.append(delta.days*86400000+delta.seconds*1000+delta.microseconds//1000)
        if max(clocks) != received:
            return fail("SOURCE_CLOCK_MISMATCH")
        values = {}
        for key in F.FEATURES:
            value = snapshot["features"][key]
            if snapshot["readiness"][key] != "READY":
                continue
            if value is None:
                return fail("READY_NULL", True)
            if key in NUMERIC:
                value = _number(value)
                if key in ("atr14", "atr_pct", "quote_recent3_vs_prior20", "quote_ratio_median20", "post_peak_quote_ratio", "bars_since_swing_high") and value < 0:
                    return fail("INVALID_FEATURE_VALUE", True)
            elif key in BOOLEANS:
                if not isinstance(value, bool):
                    return fail("INVALID_FEATURE_VALUE", True)
            elif key == "high_structure" and value not in P["structure"]["high"]:
                return fail("INVALID_FEATURE_VALUE", True)
            elif key == "low_structure" and value not in P["structure"]["low"]:
                return fail("INVALID_FEATURE_VALUE", True)
            elif key.startswith("confirmed_pivot_") and not _reference(value, close, now):
                return fail("UNCONFIRMED_OR_FUTURE_PIVOT", True)
            values[key] = value
        if values.get("breakout20") is True and values.get("breakdown20") is True:
            return fail("CONTRADICTORY_STRUCTURE", True)
        # Event-derived context cannot be used without its actual confirmed anchors.
        swing = m.get("swing_anchor")
        swing_ok = False
        if swing:
            swing_ok = (_ms(swing["observed_at_ms"]) <= now and all(
                _reference(dict(swing[k], observed_at_ms=swing["observed_at_ms"]), close, now) for k in ("high", "low"))
                and swing["low"]["pivot_open_ms"] < swing["high"]["pivot_open_ms"])
        if not swing_ok:
            for key in ("swing_retracement_close", "peak_to_close_atr", "bars_since_swing_high", "post_peak_quote_ratio"):
                values.pop(key, None)
        if "confirmed_pivot_low" not in values:
            values.pop("distance_to_confirmed_low_atr", None)
        breakout = m.get("last_breakout")
        if breakout and (not _ms(breakout["candle_open_ms"]) + duration == _ms(breakout["confirmation_boundary_ms"])
                         or breakout["confirmation_boundary_ms"] > close or _ms(breakout["observed_at_ms"]) > now):
            return fail("INVALID_BREAKOUT_REFERENCE", True)
        result["values"] = values
        return result
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return fail("INVALID_FEATURE_ENVELOPE", True)

def _clip(x, low=D(-1), high=D(1)):
    return min(high, max(low, x))

def groups(v):
    """A/S/M/P; unavailable is excluded, never coerced to zero."""
    a = s = m = p = None
    vol = v.get("atr_pct")
    keys = ("ema20_to_ema50_pct", "close_to_ema50_pct", "ema20_slope3_pct", "ema50_slope3_pct")
    if vol is not None and vol > 0 and all(k in v for k in keys):
        scales = [D(1), D(1), *P["alignment"]["slope_scales"]]
        a = sum((w*_clip(v[k]/(scale*vol)) for w, k, scale in zip(P["alignment"]["weights"], keys, scales)), D(0))
    if all(k in v for k in ("high_structure", "low_structure", "breakout20", "breakdown20")):
        s = sum((w*n for w,n in zip(P["structure"]["pivot_weights"],
                  (P["structure"]["high"][v["high_structure"]], P["structure"]["low"][v["low_structure"]]))), D(0))
        if v["breakout20"]: s = max(s, P["structure"]["breakout_floor"])
        if v["breakdown20"]: s = min(s, P["structure"]["breakdown_ceiling"])
    if vol is not None and vol > 0 and "return3_pct" in v:
        m = _clip(v["return3_pct"]/(P["momentum_atr_scale"]*vol))
    if "return3_pct" in v and "quote_recent3_vs_prior20" in v:
        r, q = v["return3_pct"], v["quote_recent3_vs_prior20"]
        p = (_clip((q-1)/P["participation_scale"]) if r > 0 else
             -_clip((q-1)/P["participation_scale"], D(0), D(1)) if r < 0 else D(0))
    return {"A": a, "S": s, "M": m, "P": p}

def _tf_score(g, tf):
    required = P["minimum"]["primary_groups" if tf == "4h" else "other_groups"]
    if any(g[k] is None for k in required): return None
    denominator = sum((w for k,w in P["group_weights"].items() if g[k] is not None), D(0))
    return sum((w*g[k] for k,w in P["group_weights"].items() if g[k] is not None), D(0))/denominator

def _coverage(gs):
    return sum((w*sum((gw for k,gw in P["group_weights"].items() if gs[tf][k] is not None), D(0))
                for tf,w in P["tf_weights"].items()), D(0))

def data_gate(primary_ready, tf_weight, coverage, contract_ok=True):
    return (contract_ok and primary_ready and tf_weight >= P["minimum"]["tf_weight"]
            and coverage >= P["minimum"]["coverage"])

def _damage(v):
    return ((v.get("breakdown20") is True and (v.get("low_structure") == "LL" or
             (v.get("distance_to_confirmed_low_atr") is not None and v["distance_to_confirmed_low_atr"] < 0)))
            or (v.get("high_structure") == "LH" and v.get("low_structure") == "LL"
                and v.get("close_to_ema50_pct") is not None and v["close_to_ema50_pct"] < 0))

def _ge(value, threshold): return value is not None and value >= threshold
def _le(value, threshold): return value is not None and value <= threshold
def _range(value, bounds): return value is not None and bounds[0] <= value <= bounds[1]

def _state(score, gs, vs, damage):
    a, s, m, p = (gs["4h"][k] for k in ("A", "S", "M", "P"))
    h, f = vs["1h"]["values"], vs["4h"]["values"]
    rules, ctx = P["state"], P["context"]
    tags = []
    if _tf_score(gs["1h"], "1h") is None: tags.append("SHORT_CONTEXT_UNAVAILABLE")
    if h.get("return3_pct") is not None and h["return3_pct"] < 0 and "swing_retracement_close" not in h:
        tags.append("CORRECTION_CONTEXT_UNAVAILABLE")
    if p is not None and p < 0: tags.append("PARTICIPATION_WEAK")
    if _ge(gs["1h"]["M"], D(1)) and not _ge(a, rules["no_uptrend_A"]): tags.append("SHORT_TERM_SPIKE")
    background = _ge(gs["1d"]["A"], rules["macro_background_A"]) or f.get("ema20_to_ema50_pct", D(0)) > 0
    weakening = _le(a, D(0)) and ((s is not None and s < 0) or (m is not None and m < 0)) and background
    healthy = (_ge(a, rules["healthy"]["A"]) and _ge(s, rules["healthy"]["S"])
               and f.get("breakdown20") is False and _ge(f.get("distance_to_confirmed_low_atr"), rules["healthy"]["low_distance"]) and not damage)
    geometry = (healthy and _range(h.get("bars_since_swing_high"), ctx["age"])
                and _ge(h.get("distance_to_confirmed_low_atr"), D(0)) and h.get("breakdown20") is False)
    retrace = h.get("swing_retracement_close")
    recovery = (geometry and retrace is not None and 0 < retrace <= ctx["recovery_retracement_max"]
                and h.get("return1_pct", D(0)) > 0 and h.get("return3_acceleration_pp", D(0)) > 0
                and (h.get("ema20_upward_recross") is True or h.get("breakout20") is True)
                and _ge(h.get("quote_ratio_median20"), ctx["recovery_quote_ratio"]))
    pullback = (geometry and _range(retrace, ctx["pullback_retracement"])
                and _range(h.get("peak_to_close_atr"), ctx["pullback_peak_atr"]) and h.get("return3_pct", D(0)) < 0)
    c, b = rules["continuation"], rules["building"]
    predicates = {
        "INSUFFICIENT_EVIDENCE": score is None,
        "TREND_WEAKENING": damage or weakening,
        "REACCELERATION": recovery, "PULLBACK_WATCH": pullback,
        "TREND_CONTINUATION": (_ge(score, c["score"]) and _ge(a,c["A"]) and _ge(s,c["S"]) and _ge(m,c["M"])
                               and not damage and (p is None or p >= c["P"])),
        "TREND_BUILDING": (_ge(score,b["score"]) and _ge(a,b["A"]) and _ge(s,b["S"])
                           and (f.get("ema20_to_ema50_pct", D(0)) > 0 or f.get("breakout20") is True)),
        "NO_UPTREND": a is not None and a < rules["no_uptrend_A"], "MIXED": True,
    }
    state = next(k for k in rules["priority"] if predicates[k])
    if state == "PULLBACK_WATCH" and _le(h.get("post_peak_quote_ratio"), ctx["contraction_quote_ratio"]):
        tags.append("PARTICIPATION_CONTRACTION")
    return {"primary": state, "secondary_tags": tags, "reason_codes": ["STATE_"+state],
            "recovery_basis": ctx["recovery_basis"] if state == "REACCELERATION" else None,
            "prior_state_transition_verified": ctx["prior_state_transition_verified"],
            "predicate_results": predicates}

def _chase(views):
    scores, reasons = {}, []
    for tf in P["chase"]["weights"]:
        v, meta = views[tf]["values"], views[tf]["metadata"]
        if not all(k in v for k in ("close_to_ema20_atr", "price_change3_atr")):
            scores[tf] = None; reasons.append(tf+":EXTENSION_UNAVAILABLE"); continue
        loc = v["close_to_ema20_atr"]
        event = meta.get("last_breakout")
        if "distance_to_last_breakout_atr" in v and event:
            age = (meta["source_candle_open_ms"]-event["candle_open_ms"])//DURATIONS[tf]
            if 0 <= age <= P["chase"]["breakout_max_age"]:
                loc = max(loc, v["distance_to_last_breakout_atr"])
            else: reasons.append(tf+":BREAKOUT_REFERENCE_EXPIRED")
        scores[tf] = 100*max(_clip(loc/P["chase"]["scale_atr"], D(0), D(1)),
                            _clip(v["price_change3_atr"]/P["chase"]["scale_atr"], D(0), D(1)))
    coverage = sum((w for tf,w in P["chase"]["weights"].items() if scores[tf] is not None), D(0))
    score = (sum((w*scores[tf] for tf,w in P["chase"]["weights"].items() if scores[tf] is not None), D(0))/coverage
             if scores[P["chase"]["required_tf"]] is not None else None)
    category = ("UNAVAILABLE" if score is None else "HIGH" if score >= P["chase"]["high"] else
                "MEDIUM" if score >= P["chase"]["medium"] else "LOW")
    return {"score": score, "category": category, "tf_scores": scores, "coverage": coverage, "reasons": reasons}

def _binance(mapping, gs, views, up_gs, up_damage):
    status = mapping.get("status", "API_UNAVAILABLE")
    rule, primary = P["binance"], gs["4h"]
    result = {"mapping_status": status, "confirmation": "UNAVAILABLE", "diagnostic_groups": gs,
              "coverage": _coverage(gs), "reasons": []}
    if status not in ("VERIFIED", "UNVERIFIED") or any(primary[k] is None for k in ("A", "M")):
        result["reasons"] = ["COUNTERPART_OR_CORE_UNAVAILABLE"]; return result
    a, s, m = primary["A"], primary["S"], primary["M"]
    conflict = _damage(views["4h"]["values"]) or (a <= rule["conflict_A"] and
                ((s is not None and s < 0) or m <= rule["conflict_M"]))
    confirmed = (not up_damage and _ge(up_gs["4h"]["A"],rule["upbit_background_A"])
                 and _ge(_tf_score(primary,"4h"),rule["confirmed_G"]) and a >= rule["confirmed_A"]
                 and _ge(s,rule["confirmed_S"]) and all(_ge(gs["1h"][k],D(0)) for k in ("A","M")))
    diagnostic = ("CONFLICTING" if conflict else "CONFIRMED" if confirmed else
                  "SUPPORTIVE" if a >= rule["supportive_A"] and m >= 0 else "NEUTRAL")
    result["directional_diagnostic"] = diagnostic
    if status == "UNVERIFIED":
        result["confirmation"] = "UNVERIFIED"; result["reasons"] = ["MAPPING_UNVERIFIED_NO_PROMOTION"]
    elif (not isinstance(mapping.get("identity_evidence"),dict)
          or mapping["identity_evidence"].get("symbol") != mapping.get("symbol")
          or not mapping["identity_evidence"].get("registry_version")
          or not mapping["identity_evidence"].get("evidence_reference")):
        result["reasons"] = ["VERIFIED_MAPPING_EVIDENCE_MISSING"]
    else:
        result["confirmation"] = diagnostic; result["reasons"] = ["SPOT_"+diagnostic]
    return result

def evaluate(bundle, instrument, observation_time_ms, price_observation=None):
    """Interpret one actual available Feature bundle; return a memory-only envelope."""
    now = _ms(observation_time_ms)
    if not isinstance(instrument,str) or not instrument.startswith("KRW-"):
        raise ValueError("Upbit KRW instrument required")
    with localcontext() as context:
        context.prec, context.rounding = P["decimal"]["precision"], ROUND_HALF_EVEN
        return F.canonical(_evaluate(bundle, instrument, now, price_observation))

def _evaluate(bundle, instrument, now, price):
    if bundle.get("schema_version") != F.SCHEMA_VERSION:
        raise ValueError("Feature bundle schema mismatch")
    mapping = bundle.get("mapping", {})
    if mapping.get("status") not in MAPPING_STATES:
        raise ValueError("unknown mapping state")
    snapshots = bundle.get("upbit", {})
    cutoffs = {s.get("metadata", {}).get("source_cutoff_ms") for s in snapshots.values()}
    if len(cutoffs) != 1: raise ValueError("one fixed source cutoff required")
    cutoff = _ms(next(iter(cutoffs)))
    if cutoff > now: raise ValueError("future source cutoff")
    views = {tf: _view(snapshots.get(tf),"UPBIT",instrument,tf,cutoff,now) for tf in P["tf_weights"]}
    gs = {tf: groups(v["values"]) for tf,v in views.items()}
    scores = {tf: _tf_score(g,tf) for tf,g in gs.items()}
    weight = sum((w for tf,w in P["tf_weights"].items() if scores[tf] is not None),D(0))
    coverage = _coverage(gs)
    primary = scores["4h"] is not None
    contract = not any(v["contract_error"] for v in views.values())
    gate = data_gate(primary,weight,coverage,contract)
    signed = sum((w*scores[tf] for tf,w in P["tf_weights"].items() if scores[tf] is not None),D(0))/weight if gate else None
    before = 100*max(D(0),signed) if signed is not None else None
    damage = _damage(views["4h"]["values"])
    score = min(before,P["damage"]["cap"]) if damage and before is not None else before
    state = _state(score,gs,views,damage)
    reasons = state["reason_codes"]
    if not primary: reasons.append("PRIMARY_CORE_UNAVAILABLE")
    if weight < P["minimum"]["tf_weight"]: reasons.append("TF_WEIGHT_BELOW_MINIMUM")
    if coverage < P["minimum"]["coverage"]: reasons.append("EVIDENCE_COVERAGE_BELOW_MINIMUM")
    if not contract: reasons.append("FEATURE_CONTRACT_REJECTED")
    if damage: reasons.append("PRIMARY_STRUCTURAL_DAMAGE_CAP")
    for tf,v in views.items():
        reasons.extend(tf+":"+r for r in v["reasons"])
        reasons.extend(tf+":NEGATIVE_"+k for k,g in gs[tf].items() if g is not None and g < 0)
    spot = {tf: _view(bundle.get("binance_spot",{}).get(tf),"BINANCE_SPOT",mapping.get("symbol"),tf,cutoff,now) for tf in P["tf_weights"]}
    spot_gs = {tf:groups(v["values"]) for tf,v in spot.items()}
    confirmation = _binance(mapping,spot_gs,spot,gs,damage)
    confirmation["reasons"].extend(tf+":"+r for tf,v in spot.items() for r in v["reasons"])
    if price is not None:
        if (price.get("instrument") != instrument or price.get("provider") != "UPBIT"
            or _number(price["price"]) <= 0 or _ms(price["received_at_ms"]) > now
            or not price.get("source_reference")):
            raise ValueError("invalid actual ticker observation")
        _ms(price["source_time_ms"])
    category = ("LOW" if not primary or not contract or coverage < P["confidence"]["medium"] else
                "HIGH" if coverage >= P["confidence"]["high"] else "MEDIUM")
    result = {
        "observation_time_utc": _iso(now), "instrument": instrument, "price_observation": price,
        "source_cutoff": {"time_ms":cutoff,"time_utc":_iso(cutoff)},
        "trend": {"score":score,"signed_score":signed,"score_before_gate":before,"tf_scores":scores,
                  "group_scores":gs,"available_masks":{tf:{k:g is not None for k,g in group.items()} for tf,group in gs.items()},
                  "available_tf_weight":weight,"data_gate_passed":gate,"primary_damage":damage,
                  "eligible":gate and not damage and state["primary"] in
                  ("TREND_BUILDING","TREND_CONTINUATION","PULLBACK_WATCH","REACCELERATION")},
        "state":state,"chase":_chase(views),"binance":confirmation,
        "data":{"coverage":coverage,"category":category,
                "tf_readiness":{tf:{"eligible":scores[tf] is not None,"ready_feature_count":len(v["values"]),
                                    "reasons":v["reasons"]} for tf,v in views.items()},
                "source_status":{tf:v["metadata"].get("source_status","UNAVAILABLE") for tf,v in views.items()}},
        "versions":{"engine_algorithm":ALGORITHM_VERSION,"engine_schema":SCHEMA_VERSION,
                    "engine_parameter":PARAMETER_VERSION,"engine_parameter_sha256":PARAMETER_SHA256,
                    "feature_algorithm":F.ALGORITHM_VERSION,"feature_schema":F.SCHEMA_VERSION,
                    "feature_parameter":F.PARAMETER_VERSION,"feature_parameter_sha256":F.PARAMETER_SHA256},
        "source": {provider:{tf:{"measurement_sha256":snap.get("measurement_sha256"),
                                "metadata":snap.get("metadata"),"readiness":snap.get("readiness")}
                            for tf,snap in bundle.get(provider,{}).items()} for provider in ("upbit","binance_spot")},
    }
    result["source"]["price_clock_warnings"] = (["TICKER_SOURCE_CLOCK_AFTER_LOCAL_RECEIPT"]
        if price is not None and price["source_time_ms"] > price["received_at_ms"] else [])
    result["observation_id"] = F.digest(result)
    return result

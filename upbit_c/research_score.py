"""Pure Upbit C V0 research score. Never publishes candidates or orders.

Initial thresholds are hypotheses, not fitted parameters or profitability evidence.
Input is a mapping of timeframe -> upbit_c.evidence.observe output.
"""
from decimal import Decimal, InvalidOperation

from upbit_b.feature_contracts import digest

VERSION = "upbit-c-research-score-0"
PARAMETERS = {
    "weights": {"selloff": 20, "capitulation": 20, "deceleration": 20,
                "defense": 25, "recovery": 15},
    "min_selloff_atr": "1.5",
    "capitulation_ratio_full": "3",
    "acceleration_full_atr": "1",
    "recovery_quote_full": "2",
}
PARAMETER_SHA256 = digest(PARAMETERS)
D = Decimal


def _number(values, key):
    raw = values.get(key)
    if raw is None or isinstance(raw, bool):
        raise ValueError("missing mandatory C evidence: " + key)
    try:
        value = D(raw)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("invalid C evidence: " + key) from None
    if not value.is_finite():
        raise ValueError("nonfinite C evidence: " + key)
    return value


def _clip(value):
    return min(D(1), max(D(0), value))


def evaluate(observations):
    """Research-only numerical fit. No candidate/entry classification is emitted."""
    if not isinstance(observations, dict) or set(observations) != {"1h", "4h", "1d"}:
        raise ValueError("three C timeframe observations required")
    vectors = {}
    for tf in ("1h", "4h", "1d"):
        item = observations[tf]
        if not isinstance(item, dict) or item.get("schema_version") != "upbit-c-rebound-evidence-0":
            raise ValueError("unrecognized C evidence")
        if item.get("timeframe") != tf or item.get("activation") != "RESEARCH_ONLY":
            raise ValueError("wrong timeframe or activation")
        if item.get("score") is not None or item.get("candidate") != "NOT_EVALUATED":
            raise ValueError("research input cannot contain active score/candidate")
        if not item.get("source_input_sha256") or not item.get("source_measurement_sha256"):
            raise ValueError("missing source hashes")
        if not isinstance(item.get("source_cutoff_ms"), int) or isinstance(item["source_cutoff_ms"], bool):
            raise ValueError("invalid source clock")
        values = item.get("values")
        if not isinstance(values, dict) or item.get("missing"):
            raise ValueError("all C research fields must be READY")
        for group in ("selloff", "capitulation", "deceleration", "defense", "recovery"):
            if item.get("groups", {}).get(group, {}).get("available") != {"selloff": 4, "capitulation": 3, "deceleration": 2, "defense": 3, "recovery": 3}[group] or item["groups"][group].get("total") != {"selloff": 4, "capitulation": 3, "deceleration": 2, "defense": 3, "recovery": 3}[group]:
                raise ValueError("incomplete C group")
        atr = _number(values, "atr_pct")
        if atr <= 0:
            raise ValueError("ATR must be positive")
        r3 = _number(values, "return3_pct")
        r5 = _number(values, "return5_pct")
        r10 = _number(values, "return10_pct")
        quote = max(_number(values, "quote_ratio_ma20"), _number(values, "quote_ratio_median20"))
        location = _number(values, "close_location")
        body = _number(values, "signed_body_ratio")
        accel = _number(values, "return3_acceleration_pp")
        r1 = _number(values, "return1_pct")
        low_distance = _number(values, "distance_to_confirmed_low_atr")
        recent = _number(values, "quote_recent3_vs_prior20")
        low = values.get("low_structure")
        breakdown = values.get("breakdown20")
        recross = values.get("ema20_upward_recross")
        if low not in ("HL", "LL", "EQ") or type(breakdown) is not bool or type(recross) is not bool:
            raise ValueError("invalid C structure flags")
        if not D(0) <= location <= D(1) or not D(-1) <= body <= D(1) or min(quote, recent) < 0:
            raise ValueError("invalid bounded C evidence")
        # Group strengths are independently interpretable; high selloff alone cannot qualify.
        selloff = _clip(max(-r3 / atr, -r5 / (atr * 2), -r10 / (atr * 3)) / 3)
        capitulation = (_clip((quote - 1) / 2) + _clip(1 - location)) / 2
        deceleration = (_clip(accel / atr) + _clip((r1 / atr + 1) / 2)) / 2
        defense = (_clip((low_distance + 1) / 2) + (D(1) if low == "HL" else D("0.5") if low == "EQ" else D(0))) / 2
        if breakdown:
            defense = D(0)
        recovery = (_clip((body + 1) / 2) + D(int(recross)) + _clip((recent - 1) / 1)) / 3
        vectors[tf] = {
            "groups": {"selloff": selloff, "capitulation": capitulation,
                       "deceleration": deceleration, "defense": defense, "recovery": recovery},
            "selloff_observed": min(r3, r5, r10) <= -atr * D(PARAMETERS["min_selloff_atr"]),
            "low_intact": not breakdown and low_distance >= 0 and low != "LL",
            "recovery_observed": body > 0 and (recross or recent >= D("1.2")),
            "deceleration_observed": accel > 0,
        }
    weights = PARAMETERS["weights"]
    # 4h is the structural anchor; 1h timing and 1d context cannot override 4h failure.
    tf_weights = {"1h": D("0.25"), "4h": D("0.60"), "1d": D("0.15")}
    group_scores = {
        g: sum(tf_weights[tf] * vectors[tf]["groups"][g] for tf in tf_weights)
        for g in weights
    }
    score = sum(group_scores[g] * weights[g] for g in weights)
    gates = {
        "selloff": vectors["4h"]["selloff_observed"] and (vectors["1h"]["selloff_observed"] or vectors["1d"]["selloff_observed"]),
        "low_defense": vectors["4h"]["low_intact"] and vectors["1h"]["low_intact"],
        "deceleration": vectors["4h"]["deceleration_observed"] or vectors["1h"]["deceleration_observed"],
        "recovery": vectors["1h"]["recovery_observed"],
    }
    return {
        "schema_version": VERSION, "parameter_sha256": PARAMETER_SHA256,
        "activation": "RESEARCH_ONLY", "score": str(score),
        "group_scores": {k: str(v) for k, v in group_scores.items()},
        "gates": gates, "research_setup": "PASS" if all(gates.values()) else "BLOCKED",
        "candidate": "NOT_EVALUATED", "entry_signal": None,
    }

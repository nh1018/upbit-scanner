"""Non-trading C phase diagnostics. Signs are descriptive, not optimized thresholds."""
from decimal import Decimal

SCHEMA_VERSION = "upbit-c-phase-diagnostics-0"
REQUIRED = {
    "selloff": ("return3_pct", "return5_pct"),
    "participation": ("quote_ratio_ma20",),
    "deceleration": ("return3_acceleration_pp",),
    "defense": ("distance_to_confirmed_low_atr", "breakdown20"),
    "recovery": ("signed_body_ratio", "ema20_upward_recross"),
}


def diagnose(evidence):
    if evidence.get("schema_version") != "upbit-c-rebound-evidence-0" or evidence.get("activation") != "RESEARCH_ONLY":
        raise ValueError("verified C research evidence required")
    v = evidence["values"]
    missing = {group: [field for field in fields if v.get(field) is None]
               for group, fields in REQUIRED.items()}
    if any(missing.values()):
        return {"schema_version": SCHEMA_VERSION, "market": evidence["market"],
                "timeframe": evidence["timeframe"], "source_cutoff_ms": evidence["source_cutoff_ms"],
                "state": "INSUFFICIENT_EVIDENCE", "observations": {},
                "missing_required": {k: x for k, x in missing.items() if x},
                "score": None, "candidate": "NOT_EVALUATED", "activation": "RESEARCH_ONLY"}
    obs = {
        "recent_3bar_loss": Decimal(v["return3_pct"]) < 0,
        "recent_5bar_loss": Decimal(v["return5_pct"]) < 0,
        "above_prior20_quote_average": Decimal(v["quote_ratio_ma20"]) > 1,
        "three_bar_momentum_improving": Decimal(v["return3_acceleration_pp"]) > 0,
        "close_above_confirmed_low": Decimal(v["distance_to_confirmed_low_atr"]) > 0,
        "new_20bar_breakdown": v["breakdown20"],
        "positive_candle_body": Decimal(v["signed_body_ratio"]) > 0,
        "ema20_upward_recross": v["ema20_upward_recross"],
    }
    # Multiple independent observations are necessary even to call a state
    # "research recovery evidence"; no state is an entry signal.
    if obs["recent_3bar_loss"] and obs["recent_5bar_loss"]:
        state = "DECLINING_WITH_RECOVERY_EVIDENCE" if (
            obs["three_bar_momentum_improving"] and obs["close_above_confirmed_low"]
            and not obs["new_20bar_breakdown"]
            and (obs["positive_candle_body"] or obs["ema20_upward_recross"])
        ) else "DECLINING_UNCONFIRMED"
    else:
        state = "NO_RECENT_MULTIWINDOW_SELLOFF"
    return {"schema_version": SCHEMA_VERSION, "market": evidence["market"],
            "timeframe": evidence["timeframe"], "source_cutoff_ms": evidence["source_cutoff_ms"],
            "state": state, "observations": obs, "missing_required": {},
            "score": None, "candidate": "NOT_EVALUATED", "activation": "RESEARCH_ONLY"}

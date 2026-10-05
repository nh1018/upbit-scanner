"""Versioned INITIAL HYPOTHESIS; none of these values establish profitability."""
from decimal import Decimal as D
from .feature_contracts import digest

ALGORITHM_VERSION = "upbit-b-trend-state-1"
SCHEMA_VERSION = "upbit-b-trend-state-output-1"
PARAMETER_VERSION = "initial-hypothesis-1"
PARAMETERS = {
    "tf_weights": {"1d": D(".15"), "4h": D(".65"), "1h": D(".20")},
    "group_weights": {"A": D(".40"), "S": D(".35"), "M": D(".15"), "P": D(".10")},
    "alignment": {"weights": [D(".30"), D(".25"), D(".30"), D(".15")],
                  "slope_scales": [D(".50"), D(".25")]},
    "structure": {"high": {"HH": D(1), "EQ": D(0), "LH": D(-1)},
                  "low": {"HL": D(1), "EQ": D(0), "LL": D(-1)},
                  "pivot_weights": [D(".50"), D(".50")],
                  "breakout_floor": D(".50"), "breakdown_ceiling": D("-.75")},
    "momentum_atr_scale": D(2), "participation_scale": D(".50"),
    "score": {"scale": D(100), "lower": D(0), "clip": [D(-1), D(1)]},
    "minimum": {"tf_weight": D(".80"), "coverage": D(".65"),
                "primary_groups": ["A", "S", "M"], "other_groups": ["A", "M"]},
    "damage": {"cap": D(49), "rules": [
        "breakdown20 AND (low_structure=LL OR distance_to_confirmed_low_atr<0)",
        "high_structure=LH AND low_structure=LL AND close_to_ema50_pct<0"]},
    "state": {"priority": ["INSUFFICIENT_EVIDENCE", "TREND_WEAKENING", "REACCELERATION",
                           "PULLBACK_WATCH", "TREND_CONTINUATION", "TREND_BUILDING", "NO_UPTREND", "MIXED"],
              "continuation": {"score": D(65), "A": D(".55"), "S": D(".25"), "M": D(0), "P": D("-.50")},
              "building": {"score": D(45), "A": D(".35"), "S": D("-.25")},
              "weakening": "A4<=0 AND (S4<0 OR M4<0) AND upward_background",
              "macro_background_A": D(".50"), "no_uptrend_A": D(".35"),
              "healthy": {"A": D(".55"), "S": D(0), "low_distance": D(0)}},
    "context": {"pullback_retracement": [D(".10"), D(".60")],
                "pullback_peak_atr": [D(".50"), D(3)], "age": [3, 20],
                "recovery_retracement_max": D(".60"), "recovery_quote_ratio": D("1.20"),
                "contraction_quote_ratio": D(1),
                "recovery_basis": "CURRENT_GEOMETRY_AND_RECOVERY_EVIDENCE",
                "prior_state_transition_verified": False},
    "chase": {"weights": {"1h": D(".80"), "4h": D(".20")}, "required_tf": "1h",
              "scale_atr": D(4), "breakout_max_age": 20, "medium": D(35), "high": D(70)},
    "binance": {"conflict_A": D("-.35"), "conflict_M": D("-.25"),
                "confirmed_G": D(".65"), "confirmed_A": D(".55"), "confirmed_S": D(".25"),
                "supportive_A": D(".35"), "upbit_background_A": D(".35"),
                "rules": "VERIFIED only; conflict first; confirmation needs nonnegative 1h A/M; no lead claim"},
    "confidence": {"high": D(".90"), "medium": D(".65"), "renormalize": False},
    "decimal": {"precision": 34, "rounding": "ROUND_HALF_EVEN"},
    "eligibility": "positive candidate state AND score data gate AND NOT primary_damage",
}
PARAMETER_SHA256 = digest(PARAMETERS)

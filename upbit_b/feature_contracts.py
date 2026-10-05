"""Approved initial calculation specification, not profitable strategy parameters."""
import hashlib
import json
from decimal import Decimal

ALGORITHM_VERSION = "upbit-b-features-1"
SCHEMA_VERSION = "upbit-b-feature-snapshot-1"
PARAMETER_VERSION = "initial-calculation-1"
PARAMETERS = {
    "ema_periods": [20, 50], "ema_seed": "segment_sma",
    "slope_bars": 3, "return_bars": [1, 3, 5, 10],
    "acceleration": "return3(t)-return3(t-3)",
    "atr_period": 14, "atr_seed": "14_true_ranges_excluding_segment_first",
    "pivot_left": 2, "pivot_right": 2, "pivot_ties": "exclude",
    "rolling_period": 20, "rolling_current": "exclude",
    "participation_recent": 3, "sparse": "latest_calendar_contiguous_segment",
    "decimal_precision": 34, "decimal_rounding": "ROUND_HALF_EVEN",
}
FEATURES = (
    "ema20", "ema50", "ema20_slope3_pct", "ema50_slope3_pct",
    "close_to_ema20_pct", "close_to_ema50_pct", "ema20_to_ema50_pct", "return5_pct",
    "return1_pct", "return3_pct", "return10_pct", "return3_acceleration_pp",
    "positive_return_streak", "signed_body_ratio", "close_location", "atr14", "atr_pct",
    "confirmed_pivot_high", "confirmed_pivot_low", "high_structure", "low_structure",
    "rolling_high20", "rolling_low20", "distance_to_rolling_high20_pct", "breakout20", "breakdown20",
    "quote_ma20_prior", "quote_median20_prior", "quote_ratio_ma20", "quote_ratio_median20",
    "quote_recent3_vs_prior20", "close_to_ema20_atr", "price_change3_atr",
    "swing_return_pct", "swing_retracement_close", "peak_to_close_atr", "bars_since_swing_high",
    "post_peak_quote_ratio", "distance_to_confirmed_low_atr", "distance_to_last_breakout_atr",
    "ema20_upward_recross",
)
REQUIRED = {
    "ema20": 20, "ema50": 50, "ema20_slope3_pct": 23, "ema50_slope3_pct": 53,
    "close_to_ema20_pct": 20, "close_to_ema50_pct": 50, "ema20_to_ema50_pct": 50,
    "return1_pct": 2, "return3_pct": 4, "return5_pct": 6, "return10_pct": 11,
    "return3_acceleration_pp": 7, "positive_return_streak": 2, "signed_body_ratio": 1,
    "close_location": 1, "atr14": 15, "atr_pct": 15,
    "rolling_high20": 21, "rolling_low20": 21, "distance_to_rolling_high20_pct": 21,
    "breakout20": 21, "breakdown20": 21, "quote_ma20_prior": 21,
    "quote_median20_prior": 21, "quote_ratio_ma20": 21, "quote_ratio_median20": 21,
    "quote_recent3_vs_prior20": 23, "close_to_ema20_atr": 20, "price_change3_atr": 15,
    "ema20_upward_recross": 21,
}

def canonical(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite Decimal")
        if value == 0:
            return "0"
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [canonical(v) for v in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise TypeError("canonical output forbids floats/unsupported types")

def dumps(value):
    return json.dumps(canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)

def digest(value):
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()

PARAMETER_SHA256 = digest(PARAMETERS)

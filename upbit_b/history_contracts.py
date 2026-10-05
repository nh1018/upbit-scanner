"""History operational contracts; do not modify strategy calculations."""
from datetime import datetime, timezone, timedelta
from . import feature_contracts as F
from . import trend_contracts as T
from .contracts import SCHEMA_VERSION as MARKET_SCHEMA

ALGORITHM_VERSION = "upbit-b-prospective-history-1"
SCHEMA_VERSION = "upbit-b-history-journal-1"
POLICY_VERSION = "initial-operations-1"
HOUR = 3600000
POLICY = {
    "universe":"all_upbit_krw", "hard_prefilter":False, "cycle_ms":HOUR,
    "heartbeat_hours":4, "controls_max":12,
    "sampling":"sha256(policy_hash,cycle_id,instrument)_ascending",
    "schedule_minute":7, "start_grace_minutes":20, "publish_deadline_minutes":45,
    "workflow_timeout_minutes":25, "git_push_attempts":3,
    "storage":"cycle_write_once_jsonl", "publication":"one_cycle_one_commit",
    "candidate":"engine_eligible_and_positive_state; unavailable=UNKNOWN",
    "quality_change":"group masks, timeframe readiness, confidence category",
    "transition":"same_cohort_both_valid_exact_consecutive_hour",
    "price":"observed_ticker_and_closed_1h_diagnostic; future_proxy_null",
    "decimal_precision":34,"decimal_rounding":"ROUND_HALF_EVEN",
}
POLICY_SHA256 = F.digest(POLICY)
CANDIDATE_STATES = frozenset({"TREND_BUILDING","TREND_CONTINUATION","PULLBACK_WATCH","REACCELERATION"})
BASE = ("atr_pct","ema20_to_ema50_pct","close_to_ema50_pct","ema20_slope3_pct","ema50_slope3_pct",
        "high_structure","low_structure","breakout20","breakdown20","return3_pct","quote_recent3_vs_prior20")
CHASE = ("close_to_ema20_atr","price_change3_atr","distance_to_last_breakout_atr")
CONTEXT = ("swing_retracement_close","peak_to_close_atr","bars_since_swing_high","distance_to_confirmed_low_atr",
           "post_peak_quote_ratio","return1_pct","return3_acceleration_pp","ema20_upward_recross","quote_ratio_median20")
EVENT_TYPES = frozenset({"ENTER_BUILDING","ENTER_CONTINUATION","ENTER_PULLBACK_WATCH","ENTER_REACCELERATION",
    "ENTER_WEAKENING","EXIT_B_CANDIDATE","CHASE_RISK_CHANGE","PRIMARY_DAMAGE","PRIMARY_DAMAGE_CLEARED",
    "DATA_LOST","DATA_RECOVERED","DATA_QUALITY_CHANGE","BINANCE_CONTEXT_CHANGE","BASELINE_STATE","STATE_REOBSERVED_AFTER_GAP"})

def versions(mapping_registry_version="unapproved-none"):
    return {"engine_algorithm":T.ALGORITHM_VERSION,"engine_schema":T.SCHEMA_VERSION,
        "engine_parameter":T.PARAMETER_VERSION,"engine_parameter_sha256":T.PARAMETER_SHA256,
        "feature_algorithm":F.ALGORITHM_VERSION,"feature_schema":F.SCHEMA_VERSION,
        "feature_parameter":F.PARAMETER_VERSION,"feature_parameter_sha256":F.PARAMETER_SHA256,
        "market_schema":MARKET_SCHEMA,"mapping_registry_version":mapping_registry_version,
        "history_algorithm":ALGORITHM_VERSION,"history_schema":SCHEMA_VERSION,
        "history_policy_version":POLICY_VERSION,"history_policy_sha256":POLICY_SHA256}

def integer(value):
    if isinstance(value,bool) or not isinstance(value,int) or value<0: raise ValueError("nonnegative integer clock required")
    return value

def iso(ms):
    return (datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(milliseconds=integer(ms))).isoformat().replace("+00:00","Z")

def clock(text):
    dt=datetime.fromisoformat(text.replace("Z","+00:00"))
    if dt.tzinfo is None or dt.utcoffset()!=timedelta(0): raise ValueError("UTC clock required")
    delta=dt-datetime(1970,1,1,tzinfo=timezone.utc)
    return integer(delta.days*86400000+delta.seconds*1000+delta.microseconds//1000)

def cohort_id(contract): return F.digest(contract)

def cycle_id(boundary,cohort,policy_hash=None):
    if integer(boundary)%HOUR: raise ValueError("UTC 1H boundary required")
    return F.digest(["upbit-b",policy_hash or POLICY_SHA256,cohort,boundary])

def observation_id(cycle,instrument,kind): return F.digest([cycle,instrument,kind])

def event_id(cohort,instrument,cycle,kind,previous,current):
    return F.digest([cohort,instrument,cycle,kind,previous,current])

def cycle_path(boundary):
    cycle_id(boundary,"path-validation")
    stamp=iso(boundary)
    return "output_upbit_b/v1/history/"+stamp[:10]+"/"+stamp[11:13]+".jsonl"

def check_window(boundary,started,now=None,policy=None):
    policy=policy or POLICY
    if boundary != integer(started)//HOUR*HOUR: raise ValueError("old cycle reconstruction prohibited")
    if started-boundary>policy["start_grace_minutes"]*60000: raise ValueError("start grace exceeded")
    if now is not None and (integer(now)<started or now-boundary>policy["publish_deadline_minutes"]*60000):
        raise ValueError("publication deadline exceeded or invalid clock")

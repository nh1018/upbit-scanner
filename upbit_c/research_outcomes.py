"""Post-observation gross-price research outcomes, not realized trade P&L."""
from dataclasses import asdict
from decimal import Decimal, localcontext
from upbit_b.feature_contracts import canonical, digest
from upbit_b.market_data import normalize, UPBIT, iso, DataError
from .research_history import validate_signal, HOUR
from .research_scan import candle_record

SCHEMA = "upbit-c-research-outcome-1"
DAY_HORIZONS = (1, 3, 7)


def evaluate(signal, days, candles, evidence, as_of_ms):
    validate_signal(signal)
    if days not in DAY_HORIZONS or type(days) is not int or type(as_of_ms) is not int:
        raise ValueError("invalid horizon/cutoff")
    anchor = signal["proxy_anchor_open_ms"]
    end = anchor + days * 24 * HOUR
    result = {"schema_version": SCHEMA, "activation": "RESEARCH_ONLY",
        "signal_id": signal["signal_id"], "market": signal["market"], "horizon_days": days,
        "anchor_type": "NEXT_1H_OPEN_PROXY", "anchor_open_ms": anchor,
        "endpoint_close_ms": end, "as_of_ms": as_of_ms, "status": "PENDING",
        "return_pct": None, "mfe_pct": None, "mae_pct": None,
        "anchor_price": None, "endpoint_price": None, "hmax": None, "lmin": None,
        "fees_included": False, "execution_price_claim": False,
        "reason": "FUTURE_HORIZON_NOT_COMPLETE", "source_evidence": list(evidence), "path": []}
    if as_of_ms < end:
        return result
    selected = [c for c in candles if anchor <= c.open_ms < end]
    expected = list(range(anchor, end, HOUR))
    seen = [c.open_ms for c in selected]
    try:
        if len(set(seen)) != len(seen):
            raise ValueError("DUPLICATE_OR_CONFLICT")
        selected.sort(key=lambda c: c.open_ms)
        if [c.open_ms for c in selected] != expected:
            raise ValueError("MISSING_PATH")
        for c in selected:
            if (c.provider != "UPBIT" or c.instrument != signal["market"] or c.timeframe != "1h"
                    or not c.completed or c.close_ms != c.open_ms + HOUR or c.close_ms > as_of_ms):
                raise ValueError("INVALID_OR_FORMING_CANDLE")
            from decimal import Decimal as D
            nums = (c.open, c.high, c.low, c.close, c.base_volume, c.quote_trade_amount)
            if (any(not isinstance(v, D) or not v.is_finite() for v in nums)
                    or min(nums[:4]) <= 0 or min(nums[4:]) < 0
                    or c.high < max(c.open,c.close,c.low) or c.low > min(c.open,c.close)):
                raise ValueError("INVALID_OHLCV")
        # Each used candle must be covered by an actual response, observed after its close.
        from upbit_b.features import _clock_ms
        for c in selected:
            if not any(ev.get("url", "").startswith(UPBIT + "/v1/candles/minutes/60?")
                       and ev.get("response_sha256") and c.open_ms in ev.get("source_row_open_times_ms", [])
                       and c.close_ms <= _clock_ms(ev["received_at_utc"]) <= as_of_ms
                       for ev in evidence):
                raise ValueError("MISSING_OBSERVATION_EVIDENCE")
    except (ValueError, KeyError, TypeError) as exc:
        result.update(status="UNVERIFIABLE", reason=str(exc))
        return result
    with localcontext() as ctx:
        ctx.prec = 34
        price, endpoint = selected[0].open, selected[-1].close
        high, low = max(c.high for c in selected), min(c.low for c in selected)
        result.update(status="MATURED", reason=None, anchor_price=str(price), endpoint_price=str(endpoint),
            return_pct=str((endpoint/price-1)*100), hmax=str(high), lmin=str(low),
            mfe_pct=str(max(Decimal(0),(high/price-1)*100)),
            mae_pct=str(min(Decimal(0),(low/price-1)*100)),
            path=[candle_record(c) for c in selected])
    result["path_sha256"] = digest(result["path"])
    return result


def fetch_outcome(client, signal, days, as_of_ms):
    validate_signal(signal)
    if type(days) is not int or days not in DAY_HORIZONS:
        raise ValueError("invalid horizon")
    end = signal["proxy_anchor_open_ms"] + days * 24 * HOUR
    if as_of_ms < end:
        return evaluate(signal, days, [], [], as_of_ms)
    try:
        rows, ev = client.get(UPBIT, "/v1/candles/minutes/60", {
            "market": signal["market"], "count": days*24, "to": iso(end)})
        if not isinstance(rows, list):
            raise DataError("candle response must be list")
        from dataclasses import replace
        candles = [replace(normalize("UPBIT", signal["market"], "1h", row), completed=True) for row in rows]
        ev = dict(ev, source_row_open_times_ms=[c.open_ms for c in candles])
        # REST evidence is known only when the response has actually returned.
        import time
        observed = max(as_of_ms, time.time_ns() // 1000000)
        return evaluate(signal, days, candles, [ev], observed)
    except (ValueError, TypeError, OSError) as exc:
        result = evaluate(signal, days, [], [], as_of_ms)
        result.update(status="UNVERIFIABLE", reason="API_OR_SOURCE_ERROR: " + str(exc))
        return result

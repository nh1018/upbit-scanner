"""Public REST inputs in memory only. No feature/score/decision calculations."""
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from dataclasses import replace
from decimal import Decimal, InvalidOperation

from .contracts import Candle, Window, DURATIONS, WINDOWS

UPBIT = "https://api.upbit.com"
BINANCE = "https://data-api.binance.vision"

class DataError(ValueError):
    pass

def integer(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise DataError("integer timestamp/unit required")
    return value

def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise DataError("decimal value required")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise DataError("invalid decimal") from None
    if not result.is_finite():
        raise DataError("non-finite decimal")
    return result

def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")

def upbit_time(value):
    if not isinstance(value, str):
        raise DataError("UTC candle time required")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.utcoffset().total_seconds() != 0 or dt.microsecond:
            raise DataError("UTC seconds required")
        return int(dt.timestamp()) * 1000
    except ValueError:
        raise DataError("invalid UTC candle time") from None

def normalize(provider, instrument, tf, row):
    duration = DURATIONS[tf]
    try:
        if provider == "UPBIT":
            if not isinstance(row, dict) or row["market"] != instrument:
                raise DataError("market mismatch")
            if tf != "1d" and integer(row["unit"]) != duration // 60000:
                raise DataError("unit mismatch")
            start = upbit_time(row["candle_date_time_utc"])
            if "candle_date_time_kst" in row:
                kst = datetime.fromisoformat(row["candle_date_time_kst"])
                if kst.tzinfo is not None or int(kst.replace(tzinfo=timezone.utc).timestamp()) * 1000 != start + 32400000:
                    raise DataError("KST/UTC mismatch")
            values = [row[k] for k in ("opening_price", "high_price", "low_price", "trade_price", "candle_acc_trade_volume", "candle_acc_trade_price")]
        elif provider == "BINANCE_SPOT":
            if not isinstance(row, list) or len(row) != 12:
                raise DataError("kline shape")
            start = integer(row[0])
            if integer(row[6]) != start + duration - 1:
                raise DataError("source close boundary mismatch")
            values = row[1:6] + [row[7]]
        else:
            raise DataError("unknown provider")
        if start < 0 or start % duration:
            raise DataError("candle boundary violation")
        o, h, l, c, v, q = map(number, values)
        if min(o, h, l, c) <= 0 or min(v, q) < 0 or h < max(o, c, l) or l > min(o, c):
            raise DataError("OHLC/volume violation")
        return Candle(provider, instrument, tf, start, start + duration, o, h, l, c, v, q)
    except (KeyError, TypeError, IndexError, ValueError) as exc:
        raise DataError(str(exc)) from None

class HTTPClient:
    """Conservative per-host pacing; injectable transport for offline tests."""
    def __init__(self, opener=urllib.request.urlopen, sleep=time.sleep, clock=time.monotonic, attempts=3, timeout=15):
        self.opener, self.sleep, self.clock = opener, sleep, clock
        self.attempts, self.timeout, self.next_request = attempts, timeout, {}

    def get(self, base, path, params):
        url = base + path + "?" + urllib.parse.urlencode(params)
        for attempt in range(self.attempts):
            self.sleep(max(0, self.next_request.get(base, 0) - self.clock()))
            self.next_request[base] = self.clock() + (0.25 if base == UPBIT else 0.2)
            try:
                with self.opener(urllib.request.Request(url, headers={"User-Agent": "upbit-b-market-data-v1"}), timeout=self.timeout) as response:
                    raw = response.read()
                    remaining = response.headers.get("Remaining-Req", "")
                    if "sec=0" in remaining.split(";")[-1].strip():
                        self.next_request[base] = self.clock() + 1
                    payload = json.loads(raw, parse_float=Decimal, parse_constant=lambda x: (_ for _ in ()).throw(DataError("non-finite JSON")))
                    return payload, {"url": url, "response_sha256": hashlib.sha256(raw).hexdigest(), "received_at_utc": iso(time.time_ns() // 1000000), "attempt": attempt + 1}
            except urllib.error.HTTPError as exc:
                if exc.code == 418:
                    raise DataError("API blocked (418); stop this run") from None
                if exc.code not in (429, 500, 502, 503, 504):
                    raise DataError(f"HTTP {exc.code}") from None
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
            except (TimeoutError, OSError):
                delay = 2 ** attempt
            except (ValueError, UnicodeError):
                raise DataError("malformed JSON response") from None
            if attempt + 1 < self.attempts:
                self.sleep(delay)
        raise DataError("API retries exhausted")

def universe(client):
    rows, evidence = client.get(UPBIT, "/v1/market/all", {"is_details": "true"})
    if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get("market"), str) for r in rows):
        raise DataError("invalid universe")
    krw = [r for r in rows if r["market"].startswith("KRW-")]
    if not krw or len({r["market"] for r in krw}) != len(krw):
        raise DataError("empty/duplicate universe")
    return tuple(sorted(krw, key=lambda r: r["market"])), evidence

def tickers(client, markets):
    result, evidence = {}, []
    for i in range(0, len(markets), 80):
        requested = markets[i:i+80]
        rows, ev = client.get(UPBIT, "/v1/ticker", {"markets": ",".join(requested)})
        if not isinstance(rows, list):
            raise DataError("invalid tickers")
        for row in rows:
            if not isinstance(row, dict) or row.get("market") not in requested or row["market"] in result or "trade_price" not in row:
                raise DataError("ticker identity mismatch")
            if number(row["trade_price"]) <= 0:
                raise DataError("invalid ticker price")
            result[row["market"]] = row
        if set(requested) - result.keys():
            raise DataError("missing ticker")
        evidence.append(ev)
    return result, tuple(evidence)

def mapping(market, exchange_info, approved=None):
    """Approval is an explicit caller-owned identity registry, never inferred."""
    if exchange_info is None:
        return {"status": "API_UNAVAILABLE", "symbol": None}
    if not isinstance(exchange_info, dict) or not isinstance(exchange_info.get("symbols"), list):
        raise DataError("invalid exchangeInfo")
    coin = market.removeprefix("KRW-")
    rows = exchange_info["symbols"]
    if any(not isinstance(r, dict) or not all(k in r for k in ("baseAsset", "quoteAsset", "symbol", "status")) for r in rows):
        raise DataError("invalid exchangeInfo symbol")
    candidates = [r for r in rows if r["baseAsset"] == coin and r["quoteAsset"] == "USDT"]
    eligible = [r for r in candidates if r["status"] == "TRADING" and r.get("isSpotTradingAllowed") is True]
    status = "NO_SYMBOL" if not candidates else "NO_ELIGIBLE_SYMBOL" if not eligible else "AMBIGUOUS" if len(eligible) != 1 else "UNVERIFIED"
    symbol = eligible[0]["symbol"] if len(eligible) == 1 else None
    approval = (approved or {}).get(market)
    if status == "UNVERIFIED" and isinstance(approval, dict) and approval.get("symbol") == symbol and approval.get("evidence_reference") and approval.get("registry_version"):
        status = "VERIFIED"
    return {"status": status, "symbol": symbol, "identity_evidence": approval if status == "VERIFIED" else None}

def exchange_info(client):
    """One bulk metadata request; API failure is optional confirmation absence."""
    try:
        info, evidence = client.get(BINANCE, "/api/v3/exchangeInfo", {})
        if not isinstance(info, dict) or not isinstance(info.get("symbols"), list):
            raise DataError("invalid exchangeInfo")
        return info, evidence
    except DataError as exc:
        return None, {"status": "API_UNAVAILABLE", "reason": str(exc)}

class MarketData:
    def __init__(self, client=None, cutoff_ms=None):
        self.client = client or HTTPClient()
        self.cutoff_ms = integer(cutoff_ms if cutoff_ms is not None else time.time_ns() // 1000000)
        if self.cutoff_ms < 0:
            raise DataError("negative cutoff")
        self.cache = {}

    def window(self, provider, instrument, tf):
        if tf not in WINDOWS or provider not in ("UPBIT", "BINANCE_SPOT"):
            raise DataError("unsupported provider/timeframe")
        key = provider, instrument, tf
        if key in self.cache:
            return self.cache[key]
        duration, target = DURATIONS[tf], WINDOWS[tf]
        end = self.cutoff_ms // duration * duration
        collected, evidence = {}, []
        try:
            # A second page accommodates an excluded forming row; bounded, not an archive crawler.
            for _ in range(3):
                if provider == "UPBIT":
                    path = "/v1/candles/days" if tf == "1d" else f"/v1/candles/minutes/{duration//60000}"
                    rows, ev = self.client.get(UPBIT, path, {"market": instrument, "count": target, "to": iso(end)})
                else:
                    rows, ev = self.client.get(BINANCE, "/api/v3/klines", {"symbol": instrument, "interval": tf, "limit": target, "endTime": end - 1, "timeZone": "0"})
                evidence.append(dict(ev))
                if not isinstance(rows, list):
                    raise DataError("candle response must be list")
                if not rows:
                    break
                parsed = [normalize(provider, instrument, tf, row) for row in rows]
                evidence[-1]["source_row_open_times_ms"] = [c.open_ms for c in parsed]
                if len({c.open_ms for c in parsed}) != len(parsed):
                    raise DataError("duplicate response candle")
                for c in parsed:
                    if c.open_ms >= end and c.close_ms <= self.cutoff_ms:
                        raise DataError("pagination range violation")
                    if c.close_ms > self.cutoff_ms:
                        continue
                    c = replace(c, completed=True)
                    if c.open_ms in collected and collected[c.open_ms] != c:
                        raise DataError("conflicting candle")
                    collected[c.open_ms] = c
                earliest = min(c.open_ms for c in parsed)
                if len(collected) >= target or earliest >= end:
                    break
                end = earliest
            candles = tuple(sorted(collected.values(), key=lambda c: c.open_ms)[-target:])
            missing = tuple(t for a, b in zip(candles, candles[1:]) for t in range(a.open_ms + duration, b.open_ms, duration))
            status = "AVAILABLE" if len(candles) == target else "INSUFFICIENT_DATA"
            if candles and candles[-1].close_ms != self.cutoff_ms // duration * duration:
                status = "INCOMPLETE_COVERAGE"
            if missing:
                status = "INCOMPLETE_COVERAGE"
            digest = hashlib.sha256(json.dumps([[c.provider, c.instrument, c.timeframe, c.open_ms, *[str(x) for x in (c.open,c.high,c.low,c.close,c.base_volume,c.quote_trade_amount)]] for c in candles], separators=(",", ":")).encode()).hexdigest()
            result = Window(status, candles, self.cutoff_ms, tuple(evidence), digest, missing)
        except (DataError, KeyError, ValueError) as exc:
            result = Window("API_ERROR" if "API" in str(exc) or "HTTP" in str(exc) else "INVALID_DATA", (), self.cutoff_ms, tuple(evidence), "", reason=str(exc))
        self.cache[key] = result
        return result

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


# ============================================================
# BTC Anytime V1.0
# Binance USDⓈ-M Futures raw market data collector
# ============================================================

SYMBOL = "BTCUSDT"

# Binance USDⓈ-M Futures REST API
FUTURES_BASE_URL = "https://fapi.binance.com"

KLINE_INTERVALS = ("15m", "1h", "4h", "1d")
KLINE_LIMIT = 300

REQUEST_TIMEOUT_SECONDS = 15

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_FILE = OUTPUT_DIR / "btc_anytime_latest.json"


# ============================================================
# Time helpers
# ============================================================

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc_from_ms(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


# ============================================================
# HTTP
# ============================================================

def get_json(
    session: requests.Session,
    path: str,
    params: dict[str, Any],
) -> Any:

    url = FUTURES_BASE_URL + path

    response = session.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# Futures Klines
# ============================================================

def fetch_klines(
    session: requests.Session,
    interval: str,
    collection_time_ms: int,
) -> dict[str, Any]:

    raw = get_json(
        session,
        "/fapi/v1/klines",
        {
            "symbol": SYMBOL,
            "interval": interval,
            "limit": KLINE_LIMIT,
        },
    )

    if not isinstance(raw, list):
        raise RuntimeError(
            f"Unexpected kline response: {interval}"
        )

    if len(raw) != KLINE_LIMIT:
        raise RuntimeError(
            f"Kline count mismatch for {interval}: "
            f"expected={KLINE_LIMIT}, actual={len(raw)}"
        )

    candles = []

    for item in raw:

        if not isinstance(item, list) or len(item) < 11:
            raise RuntimeError(
                f"Malformed kline for {interval}"
            )

        open_time_ms = int(item[0])
        close_time_ms = int(item[6])

        # Binance close_time is the nominal end timestamp
        # of the candle. A candle is considered completed
        # only if our collection time is later than it.
        is_closed = (
            close_time_ms < collection_time_ms
        )

        candles.append(
            {
                "open_time_ms": open_time_ms,
                "open_time_utc":
                    iso_utc_from_ms(open_time_ms),

                "close_time_ms": close_time_ms,
                "close_time_utc":
                    iso_utc_from_ms(close_time_ms),

                "open": item[1],
                "high": item[2],
                "low": item[3],
                "close": item[4],

                "volume": item[5],

                "quote_volume": item[7],

                "trade_count": int(item[8]),

                "taker_buy_base_volume":
                    item[9],

                "taker_buy_quote_volume":
                    item[10],

                "is_closed": is_closed,
            }
        )

    completed_count = sum(
        1
        for candle in candles
        if candle["is_closed"]
    )

    in_progress_count = (
        len(candles) - completed_count
    )

    return {
        "interval": interval,
        "requested_count": KLINE_LIMIT,
        "returned_count": len(candles),
        "completed_count": completed_count,
        "in_progress_count": in_progress_count,
        "candles": candles,
    }


# ============================================================
# Open Interest
# ============================================================

def fetch_open_interest(
    session: requests.Session,
) -> dict[str, Any]:

    raw = get_json(
        session,
        "/fapi/v1/openInterest",
        {
            "symbol": SYMBOL,
        },
    )

    required = {
        "symbol",
        "openInterest",
        "time",
    }

    if not isinstance(raw, dict):
        raise RuntimeError(
            "Unexpected Open Interest response"
        )

    missing = required - set(raw)

    if missing:
        raise RuntimeError(
            "Open Interest response missing fields: "
            f"{sorted(missing)}"
        )

    timestamp_ms = int(raw["time"])

    return {
        "symbol": raw["symbol"],
        "open_interest": raw["openInterest"],
        "timestamp_ms": timestamp_ms,
        "timestamp_utc":
            iso_utc_from_ms(timestamp_ms),
    }


# ============================================================
# Funding / Mark Price
# ============================================================

def fetch_funding(
    session: requests.Session,
) -> dict[str, Any]:

    # Premium Index contains:
    # - markPrice
    # - indexPrice
    # - lastFundingRate
    # - nextFundingTime
    # - time

    raw = get_json(
        session,
        "/fapi/v1/premiumIndex",
        {
            "symbol": SYMBOL,
        },
    )

    if not isinstance(raw, dict):
        raise RuntimeError(
            "Unexpected Premium Index response"
        )

    required = {
        "symbol",
        "markPrice",
        "indexPrice",
        "lastFundingRate",
        "nextFundingTime",
        "time",
    }

    missing = required - set(raw)

    if missing:
        raise RuntimeError(
            "Premium Index response missing fields: "
            f"{sorted(missing)}"
        )

    timestamp_ms = int(raw["time"])
    next_funding_time_ms = int(
        raw["nextFundingTime"]
    )

    return {
        "symbol": raw["symbol"],

        "mark_price":
            raw["markPrice"],

        "index_price":
            raw["indexPrice"],

        "last_funding_rate":
            raw["lastFundingRate"],

        "next_funding_time_ms":
            next_funding_time_ms,

        "next_funding_time_utc":
            iso_utc_from_ms(
                next_funding_time_ms
            ),

        "timestamp_ms":
            timestamp_ms,

        "timestamp_utc":
            iso_utc_from_ms(
                timestamp_ms
            ),
    }


# ============================================================
# Funding History
# ============================================================

def fetch_funding_history(
    session: requests.Session,
) -> list[dict[str, Any]]:

    raw = get_json(
        session,
        "/fapi/v1/fundingRate",
        {
            "symbol": SYMBOL,
            "limit": 20,
        },
    )

    if not isinstance(raw, list):
        raise RuntimeError(
            "Unexpected Funding History response"
        )

    result = []

    for item in raw:

        if not isinstance(item, dict):
            raise RuntimeError(
                "Malformed Funding History record"
            )

        if (
            "fundingRate" not in item
            or "fundingTime" not in item
        ):
            raise RuntimeError(
                "Funding History missing fields"
            )

        funding_time_ms = int(
            item["fundingTime"]
        )

        result.append(
            {
                "funding_rate":
                    item["fundingRate"],

                "funding_time_ms":
                    funding_time_ms,

                "funding_time_utc":
                    iso_utc_from_ms(
                        funding_time_ms
                    ),

                "mark_price":
                    item.get("markPrice"),
            }
        )

    return result


# ============================================================
# Atomic JSON write
# ============================================================

def write_json_atomic(
    path: Path,
    data: dict[str, Any],
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    try:

        with temp_path.open(
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(
            temp_path,
            path,
        )

    finally:

        if temp_path.exists():
            temp_path.unlink()


# ============================================================
# Main
# ============================================================

def main() -> None:

    print("=" * 72)
    print(
        "BTC Anytime V1.0 - "
        "Binance USDⓈ-M Futures Collector"
    )
    print("=" * 72)

    collection_dt = now_utc()

    collection_time_ms = int(
        collection_dt.timestamp() * 1000
    )

    generated_at_utc = iso_utc(
        collection_dt
    )

    session = requests.Session()

    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent":
                "btc-anytime-v1-collector",
        }
    )

    # --------------------------------------------------------
    # Klines
    # --------------------------------------------------------

    klines = {}

    for interval in KLINE_INTERVALS:

        print(
            f"Collecting {interval} klines..."
        )

        klines[interval] = fetch_klines(
            session,
            interval,
            collection_time_ms,
        )

    # --------------------------------------------------------
    # Derivatives
    # --------------------------------------------------------

    print("Collecting Open Interest...")

    open_interest = fetch_open_interest(
        session
    )

    print("Collecting Funding / Mark Price...")

    funding = fetch_funding(
        session
    )

    print("Collecting Funding History...")

    funding_history = (
        fetch_funding_history(
            session
        )
    )

    # --------------------------------------------------------
    # Final document
    # --------------------------------------------------------

    document = {
        "schema_version":
            "btc-anytime-v1",

        "symbol":
            SYMBOL,

        "market":
            "BINANCE_USDT_M_FUTURES",

        "generated_at_utc":
            generated_at_utc,

        "source":
            {
                "exchange":
                    "Binance",

                "market_type":
                    "USDⓈ-M Futures",

                "api_base":
                    FUTURES_BASE_URL,
            },

        "klines":
            klines,

        "derivatives":
            {
                "open_interest":
                    open_interest,

                "funding":
                    funding,

                "funding_history":
                    funding_history,
            },
    }

    # --------------------------------------------------------
    # Safety validation
    # --------------------------------------------------------

    for interval in KLINE_INTERVALS:

        candles = document[
            "klines"
        ][interval]["candles"]

        if len(candles) != KLINE_LIMIT:
            raise RuntimeError(
                f"Final validation failed: "
                f"{interval}"
            )

        open_times = [
            candle["open_time_ms"]
            for candle in candles
        ]

        if len(open_times) != len(
            set(open_times)
        ):
            raise RuntimeError(
                f"Duplicate candle detected: "
                f"{interval}"
            )

        if open_times != sorted(open_times):
            raise RuntimeError(
                f"Candles not sorted: "
                f"{interval}"
            )

    # Only write after ALL collection and
    # validation steps succeeded.

    write_json_atomic(
        OUTPUT_FILE,
        document,
    )

    print()
    print(
        f"Generated at UTC : "
        f"{generated_at_utc}"
    )

    print(
        f"Output           : "
        f"{OUTPUT_FILE}"
    )

    print()

    for interval in KLINE_INTERVALS:

        info = klines[interval]

        print(
            f"{interval:>3} : "
            f"{info['returned_count']} candles | "
            f"closed={info['completed_count']} | "
            f"in_progress={info['in_progress_count']}"
        )

    print()

    print(
        f"Open Interest    : "
        f"{open_interest['open_interest']}"
    )

    print(
        f"Funding Rate     : "
        f"{funding['last_funding_rate']}"
    )

    print(
        f"Next Funding UTC : "
        f"{funding['next_funding_time_utc']}"
    )

    print()

    print(
        "BTC Anytime raw data collection "
        "completed successfully."
    )


if __name__ == "__main__":
    main()

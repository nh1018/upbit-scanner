from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


# ============================================================
# Configuration
# ============================================================

UPBIT_BASE_URL = "https://api.upbit.com"

REQUEST_COUNT = 2
REQUEST_INTERVAL_SECONDS = 0.12
REQUEST_TIMEOUT_SECONDS = 10

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = PROJECT_ROOT / "data_market"

UNIVERSE_SNAPSHOT_DIR = (
    DATA_ROOT
    / "universe"
    / "snapshots_30m"
)

CANDLE_DIR = (
    DATA_ROOT
    / "candles"
    / "upbit_daily"
)

CANDLE_FILE = (
    CANDLE_DIR
    / "upbit_daily_candles.csv"
)


CSV_COLUMNS = [
    "market",
    "candle_time_utc",
    "candle_time_kst",
    "open",
    "high",
    "low",
    "close",
    "base_volume",
    "quote_trade_amount",
    "last_tick_timestamp_ms",
    "collected_at_utc",
]


# ============================================================
# Time helpers
# ============================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def current_daily_boundary_utc(
    dt: datetime,
) -> datetime:
    """
    Upbit daily candle boundary:

        UTC 00:00
        = KST 09:00

    The candle starting at the current UTC day's
    00:00 is still in progress.

    Therefore only candles whose start time is
    strictly earlier than this boundary are complete.
    """
    dt = dt.astimezone(timezone.utc)

    return dt.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def parse_upbit_utc(value: str) -> datetime:
    """
    Upbit candle UTC format:
        YYYY-MM-DDTHH:MM:SS
    """
    dt = datetime.strptime(
        value,
        "%Y-%m-%dT%H:%M:%S",
    )

    return dt.replace(tzinfo=timezone.utc)


# ============================================================
# Universe
# ============================================================

def find_latest_universe_snapshot() -> Path:
    files = sorted(
        UNIVERSE_SNAPSHOT_DIR.glob(
            "universe_*.json"
        )
    )

    if not files:
        raise RuntimeError(
            "No 30m Universe snapshot found: "
            f"{UNIVERSE_SNAPSHOT_DIR}"
        )

    return files[-1]


def load_universe_markets(
    snapshot_path: Path,
) -> tuple[list[str], dict[str, Any]]:

    with snapshot_path.open(
        "r",
        encoding="utf-8-sig",
    ) as f:
        data = json.load(f)

    markets_raw = data.get("markets")

    if not isinstance(markets_raw, list):
        raise RuntimeError(
            "Invalid Universe snapshot: "
            "'markets' is not a list."
        )

    markets: list[str] = []

    for item in markets_raw:
        if not isinstance(item, dict):
            continue

        market = item.get("market")

        if (
            isinstance(market, str)
            and market.startswith("KRW-")
        ):
            markets.append(market)

    markets = sorted(set(markets))

    if not markets:
        raise RuntimeError(
            "Universe snapshot contains "
            "no KRW markets."
        )

    expected_count = data.get("market_count")

    if (
        isinstance(expected_count, int)
        and expected_count != len(markets)
    ):
        raise RuntimeError(
            "Universe market_count mismatch: "
            f"snapshot={expected_count}, "
            f"loaded={len(markets)}"
        )

    return markets, data


# ============================================================
# Existing daily candle ledger
# ============================================================

def load_existing_keys() -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()

    if not CANDLE_FILE.exists():
        return keys

    with CANDLE_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        reader = csv.DictReader(f)

        if reader.fieldnames is None:
            raise RuntimeError(
                "Existing daily candle ledger "
                "has no header."
            )

        required = {
            "market",
            "candle_time_utc",
        }

        missing = required - set(reader.fieldnames)

        if missing:
            raise RuntimeError(
                "Existing daily candle ledger "
                f"missing columns: {missing}"
            )

        for row in reader:
            market = row.get("market")
            candle_time = row.get(
                "candle_time_utc"
            )

            if market and candle_time:
                keys.add(
                    (market, candle_time)
                )

    return keys


# ============================================================
# Upbit API
# ============================================================

def fetch_recent_daily_candles(
    session: requests.Session,
    market: str,
) -> list[dict[str, Any]]:

    url = (
        f"{UPBIT_BASE_URL}"
        "/v1/candles/days"
    )

    params = {
        "market": market,
        "count": REQUEST_COUNT,
    }

    response = session.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        raise RuntimeError(
            f"Unexpected API response "
            f"for {market}"
        )

    return data


# ============================================================
# Candle normalization
# ============================================================

def normalize_candle(
    raw: dict[str, Any],
    collected_at_utc: str,
) -> dict[str, Any]:

    required_fields = [
        "market",
        "candle_date_time_utc",
        "candle_date_time_kst",
        "opening_price",
        "high_price",
        "low_price",
        "trade_price",
        "timestamp",
        "candle_acc_trade_price",
        "candle_acc_trade_volume",
    ]

    missing = [
        field
        for field in required_fields
        if field not in raw
    ]

    if missing:
        raise RuntimeError(
            "Upbit daily candle "
            "missing fields: "
            f"{missing}"
        )

    return {
        "market":
            raw["market"],

        "candle_time_utc":
            raw["candle_date_time_utc"]
            + "Z",

        "candle_time_kst":
            raw["candle_date_time_kst"]
            + "+09:00",

        "open":
            raw["opening_price"],

        "high":
            raw["high_price"],

        "low":
            raw["low_price"],

        "close":
            raw["trade_price"],

        "base_volume":
            raw["candle_acc_trade_volume"],

        "quote_trade_amount":
            raw["candle_acc_trade_price"],

        "last_tick_timestamp_ms":
            raw["timestamp"],

        "collected_at_utc":
            collected_at_utc,
    }


# ============================================================
# Write
# ============================================================

def append_rows(
    rows: list[dict[str, Any]],
) -> None:

    if not rows:
        return

    CANDLE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_exists = CANDLE_FILE.exists()

    with CANDLE_FILE.open(
        "a",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=CSV_COLUMNS,
        )

        if not file_exists:
            writer.writeheader()

        writer.writerows(rows)


# ============================================================
# Main
# ============================================================

def main() -> None:

    print("=" * 72)
    print(
        "Market Data V1.0 - "
        "Upbit Completed Daily Candle Collector"
    )
    print("=" * 72)

    now = utc_now()

    collected_at_utc = iso_z(now)

    completed_before = (
        current_daily_boundary_utc(now)
    )

    universe_path = (
        find_latest_universe_snapshot()
    )

    markets, universe_data = (
        load_universe_markets(
            universe_path
        )
    )

    existing_keys = load_existing_keys()

    print(
        f"Collected at UTC       : "
        f"{collected_at_utc}"
    )

    print(
        f"Completed before UTC   : "
        f"{iso_z(completed_before)}"
    )

    print(
        f"Daily boundary KST     : "
        "09:00:00"
    )

    print(
        f"Universe snapshot      : "
        f"{universe_path}"
    )

    print(
        f"Universe snapshot UTC  : "
        f"{universe_data.get('snapshot_time_utc')}"
    )

    print(
        f"KRW markets            : "
        f"{len(markets)}"
    )

    print(
        f"Existing candle keys   : "
        f"{len(existing_keys)}"
    )

    print()

    session = requests.Session()

    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent":
                "market-data-v1-upbit-daily",
        }
    )

    new_rows: list[dict[str, Any]] = []

    new_keys: set[tuple[str, str]] = set()

    skipped_incomplete = 0
    skipped_existing = 0
    api_candles_received = 0
    failed_markets: list[str] = []

    for index, market in enumerate(
        markets,
        start=1,
    ):

        try:
            raw_candles = (
                fetch_recent_daily_candles(
                    session,
                    market,
                )
            )

            api_candles_received += len(
                raw_candles
            )

            for raw in raw_candles:

                candle_start = parse_upbit_utc(
                    raw[
                        "candle_date_time_utc"
                    ]
                )

                # Strict completed-daily-candle rule.
                #
                # Current UTC day's 00:00 candle
                # corresponds to KST 09:00 and is
                # still in progress.
                if candle_start >= completed_before:
                    skipped_incomplete += 1
                    continue

                row = normalize_candle(
                    raw,
                    collected_at_utc,
                )

                key = (
                    row["market"],
                    row["candle_time_utc"],
                )

                if key in existing_keys:
                    skipped_existing += 1
                    continue

                if key in new_keys:
                    continue

                new_keys.add(key)
                new_rows.append(row)

        except Exception as exc:
            failed_markets.append(market)

            print(
                f"[ERROR] {market}: {exc}"
            )

        if (
            index % 25 == 0
            or index == len(markets)
        ):
            print(
                f"[{index:>3}/{len(markets)}] "
                f"processed"
            )

        time.sleep(
            REQUEST_INTERVAL_SECONDS
        )

    # Deterministic ordering for the appended batch.
    new_rows.sort(
        key=lambda row: (
            row["candle_time_utc"],
            row["market"],
        )
    )

    # Final duplicate safety check.
    final_keys = [
        (
            row["market"],
            row["candle_time_utc"],
        )
        for row in new_rows
    ]

    if len(final_keys) != len(
        set(final_keys)
    ):
        raise RuntimeError(
            "Duplicate key detected "
            "inside new daily candle batch."
        )

    append_rows(new_rows)

    print()
    print("-" * 72)

    print(
        f"API candles received   : "
        f"{api_candles_received}"
    )

    print(
        f"Incomplete skipped     : "
        f"{skipped_incomplete}"
    )

    print(
        f"Existing skipped       : "
        f"{skipped_existing}"
    )

    print(
        f"New candles appended   : "
        f"{len(new_rows)}"
    )

    print(
        f"Failed markets         : "
        f"{len(failed_markets)}"
    )

    print(
        f"Daily candle ledger    : "
        f"{CANDLE_FILE}"
    )

    if failed_markets:
        print()
        print(
            "Failed market list:"
        )

        for market in failed_markets:
            print(
                f"  - {market}"
            )

        raise RuntimeError(
            "Daily candle collection completed "
            "with failed markets."
        )

    print()
    print(
        "Completed daily candle "
        "collection finished successfully."
    )


if __name__ == "__main__":
    main()

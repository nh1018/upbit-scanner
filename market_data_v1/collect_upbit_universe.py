from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import requests


UPBIT_BASE_URL = "https://api.upbit.com"

OUTPUT_DIR = Path("data_market/universe")
HISTORY_FILE = OUTPUT_DIR / "upbit_krw_history.csv"
DAILY_DIR = OUTPUT_DIR / "daily"

TIMEOUT_SECONDS = 15


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def fetch_upbit_markets() -> list[dict]:
    """
    Fetch the current Upbit market list.

    This function only retrieves market metadata.
    No strategy calculation is performed.
    """
    url = f"{UPBIT_BASE_URL}/v1/market/all"

    response = requests.get(
        url,
        params={"is_details": "true"},
        headers={"Accept": "application/json"},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        raise RuntimeError(
            f"Unexpected Upbit response type: {type(data).__name__}"
        )

    return data


def extract_krw_markets(markets: list[dict]) -> list[dict]:
    """
    Keep only KRW markets and preserve exchange-provided metadata.
    """
    result = []

    for item in markets:
        market = item.get("market")

        if not isinstance(market, str):
            continue

        if not market.startswith("KRW-"):
            continue

        result.append(
            {
                "market": market,
                "korean_name": item.get("korean_name"),
                "english_name": item.get("english_name"),
                "market_event": item.get("market_event"),
            }
        )

    result.sort(key=lambda x: x["market"])

    return result


def save_daily_snapshot(
    markets: list[dict],
    collected_at: datetime,
) -> Path:
    """
    Preserve the actual KRW universe observed at this collection point.

    One JSON file per UTC collection date is written.
    Re-running on the same date replaces that day's universe snapshot.
    """
    DAILY_DIR.mkdir(parents=True, exist_ok=True)

    date_str = collected_at.strftime("%Y%m%d")
    output_path = DAILY_DIR / f"universe_{date_str}.json"

    payload = {
        "schema_version": "market-data-v1",
        "source": "UPBIT",
        "market_type": "KRW",
        "collected_at_utc": iso_utc(collected_at),
        "market_count": len(markets),
        "markets": markets,
    }

    temp_path = output_path.with_suffix(".json.tmp")

    with temp_path.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            payload,
            f,
            ensure_ascii=False,
            indent=2,
        )
        f.write("\n")

    temp_path.replace(output_path)

    return output_path


def load_history() -> dict[str, dict]:
    """
    Load previously observed market lifecycle information.
    """
    if not HISTORY_FILE.exists():
        return {}

    history = {}

    with HISTORY_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        reader = csv.DictReader(f)

        for row in reader:
            market = row.get("market")

            if not market:
                continue

            history[market] = row

    return history


def update_history(
    current_markets: list[dict],
    collected_at: datetime,
) -> list[dict]:
    """
    Update first_seen / last_seen observations.

    IMPORTANT:
    first_seen_utc is the first time OUR collector observed the market.
    It is NOT the historical Upbit listing date.

    last_seen_utc is the most recent successful observation.
    It is NOT automatically the official delisting time.
    """
    observed_at = iso_utc(collected_at)

    old_history = load_history()

    current_map = {
        item["market"]: item
        for item in current_markets
    }

    all_markets = sorted(
        set(old_history.keys()) | set(current_map.keys())
    )

    updated = []

    for market in all_markets:
        old = old_history.get(market)
        current = current_map.get(market)

        if current is not None:
            if old is None:
                first_seen = observed_at
            else:
                first_seen = old.get("first_seen_utc") or observed_at

            row = {
                "market": market,
                "korean_name": current.get("korean_name") or "",
                "english_name": current.get("english_name") or "",
                "first_seen_utc": first_seen,
                "last_seen_utc": observed_at,
                "status": "ACTIVE",
            }

        else:
            # Market was previously observed but is absent now.
            # Do not invent an exact official delisting timestamp.
            row = {
                "market": market,
                "korean_name": old.get("korean_name", ""),
                "english_name": old.get("english_name", ""),
                "first_seen_utc": old.get("first_seen_utc", ""),
                "last_seen_utc": old.get("last_seen_utc", ""),
                "status": "NOT_OBSERVED_CURRENTLY",
            }

        updated.append(row)

    return updated


def save_history(rows: list[dict]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "market",
        "korean_name",
        "english_name",
        "first_seen_utc",
        "last_seen_utc",
        "status",
    ]

    temp_path = HISTORY_FILE.with_suffix(".csv.tmp")

    with temp_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(rows)

    temp_path.replace(HISTORY_FILE)


def main() -> None:
    collected_at = utc_now()

    print("=" * 72)
    print("Market Data V1.0 - Upbit KRW Universe Collector")
    print("=" * 72)
    print(f"Collected at UTC : {iso_utc(collected_at)}")

    raw_markets = fetch_upbit_markets()
    krw_markets = extract_krw_markets(raw_markets)

    if not krw_markets:
        raise RuntimeError(
            "No KRW markets returned. "
            "Existing universe files were not modified."
        )

    print(f"All Upbit markets : {len(raw_markets)}")
    print(f"KRW markets       : {len(krw_markets)}")

    daily_path = save_daily_snapshot(
        krw_markets,
        collected_at,
    )

    history_rows = update_history(
        krw_markets,
        collected_at,
    )

    save_history(history_rows)

    print()
    print(f"Daily snapshot : {daily_path}")
    print(f"History file   : {HISTORY_FILE}")
    print()
    print("Universe collection completed successfully.")


if __name__ == "__main__":
    main()

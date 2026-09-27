import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests


BASE_URL = "https://rest.coinapi.io"
ENDPOINT = "/v1/symbols/UPBIT/history"

API_KEY = os.getenv("COINAPI_KEY")

# 우리가 우선 확인할 Upbit 폐지 KRW market
TARGET_MARKETS = {
    "KRW-MARO": "MARO",
    "KRW-MFT": "MFT",
    "KRW-LAMB": "LAMB",
    "KRW-EDR": "EDR",
    "KRW-DMT": "DMT",
}

# 이번에 coverage를 집중 검증할 기간
COVERAGE_START = "2017-10-24T00:00:00"
COVERAGE_SPLIT = "2018-08-20T00:00:00"
COVERAGE_END = "2021-03-02T23:59:59"


def parse_dt(value):
    if not value:
        return None

    value = value.replace("Z", "+00:00")

    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def normalize_asset(value):
    if value is None:
        return None
    return str(value).upper().strip()


def get_exchange_market_code(symbol):
    """
    CoinAPI metadata에서 가능한 경우 실제 거래소 symbol을 읽는다.
    symbol_id_exchange가 없으면 base/quote로 KRW-XXX 형태를 구성한다.
    """
    exchange_symbol = symbol.get("symbol_id_exchange")

    if exchange_symbol:
        return str(exchange_symbol).upper()

    base = normalize_asset(symbol.get("asset_id_base"))
    quote = normalize_asset(symbol.get("asset_id_quote"))

    if base and quote:
        return f"{quote}-{base}"

    return None


def is_krw_spot(symbol):
    quote = normalize_asset(symbol.get("asset_id_quote"))
    symbol_type = normalize_asset(symbol.get("symbol_type"))

    return quote == "KRW" and symbol_type == "SPOT"


def fetch_historical_symbols():
    if not API_KEY:
        print("ERROR: COINAPI_KEY environment variable is missing.")
        sys.exit(1)

    url = BASE_URL + ENDPOINT

    headers = {
        "X-CoinAPI-Key": API_KEY,
        "Accept": "application/json",
        "User-Agent": "upbit-coinapi-metadata-diagnostic/1.0",
    }

    all_symbols = []

    # CoinAPI history endpoint는 page 기반 pagination을 지원한다.
    # 과도한 호출 방지를 위해 한 페이지씩 진행하고 안전 한도를 둔다.
    page = 1
    limit = 1000

    while True:
        params = {
            "page": page,
            "limit": limit,
        }

        print(f"Requesting metadata page {page}...")

        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=30,
        )

        print(f"HTTP status: {response.status_code}")

        if response.status_code != 200:
            print("CoinAPI request failed.")
            print(response.text[:2000])
            sys.exit(2)

        try:
            data = response.json()
        except Exception as exc:
            print(f"JSON decode failed: {exc}")
            print(response.text[:2000])
            sys.exit(3)

        if not isinstance(data, list):
            print("Unexpected response format.")
            print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
            sys.exit(4)

        print(f"Symbols returned on page: {len(data)}")

        if not data:
            break

        all_symbols.extend(data)

        # limit보다 적게 왔다면 마지막 페이지로 판단
        if len(data) < limit:
            break

        page += 1

        # metadata 검증 목적의 안전장치
        if page > 20:
            print("Safety stop: more than 20 metadata pages.")
            break

        time.sleep(0.25)

    return all_symbols


def find_target(symbols, market, base_asset):
    matches = []

    for symbol in symbols:
        base = normalize_asset(symbol.get("asset_id_base"))
        quote = normalize_asset(symbol.get("asset_id_quote"))

        if base != base_asset or quote != "KRW":
            continue

        exchange_code = get_exchange_market_code(symbol)

        matches.append({
            "requested_market": market,
            "exchange_market_code": exchange_code,
            "symbol_id": symbol.get("symbol_id"),
            "symbol_id_int": symbol.get("symbol_id_int"),
            "symbol_id_exchange": symbol.get("symbol_id_exchange"),
            "symbol_type": symbol.get("symbol_type"),
            "asset_id_base": symbol.get("asset_id_base"),
            "asset_id_quote": symbol.get("asset_id_quote"),
            "data_start": symbol.get("data_start"),
            "data_end": symbol.get("data_end"),
            "data_trade_start": symbol.get("data_trade_start"),
            "data_trade_end": symbol.get("data_trade_end"),
        })

    return matches


def overlaps_period(symbol, start_dt, end_dt):
    trade_start = parse_dt(symbol.get("data_trade_start"))
    trade_end = parse_dt(symbol.get("data_trade_end"))

    if trade_start is None or trade_end is None:
        return False

    return trade_start <= end_dt and trade_end >= start_dt


def save_csv(rows, path):
    fields = [
        "symbol_id",
        "symbol_id_int",
        "symbol_id_exchange",
        "symbol_type",
        "asset_id_base",
        "asset_id_quote",
        "data_start",
        "data_end",
        "data_trade_start",
        "data_trade_end",
    ]

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
            extrasaction="ignore",
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(row)


def main():
    started_at = datetime.now(timezone.utc)

    print("=" * 80)
    print("CoinAPI Upbit Historical Symbol Metadata Diagnostic")
    print("=" * 80)
    print(f"Started UTC : {started_at.isoformat()}")
    print(f"Endpoint    : {BASE_URL}{ENDPOINT}")
    print("Data scope  : METADATA ONLY")
    print("Trade API   : NOT USED")
    print("OHLCV API   : NOT USED")
    print()

    symbols = fetch_historical_symbols()

    print()
    print("=" * 80)
    print("RAW METADATA SUMMARY")
    print("=" * 80)
    print(f"Historical symbols returned: {len(symbols)}")

    krw_spot = [
        s for s in symbols
        if is_krw_spot(s)
    ]

    print(f"Historical KRW SPOT symbols: {len(krw_spot)}")

    start_2017 = parse_dt(COVERAGE_START)
    split_2018 = parse_dt(COVERAGE_SPLIT)
    end_2021 = parse_dt(COVERAGE_END)

    # 전체 목표기간과 겹치는 historical KRW symbols
    overlap_full_period = [
        s for s in krw_spot
        if overlaps_period(s, start_2017, end_2021)
    ]

    # CoinAPI 공식 공개 coverage 시작점 이후 ~ Tardis 시작 전
    overlap_coinapi_gap_period = [
        s for s in krw_spot
        if overlaps_period(s, split_2018, end_2021)
    ]

    print(
        "KRW historical symbols overlapping "
        "2017-10-24 ~ 2021-03-02:",
        len(overlap_full_period),
    )

    print(
        "KRW historical symbols overlapping "
        "2018-08-20 ~ 2021-03-02:",
        len(overlap_coinapi_gap_period),
    )

    print()
    print("=" * 80)
    print("TARGET DELISTED MARKET CHECK")
    print("=" * 80)

    target_results = {}

    for market, base_asset in TARGET_MARKETS.items():
        matches = find_target(
            krw_spot,
            market,
            base_asset,
        )

        target_results[market] = matches

        print()
        print(f"[{market}]")

        if not matches:
            print("NOT FOUND in returned CoinAPI historical metadata.")
            continue

        print(f"Matches: {len(matches)}")

        for index, match in enumerate(matches, start=1):
            print(f"  Match #{index}")
            print(f"    symbol_id          : {match['symbol_id']}")
            print(f"    symbol_id_int      : {match['symbol_id_int']}")
            print(
                f"    symbol_id_exchange : "
                f"{match['symbol_id_exchange']}"
            )
            print(f"    symbol_type        : {match['symbol_type']}")
            print(f"    base               : {match['asset_id_base']}")
            print(f"    quote              : {match['asset_id_quote']}")
            print(f"    data_start         : {match['data_start']}")
            print(f"    data_end           : {match['data_end']}")
            print(
                f"    data_trade_start   : "
                f"{match['data_trade_start']}"
            )
            print(
                f"    data_trade_end     : "
                f"{match['data_trade_end']}"
            )

    # 전체 KRW historical metadata 저장
    csv_path = "coinapi_upbit_historical_krw_symbols.csv"
    save_csv(krw_spot, csv_path)

    json_path = "coinapi_upbit_history_diagnostic.json"

    output = {
        "diagnostic": "coinapi_upbit_historical_symbol_metadata",
        "metadata_only": True,
        "trade_api_used": False,
        "ohlcv_api_used": False,
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint": BASE_URL + ENDPOINT,
        "historical_symbols_total": len(symbols),
        "historical_krw_spot_total": len(krw_spot),
        "coverage_periods": {
            "target_period": {
                "start": COVERAGE_START,
                "end": COVERAGE_END,
                "overlapping_historical_symbols": len(
                    overlap_full_period
                ),
            },
            "coinapi_to_tardis_gap": {
                "start": COVERAGE_SPLIT,
                "end": COVERAGE_END,
                "overlapping_historical_symbols": len(
                    overlap_coinapi_gap_period
                ),
            },
        },
        "target_markets": target_results,
        "historical_krw_spot_symbols": krw_spot,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print()
    print("=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)

    print(f"All historical symbols : {len(symbols)}")
    print(f"Historical KRW SPOT    : {len(krw_spot)}")
    print(
        f"Overlap 2017~2021      : "
        f"{len(overlap_full_period)}"
    )
    print(
        f"Overlap 2018~2021      : "
        f"{len(overlap_coinapi_gap_period)}"
    )

    print()

    for market, matches in target_results.items():
        if matches:
            print(
                f"{market:12s} : FOUND "
                f"({len(matches)} metadata record(s))"
            )
        else:
            print(f"{market:12s} : NOT FOUND")

    print()
    print(f"JSON saved: {json_path}")
    print(f"CSV saved : {csv_path}")


if __name__ == "__main__":
    main()

import json
import time
from datetime import datetime, timezone

import requests


UPBIT_DAY_CANDLE_URL = "https://api.upbit.com/v1/candles/days"

# 1차 진단 대상.
# 실제 거래지원 종료일은 API 테스트 결과와 별개로 추후 공식 공지와 대조한다.
# 여기서는 폐지 market의 과거 candle API 응답 여부를 확인하는 것이 목적이다.
TEST_CASES = [
    {
        "market": "KRW-MARO",
        "test_to": "2021-03-01T00:00:00Z",
    },
    {
        "market": "KRW-MFT",
        "test_to": "2021-01-01T00:00:00Z",
    },
    {
        "market": "KRW-LAMB",
        "test_to": "2021-06-01T00:00:00Z",
    },
    {
        "market": "KRW-EDR",
        "test_to": "2020-01-01T00:00:00Z",
    },
    {
        "market": "KRW-DMT",
        "test_to": "2020-01-01T00:00:00Z",
    },
]


HEADERS = {
    "Accept": "application/json",
    "User-Agent": "upbit-delisted-market-diagnostic/1.0",
}


def request_daily_candles(market, to=None, count=200):
    params = {
        "market": market,
        "count": count,
    }

    if to:
        params["to"] = to

    response = requests.get(
        UPBIT_DAY_CANDLE_URL,
        params=params,
        headers=HEADERS,
        timeout=15,
    )

    return response


def parse_response(response):
    result = {
        "http_status": response.status_code,
        "response_ok": response.ok,
        "candle_count": 0,
        "newest_candle": None,
        "oldest_candle": None,
        "error": None,
    }

    try:
        data = response.json()
    except Exception:
        result["error"] = f"Non-JSON response: {response.text[:500]}"
        return result, None

    if not response.ok:
        result["error"] = data
        return result, data

    if not isinstance(data, list):
        result["error"] = f"Unexpected JSON type: {type(data).__name__}"
        return result, data

    result["candle_count"] = len(data)

    if data:
        result["newest_candle"] = data[0].get("candle_date_time_utc")
        result["oldest_candle"] = data[-1].get("candle_date_time_utc")

    return result, data


def find_oldest_candle(market, initial_to):
    """
    Upbit day candle API를 과거 방향으로 반복 조회해서
    현재 API에서 반환 가능한 가장 오래된 candle을 찾는다.

    주의:
    - candle이 없는 날짜는 Upbit 정책상 생성되지 않을 수 있다.
    - 따라서 날짜 연속성을 가정하지 않는다.
    """

    current_to = initial_to
    oldest_candle = None
    total_received = 0
    page_count = 0
    last_http_status = None

    while True:
        response = request_daily_candles(
            market=market,
            to=current_to,
            count=200,
        )

        last_http_status = response.status_code
        page_count += 1

        if not response.ok:
            try:
                error_body = response.json()
            except Exception:
                error_body = response.text[:500]

            return {
                "oldest_candle": oldest_candle,
                "pages_checked": page_count,
                "total_candles_received": total_received,
                "last_http_status": last_http_status,
                "pagination_error": error_body,
            }

        try:
            data = response.json()
        except Exception as exc:
            return {
                "oldest_candle": oldest_candle,
                "pages_checked": page_count,
                "total_candles_received": total_received,
                "last_http_status": last_http_status,
                "pagination_error": f"JSON decode error: {exc}",
            }

        if not isinstance(data, list):
            return {
                "oldest_candle": oldest_candle,
                "pages_checked": page_count,
                "total_candles_received": total_received,
                "last_http_status": last_http_status,
                "pagination_error": "Unexpected response type",
            }

        if not data:
            break

        total_received += len(data)

        page_oldest = data[-1].get("candle_date_time_utc")

        if not page_oldest:
            break

        oldest_candle = page_oldest

        # Upbit의 'to'는 exclusive 성격으로 다음 과거 페이지 조회에 사용
        current_to = page_oldest + "Z"

        print(
            f"    page={page_count:03d} "
            f"candles={len(data):3d} "
            f"oldest={page_oldest}"
        )

        # API 호출 간격
        time.sleep(0.15)

        # 200개 미만이면 사실상 마지막 페이지일 가능성이 높지만,
        # 한 번 더 요청하여 실제 빈 응답을 확인한다.
        if page_count >= 500:
            return {
                "oldest_candle": oldest_candle,
                "pages_checked": page_count,
                "total_candles_received": total_received,
                "last_http_status": last_http_status,
                "pagination_error": "Safety limit reached (500 pages)",
            }

    return {
        "oldest_candle": oldest_candle,
        "pages_checked": page_count,
        "total_candles_received": total_received,
        "last_http_status": last_http_status,
        "pagination_error": None,
    }


def run_test(test_case):
    market = test_case["market"]
    test_to = test_case["test_to"]

    print()
    print("=" * 80)
    print(f"MARKET : {market}")
    print(f"TEST TO: {test_to}")
    print("=" * 80)

    response = request_daily_candles(
        market=market,
        to=test_to,
        count=5,
    )

    initial_result, data = parse_response(response)

    print(f"HTTP status : {initial_result['http_status']}")
    print(f"Candle count: {initial_result['candle_count']}")
    print(f"Newest      : {initial_result['newest_candle']}")
    print(f"Oldest      : {initial_result['oldest_candle']}")

    if initial_result["error"] is not None:
        print("ERROR:")
        print(json.dumps(initial_result["error"], ensure_ascii=False, indent=2))

    result = {
        "market": market,
        "requested_to": test_to,
        "http_status": initial_result["http_status"],
        "candle_returned": initial_result["candle_count"] > 0,
        "initial_candle_count": initial_result["candle_count"],
        "initial_newest_candle": initial_result["newest_candle"],
        "initial_oldest_candle": initial_result["oldest_candle"],
        "oldest_available_candle": None,
        "pages_checked": 0,
        "total_candles_received": 0,
        "pagination_error": None,
        "initial_error": initial_result["error"],
    }

    # 최초 요청에서 실제 candle이 반환된 경우에만
    # 과거 방향으로 pagination을 진행한다.
    if response.ok and isinstance(data, list) and data:
        print()
        print("Searching for oldest available candle...")

        oldest_result = find_oldest_candle(
            market=market,
            initial_to=test_to,
        )

        result["oldest_available_candle"] = oldest_result["oldest_candle"]
        result["pages_checked"] = oldest_result["pages_checked"]
        result["total_candles_received"] = oldest_result[
            "total_candles_received"
        ]
        result["pagination_error"] = oldest_result["pagination_error"]

        print()
        print(
            "OLDEST AVAILABLE:",
            result["oldest_available_candle"],
        )

    return result


def main():
    started_at = datetime.now(timezone.utc).isoformat()

    print("Upbit delisted KRW market candle diagnostic")
    print(f"Started UTC: {started_at}")
    print(f"Endpoint   : {UPBIT_DAY_CANDLE_URL}")

    results = []

    for test_case in TEST_CASES:
        result = run_test(test_case)
        results.append(result)

        time.sleep(0.3)

    finished_at = datetime.now(timezone.utc).isoformat()

    output = {
        "diagnostic": "upbit_delisted_daily_candle",
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "endpoint": UPBIT_DAY_CANDLE_URL,
        "results": results,
    }

    output_path = "delisted_upbit_diagnostic.json"

    with open(output_path, "w", encoding="utf-8") as f:
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

    for r in results:
        print(
            f"{r['market']:12s} | "
            f"HTTP={r['http_status']} | "
            f"candles={r['initial_candle_count']:3d} | "
            f"returned={str(r['candle_returned']):5s} | "
            f"oldest={r['oldest_available_candle']}"
        )

    print()
    print(f"Result saved: {output_path}")


if __name__ == "__main__":
    main()

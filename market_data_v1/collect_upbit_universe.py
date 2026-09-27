import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import requests


# ============================================================
# Market Data V1.0
# Upbit KRW Point-in-Time Universe Collector
#
# 목적:
#   - Upbit KRW 마켓 전체 Universe를 30분 슬롯 단위로 보존
#   - 향후 30분봉 / rolling-24h snapshot과 동일한
#     snapshot_time_utc 기준으로 정확하게 결합
#
# 중요:
#   - first_seen_utc / last_seen_utc는 "우리 수집기가 관측한 시각"
#   - 공식 상장일 / 상장폐지일을 의미하지 않음
#   - 기존 A V1.3 파일은 수정하지 않음
# ============================================================


UPBIT_BASE_URL = "https://api.upbit.com"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = PROJECT_ROOT / "data_market"
UNIVERSE_ROOT = OUTPUT_ROOT / "universe"

SNAPSHOT_DIR = UNIVERSE_ROOT / "snapshots_30m"
HISTORY_FILE = UNIVERSE_ROOT / "upbit_krw_history.csv"

SCHEMA_VERSION = "market-data-v1"

HISTORY_FIELDS = [
    "market",
    "korean_name",
    "english_name",
    "first_seen_utc",
    "last_seen_utc",
    "status",
]


def utc_now():
    """
    timezone-aware UTC 현재시각 반환.
    """
    return datetime.now(timezone.utc)


def format_utc(dt):
    """
    UTC datetime을 ISO-8601 Z 형식으로 변환.
    예:
        2026-09-27T14:01:58Z
    """
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_30m_slot(dt):
    """
    주어진 UTC 시각을 30분 슬롯 시작시각으로 내림(floor).

    예:
        14:01:58 -> 14:00:00
        14:29:59 -> 14:00:00
        14:30:05 -> 14:30:00
        14:59:59 -> 14:30:00
    """
    dt = dt.astimezone(timezone.utc)

    slot_minute = 0 if dt.minute < 30 else 30

    return dt.replace(
        minute=slot_minute,
        second=0,
        microsecond=0,
    )


def build_snapshot_filename(snapshot_time):
    """
    snapshot_time_utc와 파일명 기준시각을 동일하게 유지.

    예:
        2026-09-27T14:00:00Z
        ->
        universe_20260927_140000.json
    """
    return (
        f"universe_"
        f"{snapshot_time.strftime('%Y%m%d_%H%M%S')}.json"
    )


def fetch_upbit_markets():
    """
    Upbit 전체 마켓 조회.
    is_details=true로 market_event 정보도 함께 수집.
    """
    url = f"{UPBIT_BASE_URL}/v1/market/all"

    response = requests.get(
        url,
        params={"is_details": "true"},
        timeout=15,
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        raise RuntimeError(
            "Unexpected Upbit API response: expected list."
        )

    return data


def extract_krw_markets(all_markets):
    """
    전체 Upbit 마켓 중 KRW 마켓만 추출.
    market code 기준 정렬.
    """
    krw_markets = []

    for item in all_markets:
        market = item.get("market", "")

        if not market.startswith("KRW-"):
            continue

        krw_markets.append(
            {
                "market": market,
                "korean_name": item.get("korean_name", ""),
                "english_name": item.get("english_name", ""),
                "market_event": item.get("market_event"),
            }
        )

    krw_markets.sort(key=lambda x: x["market"])

    return krw_markets


def load_history():
    """
    기존 Universe history CSV 로드.

    반환:
        {
            "KRW-BTC": {...},
            ...
        }
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

            history[market] = {
                "market": market,
                "korean_name": row.get("korean_name", ""),
                "english_name": row.get("english_name", ""),
                "first_seen_utc": row.get("first_seen_utc", ""),
                "last_seen_utc": row.get("last_seen_utc", ""),
                "status": row.get("status", ""),
            }

    return history


def update_history(history, krw_markets, collected_at_utc):
    """
    현재 관측된 KRW Universe를 기준으로 history 갱신.

    first_seen_utc:
        우리 수집기가 해당 market을 처음 관측한 실제 수집시각.

    last_seen_utc:
        우리 수집기가 해당 market을 가장 최근 관측한 실제 수집시각.

    주의:
        공식 상장일 / 상폐일을 의미하지 않는다.
    """
    current_market_codes = {
        item["market"]
        for item in krw_markets
    }

    # 현재 관측된 market 갱신
    for item in krw_markets:
        market = item["market"]

        if market in history:
            history[market]["korean_name"] = item["korean_name"]
            history[market]["english_name"] = item["english_name"]
            history[market]["last_seen_utc"] = collected_at_utc
            history[market]["status"] = "ACTIVE"

        else:
            history[market] = {
                "market": market,
                "korean_name": item["korean_name"],
                "english_name": item["english_name"],
                "first_seen_utc": collected_at_utc,
                "last_seen_utc": collected_at_utc,
                "status": "ACTIVE",
            }

    # 과거에는 관측됐지만 현재 응답에는 없는 market
    #
    # DELISTED라고 단정하지 않는다.
    # API 일시 오류, 마켓 상태 변화 등 다른 원인이 있을 수 있으므로
    # "현재 관측되지 않음"으로만 기록한다.
    for market, row in history.items():
        if market not in current_market_codes:
            row["status"] = "NOT_OBSERVED_CURRENTLY"

    return history


def atomic_write_json(path, payload):
    """
    임시 파일 작성 후 replace.
    불완전한 JSON 파일이 남는 위험을 줄인다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_suffix(
        path.suffix + ".tmp"
    )

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

    temp_path.replace(path)


def atomic_write_history(history):
    """
    Universe history CSV atomic write.
    """
    HISTORY_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = HISTORY_FILE.with_suffix(
        HISTORY_FILE.suffix + ".tmp"
    )

    rows = sorted(
        history.values(),
        key=lambda x: x["market"],
    )

    with temp_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=HISTORY_FIELDS,
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(
                {
                    field: row.get(field, "")
                    for field in HISTORY_FIELDS
                }
            )

    temp_path.replace(HISTORY_FILE)


def main():
    print("=" * 72)
    print(
        "Market Data V1.0 - "
        "Upbit KRW Point-in-Time Universe Collector"
    )
    print("=" * 72)

    # --------------------------------------------------------
    # 1. 실제 API 수집시각 확정
    # --------------------------------------------------------

    collected_dt = utc_now()
    collected_at_utc = format_utc(collected_dt)

    # --------------------------------------------------------
    # 2. 30분 결합 기준 슬롯 계산
    # --------------------------------------------------------

    snapshot_dt = get_30m_slot(collected_dt)
    snapshot_time_utc = format_utc(snapshot_dt)

    snapshot_filename = build_snapshot_filename(
        snapshot_dt
    )

    snapshot_path = (
        SNAPSHOT_DIR / snapshot_filename
    )

    # --------------------------------------------------------
    # 3. Upbit Universe 수집
    # --------------------------------------------------------

    all_markets = fetch_upbit_markets()

    krw_markets = extract_krw_markets(
        all_markets
    )

    # 안전장치:
    # API 이상으로 KRW market이 0개가 된 경우
    # 기존 정상 history/snapshot을 훼손하지 않는다.
    if not krw_markets:
        raise RuntimeError(
            "No KRW markets returned from Upbit. "
            "Existing files were not modified."
        )

    # 중복 market 안전검사
    market_codes = [
        item["market"]
        for item in krw_markets
    ]

    if len(market_codes) != len(set(market_codes)):
        raise RuntimeError(
            "Duplicate KRW market codes detected. "
            "Existing files were not modified."
        )

    # --------------------------------------------------------
    # 4. Point-in-Time snapshot 구성
    # --------------------------------------------------------

    snapshot_payload = {
        "schema_version": SCHEMA_VERSION,

        "source": "UPBIT",

        "market_type": "KRW",

        # 실제 API를 수집한 시각
        "collected_at_utc": collected_at_utc,

        # 모든 Market Data 계층에서 사용할
        # 30분 결합 기준시각
        "snapshot_time_utc": snapshot_time_utc,

        "snapshot_interval": "30m",

        "market_count": len(krw_markets),

        "markets": krw_markets,
    }

    # --------------------------------------------------------
    # 5. 기존 history 로드 및 갱신
    # --------------------------------------------------------

    history = load_history()

    history = update_history(
        history=history,
        krw_markets=krw_markets,
        collected_at_utc=collected_at_utc,
    )

    # --------------------------------------------------------
    # 6. 파일 저장
    #
    # 같은 30분 슬롯 내 재실행 시:
    # 같은 snapshot 파일을 갱신한다.
    #
    # 따라서 슬롯당 최종 snapshot은 1개만 유지된다.
    # --------------------------------------------------------

    atomic_write_json(
        snapshot_path,
        snapshot_payload,
    )

    atomic_write_history(history)

    # --------------------------------------------------------
    # 7. 결과 출력
    # --------------------------------------------------------

    print(f"Collected at UTC  : {collected_at_utc}")
    print(f"Snapshot time UTC : {snapshot_time_utc}")

    print(
        f"All Upbit markets : "
        f"{len(all_markets)}"
    )

    print(
        f"KRW markets       : "
        f"{len(krw_markets)}"
    )

    print()

    print(
        f"30m snapshot : "
        f"{snapshot_path}"
    )

    print(
        f"History file : "
        f"{HISTORY_FILE}"
    )

    print()

    print(
        "Universe collection completed successfully."
    )


if __name__ == "__main__":
    main()

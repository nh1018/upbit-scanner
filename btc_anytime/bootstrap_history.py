import json
from pathlib import Path
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "output" / "btc_anytime_latest.json"
OUTPUT_ROOT = ROOT / "data_market" / "btc_anytime"

TIMEFRAMES = ("15m", "1h", "4h", "1d")


def iso_utc_from_ms(ms):
    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def main():
    if not SOURCE.exists():
        raise FileNotFoundError(f"Seed file not found: {SOURCE}")

    with SOURCE.open("r", encoding="utf-8") as f:
        source = json.load(f)

    # Collector JSON의 실제 구조에 맞춰 candles 영역 확인
    candles_root = source.get("candles")

    if not isinstance(candles_root, dict):
        raise ValueError("Missing or invalid 'candles' object in seed JSON")

    print(f"Seed source: {SOURCE}")

    total_written = 0

    for timeframe in TIMEFRAMES:
        candles = candles_root.get(timeframe)

        if not isinstance(candles, list):
            raise ValueError(
                f"Missing candle list for timeframe: {timeframe}"
            )

        output_dir = OUTPUT_ROOT / timeframe
        output_dir.mkdir(parents=True, exist_ok=True)

        output_file = output_dir / f"btc_{timeframe}_history.jsonl"

        rows = []
        seen = set()

        for candle in candles:
            # Bootstrap에는 확정봉만 사용
            if candle.get("is_closed") is not True:
                continue

            open_time_ms = candle.get("open_time_ms")

            if open_time_ms is None:
                raise ValueError(
                    f"{timeframe}: candle missing open_time_ms"
                )

            if open_time_ms in seen:
                continue

            seen.add(open_time_ms)

            row = {
                "symbol": source.get("symbol", "BTCUSDT"),
                "timeframe": timeframe,
                "time": open_time_ms,
                "candle_time_utc": candle.get(
                    "open_time_utc",
                    iso_utc_from_ms(open_time_ms)
                ),
                "open": candle.get("open"),
                "high": candle.get("high"),
                "low": candle.get("low"),
                "close": candle.get("close"),
                "volume": candle.get("volume"),
                "quote_volume": candle.get("quote_volume"),
                "trade_count": candle.get("trade_count"),
                "taker_buy_base": candle.get("taker_buy_base"),
                "taker_buy_quote": candle.get("taker_buy_quote"),
                "source": "binance_futures_seed"
            }

            rows.append(row)

        rows.sort(key=lambda x: x["time"])

        with output_file.open("w", encoding="utf-8", newline="\n") as f:
            for row in rows:
                f.write(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        separators=(",", ":")
                    )
                    + "\n"
                )

        print(
            f"{timeframe}: "
            f"source={len(candles)}, "
            f"closed_written={len(rows)}, "
            f"output={output_file}"
        )

        total_written += len(rows)

    print(f"Total closed candles written: {total_written}")


if __name__ == "__main__":
    main()

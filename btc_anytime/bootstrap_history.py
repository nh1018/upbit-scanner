import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "output" / "btc_anytime_latest.json"
OUTPUT_ROOT = ROOT / "data_market" / "btc_anytime"

TIMEFRAMES = ("15m", "1h", "4h", "1d")


def main():
    if not SOURCE.exists():
        raise FileNotFoundError(f"Seed file not found: {SOURCE}")

    with SOURCE.open("r", encoding="utf-8") as f:
        source = json.load(f)

    klines = source.get("klines")

    if not isinstance(klines, dict):
        raise ValueError("Missing or invalid 'klines' object in seed JSON")

    print(f"Seed source: {SOURCE}")
    print(f"Generated at: {source.get('generated_at_utc')}")

    total_written = 0

    for timeframe in TIMEFRAMES:
        timeframe_data = klines.get(timeframe)

        if not isinstance(timeframe_data, dict):
            raise ValueError(
                f"Missing timeframe object: klines.{timeframe}"
            )

        candles = timeframe_data.get("candles")

        if not isinstance(candles, list):
            raise ValueError(
                f"Missing candle list: klines.{timeframe}.candles"
            )

        output_dir = OUTPUT_ROOT / timeframe
        output_dir.mkdir(parents=True, exist_ok=True)

        output_file = output_dir / f"btc_{timeframe}_history.jsonl"

        rows = []
        seen = set()
        skipped_in_progress = 0

        for candle in candles:

            # 완료된 캔들만 Seed History에 저장
            if candle.get("is_closed") is not True:
                skipped_in_progress += 1
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
                "symbol": source.get("symbol"),
                "market": source.get("market"),
                "timeframe": timeframe,

                "time": open_time_ms,
                "candle_time_utc": candle.get("open_time_utc"),

                "close_time_ms": candle.get("close_time_ms"),
                "close_time_utc": candle.get("close_time_utc"),

                "open": candle.get("open"),
                "high": candle.get("high"),
                "low": candle.get("low"),
                "close": candle.get("close"),

                "volume": candle.get("volume"),
                "quote_volume": candle.get("quote_volume"),
                "trade_count": candle.get("trade_count"),

                "taker_buy_base": candle.get("taker_buy_base"),
                "taker_buy_quote": candle.get("taker_buy_quote"),

                "source": "binance_futures_seed",
                "seed_generated_at_utc": source.get(
                    "generated_at_utc"
                )
            }

            rows.append(row)

        rows.sort(key=lambda x: x["time"])

        with output_file.open(
            "w",
            encoding="utf-8",
            newline="\n"
        ) as f:
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
            f"in_progress_skipped={skipped_in_progress}"
        )

        print(f"Output: {output_file}")

        total_written += len(rows)

    print("=" * 60)
    print(f"Total closed candles written: {total_written}")


if __name__ == "__main__":
    main()

from dataclasses import dataclass
from decimal import Decimal

SCHEMA_VERSION = "upbit-b-market-data-1"
DURATIONS = {"1d": 86400000, "4h": 14400000, "1h": 3600000}
WINDOWS = {"1d": 100, "4h": 150, "1h": 200}
MAPPING_STATES = frozenset({"VERIFIED", "UNVERIFIED", "NO_SYMBOL", "AMBIGUOUS", "NO_ELIGIBLE_SYMBOL", "API_UNAVAILABLE"})

@dataclass(frozen=True)
class Candle:
    provider: str
    instrument: str
    timeframe: str
    open_ms: int
    close_ms: int  # exclusive boundary
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    base_volume: Decimal
    quote_trade_amount: Decimal
    completed: bool = False

    @property
    def natural_key(self):
        return self.provider, self.instrument, self.timeframe, self.open_ms

    @property
    def schema_version(self):
        return SCHEMA_VERSION

    @property
    def quote_unit(self):
        return "KRW" if self.provider == "UPBIT" else "USDT"

    @property
    def market_type(self):
        return "SPOT"

@dataclass(frozen=True)
class Window:
    status: str
    candles: tuple
    cutoff_ms: int
    evidence: tuple
    input_sha256: str
    missing_slots: tuple = ()
    reason: str | None = None

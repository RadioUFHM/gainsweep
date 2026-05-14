from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass
class PriceQuote:
    symbol: str
    price: Decimal
    timestamp: datetime  # UTC, tz-aware
    source: str
    confidence: float = field(default=1.0)  # reserved for aggregation; unused at MVP


class PriceProvider(Protocol):
    def get_price(self, symbol: str, vs: str = "USD") -> PriceQuote: ...
    def get_batch(self, symbols: list[str], vs: str = "USD") -> dict[str, PriceQuote]: ...
    def get_historical_hourly(
        self, symbol: str, start: datetime, end: datetime, vs: str = "USD"
    ) -> list[PriceQuote]: ...

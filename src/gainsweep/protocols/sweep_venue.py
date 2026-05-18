from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID


@dataclass
class SweepEstimate:
    venue: str
    expected_proceeds: Decimal
    estimated_fees: Decimal
    estimated_slippage_pct: float
    estimated_completion_seconds: int


@dataclass
class SweepResult:
    venue: str
    token_symbol: str
    qty_executed: Decimal
    target_stablecoin: str
    proceeds: Decimal
    fees_paid: Decimal
    executed_at: datetime
    venue_txn_ids: list[str]
    status: Literal["COMPLETE", "PARTIAL", "FAILED"]
    error_message: str | None = field(default=None)


class SweepVenue(Protocol):
    def get_supported_tokens(self) -> set[str]: ...
    def estimate_sweep(
        self, merchant_id: UUID, token: str, qty: Decimal, target: str
    ) -> SweepEstimate: ...
    def execute_sweep(
        self, merchant_id: UUID, token: str, qty: Decimal, target: str
    ) -> SweepResult: ...

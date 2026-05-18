from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Protocol


class SweepOrchestrator(Protocol):
    def execute(
        self,
        merchant_id: uuid.UUID,
        token: str,
        qty: Decimal,
        triggered_by_alert_id: uuid.UUID | None = None,
        triggered_by_schedule: bool = False,
    ) -> uuid.UUID: ...


class NotImplementedSweepOrchestrator:
    """Sweep stub for Phase 1–3. Phase 4 wires in CoinbaseSweepVenue."""

    def execute(
        self,
        merchant_id: uuid.UUID,
        token: str,
        qty: Decimal,
        triggered_by_alert_id: uuid.UUID | None = None,
        triggered_by_schedule: bool = False,
    ) -> uuid.UUID:
        raise NotImplementedError("SweepOrchestrator is implemented in Phase 4")


_: SweepOrchestrator = NotImplementedSweepOrchestrator()  # type: ignore[assignment]

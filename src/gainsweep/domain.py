from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

from gainsweep.db.models import AlertState


@dataclass
class HourlyClose:
    """One hour's closing price for a token. The close is the last price
    observation recorded during [H:00:00, H+1:00:00). (§5.3)"""

    hour: int  # 0–23 in the merchant's daily_window_timezone
    close: Decimal
    ts: datetime  # UTC timestamp of the last observation in this hour

    def to_dict(self) -> dict[str, object]:
        return {"hour": self.hour, "close": str(self.close), "ts": self.ts.isoformat()}

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> HourlyClose:
        return cls(
            hour=int(d["hour"]),  # type: ignore[arg-type]
            close=Decimal(str(d["close"])),
            ts=datetime.fromisoformat(str(d["ts"])),
        )


@dataclass
class SnapshotData:
    """In-memory representation of a TokenDailySnapshot.

    Used by DailyHighTracker and AlertEngine so they can be tested and
    back-tested without a live database. The PostgresSnapshotRepository
    (Phase 3) converts to/from the SQLAlchemy ORM model.
    """

    merchant_id: uuid.UUID
    token_symbol: str
    date: date
    hourly_closes: list[HourlyClose] = field(default_factory=list)
    daily_high: Decimal = field(default=Decimal("0"))
    daily_high_hour: int | None = field(default=None)
    current_price: Decimal = field(default=Decimal("0"))
    current_price_ts: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    position_qty: Decimal = field(default=Decimal("0"))
    cost_basis_avg: Decimal | None = field(default=None)
    alert_state: AlertState = field(default=AlertState.ARMED)
    snooze_active: bool = field(default=False)
    snooze_trigger_price: Decimal | None = field(default=None)
    last_alert_id: uuid.UUID | None = field(default=None)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ConfigData:
    """In-memory representation of a MerchantAlertConfig."""

    merchant_id: uuid.UUID
    rearm_on_new_high: bool = True
    drawdown_threshold_pct: Decimal = field(default=Decimal("5.0"))
    daily_window_timezone: str = "UTC"
    auto_sweep_enabled: bool = False
    auto_sweep_timeout_minutes: int = 30
    stablecoin_depeg_floor: Decimal = field(default=Decimal("0.97"))
    target_stablecoin: str = "USDC"

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from gainsweep.db.models import AlertState
from gainsweep.domain import ConfigData, HourlyClose, SnapshotData
from gainsweep.repositories.config import ConfigRepository
from gainsweep.repositories.snapshot import SnapshotRepository

log = logging.getLogger(__name__)


@dataclass
class _Tick:
    price: Decimal
    ts: datetime


class DailyHighTracker:
    """Consumes price ticks, maintains hourly closes and daily highs. (§5.3)

    Inject SnapshotRepository and ConfigRepository — use InMemory* variants
    for tests and the backtest harness; Postgres* variants for production.
    """

    def __init__(
        self,
        snapshot_repo: SnapshotRepository,
        config_repo: ConfigRepository,
    ) -> None:
        self._snapshots = snapshot_repo
        self._configs = config_repo
        # (merchant_id, token_symbol) → buffered ticks awaiting finalize_hour
        self._buffer: dict[tuple[uuid.UUID, str], list[_Tick]] = {}

    # ── public interface (§5.3) ───────────────────────────────────────────────

    def on_price_tick(
        self, merchant_id: uuid.UUID, token: str, price: Decimal, ts: datetime
    ) -> None:
        """Record a new price observation; update current_price on the snapshot."""
        d = _merchant_date(ts, self._merchant_tz(merchant_id))
        snapshot = self._snapshots.get_or_create(merchant_id, token, d)
        snapshot.current_price = price
        snapshot.current_price_ts = ts
        snapshot.updated_at = ts
        self._snapshots.save(snapshot)

        self._buffer.setdefault((merchant_id, token), []).append(
            _Tick(price=price, ts=ts)
        )
        log.debug(
            "tracker.tick",
            extra={"merchant_id": merchant_id, "token": token, "price": str(price)},
        )

    def finalize_hour(self, hour_start: datetime) -> None:
        """Promote buffered ticks for [hour_start, hour_start+1h) to hourly closes.

        Called on each UTC hour boundary. Recomputes daily_high and applies
        re-arm logic for any snapshot whose high increased. (§5.3)
        """
        hour_end = hour_start + timedelta(hours=1)

        for (merchant_id, token), ticks in list(self._buffer.items()):
            in_window = [t for t in ticks if hour_start <= t.ts < hour_end]
            # Keep ticks that belong to a future hour
            self._buffer[(merchant_id, token)] = [t for t in ticks if t.ts >= hour_end]

            if not in_window:
                # No observation this hour — skip; do not interpolate (§5.3)
                continue

            last_tick = in_window[-1]
            tz = self._merchant_tz(merchant_id)
            snapshot_date = _merchant_date(hour_start, tz)

            snapshot = self._snapshots.get(merchant_id, token, snapshot_date)
            if snapshot is None:
                log.warning(
                    "tracker.finalize_hour.missing_snapshot",
                    extra={"merchant_id": merchant_id, "token": token},
                )
                continue

            self._apply_close(snapshot, hour_start.hour, last_tick, merchant_id)

    def reset_for_new_day(
        self, merchant_id: uuid.UUID, token: str, tz: str
    ) -> SnapshotData:
        """Create a fresh ARMED snapshot for today in the merchant's timezone. (§5.3)

        Carries forward current_price from the previous day if available so
        the first tick of the new day doesn't start from zero.
        """
        today = datetime.now(ZoneInfo(tz)).date()
        prev = self._snapshots.get_latest(merchant_id, token)
        carry_price = prev.current_price if prev else Decimal("0")
        carry_ts = prev.current_price_ts if prev else datetime.now(timezone.utc)

        snapshot = SnapshotData(
            merchant_id=merchant_id,
            token_symbol=token,
            date=today,
            current_price=carry_price,
            current_price_ts=carry_ts,
            alert_state=AlertState.ARMED,
        )
        self._snapshots.save(snapshot)
        log.info(
            "tracker.new_day",
            extra={"merchant_id": merchant_id, "token": token, "date": str(today)},
        )
        return snapshot

    # ── private helpers ───────────────────────────────────────────────────────

    def _apply_close(
        self,
        snapshot: SnapshotData,
        hour: int,
        tick: _Tick,
        merchant_id: uuid.UUID,
    ) -> None:
        # Replace any existing close for this hour, then append the new one
        snapshot.hourly_closes = [c for c in snapshot.hourly_closes if c.hour != hour]
        snapshot.hourly_closes.append(HourlyClose(hour=hour, close=tick.price, ts=tick.ts))

        old_high = snapshot.daily_high
        closes = [c.close for c in snapshot.hourly_closes]
        new_high = max(closes)

        snapshot.daily_high = new_high
        snapshot.daily_high_hour = max(snapshot.hourly_closes, key=lambda c: c.close).hour
        snapshot.updated_at = datetime.now(timezone.utc)

        if new_high > old_high:
            self._maybe_rearm(snapshot, merchant_id)

        self._snapshots.save(snapshot)

    def _maybe_rearm(self, snapshot: SnapshotData, merchant_id: uuid.UUID) -> None:
        """Re-arm an alert when a new daily high is set (§5.3 re-arm logic)."""
        config = self._configs.get(merchant_id)
        if not config or not config.rearm_on_new_high:
            return
        if snapshot.alert_state not in (AlertState.FIRED, AlertState.SNOOZED):
            return

        snapshot.alert_state = AlertState.ARMED
        if snapshot.snooze_active:
            snapshot.snooze_active = False
            snapshot.snooze_trigger_price = None
        log.info(
            "tracker.rearm",
            extra={"merchant_id": merchant_id, "token": snapshot.token_symbol},
        )

    def _merchant_tz(self, merchant_id: uuid.UUID) -> str:
        config = self._configs.get(merchant_id)
        return config.daily_window_timezone if config else "UTC"


def _merchant_date(ts: datetime, tz: str) -> date:
    """Convert a UTC timestamp to a calendar date in the merchant's timezone."""
    return ts.astimezone(ZoneInfo(tz)).date()

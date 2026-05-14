from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from gainsweep.db.models import AlertKind, AlertResponse, AlertState
from gainsweep.domain import AlertData, ConfigData, SnapshotData
from gainsweep.protocols.price_provider import PriceProvider
from gainsweep.protocols.scheduler import JobScheduler
from gainsweep.protocols.sweep_orchestrator import SweepOrchestrator
from gainsweep.repositories.alert import AlertRepository
from gainsweep.repositories.config import ConfigRepository
from gainsweep.repositories.snapshot import SnapshotRepository
from gainsweep.tracker.daily_high import _merchant_date

log = logging.getLogger(__name__)

_DEFAULT_SNOOZE_PCT = Decimal("3.0")


class AlertEngine:
    """Evaluates snapshots for drawdown / snooze conditions and emits alerts. (§5.4)

    Called after every on_price_tick. Depends on injected repositories and
    protocols so it can run identically in production, tests, and backtests.
    """

    def __init__(
        self,
        snapshot_repo: SnapshotRepository,
        config_repo: ConfigRepository,
        alert_repo: AlertRepository,
        price_provider: PriceProvider,
        job_scheduler: JobScheduler,
        sweep_orchestrator: SweepOrchestrator,
    ) -> None:
        self._snapshots = snapshot_repo
        self._configs = config_repo
        self._alerts = alert_repo
        self._price_provider = price_provider
        self._scheduler = job_scheduler
        self._sweep = sweep_orchestrator

    # ── public interface ──────────────────────────────────────────────────────

    def evaluate(self, merchant_id: uuid.UUID, token: str) -> AlertData | None:
        """Check the current snapshot for alert conditions. Returns the fired
        alert, or None if no alert was warranted."""
        config = self._configs.get(merchant_id)
        if config is None:
            log.warning("alert_engine.no_config", extra={"merchant_id": str(merchant_id)})
            return None

        today = _merchant_date(datetime.now(timezone.utc), config.daily_window_timezone)
        snapshot = self._snapshots.get(merchant_id, token, today)
        if snapshot is None or snapshot.daily_high_hour is None:
            return None  # no hourly close yet today — nothing to evaluate

        if not self._should_fire(snapshot, config):
            return None

        # ── stablecoin health gate (§5.4, §3 principle 3) ────────────────────
        try:
            stable_quote = self._price_provider.get_price(config.target_stablecoin)
        except KeyError:
            # Price unavailable — fail safe: do not fire a sweep recommendation
            log.warning(
                "alert_engine.stablecoin_price_unavailable",
                extra={"stablecoin": config.target_stablecoin},
            )
            return None

        if stable_quote.price < config.stablecoin_depeg_floor:
            return self._emit(snapshot, config, AlertKind.STABLECOIN_DEPEG)

        # ── fire drawdown (or snooze re-trigger) alert ────────────────────────
        alert = self._emit(snapshot, config, AlertKind.DRAWDOWN)
        snapshot.alert_state = AlertState.FIRED
        snapshot.last_alert_id = alert.id
        self._snapshots.save(snapshot)

        if config.auto_sweep_enabled:
            snapshot.alert_state = AlertState.AUTO_SWEEP_PENDING
            self._snapshots.save(snapshot)
            self._scheduler.schedule(
                "auto_sweep_timeout",
                run_at=datetime.now(timezone.utc)
                + timedelta(minutes=config.auto_sweep_timeout_minutes),
                payload={"alert_id": str(alert.id)},
            )

        return alert

    def respond(
        self,
        alert_id: uuid.UUID,
        action: AlertResponse,
        snooze_delta_pct: Decimal | None = None,
    ) -> AlertData:
        """Process a merchant response to an alert. (§5.5)"""
        alert = self._alerts.get(alert_id)
        if alert is None:
            raise AlertNotFound(alert_id)
        if alert.response is not None:
            raise AlreadyResponded(alert.response)

        snapshot = self._snapshots.get(
            alert.snapshot_merchant_id,
            alert.snapshot_token_symbol,
            alert.snapshot_date,
        )
        if snapshot is None:
            raise SnapshotNotFound(alert_id)

        if action == AlertResponse.SWEEP:
            sweep_id = self._sweep.execute(
                merchant_id=alert.merchant_id,
                token=snapshot.token_symbol,
                qty=snapshot.position_qty,
                triggered_by_alert_id=alert.id,
            )
            snapshot.alert_state = AlertState.RESOLVED_SWEEP
            alert.response = AlertResponse.SWEEP
            alert.resulting_sweep_id = sweep_id

        elif action == AlertResponse.HODL:
            snapshot.alert_state = AlertState.RESOLVED_HODL
            alert.response = AlertResponse.HODL

        elif action == AlertResponse.SNOOZE:
            if snooze_delta_pct is None or snooze_delta_pct <= Decimal("0"):
                raise InvalidSnooze(snooze_delta_pct)
            trigger = snapshot.current_price * (
                Decimal("1") - snooze_delta_pct / Decimal("100")
            )
            snapshot.alert_state = AlertState.SNOOZED
            snapshot.snooze_active = True
            snapshot.snooze_trigger_price = trigger
            alert.response = AlertResponse.SNOOZE

        else:
            raise ValueError(f"Unknown action: {action!r}")

        alert.responded_at = datetime.now(timezone.utc)
        self._alerts.save(alert)
        self._snapshots.save(snapshot)

        log.info(
            "alert_engine.responded",
            extra={
                "alert_id": str(alert_id),
                "action": action.value,
                "merchant_id": str(alert.merchant_id),
            },
        )
        return alert

    # ── private helpers ───────────────────────────────────────────────────────

    def _should_fire(self, snapshot: SnapshotData, config: ConfigData) -> bool:
        if snapshot.snooze_active and snapshot.alert_state == AlertState.SNOOZED:
            if snapshot.snooze_trigger_price is None:
                return False
            return snapshot.current_price <= snapshot.snooze_trigger_price
        if snapshot.alert_state == AlertState.ARMED:
            drawdown_pct = (
                (snapshot.daily_high - snapshot.current_price)
                / snapshot.daily_high
                * Decimal("100")
            )
            return drawdown_pct >= config.drawdown_threshold_pct
        return False  # FIRED, AUTO_SWEEP_PENDING, RESOLVED_*

    def _emit(
        self,
        snapshot: SnapshotData,
        config: ConfigData,
        kind: AlertKind,
    ) -> AlertData:
        high_close = next(
            (c for c in snapshot.hourly_closes if c.hour == snapshot.daily_high_hour),
            None,
        )
        drawdown_pct = (
            float(
                (snapshot.daily_high - snapshot.current_price)
                / snapshot.daily_high
                * Decimal("100")
            )
            if snapshot.daily_high > Decimal("0")
            else 0.0
        )
        payload: dict[str, Any] = {
            "token": snapshot.token_symbol,
            "daily_high": str(snapshot.daily_high),
            "daily_high_at": high_close.ts.isoformat() if high_close else None,
            "current_price": str(snapshot.current_price),
            "drawdown_pct": round(drawdown_pct, 4),
            "position_qty": str(snapshot.position_qty),
            "position_value_usd": str(
                snapshot.current_price * snapshot.position_qty
            ),
            "unrealized_vs_high_usd": str(
                (snapshot.current_price - snapshot.daily_high) * snapshot.position_qty
            ),
            "snooze_preview": {
                "delta_pct": float(_DEFAULT_SNOOZE_PCT),
                "trigger_price": str(
                    snapshot.current_price
                    * (Decimal("1") - _DEFAULT_SNOOZE_PCT / Decimal("100"))
                ),
            },
            "auto_sweep": {
                "enabled": config.auto_sweep_enabled,
                "executes_at": (
                    (
                        datetime.now(timezone.utc)
                        + timedelta(minutes=config.auto_sweep_timeout_minutes)
                    ).isoformat()
                    if config.auto_sweep_enabled
                    else None
                ),
            },
            "config_snapshot": {
                "drawdown_threshold_pct": str(config.drawdown_threshold_pct),
                "target_stablecoin": config.target_stablecoin,
                "stablecoin_depeg_floor": str(config.stablecoin_depeg_floor),
            },
        }

        alert = AlertData(
            id=uuid.uuid4(),
            merchant_id=snapshot.merchant_id,
            snapshot_merchant_id=snapshot.merchant_id,
            snapshot_token_symbol=snapshot.token_symbol,
            snapshot_date=snapshot.date,
            kind=kind,
            fired_at=datetime.now(timezone.utc),
            payload=payload,
        )
        self._alerts.save(alert)
        log.info(
            "alert_engine.fired",
            extra={
                "alert_id": str(alert.id),
                "merchant_id": str(snapshot.merchant_id),
                "token": snapshot.token_symbol,
                "kind": kind.value,
            },
        )
        return alert


# ── domain exceptions ─────────────────────────────────────────────────────────


class AlertNotFound(Exception):
    def __init__(self, alert_id: uuid.UUID) -> None:
        super().__init__(f"Alert {alert_id} not found")
        self.alert_id = alert_id


class AlreadyResponded(Exception):
    def __init__(self, existing: AlertResponse) -> None:
        super().__init__(f"Alert already has response: {existing.value}")
        self.existing = existing


class SnapshotNotFound(Exception):
    def __init__(self, alert_id: uuid.UUID) -> None:
        super().__init__(f"Snapshot for alert {alert_id} not found")


class InvalidSnooze(Exception):
    def __init__(self, delta: Decimal | None) -> None:
        super().__init__(f"snooze_delta_pct must be > 0, got {delta!r}")

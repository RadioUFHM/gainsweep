from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from gainsweep.alert.engine import (
    AlertEngine,
    AlreadyResponded,
    AlertNotFound,
    InvalidSnooze,
)
from gainsweep.db.models import AlertKind, AlertResponse, AlertState
from gainsweep.domain import AlertData, ConfigData, SnapshotData
from gainsweep.protocols.price_provider import PriceQuote
from gainsweep.protocols.scheduler import NoopJobScheduler
from gainsweep.protocols.sweep_orchestrator import NotImplementedSweepOrchestrator
from gainsweep.repositories.alert import InMemoryAlertRepository
from gainsweep.repositories.config import InMemoryConfigRepository
from gainsweep.repositories.snapshot import InMemorySnapshotRepository

# ── fixtures ──────────────────────────────────────────────────────────────────

_MID = uuid.uuid4()
_TOKEN = "ETH"
_TODAY = date.today()


def _snap(**overrides: object) -> SnapshotData:
    defaults: dict[str, object] = dict(
        merchant_id=_MID,
        token_symbol=_TOKEN,
        date=_TODAY,
        daily_high=Decimal("3400"),
        daily_high_hour=8,
        current_price=Decimal("3400"),
        position_qty=Decimal("2"),
        alert_state=AlertState.ARMED,
    )
    defaults.update(overrides)
    return SnapshotData(**defaults)  # type: ignore[arg-type]


def _cfg(**overrides: object) -> ConfigData:
    defaults: dict[str, object] = dict(
        merchant_id=_MID,
        drawdown_threshold_pct=Decimal("5"),
        rearm_on_new_high=True,
        target_stablecoin="USDC",
        stablecoin_depeg_floor=Decimal("0.97"),
        auto_sweep_enabled=False,
    )
    defaults.update(overrides)
    return ConfigData(**defaults)  # type: ignore[arg-type]


def _price_quote(symbol: str, price: str) -> PriceQuote:
    return PriceQuote(
        symbol=symbol,
        price=Decimal(price),
        timestamp=datetime.now(timezone.utc),
        source="mock",
    )


def _engine(
    snap: SnapshotData | None = None,
    cfg: ConfigData | None = None,
    price_side_effect: Exception | None = None,
    usdc_price: str = "1.00",
    auto_sweep: bool = False,
) -> tuple[AlertEngine, InMemorySnapshotRepository, InMemoryAlertRepository]:
    snap_repo = InMemorySnapshotRepository()
    alert_repo = InMemoryAlertRepository()
    cfg_repo = InMemoryConfigRepository()
    scheduler = NoopJobScheduler()
    sweep_orch = NotImplementedSweepOrchestrator()

    s = snap or _snap()
    c = cfg or _cfg(auto_sweep_enabled=auto_sweep)
    snap_repo.save(s)
    cfg_repo.set(c)

    price_provider = MagicMock()
    if price_side_effect:
        price_provider.get_price.side_effect = price_side_effect
    else:
        price_provider.get_price.return_value = _price_quote("USDC", usdc_price)

    engine = AlertEngine(
        snapshot_repo=snap_repo,
        config_repo=cfg_repo,
        alert_repo=alert_repo,
        price_provider=price_provider,
        job_scheduler=scheduler,
        sweep_orchestrator=sweep_orch,
    )
    return engine, snap_repo, alert_repo


# ── evaluate: no-fire conditions ──────────────────────────────────────────────


def test_evaluate_returns_none_when_no_config() -> None:
    snap_repo = InMemorySnapshotRepository()
    snap_repo.save(_snap())
    engine = AlertEngine(
        snap_repo, InMemoryConfigRepository(), InMemoryAlertRepository(),
        MagicMock(), NoopJobScheduler(), NotImplementedSweepOrchestrator(),
    )
    assert engine.evaluate(_MID, _TOKEN) is None


def test_evaluate_returns_none_when_no_snapshot() -> None:
    engine, _, _ = _engine()
    assert engine.evaluate(_MID, "BTC") is None


def test_evaluate_returns_none_when_no_hourly_close_yet() -> None:
    engine, _, _ = _engine(snap=_snap(daily_high_hour=None))
    assert engine.evaluate(_MID, _TOKEN) is None


def test_evaluate_returns_none_when_price_above_threshold() -> None:
    # 3% drawdown < 5% threshold → no alert
    engine, _, _ = _engine(snap=_snap(daily_high=Decimal("3400"), current_price=Decimal("3300")))
    assert engine.evaluate(_MID, _TOKEN) is None


def test_evaluate_returns_none_when_already_fired() -> None:
    engine, _, _ = _engine(snap=_snap(alert_state=AlertState.FIRED))
    assert engine.evaluate(_MID, _TOKEN) is None


def test_evaluate_returns_none_when_resolved() -> None:
    for state in (AlertState.RESOLVED_HODL, AlertState.RESOLVED_SWEEP, AlertState.RESOLVED_EXPIRED):
        engine, _, _ = _engine(snap=_snap(alert_state=state))
        assert engine.evaluate(_MID, _TOKEN) is None


# ── evaluate: fires DRAWDOWN ──────────────────────────────────────────────────


def test_evaluate_fires_drawdown_at_threshold() -> None:
    # Exactly 5% drawdown
    engine, snap_repo, alert_repo = _engine(
        snap=_snap(daily_high=Decimal("3400"), current_price=Decimal("3230"))
    )
    alert = engine.evaluate(_MID, _TOKEN)

    assert alert is not None
    assert alert.kind == AlertKind.DRAWDOWN
    snap = snap_repo.get(_MID, _TOKEN, _TODAY)
    assert snap is not None
    assert snap.alert_state == AlertState.FIRED
    assert snap.last_alert_id == alert.id
    assert alert_repo.get(alert.id) is not None


def test_evaluate_payload_contains_expected_fields() -> None:
    engine, _, _ = _engine(
        snap=_snap(daily_high=Decimal("3400"), current_price=Decimal("3230"))
    )
    alert = engine.evaluate(_MID, _TOKEN)
    assert alert is not None
    p = alert.payload
    assert p["token"] == _TOKEN
    assert p["daily_high"] == "3400"
    assert "drawdown_pct" in p
    assert "snooze_preview" in p
    assert "auto_sweep" in p


# ── evaluate: stablecoin depeg gating ─────────────────────────────────────────


def test_evaluate_emits_depeg_alert_when_stablecoin_below_floor() -> None:
    engine, snap_repo, alert_repo = _engine(
        snap=_snap(daily_high=Decimal("3400"), current_price=Decimal("3230")),
        usdc_price="0.94",  # below 0.97 floor
    )
    alert = engine.evaluate(_MID, _TOKEN)

    assert alert is not None
    assert alert.kind == AlertKind.STABLECOIN_DEPEG
    # Snapshot must NOT transition to FIRED — never sweep into broken peg
    snap = snap_repo.get(_MID, _TOKEN, _TODAY)
    assert snap is not None
    assert snap.alert_state == AlertState.ARMED


def test_evaluate_returns_none_when_stablecoin_price_unavailable() -> None:
    engine, _, _ = _engine(
        snap=_snap(daily_high=Decimal("3400"), current_price=Decimal("3230")),
        price_side_effect=KeyError("USDC"),
    )
    assert engine.evaluate(_MID, _TOKEN) is None


# ── evaluate: snooze re-trigger ───────────────────────────────────────────────


def test_evaluate_fires_when_price_at_snooze_trigger() -> None:
    trigger = Decimal("3100")
    engine, snap_repo, _ = _engine(
        snap=_snap(
            alert_state=AlertState.SNOOZED,
            snooze_active=True,
            snooze_trigger_price=trigger,
            current_price=trigger,
        )
    )
    alert = engine.evaluate(_MID, _TOKEN)
    assert alert is not None
    assert alert.kind == AlertKind.DRAWDOWN


def test_evaluate_no_fire_when_price_above_snooze_trigger() -> None:
    engine, _, _ = _engine(
        snap=_snap(
            alert_state=AlertState.SNOOZED,
            snooze_active=True,
            snooze_trigger_price=Decimal("3100"),
            current_price=Decimal("3200"),  # above trigger
        )
    )
    assert engine.evaluate(_MID, _TOKEN) is None


# ── evaluate: auto-sweep scheduling ───────────────────────────────────────────


def test_evaluate_transitions_to_auto_sweep_pending_when_enabled() -> None:
    engine, snap_repo, _ = _engine(
        snap=_snap(daily_high=Decimal("3400"), current_price=Decimal("3230")),
        auto_sweep=True,
    )
    engine.evaluate(_MID, _TOKEN)
    snap = snap_repo.get(_MID, _TOKEN, _TODAY)
    assert snap is not None
    assert snap.alert_state == AlertState.AUTO_SWEEP_PENDING


# ── respond: HODL ─────────────────────────────────────────────────────────────


def test_respond_hodl_transitions_to_resolved_hodl() -> None:
    engine, snap_repo, alert_repo = _engine(
        snap=_snap(daily_high=Decimal("3400"), current_price=Decimal("3230"))
    )
    alert = engine.evaluate(_MID, _TOKEN)
    assert alert is not None

    result = engine.respond(alert.id, AlertResponse.HODL)

    assert result.response == AlertResponse.HODL
    snap = snap_repo.get(_MID, _TOKEN, _TODAY)
    assert snap is not None
    assert snap.alert_state == AlertState.RESOLVED_HODL


# ── respond: SNOOZE ───────────────────────────────────────────────────────────


def test_respond_snooze_sets_trigger_price() -> None:
    engine, snap_repo, _ = _engine(
        snap=_snap(daily_high=Decimal("3400"), current_price=Decimal("3230"))
    )
    alert = engine.evaluate(_MID, _TOKEN)
    assert alert is not None

    engine.respond(alert.id, AlertResponse.SNOOZE, snooze_delta_pct=Decimal("3"))

    snap = snap_repo.get(_MID, _TOKEN, _TODAY)
    assert snap is not None
    assert snap.alert_state == AlertState.SNOOZED
    assert snap.snooze_active is True
    expected = Decimal("3230") * Decimal("0.97")
    assert snap.snooze_trigger_price == expected


def test_respond_snooze_raises_invalid_snooze_for_zero_delta() -> None:
    engine, _, _ = _engine(
        snap=_snap(daily_high=Decimal("3400"), current_price=Decimal("3230"))
    )
    alert = engine.evaluate(_MID, _TOKEN)
    assert alert is not None

    with pytest.raises(InvalidSnooze):
        engine.respond(alert.id, AlertResponse.SNOOZE, snooze_delta_pct=Decimal("0"))


# ── respond: error cases ──────────────────────────────────────────────────────


def test_respond_raises_alert_not_found() -> None:
    engine, _, _ = _engine()
    with pytest.raises(AlertNotFound):
        engine.respond(uuid.uuid4(), AlertResponse.HODL)


def test_respond_raises_already_responded() -> None:
    engine, _, _ = _engine(
        snap=_snap(daily_high=Decimal("3400"), current_price=Decimal("3230"))
    )
    alert = engine.evaluate(_MID, _TOKEN)
    assert alert is not None
    engine.respond(alert.id, AlertResponse.HODL)

    with pytest.raises(AlreadyResponded):
        engine.respond(alert.id, AlertResponse.HODL)

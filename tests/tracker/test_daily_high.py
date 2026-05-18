from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from gainsweep.db.models import AlertState
from gainsweep.domain import ConfigData
from gainsweep.repositories.config import InMemoryConfigRepository
from gainsweep.repositories.snapshot import InMemorySnapshotRepository
from gainsweep.tracker.daily_high import DailyHighTracker

# ── helpers ────────────────────────────────────────────────────────────────────

_MID = uuid.uuid4()
_TOKEN = "ETH"
_DAY = datetime(2024, 5, 14, tzinfo=timezone.utc)  # hour 0 of the test day


def _tracker(rearm: bool = True) -> tuple[DailyHighTracker, InMemorySnapshotRepository]:
    snap_repo = InMemorySnapshotRepository()
    cfg_repo = InMemoryConfigRepository(
        {_MID: ConfigData(merchant_id=_MID, rearm_on_new_high=rearm)}
    )
    return DailyHighTracker(snap_repo, cfg_repo), snap_repo


def _tick(tracker: DailyHighTracker, price: float, offset_minutes: int = 0) -> datetime:
    ts = _DAY + timedelta(minutes=offset_minutes)
    tracker.on_price_tick(_MID, _TOKEN, Decimal(str(price)), ts)
    return ts


def _hour(n: int) -> datetime:
    return _DAY + timedelta(hours=n)


# ── on_price_tick ─────────────────────────────────────────────────────────────


def test_tick_creates_snapshot_on_first_call() -> None:
    tracker, repo = _tracker()
    _tick(tracker, 3400.0)
    snapshot = repo.get(_MID, _TOKEN, _DAY.date())
    assert snapshot is not None
    assert snapshot.current_price == Decimal("3400.0")


def test_tick_updates_current_price() -> None:
    tracker, repo = _tracker()
    _tick(tracker, 3400.0, offset_minutes=0)
    _tick(tracker, 3450.0, offset_minutes=5)
    snapshot = repo.get(_MID, _TOKEN, _DAY.date())
    assert snapshot is not None
    assert snapshot.current_price == Decimal("3450.0")


def test_tick_updates_current_price_ts() -> None:
    tracker, repo = _tracker()
    ts = _tick(tracker, 3400.0, offset_minutes=10)
    snapshot = repo.get(_MID, _TOKEN, _DAY.date())
    assert snapshot is not None
    assert snapshot.current_price_ts == ts


# ── finalize_hour ─────────────────────────────────────────────────────────────


def test_finalize_uses_last_tick_in_window() -> None:
    tracker, repo = _tracker()
    _tick(tracker, 3400.0, offset_minutes=10)
    _tick(tracker, 3420.0, offset_minutes=40)  # this is the last tick in hour 0
    _tick(tracker, 3430.0, offset_minutes=70)  # hour 1, should not be included

    tracker.finalize_hour(_hour(0))

    snapshot = repo.get(_MID, _TOKEN, _DAY.date())
    assert snapshot is not None
    assert len(snapshot.hourly_closes) == 1
    assert snapshot.hourly_closes[0].hour == 0
    assert snapshot.hourly_closes[0].close == Decimal("3420.0")


def test_finalize_sets_daily_high_from_closes() -> None:
    tracker, repo = _tracker()
    _tick(tracker, 3400.0, offset_minutes=10)
    tracker.finalize_hour(_hour(0))

    _tick(tracker, 3450.0, offset_minutes=70)
    tracker.finalize_hour(_hour(1))

    snapshot = repo.get(_MID, _TOKEN, _DAY.date())
    assert snapshot is not None
    assert snapshot.daily_high == Decimal("3450.0")
    assert snapshot.daily_high_hour == 1


def test_finalize_skips_hour_with_no_ticks() -> None:
    tracker, repo = _tracker()
    # No ticks at all — finalize_hour should not crash and add no close
    tracker.finalize_hour(_hour(0))

    snapshot = repo.get(_MID, _TOKEN, _DAY.date())
    assert snapshot is None  # never created, nothing to finalize


def test_finalize_does_not_interpolate_missing_hours() -> None:
    tracker, repo = _tracker()
    _tick(tracker, 3400.0, offset_minutes=10)
    tracker.finalize_hour(_hour(0))

    # No ticks in hour 1 — finalize anyway
    tracker.finalize_hour(_hour(1))

    snapshot = repo.get(_MID, _TOKEN, _DAY.date())
    assert snapshot is not None
    assert len(snapshot.hourly_closes) == 1  # only hour 0


def test_finalize_replaces_close_for_same_hour() -> None:
    tracker, repo = _tracker()
    _tick(tracker, 3400.0, offset_minutes=10)
    tracker.finalize_hour(_hour(0))

    # Simulate re-processing the same hour (e.g. correction)
    tracker.on_price_tick(_MID, _TOKEN, Decimal("3500.0"), _DAY + timedelta(minutes=20))
    tracker.finalize_hour(_hour(0))

    snapshot = repo.get(_MID, _TOKEN, _DAY.date())
    assert snapshot is not None
    assert len(snapshot.hourly_closes) == 1
    assert snapshot.hourly_closes[0].close == Decimal("3500.0")


# ── re-arm logic ──────────────────────────────────────────────────────────────


def test_rearm_transitions_fired_to_armed_on_new_high() -> None:
    tracker, repo = _tracker(rearm=True)
    _tick(tracker, 3400.0, offset_minutes=10)
    tracker.finalize_hour(_hour(0))

    # Manually set FIRED state
    snap = repo.get(_MID, _TOKEN, _DAY.date())
    assert snap is not None
    snap.alert_state = AlertState.FIRED
    repo.save(snap)

    # New higher price in hour 1
    _tick(tracker, 3600.0, offset_minutes=70)
    tracker.finalize_hour(_hour(1))

    snap = repo.get(_MID, _TOKEN, _DAY.date())
    assert snap is not None
    assert snap.alert_state == AlertState.ARMED


def test_rearm_clears_snooze_on_new_high() -> None:
    tracker, repo = _tracker(rearm=True)
    _tick(tracker, 3400.0, offset_minutes=10)
    tracker.finalize_hour(_hour(0))

    snap = repo.get(_MID, _TOKEN, _DAY.date())
    assert snap is not None
    snap.alert_state = AlertState.SNOOZED
    snap.snooze_active = True
    snap.snooze_trigger_price = Decimal("3300.0")
    repo.save(snap)

    _tick(tracker, 3600.0, offset_minutes=70)
    tracker.finalize_hour(_hour(1))

    snap = repo.get(_MID, _TOKEN, _DAY.date())
    assert snap is not None
    assert snap.alert_state == AlertState.ARMED
    assert snap.snooze_active is False
    assert snap.snooze_trigger_price is None


def test_no_rearm_when_rearm_on_new_high_is_false() -> None:
    tracker, repo = _tracker(rearm=False)
    _tick(tracker, 3400.0, offset_minutes=10)
    tracker.finalize_hour(_hour(0))

    snap = repo.get(_MID, _TOKEN, _DAY.date())
    assert snap is not None
    snap.alert_state = AlertState.FIRED
    repo.save(snap)

    _tick(tracker, 3600.0, offset_minutes=70)
    tracker.finalize_hour(_hour(1))

    snap = repo.get(_MID, _TOKEN, _DAY.date())
    assert snap is not None
    assert snap.alert_state == AlertState.FIRED  # unchanged


def test_no_rearm_when_price_does_not_exceed_current_high() -> None:
    tracker, repo = _tracker(rearm=True)
    _tick(tracker, 3400.0, offset_minutes=10)
    tracker.finalize_hour(_hour(0))

    snap = repo.get(_MID, _TOKEN, _DAY.date())
    assert snap is not None
    snap.alert_state = AlertState.FIRED
    repo.save(snap)

    # Lower price in hour 1 — no new high
    _tick(tracker, 3200.0, offset_minutes=70)
    tracker.finalize_hour(_hour(1))

    snap = repo.get(_MID, _TOKEN, _DAY.date())
    assert snap is not None
    assert snap.alert_state == AlertState.FIRED  # unchanged


# ── reset_for_new_day ─────────────────────────────────────────────────────────


def test_reset_for_new_day_creates_armed_snapshot() -> None:
    tracker, repo = _tracker()
    snapshot = tracker.reset_for_new_day(_MID, _TOKEN, "UTC")

    assert snapshot.alert_state == AlertState.ARMED
    assert snapshot.hourly_closes == []
    assert snapshot.daily_high == Decimal("0")
    assert snapshot.daily_high_hour is None


def test_reset_for_new_day_carries_forward_last_known_price() -> None:
    tracker, repo = _tracker()
    _tick(tracker, 3400.0, offset_minutes=10)

    # Simulate rolling over to next day
    snapshot = tracker.reset_for_new_day(_MID, _TOKEN, "UTC")

    assert snapshot.current_price == Decimal("3400.0")

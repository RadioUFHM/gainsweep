from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from gainsweep.domain import ConfigData
from gainsweep.protocols.sweep_venue import SweepResult
from gainsweep.repositories.config import InMemoryConfigRepository
from gainsweep.repositories.sweep import InMemorySweepRepository
from gainsweep.sweep.orchestrator import DefaultSweepOrchestrator

_MID = uuid.uuid4()
_TOKEN = "ETH"


def _result(**overrides: object) -> SweepResult:
    defaults: dict[str, object] = dict(
        venue="coinbase", token_symbol=_TOKEN, qty_executed=Decimal("2"),
        target_stablecoin="USDC", proceeds=Decimal("6460"), fees_paid=Decimal("38.76"),
        executed_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        venue_txn_ids=["order-abc-123"], status="COMPLETE", error_message=None,
    )
    defaults.update(overrides)
    return SweepResult(**defaults)  # type: ignore[arg-type]


def _orchestrator(side_effect: Exception | None = None) -> tuple[
    DefaultSweepOrchestrator, InMemorySweepRepository
]:
    sweep_repo = InMemorySweepRepository()
    config_repo = InMemoryConfigRepository()
    config_repo.set(ConfigData(merchant_id=_MID, target_stablecoin="USDC"))

    venue = MagicMock()
    if side_effect:
        venue.execute_sweep.side_effect = side_effect
    else:
        venue.execute_sweep.return_value = _result()

    orch = DefaultSweepOrchestrator(venue=venue, sweep_repo=sweep_repo, config_repo=config_repo)
    return orch, sweep_repo


def test_execute_returns_sweep_uuid() -> None:
    orch, _ = _orchestrator()
    sweep_id = orch.execute(_MID, _TOKEN, Decimal("2"))
    assert isinstance(sweep_id, uuid.UUID)


def test_execute_saves_completed_sweep() -> None:
    orch, sweep_repo = _orchestrator()
    sweep_id = orch.execute(_MID, _TOKEN, Decimal("2"), triggered_by_alert_id=uuid.uuid4())

    sweep = sweep_repo.get(sweep_id)
    assert sweep is not None
    assert sweep.status == "COMPLETE"
    assert sweep.qty_executed == Decimal("2")
    assert sweep.venue_txn_ids == ["order-abc-123"]
    assert sweep.completed_at is not None


def test_execute_saves_failed_sweep_on_venue_exception() -> None:
    orch, sweep_repo = _orchestrator(side_effect=RuntimeError("network error"))
    sweep_id = orch.execute(_MID, _TOKEN, Decimal("2"))

    sweep = sweep_repo.get(sweep_id)
    assert sweep is not None
    assert sweep.status == "FAILED"
    assert sweep.error_message == "network error"
    assert sweep.completed_at is not None


def test_execute_uses_config_target_stablecoin() -> None:
    orch, _ = _orchestrator()
    orch.execute(_MID, _TOKEN, Decimal("2"))

    venue_call = orch._venue.execute_sweep.call_args
    assert venue_call.args[3] == "USDC"


def test_execute_falls_back_to_usdc_when_no_config() -> None:
    sweep_repo = InMemorySweepRepository()
    venue = MagicMock()
    venue.execute_sweep.return_value = _result()
    orch = DefaultSweepOrchestrator(
        venue=venue,
        sweep_repo=sweep_repo,
        config_repo=InMemoryConfigRepository(),  # empty — no config for merchant
    )
    orch.execute(_MID, _TOKEN, Decimal("1"))
    assert venue.execute_sweep.call_args.args[3] == "USDC"

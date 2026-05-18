from __future__ import annotations

from functools import lru_cache

from gainsweep.protocols.scheduler import JobScheduler, NoopJobScheduler
from gainsweep.protocols.sweep_orchestrator import (
    NotImplementedSweepOrchestrator,
    SweepOrchestrator,
)
from gainsweep.repositories.alert import AlertRepository, InMemoryAlertRepository
from gainsweep.repositories.config import ConfigRepository, InMemoryConfigRepository
from gainsweep.repositories.snapshot import (
    InMemorySnapshotRepository,
    SnapshotRepository,
)


@lru_cache(maxsize=1)
def _snapshot_repo() -> InMemorySnapshotRepository:
    return InMemorySnapshotRepository()


@lru_cache(maxsize=1)
def _alert_repo() -> InMemoryAlertRepository:
    return InMemoryAlertRepository()


@lru_cache(maxsize=1)
def _config_repo() -> InMemoryConfigRepository:
    return InMemoryConfigRepository()


@lru_cache(maxsize=1)
def _job_scheduler() -> NoopJobScheduler:
    return NoopJobScheduler()


@lru_cache(maxsize=1)
def _sweep_orchestrator() -> NotImplementedSweepOrchestrator:
    return NotImplementedSweepOrchestrator()


def get_snapshot_repo() -> SnapshotRepository:
    return _snapshot_repo()


def get_alert_repo() -> AlertRepository:
    return _alert_repo()


def get_config_repo() -> ConfigRepository:
    return _config_repo()


def get_job_scheduler() -> JobScheduler:
    return _job_scheduler()


def get_sweep_orchestrator() -> SweepOrchestrator:
    return _sweep_orchestrator()

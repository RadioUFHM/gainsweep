from __future__ import annotations

import uuid
from typing import Protocol

from gainsweep.domain import SweepExecutionData


class SweepRepository(Protocol):
    def get(self, sweep_id: uuid.UUID) -> SweepExecutionData | None: ...
    def save(self, sweep: SweepExecutionData) -> None: ...


class InMemorySweepRepository:
    def __init__(self) -> None:
        self._store: dict[uuid.UUID, SweepExecutionData] = {}

    def get(self, sweep_id: uuid.UUID) -> SweepExecutionData | None:
        return self._store.get(sweep_id)

    def save(self, sweep: SweepExecutionData) -> None:
        self._store[sweep.id] = sweep

    def all(self) -> list[SweepExecutionData]:
        return list(self._store.values())


_: SweepRepository = InMemorySweepRepository()  # type: ignore[assignment]

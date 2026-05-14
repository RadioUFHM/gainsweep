from __future__ import annotations

import uuid
from typing import Protocol

from gainsweep.domain import AlertData


class AlertRepository(Protocol):
    def get(self, alert_id: uuid.UUID) -> AlertData | None: ...
    def save(self, alert: AlertData) -> None: ...


class InMemoryAlertRepository:
    def __init__(self) -> None:
        self._store: dict[uuid.UUID, AlertData] = {}

    def get(self, alert_id: uuid.UUID) -> AlertData | None:
        return self._store.get(alert_id)

    def save(self, alert: AlertData) -> None:
        self._store[alert.id] = alert

    def all(self) -> list[AlertData]:
        return list(self._store.values())


_: AlertRepository = InMemoryAlertRepository()  # type: ignore[assignment]

from __future__ import annotations

import uuid
from typing import Protocol

from gainsweep.domain import ConfigData


class ConfigRepository(Protocol):
    def get(self, merchant_id: uuid.UUID) -> ConfigData | None: ...


class InMemoryConfigRepository:
    """In-memory ConfigRepository used in tests and the backtest harness."""

    def __init__(self, configs: dict[uuid.UUID, ConfigData] | None = None) -> None:
        self._store: dict[uuid.UUID, ConfigData] = configs or {}

    def get(self, merchant_id: uuid.UUID) -> ConfigData | None:
        return self._store.get(merchant_id)

    def set(self, config: ConfigData) -> None:
        self._store[config.merchant_id] = config


_: ConfigRepository = InMemoryConfigRepository()  # type: ignore[assignment]

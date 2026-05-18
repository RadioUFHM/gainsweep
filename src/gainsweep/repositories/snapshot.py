from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Protocol

from gainsweep.domain import SnapshotData


class SnapshotRepository(Protocol):
    def get(
        self, merchant_id: uuid.UUID, token: str, d: date
    ) -> SnapshotData | None: ...

    def save(self, snapshot: SnapshotData) -> None: ...

    def get_or_create(
        self, merchant_id: uuid.UUID, token: str, d: date
    ) -> SnapshotData: ...

    def get_latest(
        self, merchant_id: uuid.UUID, token: str
    ) -> SnapshotData | None: ...


class InMemorySnapshotRepository:
    """In-memory SnapshotRepository used in tests and the backtest harness."""

    def __init__(self) -> None:
        self._store: dict[tuple[uuid.UUID, str, date], SnapshotData] = {}

    def get(self, merchant_id: uuid.UUID, token: str, d: date) -> SnapshotData | None:
        return self._store.get((merchant_id, token, d))

    def save(self, snapshot: SnapshotData) -> None:
        self._store[(snapshot.merchant_id, snapshot.token_symbol, snapshot.date)] = snapshot

    def get_or_create(
        self, merchant_id: uuid.UUID, token: str, d: date
    ) -> SnapshotData:
        existing = self.get(merchant_id, token, d)
        if existing:
            return existing
        snapshot = SnapshotData(
            merchant_id=merchant_id,
            token_symbol=token,
            date=d,
            updated_at=datetime.now(timezone.utc),
        )
        self.save(snapshot)
        return snapshot

    def get_latest(self, merchant_id: uuid.UUID, token: str) -> SnapshotData | None:
        candidates = [
            s for s in self._store.values()
            if s.merchant_id == merchant_id and s.token_symbol == token
        ]
        return max(candidates, key=lambda s: s.date) if candidates else None

    def all(self) -> list[SnapshotData]:
        return list(self._store.values())


_: SnapshotRepository = InMemorySnapshotRepository()  # type: ignore[assignment]

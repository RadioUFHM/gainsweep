from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class JobScheduler(Protocol):
    def schedule(
        self, job_type: str, run_at: datetime, payload: dict[str, Any]
    ) -> None: ...


class NoopJobScheduler:
    """Scheduler stub for Phase 1–3. Phase 5 wires in RQ / Celery / APScheduler."""

    def schedule(
        self, job_type: str, run_at: datetime, payload: dict[str, Any]
    ) -> None:
        pass


_: JobScheduler = NoopJobScheduler()  # type: ignore[assignment]

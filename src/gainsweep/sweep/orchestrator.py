from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from gainsweep.domain import SweepExecutionData
from gainsweep.protocols.sweep_orchestrator import SweepOrchestrator
from gainsweep.protocols.sweep_venue import SweepVenue
from gainsweep.repositories.config import ConfigRepository
from gainsweep.repositories.sweep import SweepRepository

log = logging.getLogger(__name__)


class DefaultSweepOrchestrator:
    """Routes sweep requests to a venue, persists execution records. (§5.8)

    Satisfies the SweepOrchestrator protocol. In Phase 4 this is wired to
    CoinbaseSweepVenue; future phases can add multi-venue routing.
    """

    def __init__(
        self,
        venue: SweepVenue,
        sweep_repo: SweepRepository,
        config_repo: ConfigRepository,
    ) -> None:
        self._venue = venue
        self._sweeps = sweep_repo
        self._configs = config_repo

    def execute(
        self,
        merchant_id: uuid.UUID,
        token: str,
        qty: Decimal,
        triggered_by_alert_id: uuid.UUID | None = None,
        triggered_by_schedule: bool = False,
    ) -> uuid.UUID:
        config = self._configs.get(merchant_id)
        target = config.target_stablecoin if config else "USDC"

        sweep = SweepExecutionData(
            id=uuid.uuid4(),
            merchant_id=merchant_id,
            triggered_by_alert_id=triggered_by_alert_id,
            triggered_by_schedule=triggered_by_schedule,
            venue=self._venue.__class__.__name__,
            token_symbol=token,
            qty_requested=qty,
            target_stablecoin=target,
            status="PENDING",
        )
        self._sweeps.save(sweep)
        log.info("sweep.pending", extra={"sweep_id": str(sweep.id), "token": token})

        try:
            result = self._venue.execute_sweep(merchant_id, token, qty, target)
            sweep.qty_executed = result.qty_executed
            sweep.proceeds = result.proceeds
            sweep.fees_paid = result.fees_paid
            sweep.status = result.status
            sweep.venue_txn_ids = list(result.venue_txn_ids)
            sweep.error_message = result.error_message
            sweep.completed_at = datetime.now(timezone.utc)
            log.info(
                "sweep.completed",
                extra={"sweep_id": str(sweep.id), "status": sweep.status},
            )
        except Exception as exc:
            sweep.status = "FAILED"
            sweep.error_message = str(exc)
            sweep.completed_at = datetime.now(timezone.utc)
            log.exception("sweep.failed", extra={"sweep_id": str(sweep.id)})
        finally:
            self._sweeps.save(sweep)

        return sweep.id


_: SweepOrchestrator = DefaultSweepOrchestrator(  # type: ignore[assignment]
    None, None, None  # type: ignore[arg-type]
)

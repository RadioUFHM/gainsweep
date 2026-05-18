from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from gainsweep.alert.engine import (
    AlertEngine,
    AlertNotFound,
    AlreadyResponded,
    InvalidSnooze,
)
from gainsweep.api import deps
from gainsweep.api.schemas import AlertDetail, RespondToAlertRequest, RespondToAlertResponse
from gainsweep.db.models import AlertResponse, AlertState
from gainsweep.domain import AlertData, SnapshotData
from gainsweep.providers.coingecko import CoinGeckoProvider
from gainsweep.repositories.alert import AlertRepository
from gainsweep.repositories.config import ConfigRepository
from gainsweep.repositories.snapshot import SnapshotRepository

router = APIRouter(tags=["alerts"])


def _engine(
    snapshot_repo: SnapshotRepository = Depends(deps.get_snapshot_repo),
    config_repo: ConfigRepository = Depends(deps.get_config_repo),
    alert_repo: AlertRepository = Depends(deps.get_alert_repo),
    job_scheduler=Depends(deps.get_job_scheduler),
    sweep_orchestrator=Depends(deps.get_sweep_orchestrator),
) -> AlertEngine:
    return AlertEngine(
        snapshot_repo=snapshot_repo,
        config_repo=config_repo,
        alert_repo=alert_repo,
        price_provider=CoinGeckoProvider(),
        job_scheduler=job_scheduler,
        sweep_orchestrator=sweep_orchestrator,
    )


def _alert_to_detail(alert: AlertData) -> AlertDetail:
    p = alert.payload
    snooze_p = p.get("snooze_preview", {})
    auto_s = p.get("auto_sweep", {})
    return AlertDetail(
        alert_id=str(alert.id),
        kind=alert.kind.value,
        token=str(p.get("token", "")),
        fired_at=alert.fired_at.isoformat(),
        daily_high=str(p.get("daily_high", "0")),
        daily_high_at=str(p["daily_high_at"]) if p.get("daily_high_at") else None,
        current_price=str(p.get("current_price", "0")),
        drawdown_pct=float(p.get("drawdown_pct", 0.0)),
        position_qty=str(p.get("position_qty", "0")),
        position_value_usd=str(p.get("position_value_usd", "0")),
        unrealized_vs_high_usd=str(p.get("unrealized_vs_high_usd", "0")),
        snooze_preview={  # type: ignore[arg-type]
            "delta_pct": float(snooze_p.get("delta_pct", 3.0)),
            "trigger_price": str(snooze_p.get("trigger_price", "0")),
        },
        auto_sweep={  # type: ignore[arg-type]
            "enabled": bool(auto_s.get("enabled", False)),
            "executes_at": auto_s.get("executes_at"),
        },
        response=alert.response.value if alert.response else None,
        responded_at=alert.responded_at.isoformat() if alert.responded_at else None,
    )


@router.get("/alerts/{alert_id}", response_model=AlertDetail)
def get_alert(
    alert_id: uuid.UUID,
    alert_repo: AlertRepository = Depends(deps.get_alert_repo),
) -> AlertDetail:
    alert = alert_repo.get(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return _alert_to_detail(alert)


@router.post("/alerts/{alert_id}/respond", response_model=RespondToAlertResponse)
def respond_to_alert(
    alert_id: uuid.UUID,
    body: RespondToAlertRequest,
    engine: AlertEngine = Depends(_engine),
    snapshot_repo: SnapshotRepository = Depends(deps.get_snapshot_repo),
    alert_repo: AlertRepository = Depends(deps.get_alert_repo),
) -> RespondToAlertResponse:
    try:
        alert = engine.respond(
            alert_id=alert_id,
            action=AlertResponse(body.action),
            snooze_delta_pct=(
                Decimal(str(body.snooze_delta_pct))
                if body.snooze_delta_pct is not None
                else None
            ),
        )
    except AlertNotFound:
        raise HTTPException(status_code=404, detail="Alert not found")
    except AlreadyResponded as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Alert already responded: {exc.existing.value}",
        )
    except InvalidSnooze as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except NotImplementedError:
        raise HTTPException(
            status_code=501,
            detail="Sweep execution is not available until Phase 4",
        )

    snapshot = snapshot_repo.get(
        alert.snapshot_merchant_id,
        alert.snapshot_token_symbol,
        alert.snapshot_date,
    )
    state = snapshot.alert_state.value if snapshot else AlertState.ARMED.value

    return RespondToAlertResponse(
        alert_id=str(alert.id),
        snapshot_state=state,
        sweep_id=str(alert.resulting_sweep_id) if alert.resulting_sweep_id else None,
        snooze_trigger_price=(
            str(snapshot.snooze_trigger_price)
            if snapshot and snapshot.snooze_trigger_price
            else None
        ),
    )

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class RespondToAlertRequest(BaseModel):
    action: Literal["SWEEP", "HODL", "SNOOZE"]
    snooze_delta_pct: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def snooze_requires_delta(self) -> RespondToAlertRequest:
        if self.action == "SNOOZE" and self.snooze_delta_pct is None:
            raise ValueError("snooze_delta_pct is required when action is SNOOZE")
        return self


class SnoozePreviewer(BaseModel):
    delta_pct: float
    trigger_price: str


class AutoSweepInfo(BaseModel):
    enabled: bool
    executes_at: str | None


class AlertDetail(BaseModel):
    """Full alert payload returned by GET /api/v1/alerts/{id}. (§7)"""

    alert_id: str
    kind: str
    token: str
    fired_at: str
    daily_high: str
    daily_high_at: str | None
    current_price: str
    drawdown_pct: float
    position_qty: str
    position_value_usd: str
    unrealized_vs_high_usd: str
    snooze_preview: SnoozePreviewer
    auto_sweep: AutoSweepInfo
    estimated_sweep: Any = None  # Phase 4: populated by SweepVenue.estimate_sweep
    response: str | None = None
    responded_at: str | None = None


class RespondToAlertResponse(BaseModel):
    alert_id: str
    snapshot_state: str
    sweep_id: str | None = None
    snooze_trigger_price: str | None = None

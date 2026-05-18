from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from gainsweep.api import deps
from gainsweep.api.app import create_app
from gainsweep.db.models import AlertKind, AlertState
from gainsweep.domain import AlertData, ConfigData, SnapshotData
from gainsweep.protocols.price_provider import PriceQuote
from gainsweep.protocols.scheduler import NoopJobScheduler
from gainsweep.protocols.sweep_orchestrator import NotImplementedSweepOrchestrator
from gainsweep.repositories.alert import InMemoryAlertRepository
from gainsweep.repositories.config import InMemoryConfigRepository
from gainsweep.repositories.snapshot import InMemorySnapshotRepository

# ── fixtures ──────────────────────────────────────────────────────────────────

_MID = uuid.uuid4()
_TOKEN = "ETH"
_TODAY = date(2024, 5, 14)


@pytest.fixture
def repos() -> tuple[
    InMemorySnapshotRepository,
    InMemoryAlertRepository,
    InMemoryConfigRepository,
]:
    return (
        InMemorySnapshotRepository(),
        InMemoryAlertRepository(),
        InMemoryConfigRepository(),
    )


@pytest.fixture
def client(repos: tuple) -> TestClient:
    snap_repo, alert_repo, config_repo = repos
    price_provider = MagicMock()
    price_provider.get_price.return_value = PriceQuote(
        symbol="USDC", price=Decimal("1.00"),
        timestamp=datetime.now(timezone.utc), source="mock",
    )
    app = create_app()
    app.dependency_overrides[deps.get_snapshot_repo] = lambda: snap_repo
    app.dependency_overrides[deps.get_alert_repo] = lambda: alert_repo
    app.dependency_overrides[deps.get_config_repo] = lambda: config_repo
    app.dependency_overrides[deps.get_job_scheduler] = lambda: NoopJobScheduler()
    app.dependency_overrides[deps.get_sweep_orchestrator] = lambda: NotImplementedSweepOrchestrator()
    # Patch the CoinGeckoProvider inside the route with our mock
    from gainsweep.api.routes import alerts as alerts_module
    original = alerts_module._engine

    def patched_engine(
        snapshot_repo=..., config_repo=..., alert_repo=...,
        job_scheduler=..., sweep_orchestrator=...,
    ):
        from gainsweep.alert.engine import AlertEngine
        return AlertEngine(
            snapshot_repo=snap_repo,
            config_repo=config_repo,
            alert_repo=alert_repo,
            price_provider=price_provider,
            job_scheduler=NoopJobScheduler(),
            sweep_orchestrator=NotImplementedSweepOrchestrator(),
        )

    alerts_module._engine = patched_engine  # type: ignore[assignment]
    yield TestClient(app)
    alerts_module._engine = original  # type: ignore[assignment]


def _seed_alert(
    alert_repo: InMemoryAlertRepository,
    snap_repo: InMemorySnapshotRepository,
    config_repo: InMemoryConfigRepository,
) -> AlertData:
    snap = SnapshotData(
        merchant_id=_MID, token_symbol=_TOKEN, date=_TODAY,
        daily_high=Decimal("3400"), daily_high_hour=8,
        current_price=Decimal("3230"), position_qty=Decimal("2"),
        alert_state=AlertState.FIRED,
    )
    snap_repo.save(snap)
    config_repo.set(ConfigData(merchant_id=_MID))
    alert = AlertData(
        id=uuid.uuid4(), merchant_id=_MID,
        snapshot_merchant_id=_MID, snapshot_token_symbol=_TOKEN,
        snapshot_date=_TODAY, kind=AlertKind.DRAWDOWN,
        fired_at=datetime.now(timezone.utc),
        payload={
            "token": _TOKEN, "daily_high": "3400", "daily_high_at": None,
            "current_price": "3230", "drawdown_pct": 5.0,
            "position_qty": "2", "position_value_usd": "6460",
            "unrealized_vs_high_usd": "-340",
            "snooze_preview": {"delta_pct": 3.0, "trigger_price": "3133.1"},
            "auto_sweep": {"enabled": False, "executes_at": None},
        },
    )
    alert_repo.save(alert)
    return alert


# ── GET /api/v1/health ────────────────────────────────────────────────────────


def test_health_returns_ok(client: TestClient) -> None:
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── GET /api/v1/alerts/{id} ───────────────────────────────────────────────────


def test_get_alert_returns_full_payload(
    client: TestClient, repos: tuple
) -> None:
    snap_repo, alert_repo, config_repo = repos
    alert = _seed_alert(alert_repo, snap_repo, config_repo)

    resp = client.get(f"/api/v1/alerts/{alert.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["alert_id"] == str(alert.id)
    assert data["kind"] == "DRAWDOWN"
    assert data["token"] == _TOKEN
    assert data["daily_high"] == "3400"
    assert data["current_price"] == "3230"
    assert data["drawdown_pct"] == 5.0
    assert "snooze_preview" in data
    assert "auto_sweep" in data


def test_get_alert_returns_404_for_unknown_id(client: TestClient) -> None:
    resp = client.get(f"/api/v1/alerts/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── POST /api/v1/alerts/{id}/respond — HODL ──────────────────────────────────


def test_respond_hodl_returns_resolved_hodl(
    client: TestClient, repos: tuple
) -> None:
    snap_repo, alert_repo, config_repo = repos
    alert = _seed_alert(alert_repo, snap_repo, config_repo)

    resp = client.post(
        f"/api/v1/alerts/{alert.id}/respond",
        json={"action": "HODL"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["snapshot_state"] == "RESOLVED_HODL"
    assert data["sweep_id"] is None
    assert data["snooze_trigger_price"] is None


# ── POST /api/v1/alerts/{id}/respond — SNOOZE ────────────────────────────────


def test_respond_snooze_returns_trigger_price(
    client: TestClient, repos: tuple
) -> None:
    snap_repo, alert_repo, config_repo = repos
    alert = _seed_alert(alert_repo, snap_repo, config_repo)

    resp = client.post(
        f"/api/v1/alerts/{alert.id}/respond",
        json={"action": "SNOOZE", "snooze_delta_pct": 3.0},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["snapshot_state"] == "SNOOZED"
    assert data["snooze_trigger_price"] is not None


def test_respond_snooze_requires_delta_pct(
    client: TestClient, repos: tuple
) -> None:
    snap_repo, alert_repo, config_repo = repos
    alert = _seed_alert(alert_repo, snap_repo, config_repo)

    resp = client.post(
        f"/api/v1/alerts/{alert.id}/respond",
        json={"action": "SNOOZE"},  # missing snooze_delta_pct
    )
    assert resp.status_code == 422


# ── POST /api/v1/alerts/{id}/respond — SWEEP (Phase 4 stub) ──────────────────


def test_respond_sweep_returns_501_until_phase_4(
    client: TestClient, repos: tuple
) -> None:
    snap_repo, alert_repo, config_repo = repos
    alert = _seed_alert(alert_repo, snap_repo, config_repo)

    resp = client.post(
        f"/api/v1/alerts/{alert.id}/respond",
        json={"action": "SWEEP"},
    )
    assert resp.status_code == 501


# ── POST error cases ──────────────────────────────────────────────────────────


def test_respond_returns_404_for_unknown_alert(client: TestClient) -> None:
    resp = client.post(
        f"/api/v1/alerts/{uuid.uuid4()}/respond",
        json={"action": "HODL"},
    )
    assert resp.status_code == 404


def test_respond_returns_409_when_already_responded(
    client: TestClient, repos: tuple
) -> None:
    snap_repo, alert_repo, config_repo = repos
    alert = _seed_alert(alert_repo, snap_repo, config_repo)

    client.post(f"/api/v1/alerts/{alert.id}/respond", json={"action": "HODL"})
    resp = client.post(f"/api/v1/alerts/{alert.id}/respond", json={"action": "HODL"})
    assert resp.status_code == 409

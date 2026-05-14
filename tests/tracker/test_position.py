from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest
import respx

from gainsweep.tracker.position import PositionTracker, _ACCOUNTS_PATH
from gainsweep.venues.coinbase import _PRODUCTION_BASE_URL, _SANDBOX_BASE_URL

_MID = uuid.uuid4()
_ACCOUNTS_URL_SANDBOX = f"{_SANDBOX_BASE_URL}{_ACCOUNTS_PATH}"
_ACCOUNTS_URL_PROD = f"{_PRODUCTION_BASE_URL}{_ACCOUNTS_PATH}"

_ACCOUNTS_RESPONSE = {
    "accounts": [
        {
            "currency": "ETH",
            "available_balance": {"value": "4.83", "currency": "ETH"},
        },
        {
            "currency": "BTC",
            "available_balance": {"value": "0.5", "currency": "BTC"},
        },
        {
            "currency": "USDC",
            "available_balance": {"value": "0", "currency": "USDC"},  # zero — excluded
        },
    ]
}


# ── instantiation ─────────────────────────────────────────────────────────────


def test_defaults_to_sandbox() -> None:
    tracker = PositionTracker("key", "pem")
    assert tracker._client.base_url == httpx.URL(_SANDBOX_BASE_URL)


def test_production_env_uses_production_url() -> None:
    tracker = PositionTracker("key", "pem", env="production")
    assert tracker._client.base_url == httpx.URL(_PRODUCTION_BASE_URL)


# ── get_positions ─────────────────────────────────────────────────────────────


@respx.mock
def test_get_positions_returns_non_zero_balances() -> None:
    respx.get(_ACCOUNTS_URL_SANDBOX).mock(
        return_value=httpx.Response(200, json=_ACCOUNTS_RESPONSE)
    )
    tracker = PositionTracker("key", "pem")
    positions = tracker.get_positions(_MID)

    assert positions == {
        "ETH": Decimal("4.83"),
        "BTC": Decimal("0.5"),
    }
    assert "USDC" not in positions


@respx.mock
def test_get_positions_excludes_zero_balances() -> None:
    respx.get(_ACCOUNTS_URL_SANDBOX).mock(
        return_value=httpx.Response(
            200,
            json={"accounts": [{"currency": "SOL", "available_balance": {"value": "0"}}]},
        )
    )
    tracker = PositionTracker("key", "pem")
    assert tracker.get_positions(_MID) == {}


@respx.mock
def test_get_positions_fails_open_on_http_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    respx.get(_ACCOUNTS_URL_SANDBOX).mock(return_value=httpx.Response(503))
    tracker = PositionTracker("key", "pem")
    result = tracker.get_positions(_MID)

    assert result == {}
    assert "position_tracker.get_positions_failed" in caplog.text


@respx.mock
def test_get_positions_handles_missing_currency_field() -> None:
    respx.get(_ACCOUNTS_URL_SANDBOX).mock(
        return_value=httpx.Response(
            200,
            json={"accounts": [{"available_balance": {"value": "1.0"}}]},  # no currency
        )
    )
    tracker = PositionTracker("key", "pem")
    assert tracker.get_positions(_MID) == {}


# ── get_cost_basis stub ───────────────────────────────────────────────────────


def test_get_cost_basis_raises_not_implemented() -> None:
    tracker = PositionTracker("key", "pem")
    with pytest.raises(NotImplementedError):
        tracker.get_cost_basis(_MID, "ETH")

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
import respx

from gainsweep.venues.coinbase import (
    CoinbaseSweepVenue,
    _PRODUCTION_BASE_URL,
    _SANDBOX_BASE_URL,
)

_PRODUCTS_PATH = "/api/v3/brokerage/products"

# Real EC P-256 key generated for tests only — never used against live API.
_TEST_SECRET = (
    "-----BEGIN EC PRIVATE KEY-----\n"
    "MHcCAQEEIMxHTSSHriav5KCYiSUthUHKjYe2yeRHSKw85REOOPoJoAoGCCqGSM49\n"
    "AwEHoUQDQgAEvK6clVW8xADEjTpmFUOEEG0UKgmVubNGPsvIMs6A1blboAokGjQQ\n"
    "dc4JBbh+LDN+8uGNAahdlPnZk8HjOW1zmg==\n"
    "-----END EC PRIVATE KEY-----\n"
)
_TEST_KEY_NAME = "organizations/proj-abc/apiKeys/key-uuid-1234"


def _make_venue(env: str = "sandbox", **kwargs: object) -> CoinbaseSweepVenue:
    return CoinbaseSweepVenue(_TEST_KEY_NAME, _TEST_SECRET, env=env, **kwargs)  # type: ignore[arg-type]


# ── instantiation and URL selection ──────────────────────────────────────────


def test_defaults_to_sandbox_base_url() -> None:
    venue = _make_venue()
    assert venue._client.base_url == httpx.URL(_SANDBOX_BASE_URL)


def test_production_env_uses_production_base_url() -> None:
    venue = _make_venue(env="production")
    assert venue._client.base_url == httpx.URL(_PRODUCTION_BASE_URL)


def test_unknown_env_falls_back_to_sandbox() -> None:
    venue = _make_venue(env="staging")
    assert venue._client.base_url == httpx.URL(_SANDBOX_BASE_URL)


# ── get_supported_tokens ──────────────────────────────────────────────────────


@respx.mock
def test_get_supported_tokens_returns_base_currencies() -> None:
    respx.get(f"{_SANDBOX_BASE_URL}{_PRODUCTS_PATH}").mock(
        return_value=httpx.Response(
            200,
            json={
                "products": [
                    {"product_id": "ETH-USDC", "base_currency_id": "ETH"},
                    {"product_id": "BTC-USDC", "base_currency_id": "BTC"},
                    {"product_id": "SOL-USDC", "base_currency_id": "SOL"},
                ]
            },
        )
    )
    venue = _make_venue()
    tokens = venue.get_supported_tokens()

    assert tokens == {"ETH", "BTC", "SOL"}


@respx.mock
def test_get_supported_tokens_deduplicates_across_pairs() -> None:
    respx.get(f"{_SANDBOX_BASE_URL}{_PRODUCTS_PATH}").mock(
        return_value=httpx.Response(
            200,
            json={
                "products": [
                    {"product_id": "ETH-USDC", "base_currency_id": "ETH"},
                    {"product_id": "ETH-USDT", "base_currency_id": "ETH"},
                ]
            },
        )
    )
    venue = _make_venue()
    tokens = venue.get_supported_tokens()

    assert tokens == {"ETH"}


@respx.mock
def test_get_supported_tokens_fails_open_on_http_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    respx.get(f"{_SANDBOX_BASE_URL}{_PRODUCTS_PATH}").mock(
        return_value=httpx.Response(503)
    )
    venue = _make_venue()
    tokens = venue.get_supported_tokens()

    assert tokens == set()
    assert "coinbase.get_products_failed" in caplog.text


@respx.mock
def test_get_supported_tokens_handles_missing_base_currency_id() -> None:
    respx.get(f"{_SANDBOX_BASE_URL}{_PRODUCTS_PATH}").mock(
        return_value=httpx.Response(
            200,
            json={
                "products": [
                    {"product_id": "ETH-USDC", "base_currency_id": "ETH"},
                    {"product_id": "WEIRD"},  # no base_currency_id
                ]
            },
        )
    )
    venue = _make_venue()
    tokens = venue.get_supported_tokens()

    assert tokens == {"ETH"}


# ── estimate_sweep ────────────────────────────────────────────────────────────

_BEST_BID_ASK_PATH = "/api/v3/brokerage/best_bid_ask"


@respx.mock
def test_estimate_sweep_returns_estimate() -> None:
    respx.get(f"{_SANDBOX_BASE_URL}{_BEST_BID_ASK_PATH}").mock(
        return_value=httpx.Response(200, json={
            "pricebooks": [{
                "product_id": "ETH-USDC",
                "bids": [{"price": "3200.00", "size": "5.0"}],
                "asks": [{"price": "3201.00", "size": "5.0"}],
            }]
        })
    )
    venue = _make_venue()
    est = venue.estimate_sweep(uuid4(), "ETH", Decimal("2"), "USDC")

    assert est.venue == "coinbase"
    assert est.expected_proceeds == Decimal("2") * Decimal("3200.00")
    assert est.estimated_fees == est.expected_proceeds * Decimal("0.006")
    assert est.estimated_slippage_pct > 0.0
    assert est.estimated_completion_seconds == 5


@respx.mock
def test_estimate_sweep_raises_on_http_error() -> None:
    respx.get(f"{_SANDBOX_BASE_URL}{_BEST_BID_ASK_PATH}").mock(
        return_value=httpx.Response(503)
    )
    venue = _make_venue()
    with pytest.raises(RuntimeError, match="estimate_sweep failed"):
        venue.estimate_sweep(uuid4(), "ETH", Decimal("1"), "USDC")


@respx.mock
def test_estimate_sweep_raises_on_empty_pricebook() -> None:
    respx.get(f"{_SANDBOX_BASE_URL}{_BEST_BID_ASK_PATH}").mock(
        return_value=httpx.Response(200, json={"pricebooks": []})
    )
    venue = _make_venue()
    with pytest.raises(RuntimeError, match="no bid data"):
        venue.estimate_sweep(uuid4(), "ETH", Decimal("1"), "USDC")


# ── execute_sweep ─────────────────────────────────────────────────────────────

_ORDERS_PATH = "/api/v3/brokerage/orders"


@respx.mock
def test_execute_sweep_returns_complete_on_success() -> None:
    respx.post(f"{_SANDBOX_BASE_URL}{_ORDERS_PATH}").mock(
        return_value=httpx.Response(200, json={
            "success": True,
            "success_response": {"order_id": "order-abc-123", "product_id": "ETH-USDC"},
        })
    )
    venue = _make_venue()
    result = venue.execute_sweep(uuid4(), "ETH", Decimal("2"), "USDC")

    assert result.status == "COMPLETE"
    assert result.venue_txn_ids == ["order-abc-123"]
    assert result.token_symbol == "ETH"
    assert result.target_stablecoin == "USDC"
    assert result.error_message is None


@respx.mock
def test_execute_sweep_returns_failed_on_http_error() -> None:
    respx.post(f"{_SANDBOX_BASE_URL}{_ORDERS_PATH}").mock(
        return_value=httpx.Response(400, text="Bad Request")
    )
    venue = _make_venue()
    result = venue.execute_sweep(uuid4(), "ETH", Decimal("1"), "USDC")

    assert result.status == "FAILED"
    assert result.error_message is not None
    assert result.venue_txn_ids == []


@respx.mock
def test_execute_sweep_returns_failed_when_success_false() -> None:
    respx.post(f"{_SANDBOX_BASE_URL}{_ORDERS_PATH}").mock(
        return_value=httpx.Response(200, json={
            "success": False,
            "error_response": {"message": "insufficient funds"},
        })
    )
    venue = _make_venue()
    result = venue.execute_sweep(uuid4(), "ETH", Decimal("999"), "USDC")

    assert result.status == "FAILED"
    assert result.error_message == "insufficient funds"


# ── _auth_headers ─────────────────────────────────────────────────────────────


def test_auth_headers_returns_bearer_token() -> None:
    venue = _make_venue()
    headers = venue._auth_headers("GET", "/api/v3/brokerage/products")
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Bearer ")
    assert headers["Content-Type"] == "application/json"


def test_auth_headers_jwt_contains_key_name_as_kid() -> None:
    import jwt as _jwt
    venue = _make_venue()
    headers = venue._auth_headers("GET", "/api/v3/brokerage/products")
    token = headers["Authorization"].removeprefix("Bearer ")
    header = _jwt.get_unverified_header(token)
    assert header["kid"] == _TEST_KEY_NAME
    assert header["alg"] == "ES256"


def test_auth_headers_jwt_contains_uri_claim() -> None:
    import jwt as _jwt
    venue = _make_venue()
    with patch("gainsweep.venues.coinbase.time") as mock_time:
        mock_time.time.return_value = 1716652800.0
        headers = venue._auth_headers("GET", "/api/v3/brokerage/products")
    token = headers["Authorization"].removeprefix("Bearer ")
    payload = _jwt.decode(token, options={"verify_signature": False})
    assert payload["uri"] == "GET api.coinbase.com/api/v3/brokerage/products"
    assert payload["iss"] == "cdp"
    assert payload["sub"] == _TEST_KEY_NAME


def test_auth_headers_strips_query_string_from_uri() -> None:
    import jwt as _jwt
    venue = _make_venue()
    headers = venue._auth_headers("GET", "/api/v3/brokerage/best_bid_ask?product_ids=ETH-USDC")
    token = headers["Authorization"].removeprefix("Bearer ")
    payload = _jwt.decode(token, options={"verify_signature": False})
    assert "?" not in payload["uri"]
    assert payload["uri"].endswith("/api/v3/brokerage/best_bid_ask")


def test_auth_headers_normalises_literal_backslash_n_in_pem() -> None:
    pem_with_escaped_newlines = _TEST_SECRET.replace("\n", "\\n")
    venue = CoinbaseSweepVenue(_TEST_KEY_NAME, pem_with_escaped_newlines)
    headers = venue._auth_headers("GET", "/api/v3/brokerage/products")
    assert headers["Authorization"].startswith("Bearer ")

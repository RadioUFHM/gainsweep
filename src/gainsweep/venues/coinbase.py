from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx

from gainsweep.protocols.sweep_venue import SweepEstimate, SweepResult, SweepVenue

log = logging.getLogger(__name__)

# §5.7: sandbox is the default; set COINBASE_ENV=production to target live.
_SANDBOX_BASE_URL = "https://api-sandbox.coinbase.com"
_PRODUCTION_BASE_URL = "https://api.coinbase.com"


class CoinbaseSweepVenue:
    """SweepVenue backed by Coinbase Advanced Trade API (§5.7).

    Phase 1: class structure and get_supported_tokens implemented.
    Phase 4: HMAC-SHA256 auth, estimate_sweep, execute_sweep.

    Satisfies the SweepVenue protocol; pass as ``venue: SweepVenue``.
    """

    def __init__(
        self,
        api_key_name: str,
        api_secret: str,
        env: str = "sandbox",
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key_name = api_key_name
        self._api_secret = api_secret
        base_url = _PRODUCTION_BASE_URL if env == "production" else _SANDBOX_BASE_URL
        self._client = client or httpx.Client(base_url=base_url, timeout=30.0)

    # ── SweepVenue interface ──────────────────────────────────────────────────

    def get_supported_tokens(self) -> set[str]:
        """Return base-currency symbols for all tradeable products on this venue."""
        try:
            resp = self._client.get(
                "/api/v3/brokerage/products",
                headers=self._auth_headers("GET", "/api/v3/brokerage/products"),
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("coinbase.get_products_failed", extra={"error": str(exc)})
            return set()

        data: dict[str, Any] = resp.json()
        return {
            p["base_currency_id"]
            for p in data.get("products", [])
            if "base_currency_id" in p
        }

    def estimate_sweep(
        self, merchant_id: UUID, token: str, qty: Decimal, target: str
    ) -> SweepEstimate:
        # Phase 4: query /api/v3/brokerage/best_bid_ask; apply slippage model.
        raise NotImplementedError("estimate_sweep is implemented in Phase 4")

    def execute_sweep(
        self, merchant_id: UUID, token: str, qty: Decimal, target: str
    ) -> SweepResult:
        # Phase 4: POST /api/v3/brokerage/orders with idempotency key.
        # Pre-execution checks: balance ≥ qty, slippage ≤ SWEEP_MAX_SLIPPAGE_PCT.
        raise NotImplementedError("execute_sweep is implemented in Phase 4")

    # ── private helpers ───────────────────────────────────────────────────────

    def _auth_headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        """HMAC-SHA256 auth for Coinbase Advanced Trade API (§5.7).

        Signature covers: timestamp + METHOD + path + body.
        CB-ACCESS-KEY is the key UUID (last segment of the full key name).
        """
        timestamp = str(int(time.time()))
        api_key_id = self._api_key_name.split("/")[-1]
        message = timestamp + method.upper() + path + body
        secret = base64.b64decode(self._api_secret)
        signature = hmac.new(secret, message.encode("utf-8"), hashlib.sha256).hexdigest()
        return {
            "CB-ACCESS-KEY": api_key_id,
            "CB-ACCESS-SIGN": signature,
            "CB-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }


# Confirm CoinbaseSweepVenue satisfies SweepVenue at import time
_: SweepVenue = CoinbaseSweepVenue("", "")  # type: ignore[assignment]

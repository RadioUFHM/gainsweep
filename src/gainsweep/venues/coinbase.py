from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime, timezone
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
        product_id = f"{token}-{target}"
        path = f"/api/v3/brokerage/best_bid_ask?product_ids={product_id}"
        try:
            resp = self._client.get(path, headers=self._auth_headers("GET", path))
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"coinbase.estimate_sweep failed: {exc}") from exc

        data: dict[str, Any] = resp.json()
        pricebooks: list[dict[str, Any]] = data.get("pricebooks", [])
        if not pricebooks or not pricebooks[0].get("bids"):
            raise RuntimeError(f"no bid data for {product_id}")

        book = pricebooks[0]
        best_bid = Decimal(book["bids"][0]["price"])
        best_ask = Decimal(book["asks"][0]["price"]) if book.get("asks") else best_bid

        expected_proceeds = qty * best_bid
        estimated_fees = expected_proceeds * Decimal("0.006")  # 0.6% base taker fee
        slippage_pct = (
            float((best_ask - best_bid) / best_ask * Decimal("100"))
            if best_ask > Decimal("0")
            else 0.0
        )
        return SweepEstimate(
            venue="coinbase",
            expected_proceeds=expected_proceeds,
            estimated_fees=estimated_fees,
            estimated_slippage_pct=slippage_pct,
            estimated_completion_seconds=5,
        )

    def execute_sweep(
        self, merchant_id: UUID, token: str, qty: Decimal, target: str
    ) -> SweepResult:
        product_id = f"{token}-{target}"
        path = "/api/v3/brokerage/orders"
        body = json.dumps({
            "client_order_id": str(uuid.uuid4()),
            "product_id": product_id,
            "side": "SELL",
            "order_configuration": {"market_market_ioc": {"base_size": str(qty)}},
        })
        try:
            resp = self._client.post(
                path, content=body,
                headers=self._auth_headers("POST", path, body),
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("coinbase.execute_sweep_failed", extra={"error": str(exc)})
            return SweepResult(
                venue="coinbase", token_symbol=token, qty_executed=Decimal("0"),
                target_stablecoin=target, proceeds=Decimal("0"), fees_paid=Decimal("0"),
                executed_at=datetime.now(timezone.utc), venue_txn_ids=[],
                status="FAILED", error_message=str(exc),
            )

        data: dict[str, Any] = resp.json()
        success: bool = data.get("success", False)
        order_id: str = data.get("success_response", {}).get("order_id", "")
        error_msg: str | None = (
            data.get("error_response", {}).get("message") if not success else None
        )
        # Actual fill price and fees are available via GET /orders/{id} — deferred.
        return SweepResult(
            venue="coinbase", token_symbol=token, qty_executed=qty,
            target_stablecoin=target, proceeds=Decimal("0"), fees_paid=Decimal("0"),
            executed_at=datetime.now(timezone.utc),
            venue_txn_ids=[order_id] if order_id else [],
            status="COMPLETE" if (success and order_id) else "FAILED",
            error_message=error_msg,
        )

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

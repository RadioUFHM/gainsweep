from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

import httpx

from gainsweep.venues.coinbase import _PRODUCTION_BASE_URL, _SANDBOX_BASE_URL

log = logging.getLogger(__name__)

_ACCOUNTS_PATH = "/api/v3/brokerage/accounts"


class PositionTracker:
    """Reads per-merchant token balances from Coinbase. (§5.2)

    Phase 2: get_positions implemented (mocked in tests).
    Phase 4: get_cost_basis from fills history; refresh cadence wired to scheduler.
    """

    def __init__(
        self,
        api_key_name: str,
        private_key_pem: str,
        env: str = "sandbox",
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key_name = api_key_name
        self._private_key_pem = private_key_pem
        base_url = _PRODUCTION_BASE_URL if env == "production" else _SANDBOX_BASE_URL
        self._client = client or httpx.Client(base_url=base_url, timeout=30.0)

    # ── public interface (§5.2) ───────────────────────────────────────────────

    def get_positions(self, merchant_id: uuid.UUID) -> dict[str, Decimal]:
        """Return {token_symbol: qty} for all non-zero balances.

        Uses Coinbase /api/v3/brokerage/accounts. Fails open on HTTP error.
        """
        try:
            resp = self._client.get(
                _ACCOUNTS_PATH,
                headers=self._auth_headers("GET", _ACCOUNTS_PATH),
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error(
                "position_tracker.get_positions_failed",
                extra={"merchant_id": str(merchant_id), "error": str(exc)},
            )
            return {}

        data: dict[str, Any] = resp.json()
        positions: dict[str, Decimal] = {}

        for account in data.get("accounts", []):
            currency = account.get("currency")
            balance_info = account.get("available_balance", {})
            raw_value = balance_info.get("value", "0")
            if not currency:
                continue
            qty = Decimal(str(raw_value))
            if qty > Decimal("0"):
                positions[currency] = qty

        return positions

    def get_cost_basis(
        self, merchant_id: uuid.UUID, token: str
    ) -> Decimal | None:
        # Phase 4: query fills history from /api/v3/brokerage/orders/historical/fills
        raise NotImplementedError("get_cost_basis is implemented in Phase 4")

    # ── private helpers ───────────────────────────────────────────────────────

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        # Phase 4: CDP JWT (ES256) signing — same pattern as CoinbaseSweepVenue
        return {}

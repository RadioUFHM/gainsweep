from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx

from gainsweep.protocols.price_provider import PriceProvider, PriceQuote

log = logging.getLogger(__name__)

_FREE_BASE_URL = "https://api.coingecko.com/api/v3"
_PRO_BASE_URL = "https://pro-api.coingecko.com/api/v3"

# Starter symbol→CoinGecko-ID mapping. CoinGecko IDs differ from ticker symbols
# and are not guaranteed unique by symbol. Extend this dict as new tokens are
# needed; Phase 2+ may move it to the DB. (§5.1)
SYMBOL_TO_COINGECKO_ID: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "MATIC": "matic-network",
    "POL": "matic-network",  # MATIC rebranded to POL 2024
    "USDC": "usd-coin",
    "USDT": "tether",
    "DAI": "dai",
    "BNB": "binancecoin",
    "ADA": "cardano",
    "DOT": "polkadot",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "UNI": "uniswap",
    "XRP": "ripple",
    "LTC": "litecoin",
    "DOGE": "dogecoin",
}

_CACHE_TTL_SECONDS: float = 30.0
# CoinGecko market_chart/range returns hourly data only for ≤ 90-day windows;
# longer ranges fall back to daily candles. We chunk at 89 days. (§5.1)
_HISTORICAL_CHUNK_DAYS = 89


@dataclass
class _CacheEntry:
    quote: PriceQuote
    expires_at: float = field(default=0.0)  # time.monotonic()


class CoinGeckoProvider:
    """PriceProvider backed by CoinGecko public/pro API (§5.1).

    Satisfies the PriceProvider protocol; pass as ``provider: PriceProvider``.
    """

    def __init__(
        self,
        api_key: str = "",
        symbol_map: dict[str, str] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        base_url = _PRO_BASE_URL if api_key else _FREE_BASE_URL
        self._api_key = api_key
        self._symbol_map: dict[str, str] = {
            **SYMBOL_TO_COINGECKO_ID,
            **(symbol_map or {}),
        }
        self._cache: dict[str, _CacheEntry] = {}
        self._client = client or httpx.Client(
            base_url=base_url,
            headers={"x-cg-pro-api-key": api_key} if api_key else {},
            timeout=10.0,
        )

    # ── PriceProvider interface ───────────────────────────────────────────────

    def get_price(self, symbol: str, vs: str = "USD") -> PriceQuote:
        results = self.get_batch([symbol], vs)
        if symbol not in results:
            raise KeyError(f"No price returned for {symbol!r} from CoinGecko")
        return results[symbol]

    def get_batch(self, symbols: list[str], vs: str = "USD") -> dict[str, PriceQuote]:
        now_mono = time.monotonic()
        fresh: dict[str, PriceQuote] = {}
        stale: list[str] = []

        for sym in symbols:
            entry = self._cache.get(sym)
            if entry and entry.expires_at > now_mono:
                fresh[sym] = entry.quote
            else:
                stale.append(sym)

        if stale:
            fetched = self._fetch_batch(stale, vs.lower())
            fresh.update(fetched)

        return fresh

    def get_historical_hourly(
        self, symbol: str, start: datetime, end: datetime, vs: str = "USD"
    ) -> list[PriceQuote]:
        cg_id = self._resolve_id(symbol)
        if cg_id is None:
            log.warning("coingecko.unknown_symbol", extra={"symbol": symbol})
            return []

        all_quotes: list[PriceQuote] = []
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(chunk_start + timedelta(days=_HISTORICAL_CHUNK_DAYS), end)
            chunk = self._fetch_historical_chunk(cg_id, symbol, chunk_start, chunk_end, vs.lower())
            all_quotes.extend(chunk)
            chunk_start = chunk_end

        return all_quotes

    # ── private helpers ───────────────────────────────────────────────────────

    def _fetch_batch(self, symbols: list[str], vs: str) -> dict[str, PriceQuote]:
        id_to_symbol: dict[str, str] = {}
        cg_ids: list[str] = []
        for sym in symbols:
            cg_id = self._resolve_id(sym)
            if cg_id is None:
                log.warning("coingecko.unknown_symbol", extra={"symbol": sym})
                continue
            cg_ids.append(cg_id)
            id_to_symbol[cg_id] = sym

        if not cg_ids:
            return {}

        try:
            resp = self._client.get(
                "/simple/price",
                params={
                    "ids": ",".join(cg_ids),
                    "vs_currencies": vs,
                    "include_last_updated_at": "true",
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("coingecko.fetch_batch_failed", extra={"error": str(exc)})
            return {}

        data: dict[str, Any] = resp.json()
        now_ts = datetime.now(timezone.utc)
        now_mono = time.monotonic()
        results: dict[str, PriceQuote] = {}

        for cg_id, values in data.items():
            sym = id_to_symbol.get(cg_id)
            if sym is None or vs not in values:
                continue
            raw_ts = values.get(f"{vs}_last_updated_at")
            ts = datetime.fromtimestamp(raw_ts, tz=timezone.utc) if raw_ts else now_ts
            quote = PriceQuote(
                symbol=sym,
                price=Decimal(str(values[vs])),
                timestamp=ts,
                source="coingecko",
            )
            results[sym] = quote
            self._cache[sym] = _CacheEntry(
                quote=quote,
                expires_at=now_mono + _CACHE_TTL_SECONDS,
            )

        return results

    def _fetch_historical_chunk(
        self,
        cg_id: str,
        symbol: str,
        start: datetime,
        end: datetime,
        vs: str,
    ) -> list[PriceQuote]:
        try:
            resp = self._client.get(
                f"/coins/{cg_id}/market_chart/range",
                params={
                    "vs_currency": vs,
                    "from": int(start.timestamp()),
                    "to": int(end.timestamp()),
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error(
                "coingecko.fetch_historical_failed",
                extra={"cg_id": cg_id, "error": str(exc)},
            )
            return []

        data: dict[str, Any] = resp.json()
        return [
            PriceQuote(
                symbol=symbol,
                price=Decimal(str(price)),
                timestamp=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                source="coingecko",
            )
            for ts_ms, price in data.get("prices", [])
        ]

    def _resolve_id(self, symbol: str) -> str | None:
        return self._symbol_map.get(symbol.upper())


# Confirm CoinGeckoProvider satisfies PriceProvider at import time
_: PriceProvider = CoinGeckoProvider()  # type: ignore[assignment]

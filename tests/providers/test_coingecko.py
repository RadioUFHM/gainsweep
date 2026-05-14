from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
import respx

from gainsweep.providers.coingecko import CoinGeckoProvider, _CACHE_TTL_SECONDS

_BASE = "https://api.coingecko.com/api/v3"
_PRICE_URL = f"{_BASE}/simple/price"
_CHART_URL_ETH = f"{_BASE}/coins/ethereum/market_chart/range"


def _make_provider(**kwargs: object) -> CoinGeckoProvider:
    return CoinGeckoProvider(**kwargs)  # type: ignore[arg-type]


# ── get_batch ─────────────────────────────────────────────────────────────────


@respx.mock
def test_get_batch_returns_price_for_known_symbol() -> None:
    respx.get(_PRICE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"ethereum": {"usd": 3421.50, "usd_last_updated_at": 1715698000}},
        )
    )
    provider = _make_provider()
    result = provider.get_batch(["ETH"])

    assert "ETH" in result
    quote = result["ETH"]
    assert quote.symbol == "ETH"
    assert quote.price == Decimal("3421.5")
    assert quote.source == "coingecko"
    assert quote.timestamp == datetime.fromtimestamp(1715698000, tz=timezone.utc)


@respx.mock
def test_get_batch_handles_multiple_symbols() -> None:
    respx.get(_PRICE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "ethereum": {"usd": 3421.50},
                "bitcoin": {"usd": 65000.0},
            },
        )
    )
    provider = _make_provider()
    result = provider.get_batch(["ETH", "BTC"])

    assert set(result.keys()) == {"ETH", "BTC"}
    assert result["BTC"].price == Decimal("65000.0")


@respx.mock
def test_get_batch_skips_unknown_symbol() -> None:
    provider = _make_provider()
    result = provider.get_batch(["UNKNOWN_XYZ"])

    assert result == {}
    assert respx.calls.call_count == 0  # no HTTP call made for unknown symbol


@respx.mock
def test_get_batch_fails_open_on_http_error(caplog: pytest.LogCaptureFixture) -> None:
    respx.get(_PRICE_URL).mock(return_value=httpx.Response(429, text="rate limited"))

    provider = _make_provider()
    result = provider.get_batch(["ETH"])

    assert result == {}
    assert "coingecko.fetch_batch_failed" in caplog.text


@respx.mock
def test_get_batch_caches_result_within_ttl() -> None:
    respx.get(_PRICE_URL).mock(
        return_value=httpx.Response(200, json={"ethereum": {"usd": 3421.50}})
    )
    provider = _make_provider()

    provider.get_batch(["ETH"])
    provider.get_batch(["ETH"])  # should hit cache

    assert respx.calls.call_count == 1


@respx.mock
def test_get_batch_refetches_after_ttl_expiry() -> None:
    respx.get(_PRICE_URL).mock(
        return_value=httpx.Response(200, json={"ethereum": {"usd": 3421.50}})
    )
    provider = _make_provider()
    provider.get_batch(["ETH"])

    # Manually expire the cache entry
    provider._cache["ETH"].expires_at = time.monotonic() - 1.0

    provider.get_batch(["ETH"])

    assert respx.calls.call_count == 2


# ── get_price ─────────────────────────────────────────────────────────────────


@respx.mock
def test_get_price_returns_single_quote() -> None:
    respx.get(_PRICE_URL).mock(
        return_value=httpx.Response(200, json={"ethereum": {"usd": 3421.50}})
    )
    provider = _make_provider()
    quote = provider.get_price("ETH")

    assert quote.symbol == "ETH"
    assert quote.price == Decimal("3421.5")


@respx.mock
def test_get_price_raises_for_unknown_symbol() -> None:
    provider = _make_provider()
    with pytest.raises(KeyError, match="UNKNOWN"):
        provider.get_price("UNKNOWN")


# ── get_historical_hourly ─────────────────────────────────────────────────────


@respx.mock
def test_get_historical_hourly_returns_price_quotes() -> None:
    ts_ms = 1715698000000
    respx.get(_CHART_URL_ETH).mock(
        return_value=httpx.Response(
            200,
            json={"prices": [[ts_ms, 3400.0], [ts_ms + 3_600_000, 3450.0]]},
        )
    )
    provider = _make_provider()
    start = datetime(2024, 5, 1, tzinfo=timezone.utc)
    end = datetime(2024, 5, 30, tzinfo=timezone.utc)

    quotes = provider.get_historical_hourly("ETH", start, end)

    assert len(quotes) == 2
    assert quotes[0].price == Decimal("3400.0")
    assert quotes[1].price == Decimal("3450.0")
    assert quotes[0].timestamp == datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


@respx.mock
def test_get_historical_hourly_returns_empty_for_unknown_symbol() -> None:
    provider = _make_provider()
    start = datetime(2024, 5, 1, tzinfo=timezone.utc)
    end = datetime(2024, 5, 30, tzinfo=timezone.utc)

    quotes = provider.get_historical_hourly("UNKNOWN_XYZ", start, end)

    assert quotes == []
    assert respx.calls.call_count == 0


@respx.mock
def test_get_historical_hourly_chunks_long_range() -> None:
    """Ranges > 89 days must be split into multiple requests."""
    respx.get(_CHART_URL_ETH).mock(
        return_value=httpx.Response(200, json={"prices": [[1715698000000, 3400.0]]})
    )
    provider = _make_provider()
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 6, 1, tzinfo=timezone.utc)  # ~152 days → 2 chunks

    provider.get_historical_hourly("ETH", start, end)

    assert respx.calls.call_count == 2


@respx.mock
def test_get_historical_hourly_fails_open_on_http_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    respx.get(_CHART_URL_ETH).mock(return_value=httpx.Response(500))
    provider = _make_provider()
    start = datetime(2024, 5, 1, tzinfo=timezone.utc)
    end = datetime(2024, 5, 30, tzinfo=timezone.utc)

    quotes = provider.get_historical_hourly("ETH", start, end)

    assert quotes == []
    assert "coingecko.fetch_historical_failed" in caplog.text


# ── custom symbol map ─────────────────────────────────────────────────────────


@respx.mock
def test_custom_symbol_map_overrides_default() -> None:
    respx.get(_PRICE_URL).mock(
        return_value=httpx.Response(
            200, json={"my-custom-token": {"usd": 1.0}}
        )
    )
    provider = _make_provider(symbol_map={"MCT": "my-custom-token"})
    result = provider.get_batch(["MCT"])

    assert "MCT" in result
    assert result["MCT"].price == Decimal("1.0")

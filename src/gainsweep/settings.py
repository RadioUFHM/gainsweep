from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://localhost/gainsweep"

    # Price provider (§5.1)
    price_provider: str = "coingecko_free"
    coingecko_api_key: str = ""
    price_poll_interval_seconds: int = 60

    # Coinbase venue (§5.7)
    # Targets sandbox by default. Set COINBASE_ENV=production to use live API.
    coinbase_env: str = "sandbox"
    coinbase_key_name: str = ""   # projects/{project_id}/apiKeys/{key_id}
    coinbase_private_key: str = ""  # base64-encoded HMAC secret
    coinbase_rate_limit_rps: int = 10
    sweep_max_slippage_pct: float = 2.0


settings = Settings()

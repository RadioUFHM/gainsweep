# Merchant Sweep — MVP Specification

**Status:** Draft v0.1 **Owner:** \[you\] **Last updated:** 2026-05-14

---

## 1\. Purpose

A back-end service that helps merchants who accept cryptocurrency for goods/services manage price-volatility risk. When a token's price drops below a configurable threshold from its daily high, the system alerts the merchant in-app and offers a one-tap sweep into a designated stablecoin.

This is **not** a trading bot. It does not seek profit. It exists to give merchants the same predictability they'd have if they were paid in fiat, while preserving optional upside.

## 2\. Scope of this document

This spec covers the MVP. In scope:

- Daily-high price tracking per `(merchant, token)`  
- Drawdown-based alert generation  
- In-app alert response loop (Sweep / HODL / Snooze 3%)  
- Optional auto-sweep on alert timeout  
- Scheduled sweeps (cron-based)  
- Sweep execution via Coinbase Advanced Trade API  
- Backtesting harness

Out of scope for MVP (but architecture must not preclude):

- Multiple sweep venues (Kraken, DEX aggregators)  
- Multiple price providers / aggregation  
- Self-custody wallet integration  
- Merchant onboarding UI  
- Fiat off-ramp  
- Tax reporting

## 3\. Architectural principles

1. **Swappable components behind interfaces.** Price providers and sweep venues are protocols, not classes. The MVP ships with one concrete implementation of each; adding others is purely additive.  
2. **Idempotent, replayable, auditable.** Every state change is logged with full context. Same input \+ same state should produce the same output. Backtests run the same code paths as production.  
3. **Fail safe, not fast.** When in doubt, alert the merchant and do nothing. Never execute a sweep on partial information. Stablecoin de-peg checks gate every sweep.  
4. **Trade-only API permissions, structurally.** The system cannot move funds off the exchange. Withdrawal is always merchant-initiated through the exchange UI.  
5. **Conservative defaults.** 1-hour closes for daily high, 5% drawdown threshold, auto-sweep OFF by default.

## 4\. Domain model

### 4.1 Entities

```
Merchant
  id                          UUID
  display_name                str
  created_at                  timestamp
  default_alert_config_id     FK -> MerchantAlertConfig

MerchantAlertConfig
  id                          UUID
  merchant_id                 FK -> Merchant
  target_stablecoin           enum: USDC | USDT | DAI
  drawdown_threshold_pct      Decimal       (default: 5.0)
  rearm_on_new_high           bool          (default: true)
  auto_sweep_enabled          bool          (default: false)
  auto_sweep_timeout_minutes  int           (default: 30)
  sweep_schedule_cron         str nullable  (e.g., "0 23 * * *")
  daily_window_timezone       str           (default: "UTC")
  stablecoin_depeg_floor      Decimal       (default: 0.97)
  quiet_hours                 JSON nullable

MerchantVenueCredential
  id                          UUID
  merchant_id                 FK -> Merchant
  venue                       enum: COINBASE | KRAKEN | ...
  api_key_ref                 str  (reference to secrets manager; NEVER stored in DB)
  scope                       enum: TRADE_ONLY (the only allowed value at MVP)
  created_at                  timestamp

TokenDailySnapshot
  PRIMARY KEY (merchant_id, token_symbol, date)
  merchant_id                 FK -> Merchant
  token_symbol                str  (e.g., "ETH", "MATIC")
  date                        date (in merchant's daily_window_timezone)
  hourly_closes               JSON: [{hour: 0, close: "3421.50", ts: "..."}]
  daily_high                  Decimal
  daily_high_hour             int  (0-23, nullable until first close)
  current_price               Decimal
  current_price_ts            timestamp
  position_qty                Decimal
  cost_basis_avg              Decimal nullable
  alert_state                 enum: ARMED | FIRED | AUTO_SWEEP_PENDING |
                                    SNOOZED | RESOLVED_SWEEP |
                                    RESOLVED_HODL | RESOLVED_EXPIRED
  snooze_active               bool
  snooze_trigger_price        Decimal nullable
  last_alert_id               FK -> Alert nullable
  updated_at                  timestamp

Alert
  id                          UUID
  merchant_id                 FK -> Merchant
  snapshot_pk                 (merchant_id, token_symbol, date) reference
  kind                        enum: DRAWDOWN | SCHEDULED_SWEEP |
                                    STABLECOIN_DEPEG | AUTO_SWEEP_ABORTED_DEPEG
  fired_at                    timestamp
  payload                     JSON  (snapshot of all relevant values at fire time)
  response                    enum: SWEEP | HODL | SNOOZE | TIMEOUT_AUTO_SWEEP |
                                    TIMEOUT_EXPIRED | NULL
  responded_at                timestamp nullable
  resulting_sweep_id          FK -> SweepExecution nullable

SweepExecution
  id                          UUID
  merchant_id                 FK -> Merchant
  triggered_by_alert_id       FK -> Alert nullable
  triggered_by_schedule       bool
  venue                       str  (e.g., "coinbase")
  token_symbol                str
  qty_requested               Decimal
  qty_executed                Decimal
  target_stablecoin           str
  proceeds                    Decimal
  fees_paid                   Decimal
  status                      enum: PENDING | COMPLETE | PARTIAL | FAILED
  venue_txn_ids               JSON array of str
  error_message               str nullable
  created_at                  timestamp
  completed_at                timestamp nullable

PriceObservation
  id                          BIGSERIAL
  token_symbol                str
  price                       Decimal
  source                      str
  observed_at                 timestamp
  INDEX (token_symbol, observed_at DESC)
```

### 4.2 State machine — TokenDailySnapshot.alert\_state

```
                    ┌─────────────────────────────────────────────────────┐
                    │                                                     │
              [new day]                                                   │
                    │                                                     │
                    ▼                                                     │
   ┌──────────► ARMED ──────► (drawdown >= threshold) ──────► FIRED ──┐  │
   │              ▲                                                    │  │
   │              │                                                    │  │
   │       [new daily high                                             │  │
   │        + rearm_on_new_high]                                       │  │
   │              │                                                    │  │
   │       ┌──────┴──────────────────────────────┐                     │  │
   │       │                                     │                     │  │
   │   from FIRED                          from SNOOZED                │  │
   │                                                                   │  │
   │   ┌── merchant taps Sweep ────► RESOLVED_SWEEP ──► (terminal) ◄───┤  │
   │   ├── merchant taps HODL ─────► RESOLVED_HODL ──► (terminal) ◄────┤  │
   │   ├── merchant taps Snooze ───► SNOOZED ────────► (drawdown ≥ ────┘  │
   │   │                                                snooze_trigger)   │
   │   │                                                                  │
   │   └── auto_sweep_enabled ────► AUTO_SWEEP_PENDING ──► (timeout) ─────┤
   │                                       │                              │
   │                                       └─► (merchant taps any) ──────┤
   │                                                                     │
   └─────────────────────────────────────────────────────────────────────┘
```

Terminal states reset to `ARMED` at the start of the next daily window.

## 5\. Component specifications

### 5.1 PriceProvider (interface)

```py
from typing import Protocol
from decimal import Decimal
from datetime import datetime
from dataclasses import dataclass

@dataclass
class PriceQuote:
    symbol: str
    price: Decimal
    timestamp: datetime  # UTC, tz-aware
    source: str
    confidence: float = 1.0  # reserved for aggregation; unused at MVP

class PriceProvider(Protocol):
    def get_price(self, symbol: str, vs: str = "USD") -> PriceQuote: ...
    def get_batch(self, symbols: list[str], vs: str = "USD") -> dict[str, PriceQuote]: ...
    def get_historical_hourly(
        self, symbol: str, start: datetime, end: datetime, vs: str = "USD"
    ) -> list[PriceQuote]: ...  # for backtesting
```

**MVP implementation:** `CoinGeckoProvider`

- Free tier; \~10-30 calls/min rate limit (verify current limits at integration time)  
- Use `simple/price` endpoint for `get_batch`  
- Use `coins/{id}/market_chart/range` for historical  
- Maintain a symbol→coingecko\_id mapping table (token symbols are not unique on CoinGecko); Phase 1 ships a hardcoded dict of common tokens; Phase 2+ may move this to the DB  
- Cache responses for 30 seconds to stay under rate limits  
- Fail open: if the provider returns an error, log it and skip this tick; do not crash the tracker  
- **Hourly granularity constraint:** `market_chart/range` returns hourly data only for ranges ≤ 90 days. For longer back-test windows, `get_historical_hourly` chunks requests into ≤ 89-day slices automatically.

**Configuration:**

```
PRICE_PROVIDER=coingecko_free
COINGECKO_API_KEY=  (optional, for pro tier)
PRICE_POLL_INTERVAL_SECONDS=60
```

### 5.2 PositionTracker

Responsible for knowing how much of each token each merchant currently holds. For the Coinbase-first MVP, this means querying Coinbase account balances per merchant.

```py
class PositionTracker:
    def get_positions(self, merchant_id: UUID) -> dict[str, Decimal]:
        """Returns {token_symbol: qty} for non-zero balances."""

    def get_cost_basis(self, merchant_id: UUID, token: str) -> Decimal | None:
        """Average cost basis if known, else None. MVP: from Coinbase fills history."""
```

Refresh cadence: every 5 minutes, plus on-demand before any sweep execution.

### 5.3 DailyHighTracker

Consumes price ticks, maintains hourly closes and daily highs.

```py
class DailyHighTracker:
    def on_price_tick(
        self, merchant_id: UUID, token: str, price: Decimal, ts: datetime
    ) -> None:
        """Update current_price; buffer for hourly-close computation."""

    def finalize_hour(self, hour_start: datetime) -> None:
        """Called on each hour boundary. Promotes buffered prices to hourly closes
        and recomputes daily_high for all affected snapshots."""

    def reset_for_new_day(self, merchant_id: UUID, token: str, tz: str) -> None:
        """Called at the merchant's local midnight. Creates a fresh snapshot."""
```

**Hourly-close rule:** the close for hour H is the **last** price observation recorded during `[H:00:00, H+1:00:00)`. If no observation exists for an hour (provider outage), record `null` and skip — do not interpolate.

**Daily-high computation:** `max(close for close in hourly_closes if close is not None)`.

**Re-arm logic:** when `daily_high` is updated to a new value AND `rearm_on_new_high == true` AND `alert_state == FIRED`, transition state to `ARMED`. (Snoozed alerts also clear on new high — `snooze_active = false`, `snooze_trigger_price = null`.)

### 5.4 AlertEngine

Runs on every price tick, after `DailyHighTracker.on_price_tick`.

```py
class AlertEngine:
    def evaluate(self, merchant_id: UUID, token: str) -> None:
        snapshot = db.get_snapshot(merchant_id, token, today_in_merchant_tz(merchant_id))
        config  = db.get_config(merchant_id)

        if snapshot.daily_high_hour is None:
            return  # no hourly close yet today

        # Determine the comparison price
        if snapshot.snooze_active and snapshot.alert_state == "SNOOZED":
            reference = snapshot.snooze_trigger_price
            should_fire = snapshot.current_price <= reference
        elif snapshot.alert_state == "ARMED":
            drawdown_pct = (snapshot.daily_high - snapshot.current_price) / snapshot.daily_high * 100
            should_fire = drawdown_pct >= config.drawdown_threshold_pct
        else:
            return  # FIRED, AUTO_SWEEP_PENDING, RESOLVED_*: nothing to do

        if not should_fire:
            return

        # Stablecoin health gate
        stable_quote = price_provider.get_price(config.target_stablecoin)
        if stable_quote.price < config.stablecoin_depeg_floor:
            self._emit_alert(merchant_id, token, kind="STABLECOIN_DEPEG", snapshot=snapshot)
            return  # do NOT recommend sweep into broken peg

        # Fire drawdown alert
        alert_id = self._emit_alert(merchant_id, token, kind="DRAWDOWN", snapshot=snapshot)
        snapshot.alert_state = "FIRED"
        snapshot.last_alert_id = alert_id
        db.save(snapshot)

        if config.auto_sweep_enabled:
            snapshot.alert_state = "AUTO_SWEEP_PENDING"
            db.save(snapshot)
            schedule_job(
                "auto_sweep_timeout",
                run_at=now() + timedelta(minutes=config.auto_sweep_timeout_minutes),
                payload={"alert_id": alert_id},
            )
```

### 5.5 Alert response endpoint

```
POST /api/v1/alerts/{alert_id}/respond

Body:
{
  "action": "SWEEP" | "HODL" | "SNOOZE",
  "snooze_delta_pct": 3.0           // required when action == SNOOZE
}

Response:
{
  "alert_id": "...",
  "snapshot_state": "RESOLVED_SWEEP" | "RESOLVED_HODL" | "SNOOZED",
  "sweep_id": "..." | null,         // present if action == SWEEP
  "snooze_trigger_price": "..." | null
}
```

Handler:

```py
def respond_to_alert(alert_id: UUID, action: str, snooze_delta_pct: float | None):
    alert = db.get_alert(alert_id)
    if alert.response is not None:
        raise AlreadyResponded(alert.response)

    snapshot = db.get_snapshot_for_alert(alert)

    if action == "SWEEP":
        sweep_id = sweep_orchestrator.execute(
            merchant_id=alert.merchant_id,
            token=snapshot.token_symbol,
            qty=snapshot.position_qty,
            triggered_by_alert_id=alert.id,
        )
        snapshot.alert_state = "RESOLVED_SWEEP"
        alert.response = "SWEEP"
        alert.resulting_sweep_id = sweep_id

    elif action == "HODL":
        snapshot.alert_state = "RESOLVED_HODL"
        alert.response = "HODL"

    elif action == "SNOOZE":
        if snooze_delta_pct is None or snooze_delta_pct <= 0:
            raise InvalidSnooze()
        snapshot.alert_state = "SNOOZED"
        snapshot.snooze_active = True
        snapshot.snooze_trigger_price = snapshot.current_price * (
            Decimal("1") - Decimal(str(snooze_delta_pct)) / Decimal("100")
        )
        alert.response = "SNOOZE"

    alert.responded_at = now()
    db.save(alert)
    db.save(snapshot)
    return build_response(...)
```

### 5.6 Auto-sweep timeout handler

```py
def on_auto_sweep_timeout(alert_id: UUID):
    alert = db.get_alert(alert_id)
    snapshot = db.get_snapshot_for_alert(alert)

    if snapshot.alert_state != "AUTO_SWEEP_PENDING":
        return  # merchant responded in time; nothing to do

    # Re-check stablecoin peg at execution time
    config = db.get_config(alert.merchant_id)
    stable_quote = price_provider.get_price(config.target_stablecoin)
    if stable_quote.price < config.stablecoin_depeg_floor:
        snapshot.alert_state = "FIRED"  # downgrade to manual
        emit_alert(alert.merchant_id, kind="AUTO_SWEEP_ABORTED_DEPEG", ...)
        db.save(snapshot)
        return

    sweep_id = sweep_orchestrator.execute(
        merchant_id=alert.merchant_id,
        token=snapshot.token_symbol,
        qty=snapshot.position_qty,
        triggered_by_alert_id=alert.id,
    )
    snapshot.alert_state = "RESOLVED_SWEEP"
    alert.response = "TIMEOUT_AUTO_SWEEP"
    alert.responded_at = now()
    alert.resulting_sweep_id = sweep_id
    db.save(snapshot)
    db.save(alert)
```

### 5.7 SweepVenue (interface)

```py
@dataclass
class SweepEstimate:
    venue: str
    expected_proceeds: Decimal
    estimated_fees: Decimal
    estimated_slippage_pct: float
    estimated_completion_seconds: int

@dataclass
class SweepResult:
    venue: str
    token_symbol: str
    qty_executed: Decimal
    target_stablecoin: str
    proceeds: Decimal
    fees_paid: Decimal
    executed_at: datetime
    venue_txn_ids: list[str]
    status: Literal["COMPLETE", "PARTIAL", "FAILED"]
    error_message: str | None = None

class SweepVenue(Protocol):
    def get_supported_tokens(self) -> set[str]: ...
    def estimate_sweep(
        self, merchant_id: UUID, token: str, qty: Decimal, target: str
    ) -> SweepEstimate: ...
    def execute_sweep(
        self, merchant_id: UUID, token: str, qty: Decimal, target: str
    ) -> SweepResult: ...
```

**MVP implementation:** `CoinbaseSweepVenue`

- Uses Coinbase Advanced Trade API  
- Per-merchant API key (trade-only scope, no withdrawal)  
- API keys stored in secrets manager (AWS Secrets Manager / GCP Secret Manager / Vault); the DB only stores a reference  
- Sweep \= market sell against the `{TOKEN}-{STABLECOIN}` pair (e.g., `ETH-USDC`)  
- If pair doesn't exist, attempt `{TOKEN}-USD` then convert USD→stablecoin in a second leg  
- Slippage protection: reject execution if `estimate` shows \>2% slippage; surface to merchant as a different alert  
- Pre-execution check: confirm balance ≥ qty (handles race conditions where balance changed since snapshot)  
- **Authentication:** HMAC-SHA256. Each request includes three headers: `CB-ACCESS-KEY` (the API key UUID, extracted as the last path segment of the full key name), `CB-ACCESS-SIGN` (HMAC-SHA256 of `timestamp + METHOD + path + body`, hex-encoded, signed with the base64-decoded API secret), and `CB-ACCESS-TIMESTAMP` (Unix timestamp as a string). The API secret is a base64-encoded value issued by the Coinbase portal.

**Configuration:**

```
COINBASE_ENV=sandbox          # default; set to "production" to target live API
                              # sandbox base: https://api-sandbox.coinbase.com
                              # production base: https://api.coinbase.com
COINBASE_KEY_NAME=projects/{project_id}/apiKeys/{key_id}
COINBASE_PRIVATE_KEY=<base64-encoded API secret from Coinbase portal>
COINBASE_RATE_LIMIT_RPS=10
SWEEP_MAX_SLIPPAGE_PCT=2.0
```

### 5.8 SweepOrchestrator

Thin coordinator above the venue. Currently routes everything to `CoinbaseSweepVenue`; later will pick venue by token, by merchant preference, or by best-price.

```py
class SweepOrchestrator:
    def __init__(self, venues: dict[str, SweepVenue], default_venue: str):
        self.venues = venues
        self.default = default_venue

    def execute(
        self, merchant_id: UUID, token: str, qty: Decimal,
        triggered_by_alert_id: UUID | None = None,
        triggered_by_schedule: bool = False,
    ) -> UUID:
        config = db.get_config(merchant_id)
        venue = self._pick_venue(merchant_id, token)

        # Idempotency: create SweepExecution row in PENDING state first
        sweep = SweepExecution(
            id=uuid4(),
            merchant_id=merchant_id,
            triggered_by_alert_id=triggered_by_alert_id,
            triggered_by_schedule=triggered_by_schedule,
            venue=venue.__class__.__name__,
            token_symbol=token,
            qty_requested=qty,
            target_stablecoin=config.target_stablecoin,
            status="PENDING",
            created_at=now(),
        )
        db.save(sweep)

        try:
            result = venue.execute_sweep(merchant_id, token, qty, config.target_stablecoin)
            sweep.qty_executed = result.qty_executed
            sweep.proceeds = result.proceeds
            sweep.fees_paid = result.fees_paid
            sweep.status = result.status
            sweep.venue_txn_ids = result.venue_txn_ids
            sweep.completed_at = now()
        except Exception as e:
            sweep.status = "FAILED"
            sweep.error_message = str(e)
            sweep.completed_at = now()
            log.exception("Sweep failed", extra={"sweep_id": sweep.id})
        finally:
            db.save(sweep)

        return sweep.id

    def _pick_venue(self, merchant_id: UUID, token: str) -> SweepVenue:
        # MVP: always default. Future: routing logic.
        return self.venues[self.default]
```

### 5.9 SweepScheduler

Cron-based scheduled sweeps, independent of drawdown alerts.

- Reads `MerchantAlertConfig.sweep_schedule_cron` for every merchant  
- At each scheduled time, emits a `SCHEDULED_SWEEP` alert (same overlay, same response options)  
- Auto-sweep timeout behavior applies the same way

### 5.10 Backtesting harness

```py
@dataclass
class BacktestConfig:
    merchant_config: MerchantAlertConfig  # the strategy under test
    receipts: list[Receipt]                # synthesized or real merchant history
    start_date: date
    end_date: date
    merchant_behavior: MerchantBehaviorModel  # how the simulated merchant responds

@dataclass
class Receipt:
    timestamp: datetime
    token: str
    qty: Decimal
    price_at_receipt: Decimal

@dataclass
class MerchantBehaviorModel:
    p_sweep: float = 1.0      # MVP default: always sweep on alert
    p_hodl: float = 0.0
    p_snooze: float = 0.0
    response_delay_minutes: int = 0  # how long after alert to respond

@dataclass
class BacktestResult:
    config: BacktestConfig
    alerts_fired: int
    sweeps_executed: int
    snoozes_taken: int
    hodls_taken: int
    final_stablecoin_balance: Decimal
    final_token_holdings_value: Decimal
    total_value: Decimal              # final stable + final token value at end_date

    # Counterfactuals
    counterfactual_hodl_value: Decimal       # held everything, never converted
    counterfactual_immediate_sweep: Decimal  # converted every receipt instantly

    # Quality metrics
    drawdowns_avoided_usd: Decimal           # sum of (high - sweep_price) * qty per sweep
    sweeps_with_regret_24h: int              # sweeps where price was higher 24h later
    sweeps_with_regret_72h: int
    avg_regret_pct_24h: float                # mean of (price_24h_later - sweep_price) / sweep_price
    avg_regret_pct_72h: float

    # Per-token breakdown
    by_token: dict[str, "BacktestResult"]
```

**Implementation rule:** the harness uses the same `DailyHighTracker`, `AlertEngine`, and snapshot logic as production. The only differences are (a) the price source is `PriceProvider.get_historical_hourly` rather than live ticks, and (b) the merchant response is simulated by `MerchantBehaviorModel`. This means a bug in the alert logic shows up identically in backtests and production.

**CLI usage:**

```shell
python -m gainsweep.backtest \
  --tokens ETH,MATIC,SOL \
  --start 2025-01-01 \
  --end 2026-01-01 \
  --threshold-pct 5.0 \
  --rearm-on-new-high \
  --receipt-schedule receipts.json \
  --output report.json
```

## 6\. API surface (MVP)

```
GET  /api/v1/health
GET  /api/v1/merchants/{merchant_id}/snapshots          # current day's snapshots
GET  /api/v1/merchants/{merchant_id}/snapshots/{date}   # historical
GET  /api/v1/merchants/{merchant_id}/alerts             # filterable by status, date
POST /api/v1/alerts/{alert_id}/respond                  # see §5.5
GET  /api/v1/merchants/{merchant_id}/sweeps             # sweep history
GET  /api/v1/merchants/{merchant_id}/config
PUT  /api/v1/merchants/{merchant_id}/config
POST /api/v1/backtest                                   # run a backtest job
GET  /api/v1/backtest/{job_id}                          # poll for results
```

Authentication: bearer tokens, scoped per merchant. (Auth details out of scope for this spec; assume an upstream identity service.)

## 7\. The overlay UI contract

The in-app overlay is a separate front-end concern, but the back-end must provide:

```
GET /api/v1/alerts/{alert_id}

Returns:
{
  "alert_id": "...",
  "kind": "DRAWDOWN",
  "token": "ETH",
  "fired_at": "2026-05-14T14:32:17Z",
  "daily_high": "3421.50",
  "daily_high_at": "2026-05-14T08:00:00Z",
  "current_price": "3243.67",
  "drawdown_pct": 5.20,
  "position_qty": "4.83",
  "position_value_usd": "15666.92",
  "unrealized_vs_high_usd": "-859.20",
  "estimated_sweep": {
    "proceeds_stablecoin": "15510.00",
    "estimated_fees": "78.33",
    "estimated_slippage_pct": 0.4,
    "target_stablecoin": "USDC"
  },
  "snooze_preview": {
    "delta_pct": 3.0,
    "trigger_price": "3146.36",
    "trigger_drawdown_pct": 8.04
  },
  "auto_sweep": {
    "enabled": false,
    "executes_at": null
  }
}
```

The front end renders three buttons: **Sweep** (with proceeds preview), **HODL** (with rearm explanation), **Snooze 3%** (with trigger price preview).

## 8\. Security & compliance constraints

1. **API keys never in DB.** Always via secrets manager reference.  
2. **Trade-only scope enforced on credential creation.** Reject keys with withdrawal permissions; surface a clear error.  
3. **No PII in logs.** Merchant IDs only, never names/emails.  
4. **All sweep executions logged with full reasoning context:** snapshot at alert time, snapshot at execution time, config at execution time, stablecoin price at execution time, venue response. This is the audit trail if a merchant disputes a sweep.  
5. **Auto-sweep opt-in requires explicit acknowledgment** — not a settings checkbox, a dedicated flow with explanatory copy and a typed confirmation. Front-end concern but spec'd here.  
6. **Rate limiting.** Per-merchant API rate limits prevent a compromised front-end from spamming sweep requests.

## 9\. Observability

Metrics (Prometheus or equivalent):

- `price_provider_request_total{provider, status}`  
- `price_provider_latency_seconds{provider}`  
- `alerts_fired_total{kind, merchant_id_hashed}`  
- `sweeps_executed_total{venue, status}`  
- `sweep_proceeds_usd_total{venue}`  
- `sweep_regret_pct_24h` (histogram, populated by a daily job)  
- `snapshot_state_transitions_total{from_state, to_state}`

Structured logs: every state transition, every API call to providers/venues, every error. JSON format, correlation IDs across requests.

Dashboards (minimum):

- Live alerts in flight by state  
- Daily sweep volume by venue  
- Price provider health (success rate, latency)  
- Sweep regret trend (rolling 30-day)

## 10\. Tech stack recommendation

Not prescriptive, but the spec is written assuming:

- **Language:** Python 3.12+  
- **Framework:** FastAPI (async, type hints, OpenAPI for free)  
- **DB:** PostgreSQL 15+  
- **Queue/scheduler:** Redis \+ RQ for jobs; or Celery if you prefer; or APScheduler for the truly minimal version  
- **Decimal handling:** Python `Decimal` everywhere financial values touch the system. Never floats. PostgreSQL `NUMERIC(36, 18)`.  
- **Time:** All timestamps stored UTC, tz-aware. `datetime.now(timezone.utc)` only.

If you prefer Node/TypeScript or Go, the spec translates cleanly. The interface boundaries are language-agnostic.

## 11\. Build order (suggested phases)

**Phase 1 — Foundation (no live behavior yet)**

- Project scaffolding, DB schema, migrations  
- `PriceProvider` interface \+ `CoinGeckoProvider` implementation  
- `SweepVenue` interface \+ `CoinbaseSweepVenue` skeleton (no live calls)  
- Tests for both, with mocked HTTP

**Phase 2 — Tracker**

- `DailyHighTracker` with hourly-close finalization  
- `PositionTracker` reading Coinbase balances  
- Snapshot state machine \+ transitions

**Phase 3 — Alerts**

- `AlertEngine` evaluation logic  
- Alert API endpoints (`GET /alerts/{id}`, `POST /alerts/{id}/respond`)  
- Stablecoin de-peg gating

**Phase 4 — Sweep execution**

- `CoinbaseSweepVenue` live calls (in sandbox first)  
- `SweepOrchestrator`  
- Auto-sweep timeout handler

**Phase 5 — Scheduled sweeps \+ backtesting**

- `SweepScheduler`  
- `Backtest` harness with counterfactuals and regret metrics  
- CLI for running backtests

**Phase 6 — Hardening**

- Observability (metrics, logs, dashboards)  
- Load testing  
- Sandbox→production cutover plan

## 12\. Open questions

Tracked here, to be resolved before the relevant phase:

- **Q1 (Phase 2):** Daily-window timezone — UTC by default, but does the MVP need to support merchant-local windows, or can that wait?  
- **Q2 (Phase 3):** Alert delivery for the in-app overlay — long-polling, WebSocket, server-sent events? Front-end choice but back-end must support.  
- **Q3 (Phase 4):** Coinbase trading pair coverage — for any token where `{TOKEN}-{STABLECOIN}` doesn't exist, what's the two-leg routing logic? Spec'd as "sell to USD then convert" but the second leg needs more thought.  
- **Q4 (Phase 5):** Backtest receipt schedules — do we synthesize realistic merchant transaction patterns, or require the merchant to upload their own history?  
- **Q5 (Phase 6):** Production deployment target — managed Kubernetes, Fly.io, Render, etc.?

## 13\. Glossary

- **Daily high:** the maximum 1-hour close observed since the start of the current daily window.  
- **Daily window:** a 24-hour period starting at midnight in the merchant's configured timezone (UTC by default).  
- **Drawdown:** the percentage drop from the daily high to the current price.  
- **Re-arm:** transitioning a snapshot from `FIRED` back to `ARMED` when a new daily high is set, enabling another alert in the same day.  
- **Snooze:** merchant action that suppresses further alerts until price drops by an additional configurable percentage from the snooze-time price.  
- **Sweep:** converting a token holding to a stablecoin.  
- **Sweep venue:** an exchange or DEX where sweeps are executed.  
- **Sweep regret:** the difference between a sweep's execution price and the token's price some time after execution; measures whether the algorithm sold too early.  
- **Trade-only scope:** API key permissions limited to placing orders, with no withdrawal capability.


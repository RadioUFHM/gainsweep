from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# §10: all financial values use NUMERIC(36, 18) — never float
_NUMERIC = sa.NUMERIC(36, 18)


class AlertState(str, enum.Enum):
    ARMED = "ARMED"
    FIRED = "FIRED"
    AUTO_SWEEP_PENDING = "AUTO_SWEEP_PENDING"
    SNOOZED = "SNOOZED"
    RESOLVED_SWEEP = "RESOLVED_SWEEP"
    RESOLVED_HODL = "RESOLVED_HODL"
    RESOLVED_EXPIRED = "RESOLVED_EXPIRED"


class AlertKind(str, enum.Enum):
    DRAWDOWN = "DRAWDOWN"
    SCHEDULED_SWEEP = "SCHEDULED_SWEEP"
    STABLECOIN_DEPEG = "STABLECOIN_DEPEG"
    AUTO_SWEEP_ABORTED_DEPEG = "AUTO_SWEEP_ABORTED_DEPEG"


class AlertResponse(str, enum.Enum):
    SWEEP = "SWEEP"
    HODL = "HODL"
    SNOOZE = "SNOOZE"
    TIMEOUT_AUTO_SWEEP = "TIMEOUT_AUTO_SWEEP"
    TIMEOUT_EXPIRED = "TIMEOUT_EXPIRED"


class SweepStatus(str, enum.Enum):
    PENDING = "PENDING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class VenueKind(str, enum.Enum):
    COINBASE = "COINBASE"
    KRAKEN = "KRAKEN"


class StablecoinKind(str, enum.Enum):
    USDC = "USDC"
    USDT = "USDT"
    DAI = "DAI"


class ApiScope(str, enum.Enum):
    TRADE_ONLY = "TRADE_ONLY"


class Base(DeclarativeBase):
    pass


class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    # Circular FK resolved via ALTER TABLE in migration (use_alter=True)
    default_alert_config_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey(
            "merchant_alert_configs.id",
            use_alter=True,
            name="fk_merchant_default_config",
        ),
        nullable=True,
    )


class MerchantAlertConfig(Base):
    __tablename__ = "merchant_alert_configs"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("merchants.id"), nullable=False
    )
    target_stablecoin: Mapped[StablecoinKind] = mapped_column(
        sa.Enum(StablecoinKind), nullable=False
    )
    drawdown_threshold_pct: Mapped[Decimal] = mapped_column(_NUMERIC, nullable=False)
    rearm_on_new_high: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default="true")
    auto_sweep_enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default="false")
    auto_sweep_timeout_minutes: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="30")
    sweep_schedule_cron: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    daily_window_timezone: Mapped[str] = mapped_column(sa.String(100), nullable=False, server_default="UTC")
    stablecoin_depeg_floor: Mapped[Decimal] = mapped_column(_NUMERIC, nullable=False)
    quiet_hours: Mapped[Any] = mapped_column(JSONB, nullable=True)


class MerchantVenueCredential(Base):
    __tablename__ = "merchant_venue_credentials"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("merchants.id"), nullable=False
    )
    venue: Mapped[VenueKind] = mapped_column(sa.Enum(VenueKind), nullable=False)
    # Reference to secrets manager entry — raw key is NEVER stored here (§8)
    api_key_ref: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    scope: Mapped[ApiScope] = mapped_column(
        sa.Enum(ApiScope), nullable=False, server_default="TRADE_ONLY"
    )
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("merchants.id"), nullable=False
    )
    # Snapshot composite PK stored as three columns; no FK constraint (composite refs
    # are awkward in SQLAlchemy; the application layer enforces consistency).
    snapshot_merchant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    snapshot_token_symbol: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    kind: Mapped[AlertKind] = mapped_column(sa.Enum(AlertKind), nullable=False)
    fired_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    response: Mapped[AlertResponse | None] = mapped_column(sa.Enum(AlertResponse), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    # Circular FK resolved via ALTER TABLE in migration
    resulting_sweep_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey(
            "sweep_executions.id",
            use_alter=True,
            name="fk_alert_sweep",
        ),
        nullable=True,
    )


class SweepExecution(Base):
    __tablename__ = "sweep_executions"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("merchants.id"), nullable=False
    )
    triggered_by_alert_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("alerts.id"), nullable=True
    )
    triggered_by_schedule: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default="false"
    )
    venue: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    token_symbol: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    qty_requested: Mapped[Decimal] = mapped_column(_NUMERIC, nullable=False)
    qty_executed: Mapped[Decimal | None] = mapped_column(_NUMERIC, nullable=True)
    target_stablecoin: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    proceeds: Mapped[Decimal | None] = mapped_column(_NUMERIC, nullable=True)
    fees_paid: Mapped[Decimal | None] = mapped_column(_NUMERIC, nullable=True)
    status: Mapped[SweepStatus] = mapped_column(
        sa.Enum(SweepStatus), nullable=False, server_default="PENDING"
    )
    venue_txn_ids: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="[]")
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class TokenDailySnapshot(Base):
    __tablename__ = "token_daily_snapshots"

    # Composite primary key: (merchant_id, token_symbol, date)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("merchants.id"), primary_key=True
    )
    token_symbol: Mapped[str] = mapped_column(sa.String(20), primary_key=True)
    date: Mapped[date] = mapped_column(sa.Date, primary_key=True)

    hourly_closes: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="[]")
    daily_high: Mapped[Decimal] = mapped_column(_NUMERIC, nullable=False)
    daily_high_hour: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    current_price: Mapped[Decimal] = mapped_column(_NUMERIC, nullable=False)
    current_price_ts: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    position_qty: Mapped[Decimal] = mapped_column(_NUMERIC, nullable=False)
    cost_basis_avg: Mapped[Decimal | None] = mapped_column(_NUMERIC, nullable=True)
    alert_state: Mapped[AlertState] = mapped_column(
        sa.Enum(AlertState), nullable=False, server_default="ARMED"
    )
    snooze_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default="false")
    snooze_trigger_price: Mapped[Decimal | None] = mapped_column(_NUMERIC, nullable=True)
    # Circular FK resolved via ALTER TABLE in migration
    last_alert_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey(
            "alerts.id",
            use_alter=True,
            name="fk_snapshot_last_alert",
        ),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class PriceObservation(Base):
    __tablename__ = "price_observations"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    token_symbol: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    price: Mapped[Decimal] = mapped_column(_NUMERIC, nullable=False)
    source: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # §4.1: INDEX (token_symbol, observed_at DESC) for efficient time-series queries
        sa.Index("ix_price_observations_symbol_ts", "token_symbol", "observed_at"),
    )

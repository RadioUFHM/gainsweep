"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-05-14

Domain model from §4.1. Tables are created in dependency order to satisfy FK
constraints; circular references (merchant↔alert_config, snapshot↔alert,
alert↔sweep) are resolved with deferred ALTER TABLE foreign keys at the end.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NUMERIC = sa.NUMERIC(36, 18)

# Named PostgreSQL enum types declared once so upgrade/downgrade stay in sync
_alert_state = sa.Enum(
    "ARMED", "FIRED", "AUTO_SWEEP_PENDING", "SNOOZED",
    "RESOLVED_SWEEP", "RESOLVED_HODL", "RESOLVED_EXPIRED",
    name="alertstate",
)
_alert_kind = sa.Enum(
    "DRAWDOWN", "SCHEDULED_SWEEP", "STABLECOIN_DEPEG", "AUTO_SWEEP_ABORTED_DEPEG",
    name="alertkind",
)
_alert_response = sa.Enum(
    "SWEEP", "HODL", "SNOOZE", "TIMEOUT_AUTO_SWEEP", "TIMEOUT_EXPIRED",
    name="alertresponse",
)
_sweep_status = sa.Enum("PENDING", "COMPLETE", "PARTIAL", "FAILED", name="sweepstatus")
_venue_kind = sa.Enum("COINBASE", "KRAKEN", name="venuekind")
_stablecoin_kind = sa.Enum("USDC", "USDT", "DAI", name="stablecoinkind")
_api_scope = sa.Enum("TRADE_ONLY", name="apiscope")


def upgrade() -> None:
    # ── 1. merchants (default_alert_config_id FK added later via ALTER) ───────
    op.create_table(
        "merchants",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("default_alert_config_id", PGUUID(as_uuid=True), nullable=True),
    )

    # ── 2. merchant_alert_configs ─────────────────────────────────────────────
    op.create_table(
        "merchant_alert_configs",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "merchant_id", PGUUID(as_uuid=True),
            sa.ForeignKey("merchants.id"), nullable=False
        ),
        sa.Column("target_stablecoin", _stablecoin_kind, nullable=False),
        sa.Column("drawdown_threshold_pct", _NUMERIC, nullable=False),
        sa.Column("rearm_on_new_high", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("auto_sweep_enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("auto_sweep_timeout_minutes", sa.Integer, nullable=False, server_default="30"),
        sa.Column("sweep_schedule_cron", sa.String(100), nullable=True),
        sa.Column(
            "daily_window_timezone", sa.String(100), nullable=False, server_default="UTC"
        ),
        sa.Column("stablecoin_depeg_floor", _NUMERIC, nullable=False),
        sa.Column("quiet_hours", JSONB, nullable=True),
    )

    # ── 3. merchant_venue_credentials ─────────────────────────────────────────
    op.create_table(
        "merchant_venue_credentials",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "merchant_id", PGUUID(as_uuid=True),
            sa.ForeignKey("merchants.id"), nullable=False
        ),
        sa.Column("venue", _venue_kind, nullable=False),
        sa.Column("api_key_ref", sa.String(500), nullable=False),
        sa.Column("scope", _api_scope, nullable=False, server_default="TRADE_ONLY"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── 4. alerts (resulting_sweep_id FK added later via ALTER) ───────────────
    op.create_table(
        "alerts",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "merchant_id", PGUUID(as_uuid=True),
            sa.ForeignKey("merchants.id"), nullable=False
        ),
        sa.Column("snapshot_merchant_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_token_symbol", sa.String(20), nullable=False),
        sa.Column("snapshot_date", sa.Date, nullable=False),
        sa.Column("kind", _alert_kind, nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("response", _alert_response, nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resulting_sweep_id", PGUUID(as_uuid=True), nullable=True),
    )

    # ── 5. sweep_executions ───────────────────────────────────────────────────
    op.create_table(
        "sweep_executions",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "merchant_id", PGUUID(as_uuid=True),
            sa.ForeignKey("merchants.id"), nullable=False
        ),
        sa.Column(
            "triggered_by_alert_id", PGUUID(as_uuid=True),
            sa.ForeignKey("alerts.id"), nullable=True
        ),
        sa.Column(
            "triggered_by_schedule", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column("venue", sa.String(50), nullable=False),
        sa.Column("token_symbol", sa.String(20), nullable=False),
        sa.Column("qty_requested", _NUMERIC, nullable=False),
        sa.Column("qty_executed", _NUMERIC, nullable=True),
        sa.Column("target_stablecoin", sa.String(10), nullable=False),
        sa.Column("proceeds", _NUMERIC, nullable=True),
        sa.Column("fees_paid", _NUMERIC, nullable=True),
        sa.Column("status", _sweep_status, nullable=False, server_default="PENDING"),
        sa.Column("venue_txn_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── 6. token_daily_snapshots (last_alert_id FK added later via ALTER) ─────
    op.create_table(
        "token_daily_snapshots",
        sa.Column(
            "merchant_id", PGUUID(as_uuid=True),
            sa.ForeignKey("merchants.id"), primary_key=True
        ),
        sa.Column("token_symbol", sa.String(20), primary_key=True),
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("hourly_closes", JSONB, nullable=False, server_default="[]"),
        sa.Column("daily_high", _NUMERIC, nullable=False),
        sa.Column("daily_high_hour", sa.Integer, nullable=True),
        sa.Column("current_price", _NUMERIC, nullable=False),
        sa.Column("current_price_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("position_qty", _NUMERIC, nullable=False),
        sa.Column("cost_basis_avg", _NUMERIC, nullable=True),
        sa.Column("alert_state", _alert_state, nullable=False, server_default="ARMED"),
        sa.Column("snooze_active", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("snooze_trigger_price", _NUMERIC, nullable=True),
        sa.Column("last_alert_id", PGUUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── 7. price_observations ─────────────────────────────────────────────────
    op.create_table(
        "price_observations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("token_symbol", sa.String(20), nullable=False),
        sa.Column("price", _NUMERIC, nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    # §4.1: INDEX (token_symbol, observed_at DESC) for time-series range queries
    op.execute(
        "CREATE INDEX ix_price_observations_symbol_ts "
        "ON price_observations (token_symbol, observed_at DESC)"
    )

    # ── 8. Deferred FKs — circular references resolved with ALTER TABLE ────────
    op.create_foreign_key(
        "fk_merchant_default_config",
        "merchants", "merchant_alert_configs",
        ["default_alert_config_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_snapshot_last_alert",
        "token_daily_snapshots", "alerts",
        ["last_alert_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_alert_sweep",
        "alerts", "sweep_executions",
        ["resulting_sweep_id"], ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_alert_sweep", "alerts", type_="foreignkey")
    op.drop_constraint("fk_snapshot_last_alert", "token_daily_snapshots", type_="foreignkey")
    op.drop_constraint("fk_merchant_default_config", "merchants", type_="foreignkey")

    op.drop_table("price_observations")
    op.drop_table("token_daily_snapshots")
    op.drop_table("sweep_executions")
    op.drop_table("alerts")
    op.drop_table("merchant_venue_credentials")
    op.drop_table("merchant_alert_configs")
    op.drop_table("merchants")

    bind = op.get_bind()
    _alert_state.drop(bind, checkfirst=True)
    _alert_kind.drop(bind, checkfirst=True)
    _alert_response.drop(bind, checkfirst=True)
    _sweep_status.drop(bind, checkfirst=True)
    _venue_kind.drop(bind, checkfirst=True)
    _stablecoin_kind.drop(bind, checkfirst=True)
    _api_scope.drop(bind, checkfirst=True)

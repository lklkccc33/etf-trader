"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-14

"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "accounts",
        sa.Column("account_id", sa.String(36), primary_key=True),
        sa.Column("exchange", sa.String(20), nullable=False, server_default="KIS"),
        sa.Column("env", sa.Enum("REAL", "VIRTUAL", name="account_env"), nullable=False),
        sa.Column("cano", sa.String(8), nullable=False),
        sa.Column("acnt_prdt_cd", sa.String(2), nullable=False),
        sa.Column("encrypted_appkey", sa.String(255), nullable=False),
        sa.Column("encrypted_appsecret", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("cano", "acnt_prdt_cd", "env", name="uq_accounts_account"),
    )

    op.create_table(
        "token_cache",
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.account_id"), primary_key=True),
        sa.Column("access_token", sa.String(512), nullable=False),
        sa.Column("issued_at", sa.DateTime, nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "trade_sessions",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.account_id"), nullable=False),
        sa.Column("seed_money", sa.DECIMAL(20, 4), nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_trade_sessions_account_id", "trade_sessions", ["account_id"])

    op.create_table(
        "trade_records",
        sa.Column("record_id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("trade_sessions.session_id"), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.account_id"), nullable=False),
        sa.Column("strategy_name", sa.String(100), nullable=False),
        sa.Column("traded_at", sa.DateTime, nullable=False),
        sa.Column("exchange_code", sa.Enum("NASD", "NYSE", "AMEX", name="exchange_code"), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("side", sa.Enum("BUY", "SELL", name="trade_side"), nullable=False),
        sa.Column("volume", sa.DECIMAL(20, 4), nullable=False),
        sa.Column("price", sa.DECIMAL(20, 4), nullable=False),
        sa.Column("value", sa.DECIMAL(20, 4), nullable=False),
        sa.Column("balance", sa.DECIMAL(20, 4), nullable=False),
        sa.Column("kis_order_no", sa.String(50), nullable=True),
    )
    op.create_index("ix_trade_records_session_id", "trade_records", ["session_id"])
    op.create_index("ix_trade_records_account_id", "trade_records", ["account_id"])
    op.create_index("ix_trade_records_traded_at", "trade_records", ["traded_at"])

    op.create_table(
        "order_errors",
        sa.Column("error_id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("trade_sessions.session_id"), nullable=False),
        sa.Column("requested_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("request_payload", sa.JSON, nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
    )
    op.create_index("ix_order_errors_session_id", "order_errors", ["session_id"])


def downgrade():
    op.drop_table("order_errors")
    op.drop_index("ix_trade_records_traded_at", table_name="trade_records")
    op.drop_index("ix_trade_records_account_id", table_name="trade_records")
    op.drop_index("ix_trade_records_session_id", table_name="trade_records")
    op.drop_table("trade_records")
    op.drop_index("ix_trade_sessions_account_id", table_name="trade_sessions")
    op.drop_table("trade_sessions")
    op.drop_table("token_cache")
    op.drop_table("accounts")

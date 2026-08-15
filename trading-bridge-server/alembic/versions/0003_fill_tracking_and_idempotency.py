"""fill tracking and idempotency key

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-14

"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("trade_records", sa.Column("client_order_id", sa.String(64), nullable=True))
    op.add_column(
        "trade_records",
        sa.Column(
            "fill_status",
            sa.Enum("PENDING", "FILLED", "PARTIAL", "CANCELLED", name="fill_status"),
            nullable=False,
            server_default="PENDING",
        ),
    )
    op.add_column("trade_records", sa.Column("filled_volume", sa.DECIMAL(20, 4), nullable=True))
    op.add_column("trade_records", sa.Column("filled_price", sa.DECIMAL(20, 4), nullable=True))
    op.add_column("trade_records", sa.Column("fill_checked_at", sa.DateTime, nullable=True))
    op.create_unique_constraint(
        "uq_trade_records_client_order_id", "trade_records", ["account_id", "client_order_id"]
    )

    op.add_column("order_errors", sa.Column("client_order_id", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("order_errors", "client_order_id")

    op.drop_constraint("uq_trade_records_client_order_id", "trade_records", type_="unique")
    op.drop_column("trade_records", "fill_checked_at")
    op.drop_column("trade_records", "filled_price")
    op.drop_column("trade_records", "filled_volume")
    op.drop_column("trade_records", "fill_status")
    op.drop_column("trade_records", "client_order_id")

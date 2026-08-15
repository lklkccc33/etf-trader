"""trade_records.balance nullable

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-14

"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "trade_records",
        "balance",
        existing_type=sa.DECIMAL(20, 4),
        nullable=True,
    )


def downgrade():
    op.alter_column(
        "trade_records",
        "balance",
        existing_type=sa.DECIMAL(20, 4),
        nullable=False,
    )

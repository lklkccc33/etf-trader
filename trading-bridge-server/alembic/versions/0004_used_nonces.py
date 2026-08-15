"""used_nonces table for multi-worker-safe replay protection

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-14

"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "used_nonces",
        sa.Column("nonce", sa.String(128), primary_key=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("used_nonces")

"""Add _pending_ref_code to users for deferred referral processing

Stores the referrer's code at signup; credited only after email verification.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-11 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision      = "0003"
down_revision = "0002"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.add_column("users", sa.Column(
        "_pending_ref_code", sa.String(16), nullable=True
    ))


def downgrade() -> None:
    op.drop_column("users", "_pending_ref_code")

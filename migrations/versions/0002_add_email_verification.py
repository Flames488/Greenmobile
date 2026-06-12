"""Add email verification token columns to users

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-11 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision      = "0002"
down_revision = "0001"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.add_column("users", sa.Column(
        "email_verify_token", sa.String(64), nullable=True
    ))
    op.add_column("users", sa.Column(
        "token_expires_at", sa.DateTime(), nullable=True
    ))
    op.create_index(
        "ix_users_email_verify_token",
        "users",
        ["email_verify_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_users_email_verify_token", table_name="users")
    op.drop_column("users", "token_expires_at")
    op.drop_column("users", "email_verify_token")

"""Initial schema — users, reward_tiers, claimed_rewards

Revision ID: 0001
Revises:
Create Date: 2025-06-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id",               sa.Integer(),     primary_key=True),
        sa.Column("email",            sa.String(255),   nullable=False),
        sa.Column("password_hash",    sa.String(255),   nullable=True),
        sa.Column("verified",         sa.Boolean(),     nullable=False, server_default=sa.false()),
        sa.Column("referral_code",    sa.String(16),    nullable=False),
        sa.Column("referred_by",      sa.String(16),    sa.ForeignKey("users.referral_code"), nullable=True),
        sa.Column("referrals_count",  sa.Integer(),     nullable=False, server_default="0"),
        sa.Column("waitlist_position",sa.Integer(),     nullable=True),
        sa.Column("joined_at",        sa.DateTime(),    nullable=False, server_default=sa.text("NOW()")),
        sa.Column("verified_at",      sa.DateTime(),    nullable=True),
    )
    op.create_index("ix_users_email",         "users", ["email"],         unique=True)
    op.create_index("ix_users_referral_code", "users", ["referral_code"], unique=True)

    # ── reward_tiers ───────────────────────────────────────────────────────────
    op.create_table(
        "reward_tiers",
        sa.Column("id",                  sa.Integer(),    primary_key=True),
        sa.Column("referrals_required",  sa.Integer(),    nullable=False, unique=True),
        sa.Column("reward_name",         sa.String(255),  nullable=False),
        sa.Column("reward_description",  sa.Text(),       nullable=True),
        sa.Column("badge_emoji",         sa.String(8),    nullable=True, server_default="'🎁'"),
    )

    # ── claimed_rewards ────────────────────────────────────────────────────────
    op.create_table(
        "claimed_rewards",
        sa.Column("id",         sa.Integer(),  primary_key=True),
        sa.Column("user_id",    sa.Integer(),  sa.ForeignKey("users.id"),         nullable=False),
        sa.Column("tier_id",    sa.Integer(),  sa.ForeignKey("reward_tiers.id"),  nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )


def downgrade() -> None:
    op.drop_table("claimed_rewards")
    op.drop_table("reward_tiers")
    op.drop_index("ix_users_referral_code", table_name="users")
    op.drop_index("ix_users_email",         table_name="users")
    op.drop_table("users")

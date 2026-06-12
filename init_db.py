#!/usr/bin/env python
"""
One-time local setup helper.

Run after creating your Postgres database:
    python init_db.py

What it does:
  1. Runs all Alembic migrations  (creates tables)
  2. Seeds default reward tiers
  3. Optionally creates an admin user (set ADMIN_EMAIL / ADMIN_PASSWORD env vars)
"""
import os
import secrets

# Apply .env before importing app
from dotenv import load_dotenv
load_dotenv()

from app import app, db, bcrypt
from app import User, RewardTier


def run_migrations():
    from flask_migrate import upgrade
    with app.app_context():
        upgrade()
    print("✅ Migrations applied.")


def seed_reward_tiers():
    with app.app_context():
        if RewardTier.query.count() > 0:
            print("ℹ️  Reward tiers already exist — skipping seed.")
            return

        tiers = [
            RewardTier(
                referrals_required=1,
                reward_name="Early Adopter Badge",
                reward_description="Exclusive badge shown on your profile at launch.",
                badge_emoji="⭐",
            ),
            RewardTier(
                referrals_required=3,
                reward_name="2 Days Unlimited Access",
                reward_description="Full unrestricted access to every Ziva Pro feature for 48 hours on launch day.",
                badge_emoji="🎁",
            ),
            RewardTier(
                referrals_required=10,
                reward_name="1 Month Pro Free",
                reward_description="One full month of Ziva Pro at no cost — applied automatically on launch.",
                badge_emoji="🏆",
            ),
            RewardTier(
                referrals_required=25,
                reward_name="Founding Member Lifetime Deal",
                reward_description="Permanent discounted access, your name in the credits, and priority support forever.",
                badge_emoji="👑",
            ),
        ]
        db.session.bulk_save_objects(tiers)
        db.session.commit()
        print(f"✅ Seeded {len(tiers)} reward tiers.")


def create_admin():
    email    = os.getenv("ADMIN_EMAIL")
    password = os.getenv("ADMIN_PASSWORD")

    if not email or not password:
        print("ℹ️  ADMIN_EMAIL / ADMIN_PASSWORD not set — skipping admin creation.")
        print("   To create an admin later, set those vars and re-run this script.")
        return

    with app.app_context():
        if User.query.filter_by(email=email).first():
            print(f"ℹ️  Admin {email} already exists — skipping.")
            return

        admin = User(
            email=email,
            referral_code=secrets.token_hex(4),
            verified=True,
        )
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        print(f"✅ Admin account created: {email}")


if __name__ == "__main__":
    run_migrations()
    seed_reward_tiers()
    create_admin()
    print("\n🚀 Ziva Pro is ready. Run:  flask run")

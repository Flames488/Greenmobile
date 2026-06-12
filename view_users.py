#!/usr/bin/env python
"""
Quick CLI to inspect users from the PostgreSQL database.
Usage:  python view_users.py
        DATABASE_URL=postgresql://... python view_users.py
"""
from dotenv import load_dotenv
load_dotenv()

from app import app, User

with app.app_context():
    users = User.query.order_by(User.waitlist_position).all()
    if not users:
        print("No users yet.")
    else:
        print(f"{'#':<5} {'Email':<40} {'Referrals':<10} {'Position':<10} {'Joined'}")
        print("-" * 80)
        for u in users:
            print(
                f"{u.id:<5} {u.email:<40} {u.referrals_count:<10} "
                f"{'#' + str(u.waitlist_position) if u.waitlist_position else '—':<10} "
                f"{u.joined_at.strftime('%Y-%m-%d')}"
            )

#!/usr/bin/env python3
"""Render startup helper.

Ensures the database exists and is seeded ONCE, without wiping data on every
restart/redeploy. `seed()` is destructive (drop_all), so we only call it when
the users table is empty (i.e. a brand-new database). Then Render's start command
launches gunicorn.

Usage (Render start command):
    python render_start.py && cd app && gunicorn --workers 1 --bind 0.0.0.0:$PORT app:app
"""
import os
import sys

APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
sys.path.insert(0, APP_DIR)

from app import app  # noqa: E402


def main():
    with app.app_context():
        from models import db, User
        db.create_all()
        try:
            has_users = User.query.count() > 0
        except Exception:
            has_users = False
        if has_users:
            print("[render_start] database already seeded — skipping seed.")
            return
        from seed import seed
        seed()
        print("[render_start] fresh database seeded.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run the Meridian Scheduler from the project root.

    python run.py            # start the dev server on http://127.0.0.1:5000
    python run.py --seed     # (re)create and seed the database, then start

The Flask app lives in app/. This puts app/ on the path and launches it, so you
don't need to cd into the package or set --app.
"""
import os
import sys

APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
sys.path.insert(0, APP_DIR)

from app import app  # noqa: E402


def _seed():
    from seed import seed
    with app.app_context():
        seed()
    print("database seeded -> data/meridian.db")


if __name__ == "__main__":
    if "--seed" in sys.argv:
        _seed()
    app.run(debug=True)

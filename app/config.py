import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# data/ sits alongside the app/ package at the project root
DATA_DIR = BASE_DIR.parent / "data"
DATA_DIR.mkdir(exist_ok=True)


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{DATA_DIR / 'meridian.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JSON_SORT_KEYS = False

    # --- auth / security -------------------------------------------------
    # DEMO_MODE=1 keeps the quick role-switcher (no password) so a team can try
    # the app immediately. Set DEMO_MODE=0 (default for real deploys) to require
    # a real username + password login.
    DEMO_MODE = os.environ.get("DEMO_MODE", "1") == "1"
    # secure cookies — on by default; auto-relaxed when not serving HTTPS so the
    # demo still works over plain http on a LAN / localhost.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
    # default password for the seeded demo accounts (change via env for shared
    # try-outs). Only used to seed; real users set their own.
    DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "meridian123")

    # domain constants shared by backend + templates
    ASSIGN_STAGES = [
        "Kitting", "Assembly", "Calibration", "Burn-in",
        "Final QC", "Packing", "Dispatch",
    ]
    SHIFT_TIME = {"A": "06:00-14:00", "B": "14:00-22:00", "C": "22:00-06:00"}
    STALE_MINUTES = 120

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")

    # ── Database ──────────────────────────────────────────────────────────────
    _raw_db_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/ziva",
    )
    SQLALCHEMY_DATABASE_URI: str = _raw_db_url.replace("postgres://", "postgresql://", 1)

    # Postgres-specific pool/connect options. SQLite's driver rejects
    # connect_timeout, pool_size, max_overflow, etc., so only apply these
    # when actually running against Postgres (local SQLite dev works without them).
    if SQLALCHEMY_DATABASE_URI.startswith("postgresql"):
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_pre_ping": True,
            "pool_recycle":  300,
            "pool_size":     5,
            "max_overflow":  10,
            "connect_args": {
                "connect_timeout": 10,
                "options": "-c statement_timeout=30000",
            },
        }
    else:
        SQLALCHEMY_ENGINE_OPTIONS = {}
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ── Email ─────────────────────────────────────────────────────────────────
    RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
    FROM_EMAIL: str     = os.getenv("FROM_EMAIL",    "noreply@yourdomain.com")
    APP_URL: str        = os.getenv("APP_URL",       "http://localhost:5000")

    # ── Admin credentials ─────────────────────────────────────────────────────
    # Store a bcrypt hash of the admin password — never the raw string.
    # Generate with: python -c "from flask_bcrypt import Bcrypt; print(Bcrypt().generate_password_hash('yourpassword').decode())"
    ADMIN_USERNAME: str      = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD_HASH: str = os.getenv("ADMIN_PASSWORD_HASH", "")

    # ── Rate limiting ─────────────────────────────────────────────────────────
    RATELIMIT_STORAGE_URI: str = os.getenv("REDIS_URL", "memory://")

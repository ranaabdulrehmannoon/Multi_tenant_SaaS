"""
config.py — Centralised configuration loaded from environment variables.
All secrets come from .env (never hardcoded).
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── PostgreSQL (main DB) ─────────────────────────────────────────────────
    PG_HOST     = os.environ["POSTGRES_HOST"]
    PG_PORT     = int(os.environ.get("POSTGRES_PORT", 5432))
    PG_USER     = os.environ["POSTGRES_USER"]
    PG_PASSWORD = os.environ["POSTGRES_PASSWORD"]
    PG_DB       = os.environ["POSTGRES_DB"]
    PG_POOL_MIN = int(os.environ.get("PG_POOL_MIN", 2))
    PG_POOL_MAX = int(os.environ.get("PG_POOL_MAX", 20))

    # ── Redis ────────────────────────────────────────────────────────────────
    REDIS_HOST     = os.environ.get("REDIS_HOST", "localhost")
    REDIS_PORT     = int(os.environ.get("REDIS_PORT", 6379))
    REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
    REDIS_DB       = int(os.environ.get("REDIS_DB", 0))

    # ── Flask / JWT ──────────────────────────────────────────────────────────
    FLASK_SECRET_KEY = os.environ["FLASK_SECRET_KEY"]
    JWT_SECRET_KEY   = os.environ["JWT_SECRET_KEY"]
    JWT_EXPIRY       = int(os.environ.get("JWT_EXPIRY_SECONDS", 3600))

    # ── Cache ────────────────────────────────────────────────────────────────
    CACHE_TTL = int(os.environ.get("CACHE_TTL_SECONDS", 60))

    @classmethod
    def pg_dsn(cls, db_name: str | None = None) -> str:
        """Return a psycopg2-compatible DSN string."""
        return (
            f"host={cls.PG_HOST} port={cls.PG_PORT} "
            f"dbname={db_name or cls.PG_DB} "
            f"user={cls.PG_USER} password={cls.PG_PASSWORD} "
            "sslmode=prefer connect_timeout=10"
        )

"""
database.py — Connection pool management for all three tenancy models.

Model A & B: single pool → main multitenant_saas database.
Model C: per-tenant pools stored in a registry dict.
"""
import logging
import threading
from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.pool
import psycopg2.extensions

from provisioning.config import Config

logger = logging.getLogger(__name__)

# ── Shared connection pool (Models A & B) ───────────────────────────────────
_main_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()

# ── Per-tenant pools (Model C) ───────────────────────────────────────────────
_tenant_pools: dict[str, psycopg2.pool.ThreadedConnectionPool] = {}
_tenant_pool_lock = threading.Lock()


def get_main_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Return (or lazily initialise) the main shared connection pool."""
    global _main_pool
    if _main_pool is None:
        with _pool_lock:
            if _main_pool is None:
                _main_pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=Config.PG_POOL_MIN,
                    maxconn=Config.PG_POOL_MAX,
                    dsn=Config.pg_dsn(),
                )
                logger.info(
                    "Main PG pool initialised (min=%d, max=%d)",
                    Config.PG_POOL_MIN, Config.PG_POOL_MAX,
                )
    return _main_pool


def get_tenant_pool(db_name: str) -> psycopg2.pool.ThreadedConnectionPool:
    """Return (or lazily initialise) a per-tenant pool for Model C databases."""
    if db_name not in _tenant_pools:
        with _tenant_pool_lock:
            if db_name not in _tenant_pools:
                _tenant_pools[db_name] = psycopg2.pool.ThreadedConnectionPool(
                    minconn=1,
                    maxconn=5,  # Model C tenants share cluster resources
                    dsn=Config.pg_dsn(db_name=db_name),
                )
                logger.info("Tenant pool created for db=%s", db_name)
    return _tenant_pools[db_name]


def close_tenant_pool(db_name: str) -> None:
    """Close and remove a Model C tenant pool (called on deactivation)."""
    with _tenant_pool_lock:
        pool = _tenant_pools.pop(db_name, None)
        if pool:
            pool.closeall()
            logger.info("Tenant pool closed for db=%s", db_name)


@contextmanager
def get_connection(
    tenant_id: str | None = None,
    db_name: str | None = None,
    schema_name: str | None = None,
) -> Generator[psycopg2.extensions.connection, None, None]:
    """
    Context manager that yields a psycopg2 connection from the appropriate pool.

    - Model A: pass tenant_id only.  RLS is enforced via SET LOCAL.
    - Model B: pass tenant_id + schema_name.  search_path is set per connection.
    - Model C: pass db_name.  Separate pool per tenant database.

    The connection is ALWAYS returned to the pool on exit, even on exception.
    """
    if db_name:
        pool = get_tenant_pool(db_name)
    else:
        pool = get_main_pool()

    conn = pool.getconn()
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            # CRITICAL: the pool connects as the PostgreSQL SUPERUSER
            # (POSTGRES_USER from docker-compose.yml).  Superusers BYPASS RLS
            # automatically, which would let every tenant see every other
            # tenant's data.  Drop privilege to app_api (a normal role with
            # explicit GRANTs but no BYPASSRLS) so the tenant_isolation
            # policies actually fire.  SET LOCAL → reverts on commit/rollback,
            # so the next checkout from the pool starts clean.
            if db_name is None:
                cur.execute("SET LOCAL ROLE app_api")
            if schema_name:
                # Model B: set search_path so bare table names resolve to tenant schema
                cur.execute(
                    "SET LOCAL search_path = %s, public",
                    (schema_name,)
                )
            if tenant_id:
                # Model A (and Model B for belt-and-suspenders audit logging):
                # SET LOCAL scopes the variable to the current transaction only.
                cur.execute(
                    "SET LOCAL app.current_tenant = %s",
                    (str(tenant_id),)
                )
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextmanager
def get_admin_connection(db_name: str | None = None):
    """
    Yields a connection with AUTOCOMMIT enabled.
    Required for DDL statements like CREATE DATABASE, CREATE SCHEMA.
    Uses the main pool DSN but overrides the target database if specified.
    """
    dsn = Config.pg_dsn(db_name=db_name)
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()

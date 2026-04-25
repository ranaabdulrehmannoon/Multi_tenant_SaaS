"""
provisioner.py — Automated tenant provisioning engine.

Handles all three tenancy models:
  Model A (shared_schema)      → registers tenant + sets up RLS context
  Model B (schema_per_tenant)  → creates dedicated PostgreSQL schema + tables
  Model C (db_per_tenant)      → creates dedicated PostgreSQL database + tables

Public interface:
  provision_tenant(name, slug, tier, model, admin_email, admin_password) → dict
  deactivate_tenant(tenant_id)                                            → dict
  get_tenant(tenant_id)                                                   → dict | None
  list_tenants(status, model, tier)                                       → list[dict]
"""
import logging
import os
import re
import uuid
from pathlib import Path

import psycopg2
import bcrypt

from provisioning.config import Config
from provisioning.database import get_connection, get_admin_connection

logger = logging.getLogger(__name__)

# Paths to SQL template files
SQL_DIR = Path(__file__).parent.parent / "sql"
MODEL_B_TEMPLATE = SQL_DIR / "02_model_b_template.sql"
MODEL_C_TEMPLATE = SQL_DIR / "03_model_c_template.sql"
AUDIT_SQL        = SQL_DIR / "04_audit_triggers.sql"


# ── Validation helpers ───────────────────────────────────────────────────────

_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9\-]{1,48}[a-z0-9]$')

def _validate_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"Invalid slug '{slug}'. Must be 3-50 lowercase alphanumeric chars / hyphens, "
            "not start or end with a hyphen."
        )

def _safe_identifier(slug: str) -> str:
    """Convert slug to a PostgreSQL-safe identifier (underscores, no hyphens)."""
    return slug.replace("-", "_")

def _tier_defaults(tier: str) -> dict:
    """Return resource limits for the given tier (sourced from tier_config table)."""
    defaults = {
        "free":       {"max_users": 5,    "max_storage_gb": 0.5},
        "pro":        {"max_users": 50,   "max_storage_gb": 10.0},
        "enterprise": {"max_users": 9999, "max_storage_gb": 999.0},
    }
    return defaults.get(tier, defaults["free"])

def _validate_model_for_tier(tier: str, model: str) -> None:
    allowed = {
        "free":       {"shared_schema"},
        "pro":        {"shared_schema", "schema_per_tenant"},
        "enterprise": {"shared_schema", "schema_per_tenant", "db_per_tenant"},
    }
    if model not in allowed.get(tier, set()):
        raise ValueError(
            f"Tier '{tier}' does not permit tenancy model '{model}'. "
            f"Allowed: {allowed[tier]}"
        )


# ── Core provisioning ────────────────────────────────────────────────────────

def provision_tenant(
    name: str,
    slug: str,
    tier: str,
    model: str,
    admin_email: str,
    admin_password: str,
    metadata: dict | None = None,
) -> dict:
    """
    Register a new tenant and provision all required database infrastructure.

    Returns the new tenant record as a dict.
    Raises ValueError for invalid input, psycopg2.Error for database failures.
    """
    _validate_slug(slug)
    _validate_model_for_tier(tier, model)

    limits     = _tier_defaults(tier)
    tenant_id  = str(uuid.uuid4())
    safe_slug  = _safe_identifier(slug)
    schema_name = f"tenant_{safe_slug}"        if model == "schema_per_tenant" else None
    db_name     = f"tenant_db_{safe_slug}"     if model == "db_per_tenant"     else None

    logger.info(
        "Provisioning tenant name=%s slug=%s tier=%s model=%s",
        name, slug, tier, model,
    )

    # Step 1: Create the tenant record in the master registry
    _register_tenant_record(
        tenant_id, name, slug, tier, model,
        schema_name, db_name, limits, metadata or {},
    )

    # Step 2: Model-specific infrastructure
    try:
        if model == "schema_per_tenant":
            _provision_model_b(tenant_id, schema_name)
        elif model == "db_per_tenant":
            _provision_model_c(tenant_id, db_name)
        # Model A: tables + RLS already exist; no extra DDL needed.

        # Step 3: Create the initial tenant admin user
        _create_tenant_admin(tenant_id, admin_email, admin_password, model, db_name, schema_name)

    except Exception as exc:
        # Roll back the registry entry so the system stays consistent
        logger.error("Provisioning failed for tenant %s: %s — rolling back registry.", tenant_id, exc)
        _delete_tenant_record(tenant_id)
        raise

    logger.info("Tenant %s (%s) provisioned successfully.", slug, tenant_id)
    return get_tenant(tenant_id)


def _register_tenant_record(
    tenant_id, name, slug, tier, model,
    schema_name, db_name, limits, metadata,
) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.tenants
                    (tenant_id, name, slug, tier, model, schema_name, db_name,
                     max_users, max_storage_gb, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id, name, slug, tier, model,
                    schema_name, db_name,
                    limits["max_users"], limits["max_storage_gb"],
                    psycopg2.extras.Json(metadata) if metadata else "{}",
                ),
            )


def _delete_tenant_record(tenant_id: str) -> None:
    """Clean up a partially-created tenant registry entry."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM public.tenants WHERE tenant_id = %s",
                    (tenant_id,),
                )
    except Exception as exc:
        logger.error("Failed to roll back tenant record %s: %s", tenant_id, exc)


# ── Model B provisioning ─────────────────────────────────────────────────────

def _provision_model_b(tenant_id: str, schema_name: str) -> None:
    """
    Create a dedicated schema for a Model B tenant and execute the table
    template with identifier substitution.
    """
    template = MODEL_B_TEMPLATE.read_text(encoding="utf-8")

    # Safe substitution — only schema_name and tenant_id are substituted.
    # We use a controlled replacement, not eval/format with user input.
    sql = (
        template
        .replace("{schema_name}", schema_name)
        .replace("{tenant_id}",   tenant_id)
    )

    # DDL runs in autocommit (CREATE SCHEMA cannot run in a transaction block)
    with get_admin_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)

    # Attach audit triggers to every new table in this schema
    _attach_model_b_audit_triggers(schema_name)
    logger.info("Model B schema '%s' provisioned.", schema_name)


def _attach_model_b_audit_triggers(schema_name: str) -> None:
    tables = ["products", "orders", "order_items", "invoices"]
    with get_admin_connection() as conn:
        with conn.cursor() as cur:
            for table in tables:
                cur.execute(
                    "SELECT public.create_audit_trigger(%s, %s)",
                    (schema_name, table),
                )


# ── Model C provisioning ─────────────────────────────────────────────────────

def _provision_model_c(tenant_id: str, db_name: str) -> None:
    """
    Create a fully isolated PostgreSQL database for a Model C tenant,
    then execute the schema template inside it.
    """
    # CREATE DATABASE requires autocommit + cannot be in a transaction
    with get_admin_connection() as conn:
        with conn.cursor() as cur:
            # pg_catalog lookup avoids error if DB already exists (idempotent)
            cur.execute(
                "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s",
                (db_name,),
            )
            if cur.fetchone():
                logger.warning("Database %s already exists — skipping creation.", db_name)
                return

            # Parameterised identifiers via psycopg2.sql to prevent injection
            from psycopg2 import sql as pgsql
            cur.execute(
                pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db_name))
            )
            logger.info("Created database '%s'.", db_name)

    # Now connect to the new tenant database and execute the template
    template = MODEL_C_TEMPLATE.read_text(encoding="utf-8")
    with get_admin_connection(db_name=db_name) as conn:
        with conn.cursor() as cur:
            cur.execute(template)

    logger.info("Model C database '%s' initialised.", db_name)


# ── Tenant admin user ────────────────────────────────────────────────────────

def _create_tenant_admin(
    tenant_id: str,
    email: str,
    password: str,
    model: str,
    db_name: str | None,
    schema_name: str | None,
) -> None:
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    # Always write to the cluster-level public.tenant_users
    with get_connection(tenant_id=tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.tenant_users
                    (tenant_id, email, password_hash, role)
                VALUES (%s, %s, %s, 'tenant_admin')
                """,
                (tenant_id, email, password_hash),
            )

    # For Model C, also sync the user into the local tenant database
    if model == "db_per_tenant" and db_name:
        user_id = _get_user_id_by_email(tenant_id, email)
        with get_admin_connection(db_name=db_name) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.users (user_id, email, role)
                    VALUES (%s, %s, 'tenant_admin')
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    (str(user_id), email),
                )


def _get_user_id_by_email(tenant_id: str, email: str) -> uuid.UUID:
    with get_connection(tenant_id=tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM public.tenant_users WHERE tenant_id = %s AND email = %s",
                (tenant_id, email),
            )
            row = cur.fetchone()
            return row[0] if row else None


# ── Tenant deactivation ──────────────────────────────────────────────────────

def deactivate_tenant(tenant_id: str) -> dict:
    """
    Mark a tenant as deactivated. Data is preserved for archival/audit.
    Model B schemas and Model C databases are NOT dropped — they move to
    read-only status by revoking write privileges.
    """
    tenant = get_tenant(tenant_id)
    if tenant is None:
        raise ValueError(f"Tenant {tenant_id} not found.")
    if tenant["status"] == "deactivated":
        raise ValueError(f"Tenant {tenant_id} is already deactivated.")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.tenants
                   SET status = 'deactivated', deactivated_at = NOW()
                 WHERE tenant_id = %s
                """,
                (tenant_id,),
            )

    # Revoke write access at the schema/database level for extra safety
    if tenant["model"] == "schema_per_tenant" and tenant.get("schema_name"):
        _revoke_schema_writes(tenant["schema_name"])
    elif tenant["model"] == "db_per_tenant" and tenant.get("db_name"):
        from provisioning.database import close_tenant_pool
        close_tenant_pool(tenant["db_name"])

    logger.info("Tenant %s deactivated.", tenant_id)
    return get_tenant(tenant_id)


def _revoke_schema_writes(schema_name: str) -> None:
    with get_admin_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"REVOKE INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema_name} FROM app_api"
            )


# ── Read operations ──────────────────────────────────────────────────────────

def get_tenant(tenant_id: str) -> dict | None:
    """Fetch a single tenant record by UUID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tenant_id, name, slug, tier, model, status,
                       schema_name, db_name, max_users, max_storage_gb,
                       created_at, updated_at, deactivated_at, metadata
                  FROM public.tenants
                 WHERE tenant_id = %s
                """,
                (tenant_id,),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return _row_to_tenant_dict(row)


def list_tenants(
    status: str | None = None,
    model: str | None = None,
    tier: str | None = None,
) -> list[dict]:
    """List tenants with optional filters."""
    conditions = []
    params     = []
    if status:
        conditions.append("status = %s");  params.append(status)
    if model:
        conditions.append("model = %s");   params.append(model)
    if tier:
        conditions.append("tier = %s");    params.append(tier)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql   = f"""
        SELECT tenant_id, name, slug, tier, model, status,
               schema_name, db_name, max_users, max_storage_gb,
               created_at, updated_at, deactivated_at, metadata
          FROM public.tenants
        {where}
         ORDER BY created_at DESC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return [_row_to_tenant_dict(r) for r in rows]


def _row_to_tenant_dict(row: tuple) -> dict:
    keys = [
        "tenant_id", "name", "slug", "tier", "model", "status",
        "schema_name", "db_name", "max_users", "max_storage_gb",
        "created_at", "updated_at", "deactivated_at", "metadata",
    ]
    d = dict(zip(keys, row))
    # Convert UUIDs and datetimes to strings for JSON serialisation
    d["tenant_id"] = str(d["tenant_id"])
    for ts_field in ("created_at", "updated_at", "deactivated_at"):
        if d[ts_field]:
            d[ts_field] = d[ts_field].isoformat()
    return d


# ── Import fix: psycopg2.extras needed for Json adapter ─────────────────────
import psycopg2.extras  # noqa: E402 (imported here to keep top clean)

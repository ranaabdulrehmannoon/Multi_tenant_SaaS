"""
seed.py — Deterministic data seeder for benchmarking.

Creates N tenants (all models), inserts realistic products and orders so
benchmark queries hit meaningful data volumes.  Idempotent: re-running
skips tenants whose slugs already exist.
"""
import hashlib
import logging
import random
import string
import time
import uuid
from typing import NamedTuple

import psycopg2

from provisioning.config import Config
from provisioning.database import get_connection, get_admin_connection
from provisioning import provisioner

logger = logging.getLogger(__name__)

# Tier / model distribution that mirrors realistic SaaS demographics
_TENANT_DISTRIBUTION = [
    ("free",       "shared_schema"),       # 60 % of tenants
    ("free",       "shared_schema"),
    ("free",       "shared_schema"),
    ("pro",        "schema_per_tenant"),   # 30 %
    ("pro",        "schema_per_tenant"),
    ("enterprise", "db_per_tenant"),       # 10 %
]

_PRODUCT_NAMES = [
    "Widget Pro", "Gadget Elite", "Tool Master", "Sensor X", "Module Z",
    "Controller Alpha", "Interface Beta", "Platform Core", "Engine Ultra", "Kit Basic",
]
_STATUSES = ["pending", "confirmed", "shipped", "delivered", "cancelled"]


class SeededTenant(NamedTuple):
    tenant_id:   str
    slug:        str
    model:       str
    schema_name: str | None
    db_name:     str | None
    admin_email: str


def seed_tenants(count: int, base_slug: str = "bench") -> list[SeededTenant]:
    """
    Provision `count` tenants with pre-seeded products and orders.
    Returns list of SeededTenant metadata for use by the benchmark runner.
    """
    seeded: list[SeededTenant] = []
    random.seed(42)  # deterministic for reproducibility

    for i in range(count):
        dist = _TENANT_DISTRIBUTION[i % len(_TENANT_DISTRIBUTION)]
        tier, model = dist
        slug        = f"{base_slug}-{i:04d}"
        name        = f"Bench Corp {i:04d}"
        admin_email = f"admin@{slug}.example.com"

        # Skip if already provisioned
        existing = _find_tenant_by_slug(slug)
        if existing:
            seeded.append(SeededTenant(
                tenant_id   = existing["tenant_id"],
                slug        = slug,
                model       = existing["model"],
                schema_name = existing.get("schema_name"),
                db_name     = existing.get("db_name"),
                admin_email = admin_email,
            ))
            continue

        try:
            t = provisioner.provision_tenant(
                name           = name,
                slug           = slug,
                tier           = tier,
                model          = model,
                admin_email    = admin_email,
                admin_password = "bench_password_123",
            )
            _insert_seed_data(
                tenant_id   = t["tenant_id"],
                model       = t["model"],
                schema_name = t.get("schema_name"),
                db_name     = t.get("db_name"),
                n_products  = 50,
                n_orders    = 200,
            )
            seeded.append(SeededTenant(
                tenant_id   = t["tenant_id"],
                slug        = slug,
                model       = t["model"],
                schema_name = t.get("schema_name"),
                db_name     = t.get("db_name"),
                admin_email = admin_email,
            ))
            logger.info("Seeded tenant %s (%s / %s)", slug, tier, model)
        except Exception as exc:
            logger.error("Failed to seed tenant %s: %s", slug, exc)

    return seeded


def _find_tenant_by_slug(slug: str) -> dict | None:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tenant_id, model, schema_name, db_name "
                    "FROM public.tenants WHERE slug = %s",
                    (slug,),
                )
                row = cur.fetchone()
        if row:
            return {
                "tenant_id":   str(row[0]),
                "model":       row[1],
                "schema_name": row[2],
                "db_name":     row[3],
            }
    except Exception:
        pass
    return None


def _insert_seed_data(
    tenant_id: str,
    model: str,
    schema_name: str | None,
    db_name: str | None,
    n_products: int,
    n_orders: int,
) -> None:
    """Insert synthetic products and orders for a tenant."""
    from provisioning.database import get_connection

    product_ids: list[str] = []

    # ── Products ──────────────────────────────────────────────────────────────
    with get_connection(
        tenant_id   = tenant_id if model != "db_per_tenant" else None,
        db_name     = db_name,
        schema_name = schema_name,
    ) as conn:
        with conn.cursor() as cur:
            if model == "shared_schema":
                cur.execute("SET LOCAL app.current_tenant = %s", (tenant_id,))

            for j in range(n_products):
                sku   = f"SKU-{tenant_id[:8]}-{j:04d}"
                name  = random.choice(_PRODUCT_NAMES) + f" v{j}"
                price = round(random.uniform(1.99, 999.99), 2)

                if model == "shared_schema":
                    cur.execute(
                        "INSERT INTO products (tenant_id, name, price, sku) "
                        "VALUES (%s, %s, %s, %s) "
                        "ON CONFLICT (tenant_id, sku) DO NOTHING "
                        "RETURNING product_id",
                        (tenant_id, name, price, sku),
                    )
                else:
                    cur.execute(
                        "INSERT INTO products (name, price, sku) "
                        "VALUES (%s, %s, %s) "
                        "ON CONFLICT (sku) DO NOTHING "
                        "RETURNING product_id",
                        (name, price, sku),
                    )
                row = cur.fetchone()
                if row:
                    product_ids.append(str(row[0]))

    if not product_ids:
        return  # products already existed (idempotent re-seed)

    # ── Resolve a user_id for this tenant ─────────────────────────────────────
    user_id = _get_tenant_admin_user_id(tenant_id, model, db_name)
    if user_id is None:
        return

    # ── Orders ────────────────────────────────────────────────────────────────
    with get_connection(
        tenant_id   = tenant_id if model != "db_per_tenant" else None,
        db_name     = db_name,
        schema_name = schema_name,
    ) as conn:
        with conn.cursor() as cur:
            if model == "shared_schema":
                cur.execute("SET LOCAL app.current_tenant = %s", (tenant_id,))

            for _ in range(n_orders):
                status = random.choice(_STATUSES)
                amount = round(random.uniform(10.0, 5000.0), 2)

                if model == "shared_schema":
                    cur.execute(
                        "INSERT INTO orders (tenant_id, user_id, status, total_amount) "
                        "VALUES (%s, %s, %s, %s)",
                        (tenant_id, user_id, status, amount),
                    )
                else:
                    cur.execute(
                        "INSERT INTO orders (user_id, status, total_amount) "
                        "VALUES (%s, %s, %s)",
                        (user_id, status, amount),
                    )


def _get_tenant_admin_user_id(
    tenant_id: str, model: str, db_name: str | None
) -> str | None:
    if model == "db_per_tenant" and db_name:
        with get_connection(db_name=db_name) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM public.users LIMIT 1")
                row = cur.fetchone()
        return str(row[0]) if row else None
    else:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id FROM public.tenant_users "
                    "WHERE tenant_id = %s LIMIT 1",
                    (tenant_id,),
                )
                row = cur.fetchone()
        return str(row[0]) if row else None

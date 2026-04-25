"""
tenants.py — Tenant provisioning and data-access endpoints.

POST   /tenants                   Provision new tenant
GET    /tenants/<id>              Get tenant info
DELETE /tenants/<id>              Deactivate tenant
POST   /tenants/<id>/users        Create user in tenant
GET    /tenants/<id>/data         Query tenant data (RLS enforced)
POST   /tenants/<id>/data         Insert data into tenant
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt
import psycopg2
from flask import Blueprint, request, jsonify, g

from api.auth import require_auth, require_role, require_superadmin, set_pg_session_context
from cache.redis_client import get_redis, cache_get, cache_set, cache_invalidate_tenant
from provisioning.config import Config
from provisioning.database import get_connection
from provisioning import provisioner

tenants_bp = Blueprint("tenants", __name__)
logger = logging.getLogger(__name__)


# ── Helper: structured error responses ───────────────────────────────────────
def _pg_error_response(exc: psycopg2.Error):
    return jsonify({
        "error":    "Database error",
        "pg_code":  exc.pgcode,
        "detail":   exc.pgerror or str(exc),
    }), 500


# ── POST /tenants — provision new tenant ─────────────────────────────────────
@tenants_bp.post("/tenants")
@require_superadmin
def create_tenant():
    body = request.get_json(silent=True) or {}
    required = ("name", "slug", "tier", "model", "admin_email", "admin_password")
    missing  = [f for f in required if not body.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {missing}"}), 400

    try:
        tenant = provisioner.provision_tenant(
            name           = body["name"],
            slug           = body["slug"],
            tier           = body["tier"],
            model          = body["model"],
            admin_email    = body["admin_email"],
            admin_password = body["admin_password"],
            metadata       = body.get("metadata", {}),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422
    except psycopg2.IntegrityError as exc:
        return jsonify({"error": "Tenant name or slug already exists", "detail": str(exc)}), 409
    except psycopg2.Error as exc:
        return _pg_error_response(exc)

    return jsonify(tenant), 201


# ── GET /tenants/<id> ─────────────────────────────────────────────────────────
@tenants_bp.get("/tenants/<tenant_id>")
@require_auth
def get_tenant(tenant_id):
    # Tenant users may only fetch their own tenant; superadmin can fetch any
    if g.role != "superadmin" and g.tenant_id != tenant_id:
        return jsonify({"error": "Forbidden"}), 403

    tenant = provisioner.get_tenant(tenant_id)
    if tenant is None:
        return jsonify({"error": "Tenant not found"}), 404
    return jsonify(tenant)


# ── DELETE /tenants/<id> ──────────────────────────────────────────────────────
@tenants_bp.delete("/tenants/<tenant_id>")
@require_superadmin
def delete_tenant(tenant_id):
    try:
        tenant = provisioner.deactivate_tenant(tenant_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except psycopg2.Error as exc:
        return _pg_error_response(exc)

    # Invalidate all cached data for this tenant
    cache_invalidate_tenant(tenant_id)
    return jsonify(tenant)


# ── POST /tenants/<id>/users ──────────────────────────────────────────────────
@tenants_bp.post("/tenants/<tenant_id>/users")
@require_auth
@require_role("superadmin", "tenant_admin")
def create_user(tenant_id):
    if g.role == "tenant_admin" and g.tenant_id != tenant_id:
        return jsonify({"error": "Forbidden"}), 403

    body = request.get_json(silent=True) or {}
    if not body.get("email") or not body.get("password"):
        return jsonify({"error": "email and password are required"}), 400

    role = body.get("role", "tenant_user")
    if role not in ("tenant_admin", "tenant_user", "tenant_readonly"):
        return jsonify({"error": f"Invalid role '{role}'"}), 422

    password_hash = bcrypt.hashpw(body["password"].encode(), bcrypt.gensalt()).decode()

    try:
        with get_connection(tenant_id=tenant_id) as conn:
            set_pg_session_context(conn, tenant_id, g.user_id)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.tenant_users
                        (tenant_id, email, password_hash, role, metadata)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING user_id, email, role, created_at
                    """,
                    (tenant_id, body["email"], password_hash, role,
                     json.dumps(body.get("metadata", {}))),
                )
                row = cur.fetchone()
    except psycopg2.IntegrityError:
        return jsonify({"error": "Email already exists in this tenant"}), 409
    except psycopg2.Error as exc:
        return _pg_error_response(exc)

    return jsonify({
        "user_id":    str(row[0]),
        "email":      row[1],
        "role":       row[2],
        "created_at": row[3].isoformat(),
    }), 201


# ── POST /auth/login — issue JWT ─────────────────────────────────────────────
@tenants_bp.post("/auth/login")
def login():
    body = request.get_json(silent=True) or {}
    if not body.get("email") or not body.get("password"):
        return jsonify({"error": "email and password required"}), 400

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.user_id, u.tenant_id, u.password_hash, u.role, u.is_active,
                       t.status AS tenant_status
                  FROM public.tenant_users u
                  JOIN public.tenants t USING (tenant_id)
                 WHERE u.email = %s
                """,
                (body["email"],),
            )
            row = cur.fetchone()

    if row is None:
        return jsonify({"error": "Invalid credentials"}), 401

    user_id, tenant_id, pw_hash, role, is_active, tenant_status = row

    if not is_active:
        return jsonify({"error": "Account disabled"}), 403
    if tenant_status != "active":
        return jsonify({"error": "Tenant account is not active"}), 403
    if not bcrypt.checkpw(body["password"].encode(), pw_hash.encode()):
        return jsonify({"error": "Invalid credentials"}), 401

    payload = {
        "sub":       str(user_id),
        "tenant_id": str(tenant_id),
        "role":      role,
        "exp":       datetime.now(tz=timezone.utc) + timedelta(seconds=Config.JWT_EXPIRY),
        "iat":       datetime.now(tz=timezone.utc),
    }
    token = jwt.encode(payload, Config.JWT_SECRET_KEY, algorithm="HS256")

    # Cache session in Redis
    redis = get_redis()
    if redis:
        redis.setex(f"session:{user_id}", Config.JWT_EXPIRY, token)

    return jsonify({"access_token": token, "expires_in": Config.JWT_EXPIRY})


# ── GET /tenants/<id>/data — query tenant data (RLS enforced) ─────────────────
@tenants_bp.get("/tenants/<tenant_id>/data")
@require_auth
def get_tenant_data(tenant_id):
    if g.role not in ("superadmin",) and g.tenant_id != tenant_id:
        return jsonify({"error": "Forbidden"}), 403

    table = request.args.get("table", "orders")
    if table not in ("orders", "products", "invoices", "order_items"):
        return jsonify({"error": f"Unknown table '{table}'"}), 400

    limit  = min(int(request.args.get("limit",  50)),  500)
    offset = int(request.args.get("offset", 0))

    # Cache key: deterministic hash of tenant + query params
    cache_key = (
        f"tenant:{tenant_id}:query:"
        + hashlib.sha256(
            f"{table}:{limit}:{offset}".encode()
        ).hexdigest()[:16]
    )
    cached = cache_get(cache_key)
    if cached:
        return jsonify({"data": cached, "source": "cache"})

    # Determine tenancy model to route connection correctly
    tenant = provisioner.get_tenant(tenant_id)
    if tenant is None:
        return jsonify({"error": "Tenant not found"}), 404
    if tenant["status"] != "active":
        return jsonify({"error": "Tenant is not active"}), 403

    model       = tenant["model"]
    schema_name = tenant.get("schema_name")
    db_name     = tenant.get("db_name")

    try:
        rows = _fetch_tenant_data(
            tenant_id, model, table, limit, offset,
            schema_name=schema_name, db_name=db_name,
        )
    except psycopg2.Error as exc:
        return _pg_error_response(exc)

    cache_set(cache_key, rows, ttl=Config.CACHE_TTL)
    return jsonify({"data": rows, "source": "db"})


def _fetch_tenant_data(
    tenant_id, model, table, limit, offset,
    schema_name=None, db_name=None,
) -> list[dict]:
    with get_connection(
        tenant_id   = tenant_id if model != "db_per_tenant" else None,
        db_name     = db_name,
        schema_name = schema_name,
    ) as conn:
        with conn.cursor() as cur:
            if model == "shared_schema":
                set_pg_session_context(conn, tenant_id)
            cur.execute(
                f"SELECT * FROM {table} LIMIT %s OFFSET %s",  # noqa: S608
                (limit, offset),
            )
            cols = [desc[0] for desc in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]

    # Serialise non-JSON-native types
    for row in rows:
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
            elif isinstance(v, uuid.UUID):
                row[k] = str(v)
    return rows


# ── POST /tenants/<id>/data — insert data ────────────────────────────────────
@tenants_bp.post("/tenants/<tenant_id>/data")
@require_auth
@require_role("superadmin", "tenant_admin", "tenant_user")
def insert_tenant_data(tenant_id):
    if g.role not in ("superadmin",) and g.tenant_id != tenant_id:
        return jsonify({"error": "Forbidden"}), 403

    body = request.get_json(silent=True) or {}
    table = body.get("table")
    data  = body.get("data", {})

    if table not in ("products", "orders"):
        return jsonify({"error": "Only 'products' and 'orders' inserts are supported via API"}), 400
    if not data:
        return jsonify({"error": "'data' payload is required"}), 400

    tenant = provisioner.get_tenant(tenant_id)
    if tenant is None or tenant["status"] != "active":
        return jsonify({"error": "Tenant not found or inactive"}), 404

    model       = tenant["model"]
    schema_name = tenant.get("schema_name")
    db_name     = tenant.get("db_name")

    try:
        if table == "products":
            result = _insert_product(tenant_id, model, data, schema_name, db_name)
        else:
            result = _insert_order(tenant_id, model, data, schema_name, db_name)
    except psycopg2.errors.UniqueViolation as exc:
        detail = str(exc).split("DETAIL:")[-1].strip() if "DETAIL:" in str(exc) else str(exc)
        return jsonify({"error": "Duplicate value — " + detail}), 409
    except psycopg2.errors.NotNullViolation:
        return jsonify({"error": "A required field is missing"}), 400
    except psycopg2.errors.ForeignKeyViolation:
        return jsonify({"error": "Referenced record does not exist"}), 400
    except psycopg2.Error as exc:
        return jsonify({"error": "Database error", "detail": str(exc)}), 500

    # Invalidate query cache for this tenant on write
    cache_invalidate_tenant(tenant_id)
    return jsonify(result), 201


def _insert_product(tenant_id, model, data, schema_name, db_name):
    tbl = "products"
    tenant_col = "tenant_id, " if model == "shared_schema" else ""
    tenant_val = "%s, "        if model == "shared_schema" else ""
    params_base = [data["name"], data.get("price", 0), data.get("sku", "")]
    if model == "shared_schema":
        params_base.insert(0, tenant_id)

    with get_connection(
        tenant_id   = tenant_id if model != "db_per_tenant" else None,
        db_name     = db_name,
        schema_name = schema_name,
    ) as conn:
        if model == "shared_schema":
            set_pg_session_context(conn, tenant_id, g.user_id)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {tbl} ({tenant_col}name, price, sku)
                VALUES ({tenant_val}%s, %s, %s)
                RETURNING product_id, name, price, sku, created_at
                """,
                params_base,
            )
            row = cur.fetchone()
    return {
        "product_id": str(row[0]),
        "name":       row[1],
        "price":      float(row[2]),
        "sku":        row[3],
        "created_at": row[4].isoformat(),
    }


def _insert_order(tenant_id, model, data, schema_name, db_name):
    tbl = "orders"
    user_id = data.get("user_id", g.user_id)
    tenant_col = "tenant_id, " if model == "shared_schema" else ""
    tenant_val = "%s, "        if model == "shared_schema" else ""
    params = [user_id, data.get("total_amount", 0), data.get("currency", "USD")]
    if model == "shared_schema":
        params.insert(0, tenant_id)

    with get_connection(
        tenant_id   = tenant_id if model != "db_per_tenant" else None,
        db_name     = db_name,
        schema_name = schema_name,
    ) as conn:
        if model == "shared_schema":
            set_pg_session_context(conn, tenant_id, g.user_id)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {tbl} ({tenant_col}user_id, total_amount, currency)
                VALUES ({tenant_val}%s, %s, %s)
                RETURNING order_id, status, total_amount, created_at
                """,
                params,
            )
            row = cur.fetchone()
    return {
        "order_id":     str(row[0]),
        "status":       row[1],
        "total_amount": float(row[2]),
        "created_at":   row[3].isoformat(),
    }

"""
auth.py — JWT middleware and tenant context injection.

Every protected endpoint:
  1. Validates the Bearer JWT
  2. Sets app.current_tenant + app.current_user in the PostgreSQL session
  3. Injects tenant info into Flask's g object
"""
import logging
from functools import wraps

import jwt
import psycopg2
from flask import request, jsonify, g

from provisioning.config import Config
from provisioning.database import get_connection, get_main_pool

logger = logging.getLogger(__name__)


def _decode_token(token: str) -> dict:
    return jwt.decode(
        token,
        Config.JWT_SECRET_KEY,
        algorithms=["HS256"],
        options={"require": ["sub", "tenant_id", "role", "exp"]},
    )


def require_auth(f):
    """Decorator: validates JWT and sets tenant context on g."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or malformed Authorization header"}), 401

        token = auth_header.split(" ", 1)[1]
        try:
            payload = _decode_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except jwt.InvalidTokenError as exc:
            return jsonify({"error": f"Invalid token: {exc}"}), 401

        g.user_id   = payload["sub"]
        g.tenant_id = payload["tenant_id"]
        g.role      = payload["role"]
        return f(*args, **kwargs)

    return decorated


def require_role(*allowed_roles: str):
    """Decorator: checks g.role is in allowed_roles (apply AFTER require_auth)."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not hasattr(g, "role") or g.role not in allowed_roles:
                return jsonify({"error": "Insufficient permissions"}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


def require_superadmin(f):
    return require_auth(require_role("superadmin")(f))


def set_pg_session_context(conn, tenant_id: str, user_id: str | None = None) -> None:
    """
    SET LOCAL app.current_tenant (and optionally app.current_user) on an
    already-open connection.  Must be called inside an open transaction.
    """
    with conn.cursor() as cur:
        cur.execute("SET LOCAL app.current_tenant = %s", (str(tenant_id),))
        if user_id:
            cur.execute('SET LOCAL "app.current_user" = %s', (str(user_id),))

"""
app.py — Flask application factory.
Run with:  flask --app api/app run
           or via docker-compose (see docker-compose.yml)
"""
import logging
import os

import bcrypt
from flask import Flask, jsonify, redirect, url_for

from provisioning.config import Config


def _refresh_audit_trigger_function() -> None:
    """
    The original fn_audit_log() guarded the tenant_id fallback with
    EXCEPTION WHEN others, but NULL coercion (`NULL::UUID`) does not throw,
    so for Model B inserts (no tenant_id column on the row) v_tenant_id
    stayed NULL and the audit_log NOT NULL constraint blocked the entire
    INSERT.  Re-deploy a corrected version on every startup so live DBs
    pick up the fix without a volume wipe.
    """
    from provisioning.database import get_main_pool

    log = logging.getLogger(__name__)
    fn_sql = """
    CREATE OR REPLACE FUNCTION public.fn_audit_log()
    RETURNS TRIGGER
    SECURITY DEFINER
    LANGUAGE plpgsql AS $body$
    DECLARE
        v_tenant_id UUID;
        v_user_id   UUID;
        v_row_id    TEXT;
        v_old       JSONB := NULL;
        v_new       JSONB := NULL;
    BEGIN
        IF TG_OP = 'DELETE' THEN
            v_tenant_id := (row_to_json(OLD) ->> 'tenant_id')::UUID;
            v_row_id := COALESCE(
                row_to_json(OLD) ->> (TG_TABLE_NAME || '_id'),
                row_to_json(OLD) ->> 'order_id',
                row_to_json(OLD) ->> 'product_id',
                row_to_json(OLD) ->> 'item_id',
                row_to_json(OLD) ->> 'invoice_id'
            );
            v_old := row_to_json(OLD)::JSONB;
        ELSIF TG_OP = 'INSERT' THEN
            v_tenant_id := (row_to_json(NEW) ->> 'tenant_id')::UUID;
            v_row_id := COALESCE(
                row_to_json(NEW) ->> (TG_TABLE_NAME || '_id'),
                row_to_json(NEW) ->> 'order_id',
                row_to_json(NEW) ->> 'product_id'
            );
            v_new := row_to_json(NEW)::JSONB;
        ELSE
            v_tenant_id := (row_to_json(NEW) ->> 'tenant_id')::UUID;
            v_row_id := COALESCE(
                row_to_json(NEW) ->> (TG_TABLE_NAME || '_id'),
                row_to_json(NEW) ->> 'order_id'
            );
            v_old := row_to_json(OLD)::JSONB;
            v_new := row_to_json(NEW)::JSONB;
        END IF;

        IF v_tenant_id IS NULL THEN
            BEGIN
                v_tenant_id := current_setting('app.current_tenant', TRUE)::UUID;
            EXCEPTION WHEN others THEN
                v_tenant_id := NULL;
            END;
        END IF;

        BEGIN
            v_user_id := current_setting('app.current_user', TRUE)::UUID;
        EXCEPTION WHEN others THEN
            v_user_id := NULL;
        END;

        INSERT INTO public.audit_log (
            tenant_id, user_id, table_name, schema_name,
            operation, row_id, old_value, new_value
        ) VALUES (
            v_tenant_id, v_user_id, TG_TABLE_NAME, TG_TABLE_SCHEMA,
            TG_OP, v_row_id, v_old, v_new
        );

        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END;
    $body$;
    """
    pool = get_main_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(fn_sql)
        log.info("fn_audit_log() refreshed (Model B NULL tenant_id fallback fixed).")
    except Exception as exc:
        log.warning("Could not refresh fn_audit_log: %s", exc)
    finally:
        pool.putconn(conn)


def _drop_legacy_bypass_policies() -> None:
    """
    Earlier versions of 01_model_a_shared_schema.sql created
    `superadmin_bypass_*` policies USING (TRUE).  Because the API role
    inherits from app_superadmin, these policies OR-combined with the
    tenant_isolation policies and effectively disabled RLS — every tenant
    saw every other tenant's data.

    Drop them on startup if they exist so the fix applies to live DBs
    without requiring a volume wipe.
    """
    from provisioning.database import get_main_pool

    log = logging.getLogger(__name__)
    pool = get_main_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            for tbl in ("products", "orders", "order_items", "invoices"):
                cur.execute(
                    f"DROP POLICY IF EXISTS superadmin_bypass_{tbl} ON public.{tbl}"
                )
        log.info("Legacy superadmin_bypass_* RLS policies dropped (if present).")
    except Exception as exc:
        log.warning("Could not drop legacy bypass policies: %s", exc)
    finally:
        pool.putconn(conn)


def _seed_demo_accounts() -> None:
    """
    Idempotently ensure the two demo accounts exist with passwords hashed by
    Python's bcrypt (the same library the /auth/login endpoint verifies with).

    Re-runs on every Flask startup so the demo passwords always match, even if
    a previous DB init seeded them with an incompatible hash.
    """
    from provisioning.database import get_main_pool

    accounts = [
        ("system",     "enterprise", "shared_schema", "active", 9999, 9999.00,
         "admin@system.test",     "superadmin_pass", "superadmin",   "System"),
        ("demo-corp",  "pro",        "shared_schema", "active",   50,   10.00,
         "admin@demo-corp.test", "demo_pass",       "tenant_admin", "Demo Corp"),
    ]
    log = logging.getLogger(__name__)
    pool = get_main_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            for slug, tier, model, status, mu, mg, email, pwd, role, name in accounts:
                pwd_hash = bcrypt.hashpw(pwd.encode(), bcrypt.gensalt(10)).decode()
                cur.execute(
                    """
                    INSERT INTO public.tenants
                        (name, slug, tier, model, status, max_users, max_storage_gb)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (slug) DO NOTHING
                    """,
                    (name, slug, tier, model, status, mu, mg),
                )
                cur.execute(
                    """
                    INSERT INTO public.tenant_users (tenant_id, email, password_hash, role)
                    SELECT t.tenant_id, %s, %s, %s
                      FROM public.tenants t
                     WHERE t.slug = %s
                    ON CONFLICT (tenant_id, email)
                    DO UPDATE SET password_hash = EXCLUDED.password_hash
                    """,
                    (email, pwd_hash, role, slug),
                )
        log.info("Demo accounts seeded/refreshed (admin@system.test, admin@demo-corp.test)")
    except Exception as exc:
        log.warning("Demo account seeding skipped: %s", exc)
    finally:
        pool.putconn(conn)


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "..", "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "..", "static"),
        static_url_path="/static",
    )
    app.config["SECRET_KEY"] = Config.FLASK_SECRET_KEY

    # ── Logging ───────────────────────────────────────────────────────────────
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("FLASK_ENV") == "development" else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # ── Blueprints ────────────────────────────────────────────────────────────
    from api.routes.tenants import tenants_bp
    from api.routes.admin   import admin_bp
    from api.routes.ui      import ui_bp

    app.register_blueprint(tenants_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(ui_bp)

    # Drop legacy RLS bypass policies that broke tenant isolation
    try:
        _drop_legacy_bypass_policies()
    except Exception as exc:
        logging.getLogger(__name__).warning("drop_legacy_bypass_policies failed: %s", exc)

    # Re-deploy the fixed audit trigger function (handles Model B NULL tenant_id)
    try:
        _refresh_audit_trigger_function()
    except Exception as exc:
        logging.getLogger(__name__).warning("refresh_audit_trigger_function failed: %s", exc)

    # Flush stale per-tenant query cache from before the RLS fix landed
    try:
        from cache.redis_client import get_redis
        r = get_redis()
        if r:
            for key in r.scan_iter(match="tenant:*:query:*", count=200):
                r.delete(key)
            logging.getLogger(__name__).info("Stale tenant query cache flushed.")
    except Exception as exc:
        logging.getLogger(__name__).warning("cache flush at startup failed: %s", exc)

    # Ensure the two demo accounts always have passwords that the Python
    # bcrypt verifier in /auth/login can validate.
    try:
        _seed_demo_accounts()
    except Exception as exc:
        logging.getLogger(__name__).warning("seed_demo_accounts failed at startup: %s", exc)

    # Redirect root → login page
    @app.get("/")
    def index():
        return redirect("/ui/login")

    # ── Health check ──────────────────────────────────────────────────────────
    @app.get("/health")
    def health():
        from provisioning.database import get_main_pool
        from cache.redis_client import get_redis
        pg_ok    = False
        redis_ok = False
        try:
            pool = get_main_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            pool.putconn(conn)
            pg_ok = True
        except Exception as exc:
            logging.getLogger(__name__).warning("PG health check failed: %s", exc)

        try:
            r = get_redis()
            if r:
                r.ping()
                redis_ok = True
        except Exception as exc:
            logging.getLogger(__name__).warning("Redis health check failed: %s", exc)

        status = "ok" if (pg_ok and redis_ok) else "degraded"
        return jsonify({
            "status":    status,
            "postgres":  "ok" if pg_ok    else "unavailable",
            "redis":     "ok" if redis_ok else "unavailable",
        }), 200 if status == "ok" else 503

    # ── Global error handlers ─────────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"error": "Method not allowed"}), 405

    @app.errorhandler(500)
    def internal_error(e):
        return jsonify({"error": "Internal server error"}), 500

    return app


# Allow direct invocation: python api/app.py
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

"""
app.py — Flask application factory.
Run with:  flask --app api/app run
           or via docker-compose (see docker-compose.yml)
"""
import logging
import os

from flask import Flask, jsonify, redirect, url_for

from provisioning.config import Config


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

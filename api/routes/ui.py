"""
ui.py — Flask routes that serve HTML templates for the GUI.
All data fetching happens client-side via JavaScript → existing API endpoints.
These routes only serve templates; they do NOT proxy API calls.
"""
from flask import Blueprint, render_template, redirect, url_for

ui_bp = Blueprint("ui", __name__, url_prefix="/ui")


@ui_bp.get("/")
@ui_bp.get("/dashboard")
def dashboard():
    return render_template("dashboard.html", active_page="dashboard")


@ui_bp.get("/tenants")
def tenants():
    return render_template("tenants.html", active_page="tenants")


@ui_bp.get("/tenants/<tenant_id>")
def tenant_detail(tenant_id):
    return render_template("tenant_detail.html", active_page="tenants")


@ui_bp.get("/metrics")
def metrics():
    return render_template("metrics.html", active_page="metrics")


@ui_bp.get("/audit")
def audit():
    return render_template("audit.html", active_page="audit")


@ui_bp.get("/login")
def login():
    return render_template("login.html")


@ui_bp.get("/benchmark")
def benchmark():
    # Placeholder — benchmark runner page (static info for now)
    return render_template("benchmark.html", active_page="benchmark")


@ui_bp.get("/settings")
def settings():
    return render_template("settings.html", active_page="settings")

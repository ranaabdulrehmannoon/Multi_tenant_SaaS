"""
test_provisioner.py
===================
Integration tests for the tenant provisioning engine.
Verifies that all three models are provisioned correctly and that
validation, deactivation, and idempotency rules work as expected.

Run with:
    python -m pytest tests/test_provisioner.py -v
"""
import uuid
import unittest

import psycopg2

from provisioning import provisioner
from provisioning.database import get_connection, get_admin_connection
from provisioning.config import Config


def _unique_slug(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class TestProvisionModelA(unittest.TestCase):
    """Provision a free-tier shared_schema tenant and verify all artifacts."""

    def test_provision_creates_registry_record(self):
        slug   = _unique_slug("prov-a")
        tenant = provisioner.provision_tenant(
            name="Prov Test A", slug=slug, tier="free",
            model="shared_schema",
            admin_email=f"admin@{slug}.test",
            admin_password="pass123",
        )

        self.assertEqual(tenant["slug"],   slug)
        self.assertEqual(tenant["model"],  "shared_schema")
        self.assertEqual(tenant["tier"],   "free")
        self.assertEqual(tenant["status"], "active")
        self.assertIsNotNone(tenant["tenant_id"])
        self.assertIsNone(tenant.get("schema_name"))
        self.assertIsNone(tenant.get("db_name"))

        # max_users must match tier_config
        self.assertEqual(tenant["max_users"], 5)

    def test_provision_creates_admin_user(self):
        slug   = _unique_slug("prov-a-user")
        tenant = provisioner.provision_tenant(
            name="Prov Test A User", slug=slug, tier="free",
            model="shared_schema",
            admin_email=f"admin@{slug}.test",
            admin_password="pass123",
        )

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT role FROM public.tenant_users "
                    "WHERE tenant_id = %s AND email = %s",
                    (tenant["tenant_id"], f"admin@{slug}.test"),
                )
                row = cur.fetchone()

        self.assertIsNotNone(row, "Admin user not created")
        self.assertEqual(row[0], "tenant_admin")


class TestProvisionModelB(unittest.TestCase):
    """Provision a pro-tier schema_per_tenant tenant and verify schema creation."""

    def test_provision_creates_schema(self):
        slug   = _unique_slug("prov-b")
        tenant = provisioner.provision_tenant(
            name="Prov Test B", slug=slug, tier="pro",
            model="schema_per_tenant",
            admin_email=f"admin@{slug}.test",
            admin_password="pass123",
        )

        expected_schema = f"tenant_{slug.replace('-', '_')}"
        self.assertEqual(tenant["schema_name"], expected_schema)

        # Verify the schema physically exists in PostgreSQL
        with get_admin_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM information_schema.schemata "
                    "WHERE schema_name = %s",
                    (expected_schema,),
                )
                exists = cur.fetchone() is not None

        self.assertTrue(exists, f"Schema '{expected_schema}' was not created")

    def test_provision_creates_tables_in_schema(self):
        slug   = _unique_slug("prov-b-tables")
        tenant = provisioner.provision_tenant(
            name="Prov Test B Tables", slug=slug, tier="pro",
            model="schema_per_tenant",
            admin_email=f"admin@{slug}.test",
            admin_password="pass123",
        )
        schema = tenant["schema_name"]

        with get_admin_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = %s ORDER BY table_name",
                    (schema,),
                )
                tables = {row[0] for row in cur.fetchall()}

        expected = {"products", "orders", "order_items", "invoices"}
        self.assertTrue(
            expected.issubset(tables),
            f"Missing tables in schema {schema}. Found: {tables}"
        )

    def test_audit_triggers_attached_to_schema_tables(self):
        slug   = _unique_slug("prov-b-audit")
        tenant = provisioner.provision_tenant(
            name="Prov Test B Audit", slug=slug, tier="pro",
            model="schema_per_tenant",
            admin_email=f"admin@{slug}.test",
            admin_password="pass123",
        )
        schema = tenant["schema_name"]

        with get_admin_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT trigger_name FROM information_schema.triggers "
                    "WHERE trigger_schema = %s",
                    (schema,),
                )
                triggers = {row[0] for row in cur.fetchall()}

        audit_triggers = {f"trg_audit_{t}" for t in ("products","orders","order_items","invoices")}
        self.assertTrue(
            audit_triggers.issubset(triggers),
            f"Audit triggers missing in schema {schema}. Found: {triggers}"
        )


class TestProvisionModelC(unittest.TestCase):
    """Provision an enterprise-tier db_per_tenant tenant and verify DB creation."""

    def test_provision_creates_database(self):
        slug   = _unique_slug("prov-c")
        tenant = provisioner.provision_tenant(
            name="Prov Test C", slug=slug, tier="enterprise",
            model="db_per_tenant",
            admin_email=f"admin@{slug}.test",
            admin_password="pass123",
        )
        expected_db = f"tenant_db_{slug.replace('-', '_')}"
        self.assertEqual(tenant["db_name"], expected_db)

        with get_admin_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s",
                    (expected_db,),
                )
                exists = cur.fetchone() is not None

        self.assertTrue(exists, f"Database '{expected_db}' was not created")

    def test_tenant_db_has_correct_tables(self):
        slug   = _unique_slug("prov-c-tbl")
        tenant = provisioner.provision_tenant(
            name="Prov Test C Tables", slug=slug, tier="enterprise",
            model="db_per_tenant",
            admin_email=f"admin@{slug}.test",
            admin_password="pass123",
        )
        db_name = tenant["db_name"]

        with get_admin_connection(db_name=db_name) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' ORDER BY table_name"
                )
                tables = {row[0] for row in cur.fetchall()}

        expected = {"products", "orders", "order_items", "invoices", "users", "audit_log"}
        self.assertTrue(
            expected.issubset(tables),
            f"Missing tables in tenant DB. Found: {tables}"
        )


class TestProvisionValidation(unittest.TestCase):
    """Input validation and business rule enforcement."""

    def test_invalid_slug_rejected(self):
        with self.assertRaises(ValueError):
            provisioner.provision_tenant(
                name="Bad Slug", slug="Bad Slug!!", tier="free",
                model="shared_schema",
                admin_email="a@b.com", admin_password="pass",
            )

    def test_free_tier_cannot_use_schema_per_tenant(self):
        with self.assertRaises(ValueError):
            provisioner.provision_tenant(
                name="Tier Mismatch", slug=_unique_slug("tier-mismatch"),
                tier="free", model="schema_per_tenant",
                admin_email="a@b.com", admin_password="pass",
            )

    def test_free_tier_cannot_use_db_per_tenant(self):
        with self.assertRaises(ValueError):
            provisioner.provision_tenant(
                name="Tier Mismatch DB", slug=_unique_slug("tier-mismatch-db"),
                tier="free", model="db_per_tenant",
                admin_email="a@b.com", admin_password="pass",
            )

    def test_duplicate_slug_rejected(self):
        slug = _unique_slug("dup-slug")
        provisioner.provision_tenant(
            name="Dup Slug First", slug=slug, tier="free",
            model="shared_schema",
            admin_email=f"a@{slug}.test", admin_password="pass",
        )
        with self.assertRaises(psycopg2.IntegrityError):
            provisioner.provision_tenant(
                name="Dup Slug Second", slug=slug, tier="free",
                model="shared_schema",
                admin_email=f"b@{slug}.test", admin_password="pass",
            )


class TestDeactivation(unittest.TestCase):
    """Tenant deactivation and status transitions."""

    def test_deactivation_sets_status(self):
        slug   = _unique_slug("deact")
        tenant = provisioner.provision_tenant(
            name="Deact Test", slug=slug, tier="free",
            model="shared_schema",
            admin_email=f"admin@{slug}.test", admin_password="pass",
        )
        deactivated = provisioner.deactivate_tenant(tenant["tenant_id"])

        self.assertEqual(deactivated["status"], "deactivated")
        self.assertIsNotNone(deactivated["deactivated_at"])

    def test_deactivating_already_deactivated_raises(self):
        slug   = _unique_slug("deact2")
        tenant = provisioner.provision_tenant(
            name="Deact Test 2", slug=slug, tier="free",
            model="shared_schema",
            admin_email=f"admin@{slug}.test", admin_password="pass",
        )
        provisioner.deactivate_tenant(tenant["tenant_id"])

        with self.assertRaises(ValueError):
            provisioner.deactivate_tenant(tenant["tenant_id"])

    def test_get_nonexistent_tenant_returns_none(self):
        result = provisioner.get_tenant(str(uuid.uuid4()))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)

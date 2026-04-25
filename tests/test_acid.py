"""
test_acid.py
============
Demonstrates and verifies all four ACID properties against the
multi-tenant PostgreSQL schema.

A  — Atomicity   : a transaction that partially fails rolls back entirely
T  — not tested separately (Consistency is checked via constraints)
I  — Isolation   : concurrent transactions cannot see each other's uncommitted data
C  — Consistency : CHECK constraints and FK constraints reject invalid data
D  — Durability  : committed data survives a simulated connection drop

Run with:
    python -m pytest tests/test_acid.py -v
"""
import threading
import time
import unittest
import uuid

import psycopg2
import psycopg2.errors

from provisioning.config import Config
from provisioning import provisioner


# ── Helpers ───────────────────────────────────────────────────────────────────

def _raw_conn(autocommit: bool = False) -> psycopg2.extensions.connection:
    conn = psycopg2.connect(Config.pg_dsn())
    conn.autocommit = autocommit
    return conn


def _get_or_create_tenant(slug: str) -> dict:
    tenants = provisioner.list_tenants()
    for t in tenants:
        if t["slug"] == slug:
            return t
    return provisioner.provision_tenant(
        name           = f"ACID Test Tenant {slug}",
        slug           = slug,
        tier           = "free",
        model          = "shared_schema",
        admin_email    = f"admin@{slug}.test",
        admin_password = "acid_test_pass",
    )


def _user_id_for_tenant(tenant_id: str) -> str:
    conn = _raw_conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM public.tenant_users WHERE tenant_id = %s LIMIT 1",
                (tenant_id,),
            )
            row = cur.fetchone()
    conn.close()
    return str(row[0])


# ── ACID Test Class ───────────────────────────────────────────────────────────

class TestACIDAtomicity(unittest.TestCase):
    """
    A — Atomicity: all-or-nothing.
    A transaction that inserts an order and then tries to insert a duplicate
    product (violating UNIQUE) must roll back the entire transaction,
    leaving no partial state in the database.
    """

    @classmethod
    def setUpClass(cls):
        cls.tenant = _get_or_create_tenant("acid-atomicity")
        cls.tid    = cls.tenant["tenant_id"]
        cls.uid    = _user_id_for_tenant(cls.tid)

        # Insert the "existing" product that will cause the duplicate later
        conn = _raw_conn()
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL app.current_tenant = %s", (cls.tid,))
                cur.execute(
                    "INSERT INTO public.products (tenant_id, name, price, sku) "
                    "VALUES (%s, 'Existing Product', 9.99, 'ACID-SKU-001') "
                    "ON CONFLICT (tenant_id, sku) DO NOTHING",
                    (cls.tid,),
                )
        conn.close()

    def test_partial_failure_rolls_back_entirely(self):
        """
        Transaction:
          1. INSERT INTO orders  ← succeeds
          2. INSERT INTO products with duplicate SKU  ← fails (IntegrityError)
        Expected: the order from step 1 is NOT present (full rollback).
        """
        conn = _raw_conn()
        order_id = str(uuid.uuid4())
        rolled_back = False

        try:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL app.current_tenant = %s", (self.tid,))

                # Step 1: insert an order — succeeds in isolation
                cur.execute(
                    "INSERT INTO public.orders "
                    "(order_id, tenant_id, user_id, status, total_amount) "
                    "VALUES (%s, %s, %s, 'pending', 42.00)",
                    (order_id, self.tid, self.uid),
                )

                # Step 2: intentional duplicate — triggers rollback
                cur.execute(
                    "INSERT INTO public.products (tenant_id, name, price, sku) "
                    "VALUES (%s, 'Dup Product', 1.00, 'ACID-SKU-001')",  # duplicate SKU
                    (self.tid,),
                )
            conn.commit()  # should never reach here
        except psycopg2.IntegrityError:
            conn.rollback()
            rolled_back = True
        finally:
            conn.close()

        self.assertTrue(rolled_back, "Expected IntegrityError was not raised")

        # Verify the order does NOT exist (atomicity: partial state was rolled back)
        conn2 = _raw_conn()
        with conn2:
            with conn2.cursor() as cur:
                cur.execute("SET LOCAL app.current_tenant = %s", (self.tid,))
                cur.execute(
                    "SELECT 1 FROM public.orders WHERE order_id = %s",
                    (order_id,),
                )
                exists = cur.fetchone() is not None
        conn2.close()

        self.assertFalse(
            exists,
            "ATOMICITY VIOLATED: order from failed transaction persisted in database"
        )

    def test_successful_transaction_commits_all(self):
        """
        Symmetric test: a transaction that SUCCEEDS must commit ALL changes.
        """
        conn = _raw_conn()
        order_id   = str(uuid.uuid4())
        product_id = None

        with conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL app.current_tenant = %s", (self.tid,))

                cur.execute(
                    "INSERT INTO public.products (tenant_id, name, price, sku) "
                    "VALUES (%s, 'Atomic Product', 19.99, %s) "
                    "RETURNING product_id",
                    (self.tid, f"ACID-GOOD-{uuid.uuid4().hex[:8]}"),
                )
                product_id = str(cur.fetchone()[0])

                cur.execute(
                    "INSERT INTO public.orders "
                    "(order_id, tenant_id, user_id, status, total_amount) "
                    "VALUES (%s, %s, %s, 'confirmed', 19.99)",
                    (order_id, self.tid, self.uid),
                )

        conn.close()

        # Both must exist
        conn3 = _raw_conn()
        with conn3:
            with conn3.cursor() as cur:
                cur.execute("SET LOCAL app.current_tenant = %s", (self.tid,))
                cur.execute(
                    "SELECT 1 FROM public.products WHERE product_id = %s",
                    (product_id,),
                )
                prod_exists = cur.fetchone() is not None

                cur.execute(
                    "SELECT 1 FROM public.orders WHERE order_id = %s",
                    (order_id,),
                )
                order_exists = cur.fetchone() is not None
        conn3.close()

        self.assertTrue(prod_exists,  "Product from committed transaction not found")
        self.assertTrue(order_exists, "Order from committed transaction not found")


class TestACIDIsolation(unittest.TestCase):
    """
    I — Isolation: uncommitted changes in transaction T1 are invisible to T2.
    PostgreSQL default isolation: READ COMMITTED.
    We test that dirty reads are impossible.
    """

    @classmethod
    def setUpClass(cls):
        cls.tenant = _get_or_create_tenant("acid-isolation")
        cls.tid    = cls.tenant["tenant_id"]
        cls.uid    = _user_id_for_tenant(cls.tid)

    def test_dirty_read_impossible(self):
        """
        T1 inserts an order but does NOT commit.
        T2 (separate connection) must NOT see T1's uncommitted row.
        After T1 rolls back, T2 confirms the row never existed.
        """
        order_id    = str(uuid.uuid4())
        barrier_start  = threading.Barrier(2)
        barrier_read   = threading.Barrier(2)
        dirty_read_count = [0]   # mutable reference shared across threads

        def transaction_t1():
            """Insert, pause for T2 to read, then rollback."""
            conn = _raw_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SET LOCAL app.current_tenant = %s", (self.tid,))
                    cur.execute(
                        "INSERT INTO public.orders "
                        "(order_id, tenant_id, user_id, status, total_amount) "
                        "VALUES (%s, %s, %s, 'pending', 999.00)",
                        (order_id, self.tid, self.uid),
                    )
                    barrier_start.wait()   # signal T2: row is inserted (uncommitted)
                    barrier_read.wait()    # wait for T2 to finish its read
                conn.rollback()            # rollback — row should never have existed
            finally:
                conn.close()

        def transaction_t2():
            """Read while T1's insert is uncommitted."""
            conn = _raw_conn()
            barrier_start.wait()  # wait until T1 has inserted (but not committed)
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute("SET LOCAL app.current_tenant = %s", (self.tid,))
                        cur.execute(
                            "SELECT 1 FROM public.orders WHERE order_id = %s",
                            (order_id,),
                        )
                        if cur.fetchone() is not None:
                            dirty_read_count[0] += 1
            finally:
                barrier_read.wait()  # signal T1 to continue
                conn.close()

        t1 = threading.Thread(target=transaction_t1, daemon=True)
        t2 = threading.Thread(target=transaction_t2, daemon=True)
        t1.start(); t2.start()
        t1.join(timeout=10); t2.join(timeout=10)

        self.assertEqual(
            dirty_read_count[0], 0,
            "ISOLATION VIOLATED: T2 performed a dirty read of T1's uncommitted INSERT"
        )

        # Final verification: row was fully rolled back
        conn = _raw_conn()
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL app.current_tenant = %s", (self.tid,))
                cur.execute("SELECT 1 FROM public.orders WHERE order_id = %s", (order_id,))
                still_exists = cur.fetchone() is not None
        conn.close()

        self.assertFalse(still_exists, "Rolled-back row persisted — ATOMICITY + ISOLATION broken")


class TestACIDConsistency(unittest.TestCase):
    """
    C — Consistency: the database rejects state that violates defined constraints.
    Tests CHECK constraints, NOT NULL, FOREIGN KEY, and UNIQUE.
    """

    @classmethod
    def setUpClass(cls):
        cls.tenant = _get_or_create_tenant("acid-consistency")
        cls.tid    = cls.tenant["tenant_id"]
        cls.uid    = _user_id_for_tenant(cls.tid)

    def _assert_rejected(self, sql: str, params: tuple, label: str):
        conn = _raw_conn()
        rejected = False
        try:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL app.current_tenant = %s", (self.tid,))
                cur.execute(sql, params)
            conn.commit()
        except (psycopg2.IntegrityError, psycopg2.DataError, psycopg2.errors.CheckViolation):
            rejected = True
            conn.rollback()
        except Exception as e:
            rejected = True
            conn.rollback()
        finally:
            conn.close()
        self.assertTrue(rejected, f"CONSISTENCY VIOLATED: {label} was not rejected")

    def test_negative_price_rejected(self):
        """CHECK (price >= 0) must reject negative prices."""
        self._assert_rejected(
            "INSERT INTO public.products (tenant_id, name, price, sku) "
            "VALUES (%s, 'Cheap', -1.00, 'NEG-001')",
            (self.tid,),
            "negative price",
        )

    def test_invalid_order_status_rejected(self):
        """CHECK (status IN (...)) must reject unknown status values."""
        self._assert_rejected(
            "INSERT INTO public.orders "
            "(tenant_id, user_id, status, total_amount) "
            "VALUES (%s, %s, 'flying', 10.00)",
            (self.tid, self.uid),
            "invalid order status 'flying'",
        )

    def test_null_tenant_id_rejected(self):
        """NOT NULL on tenant_id must prevent rows without a tenant."""
        self._assert_rejected(
            "INSERT INTO public.products (tenant_id, name, price, sku) "
            "VALUES (NULL, 'No Tenant', 9.00, 'NULL-001')",
            (),
            "NULL tenant_id",
        )

    def test_orphaned_order_item_rejected(self):
        """FK (order_id → orders) must reject items with non-existent order."""
        fake_order_id = str(uuid.uuid4())
        fake_prod_id  = str(uuid.uuid4())
        self._assert_rejected(
            "INSERT INTO public.order_items "
            "(tenant_id, order_id, product_id, quantity, unit_price) "
            "VALUES (%s, %s::UUID, %s::UUID, 1, 5.00)",
            (self.tid, fake_order_id, fake_prod_id),
            "orphaned order_item (FK violation)",
        )

    def test_duplicate_sku_rejected(self):
        """UNIQUE (tenant_id, sku) must prevent duplicate SKUs per tenant."""
        sku = f"DUPE-{uuid.uuid4().hex[:8]}"
        conn = _raw_conn()
        # First insert succeeds
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL app.current_tenant = %s", (self.tid,))
                cur.execute(
                    "INSERT INTO public.products (tenant_id, name, price, sku) "
                    "VALUES (%s, 'First', 1.00, %s)",
                    (self.tid, sku),
                )
        conn.close()

        # Second insert with same SKU must fail
        self._assert_rejected(
            "INSERT INTO public.products (tenant_id, name, price, sku) "
            "VALUES (%s, 'Second', 2.00, %s)",
            (self.tid, sku),
            f"duplicate SKU '{sku}'",
        )


class TestACIDDurability(unittest.TestCase):
    """
    D — Durability: committed data persists after the connection is dropped.
    Simulated by committing, forcibly closing the connection, then opening
    a new connection and verifying the data is still present.
    """

    @classmethod
    def setUpClass(cls):
        cls.tenant = _get_or_create_tenant("acid-durability")
        cls.tid    = cls.tenant["tenant_id"]
        cls.uid    = _user_id_for_tenant(cls.tid)

    def test_committed_data_survives_connection_drop(self):
        """
        1. Open connection C1, INSERT product, COMMIT, close C1 immediately.
        2. Open a brand-new connection C2.
        3. Verify the product is still there.
        """
        sku        = f"DURABLE-{uuid.uuid4().hex[:8]}"
        product_id = None

        # Connection 1: insert and commit
        c1 = _raw_conn()
        with c1:
            with c1.cursor() as cur:
                cur.execute("SET LOCAL app.current_tenant = %s", (self.tid,))
                cur.execute(
                    "INSERT INTO public.products (tenant_id, name, price, sku) "
                    "VALUES (%s, 'Durable Product', 42.00, %s) "
                    "RETURNING product_id",
                    (self.tid, sku),
                )
                product_id = str(cur.fetchone()[0])
        c1.close()  # ← connection dropped immediately after commit

        # Connection 2: verify from scratch
        c2 = _raw_conn()
        with c2:
            with c2.cursor() as cur:
                cur.execute("SET LOCAL app.current_tenant = %s", (self.tid,))
                cur.execute(
                    "SELECT name, price FROM public.products WHERE product_id = %s",
                    (product_id,),
                )
                row = cur.fetchone()
        c2.close()

        self.assertIsNotNone(
            row,
            "DURABILITY VIOLATED: committed product not found after connection drop"
        )
        self.assertEqual(row[0], "Durable Product")
        self.assertEqual(float(row[1]), 42.00)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)

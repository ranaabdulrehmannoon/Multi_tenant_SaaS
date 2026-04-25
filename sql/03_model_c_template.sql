-- =============================================================================
-- 03_model_c_template.sql
-- MODEL C: Isolated Database (Database-Per-Tenant)
--
-- This file is executed INSIDE the newly created tenant database.
-- The Python provisioner:
--   1. Creates a new PostgreSQL database: CREATE DATABASE {db_name}
--   2. Connects to that new database
--   3. Executes this file verbatim (no tenant_id column needed)
--
-- Each tenant database is completely isolated — no shared data, no shared
-- schema, no shared roles (roles ARE cluster-level; we reuse app_api).
-- PostgreSQL 15+
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- Products
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.products (
    product_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    price           NUMERIC(12,2) NOT NULL CHECK (price >= 0),
    sku             VARCHAR(100) NOT NULL UNIQUE,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    attributes      JSONB        NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Users (local copy; synced from cluster-level public.tenant_users by provisioner)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.users (
    user_id         UUID        PRIMARY KEY,
    email           VARCHAR(320) NOT NULL UNIQUE,
    role            VARCHAR(20)  NOT NULL DEFAULT 'tenant_user'
                        CHECK (role IN ('tenant_admin','tenant_user','tenant_readonly')),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Orders
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.orders (
    order_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        NOT NULL REFERENCES public.users (user_id),
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','confirmed','shipped','delivered','cancelled','refunded')),
    total_amount    NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
    currency        CHAR(3)      NOT NULL DEFAULT 'USD',
    shipping_addr   JSONB        NOT NULL DEFAULT '{}',
    notes           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Order Items
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.order_items (
    item_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID        NOT NULL REFERENCES public.orders (order_id) ON DELETE CASCADE,
    product_id      UUID        NOT NULL REFERENCES public.products (product_id),
    quantity        INTEGER      NOT NULL CHECK (quantity > 0),
    unit_price      NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    line_total      NUMERIC(12,2) GENERATED ALWAYS AS (quantity * unit_price) STORED,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Invoices
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.invoices (
    invoice_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID        NOT NULL REFERENCES public.orders (order_id),
    invoice_number  VARCHAR(50)  NOT NULL UNIQUE,
    issued_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    due_at          TIMESTAMPTZ,
    paid_at         TIMESTAMPTZ,
    amount_due      NUMERIC(12,2) NOT NULL,
    amount_paid     NUMERIC(12,2) NOT NULL DEFAULT 0,
    status          VARCHAR(20)  NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','issued','paid','overdue','void')),
    line_items      JSONB        NOT NULL DEFAULT '[]'
);

-- ---------------------------------------------------------------------------
-- Local audit log (each tenant DB keeps its own immutable audit trail)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.audit_log (
    log_id          BIGSERIAL   PRIMARY KEY,
    user_id         UUID,
    table_name      VARCHAR(100) NOT NULL,
    operation       VARCHAR(10)  NOT NULL CHECK (operation IN ('INSERT','UPDATE','DELETE')),
    row_id          TEXT,
    old_value       JSONB,
    new_value       JSONB,
    db_user         TEXT        NOT NULL DEFAULT current_user,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_created
    ON public.audit_log (created_at DESC);

-- ---------------------------------------------------------------------------
-- updated_at helper
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_products_updated_at
    BEFORE UPDATE ON public.products
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

CREATE TRIGGER trg_orders_updated_at
    BEFORE UPDATE ON public.orders
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_products_active
    ON public.products (is_active, created_at DESC) WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_orders_status
    ON public.orders (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_orders_user
    ON public.orders (user_id) WHERE status NOT IN ('cancelled','refunded');

CREATE INDEX IF NOT EXISTS idx_order_items_order
    ON public.order_items (order_id);

CREATE INDEX IF NOT EXISTS idx_order_items_product
    ON public.order_items (product_id);

CREATE INDEX IF NOT EXISTS idx_invoices_status
    ON public.invoices (status) WHERE status IN ('issued','overdue');

CREATE INDEX IF NOT EXISTS idx_products_attributes
    ON public.products USING GIN (attributes);

-- ---------------------------------------------------------------------------
-- Permissions
-- ---------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO app_api;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON public.products, public.orders, public.order_items, public.invoices, public.users
    TO app_api;
GRANT INSERT ON public.audit_log TO app_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_api;

-- =============================================================================
-- 02_model_b_template.sql
-- MODEL B: Shared Database, Separate Schemas (Schema-Per-Tenant)
--
-- This file is a TEMPLATE executed by the Python provisioner at tenant
-- registration time. The provisioner does string substitution:
--   {schema_name}  → e.g., tenant_acme_corp
--   {tenant_id}    → the tenant's UUID
--
-- Each tenant gets their own PostgreSQL schema with identical table structures
-- but no tenant_id column needed — isolation is enforced by the schema boundary.
-- The application sets search_path = {schema_name}, public per connection.
-- PostgreSQL 15+
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Create the tenant schema
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS {schema_name};

COMMENT ON SCHEMA {schema_name} IS
    'Dedicated schema for tenant {tenant_id} (Model B: schema-per-tenant).';

-- ---------------------------------------------------------------------------
-- Products
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS {schema_name}.products (
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
-- Orders
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS {schema_name}.orders (
    order_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        NOT NULL,  -- references public.tenant_users.user_id
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
CREATE TABLE IF NOT EXISTS {schema_name}.order_items (
    item_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID        NOT NULL REFERENCES {schema_name}.orders (order_id) ON DELETE CASCADE,
    product_id      UUID        NOT NULL REFERENCES {schema_name}.products (product_id),
    quantity        INTEGER      NOT NULL CHECK (quantity > 0),
    unit_price      NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    line_total      NUMERIC(12,2) GENERATED ALWAYS AS (quantity * unit_price) STORED,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Invoices
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS {schema_name}.invoices (
    invoice_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID        NOT NULL REFERENCES {schema_name}.orders (order_id),
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
-- Triggers: updated_at
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION {schema_name}.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_products_updated_at
    BEFORE UPDATE ON {schema_name}.products
    FOR EACH ROW EXECUTE FUNCTION {schema_name}.set_updated_at();

CREATE TRIGGER trg_orders_updated_at
    BEFORE UPDATE ON {schema_name}.orders
    FOR EACH ROW EXECUTE FUNCTION {schema_name}.set_updated_at();

-- ---------------------------------------------------------------------------
-- Indexes — same strategy as Model A but no tenant_id column needed
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_{schema_name}_products_active
    ON {schema_name}.products (is_active, created_at DESC)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_{schema_name}_orders_status
    ON {schema_name}.orders (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_{schema_name}_orders_user
    ON {schema_name}.orders (user_id)
    WHERE status NOT IN ('cancelled', 'refunded');

CREATE INDEX IF NOT EXISTS idx_{schema_name}_order_items_order
    ON {schema_name}.order_items (order_id);

CREATE INDEX IF NOT EXISTS idx_{schema_name}_order_items_product
    ON {schema_name}.order_items (product_id);

CREATE INDEX IF NOT EXISTS idx_{schema_name}_invoices_status
    ON {schema_name}.invoices (status)
    WHERE status IN ('issued', 'overdue');

CREATE INDEX IF NOT EXISTS idx_{schema_name}_products_attributes
    ON {schema_name}.products USING GIN (attributes);

-- ---------------------------------------------------------------------------
-- Grant schema access to the API role
-- (search_path isolates the tenant; no RLS required for Model B)
-- ---------------------------------------------------------------------------
GRANT USAGE ON SCHEMA {schema_name} TO app_api;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA {schema_name} TO app_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA {schema_name}
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {schema_name} TO app_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA {schema_name}
    GRANT USAGE, SELECT ON SEQUENCES TO app_api;

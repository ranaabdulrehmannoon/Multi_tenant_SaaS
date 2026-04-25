-- =============================================================================
-- 01_model_a_shared_schema.sql
-- MODEL A: Shared Schema (Single Table with tenant_id + Row-Level Security)
--
-- All tenants share one physical set of tables.
-- PostgreSQL RLS policies enforce strict data isolation at the engine level.
-- The application sets app.current_tenant before every query.
-- PostgreSQL 15+
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Products table (shared across all Model-A tenants)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.products (
    product_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES public.tenants (tenant_id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    price           NUMERIC(12,2) NOT NULL CHECK (price >= 0),
    sku             VARCHAR(100) NOT NULL,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    attributes      JSONB        NOT NULL DEFAULT '{}',  -- tenant-defined flexible fields
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT products_tenant_sku_unique UNIQUE (tenant_id, sku)
);

COMMENT ON TABLE public.products IS 'Model A: shared-schema products table, isolated by tenant_id via RLS.';

-- ---------------------------------------------------------------------------
-- Orders table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.orders (
    order_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES public.tenants (tenant_id) ON DELETE CASCADE,
    user_id         UUID        NOT NULL REFERENCES public.tenant_users (user_id),
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','confirmed','shipped','delivered','cancelled','refunded')),
    total_amount    NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
    currency        CHAR(3)      NOT NULL DEFAULT 'USD',
    shipping_addr   JSONB        NOT NULL DEFAULT '{}',
    notes           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.orders IS 'Model A: shared-schema orders table, isolated by tenant_id via RLS.';

-- ---------------------------------------------------------------------------
-- Order items table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.order_items (
    item_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES public.tenants (tenant_id) ON DELETE CASCADE,
    order_id        UUID        NOT NULL REFERENCES public.orders (order_id) ON DELETE CASCADE,
    product_id      UUID        NOT NULL REFERENCES public.products (product_id),
    quantity        INTEGER      NOT NULL CHECK (quantity > 0),
    unit_price      NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    line_total      NUMERIC(12,2) GENERATED ALWAYS AS (quantity * unit_price) STORED,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.order_items IS 'Model A: shared-schema line items, isolated by tenant_id via RLS.';

-- ---------------------------------------------------------------------------
-- Invoices table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.invoices (
    invoice_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES public.tenants (tenant_id) ON DELETE CASCADE,
    order_id        UUID        NOT NULL REFERENCES public.orders (order_id),
    invoice_number  VARCHAR(50)  NOT NULL,
    issued_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    due_at          TIMESTAMPTZ,
    paid_at         TIMESTAMPTZ,
    amount_due      NUMERIC(12,2) NOT NULL,
    amount_paid     NUMERIC(12,2) NOT NULL DEFAULT 0,
    status          VARCHAR(20)  NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','issued','paid','overdue','void')),
    line_items      JSONB        NOT NULL DEFAULT '[]',

    CONSTRAINT invoices_tenant_number_unique UNIQUE (tenant_id, invoice_number)
);

COMMENT ON TABLE public.invoices IS 'Model A: shared-schema invoices, isolated by tenant_id via RLS.';

-- ---------------------------------------------------------------------------
-- updated_at triggers on Model A tables
-- ---------------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_products_updated_at   ON public.products;
DROP TRIGGER IF EXISTS trg_orders_updated_at     ON public.orders;

CREATE TRIGGER trg_products_updated_at
    BEFORE UPDATE ON public.products
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

CREATE TRIGGER trg_orders_updated_at
    BEFORE UPDATE ON public.orders
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ---------------------------------------------------------------------------
-- INDEXING STRATEGY — Model A
--
-- Every WHERE clause in the application starts with tenant_id.
-- Partial indexes on tenant_id + a selective column cut index size dramatically.
-- ---------------------------------------------------------------------------

-- B-tree composite: the primary access pattern for all tenant queries
CREATE INDEX IF NOT EXISTS idx_products_tenant_active
    ON public.products (tenant_id, is_active, created_at DESC)
    WHERE is_active = TRUE;
-- WHY: nearly all product queries filter "active products for tenant X, newest first"

CREATE INDEX IF NOT EXISTS idx_orders_tenant_status
    ON public.orders (tenant_id, status, created_at DESC);
-- WHY: order dashboards filter by status; composite avoids sequential scan on large tables

CREATE INDEX IF NOT EXISTS idx_orders_tenant_user
    ON public.orders (tenant_id, user_id)
    WHERE status NOT IN ('cancelled', 'refunded');
-- WHY: "my orders" view; partial index excludes terminal-state rows (typically 30-40% of data)

CREATE INDEX IF NOT EXISTS idx_order_items_order
    ON public.order_items (tenant_id, order_id);
-- WHY: every order detail page joins order_items on order_id, always scoped to tenant

CREATE INDEX IF NOT EXISTS idx_order_items_product
    ON public.order_items (tenant_id, product_id);
-- WHY: "how many times was product X sold by tenant Y" query pattern

CREATE INDEX IF NOT EXISTS idx_invoices_tenant_status
    ON public.invoices (tenant_id, status)
    WHERE status IN ('issued', 'overdue');
-- WHY: AR dashboards focus on unpaid invoices; skip paid/void rows

-- GIN index on JSONB attributes
CREATE INDEX IF NOT EXISTS idx_products_attributes
    ON public.products USING GIN (attributes);
-- WHY: tenants store flexible product attributes; GIN allows key/value containment queries

-- ---------------------------------------------------------------------------
-- ROW-LEVEL SECURITY (RLS)
-- The application MUST run: SET LOCAL app.current_tenant = '<uuid>';
-- inside every transaction before touching tenant tables.
-- ---------------------------------------------------------------------------

-- PRODUCTS
ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.products FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_products ON public.products;
CREATE POLICY tenant_isolation_products ON public.products
    AS PERMISSIVE
    FOR ALL
    TO app_api
    USING (
        tenant_id = current_setting('app.current_tenant', TRUE)::UUID
    )
    WITH CHECK (
        tenant_id = current_setting('app.current_tenant', TRUE)::UUID
    );

-- ORDERS
ALTER TABLE public.orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.orders FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_orders ON public.orders;
CREATE POLICY tenant_isolation_orders ON public.orders
    AS PERMISSIVE
    FOR ALL
    TO app_api
    USING (
        tenant_id = current_setting('app.current_tenant', TRUE)::UUID
    )
    WITH CHECK (
        tenant_id = current_setting('app.current_tenant', TRUE)::UUID
    );

-- ORDER_ITEMS
ALTER TABLE public.order_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.order_items FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_order_items ON public.order_items;
CREATE POLICY tenant_isolation_order_items ON public.order_items
    AS PERMISSIVE
    FOR ALL
    TO app_api
    USING (
        tenant_id = current_setting('app.current_tenant', TRUE)::UUID
    )
    WITH CHECK (
        tenant_id = current_setting('app.current_tenant', TRUE)::UUID
    );

-- INVOICES
ALTER TABLE public.invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.invoices FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_invoices ON public.invoices;
CREATE POLICY tenant_isolation_invoices ON public.invoices
    AS PERMISSIVE
    FOR ALL
    TO app_api
    USING (
        tenant_id = current_setting('app.current_tenant', TRUE)::UUID
    )
    WITH CHECK (
        tenant_id = current_setting('app.current_tenant', TRUE)::UUID
    );

-- Superadmin bypass: app_superadmin sees all rows (for analytics/monitoring)
CREATE POLICY superadmin_bypass_products ON public.products
    AS PERMISSIVE FOR ALL TO app_superadmin USING (TRUE) WITH CHECK (TRUE);

CREATE POLICY superadmin_bypass_orders ON public.orders
    AS PERMISSIVE FOR ALL TO app_superadmin USING (TRUE) WITH CHECK (TRUE);

CREATE POLICY superadmin_bypass_order_items ON public.order_items
    AS PERMISSIVE FOR ALL TO app_superadmin USING (TRUE) WITH CHECK (TRUE);

CREATE POLICY superadmin_bypass_invoices ON public.invoices
    AS PERMISSIVE FOR ALL TO app_superadmin USING (TRUE) WITH CHECK (TRUE);

-- ---------------------------------------------------------------------------
-- GRANT table permissions to the API role
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE
    ON public.products, public.orders, public.order_items, public.invoices
    TO app_api;

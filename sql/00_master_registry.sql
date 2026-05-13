

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid(), crypt()
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";  -- query performance
CREATE EXTENSION IF NOT EXISTS "btree_gin";  -- GIN on scalar types

-- ---------------------------------------------------------------------------
-- RBAC Roles
-- Four-tier hierarchy: superadmin > tenant_admin > tenant_user > tenant_readonly
-- ---------------------------------------------------------------------------

-- System-level roles (created at the PostgreSQL cluster level)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_superadmin') THEN
        CREATE ROLE app_superadmin NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_tenant_admin') THEN
        CREATE ROLE app_tenant_admin NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_tenant_user') THEN
        CREATE ROLE app_tenant_user NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_tenant_readonly') THEN
        CREATE ROLE app_tenant_readonly NOLOGIN;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_api') THEN
        CREATE ROLE app_api LOGIN PASSWORD 'app_api_password';
    END IF;
END
$$;

-- Role hierarchy grants
GRANT app_tenant_readonly TO app_tenant_user;
GRANT app_tenant_user     TO app_tenant_admin;
GRANT app_tenant_admin    TO app_superadmin;
GRANT app_superadmin      TO app_api;

-- ---------------------------------------------------------------------------
-- Master Tenant Registry
-- Central table tracking ALL tenants across all three tenancy models.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.tenants (
    tenant_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    slug            VARCHAR(100) NOT NULL,  -- URL-safe identifier, e.g. "acme-corp"
    tier            VARCHAR(20)  NOT NULL CHECK (tier IN ('free', 'pro', 'enterprise')),
    model           VARCHAR(30)  NOT NULL CHECK (model IN ('shared_schema', 'schema_per_tenant', 'db_per_tenant')),
    status          VARCHAR(20)  NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'suspended', 'deactivated')),
    
    schema_name     VARCHAR(100),
    
    db_name         VARCHAR(100),
    -- Resource limits per tier
    max_users       INTEGER      NOT NULL DEFAULT 10,
    max_storage_gb  NUMERIC(10,2) NOT NULL DEFAULT 1.00,
    -- Lifecycle timestamps
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    deactivated_at  TIMESTAMPTZ,
    -- Flexible per-tenant configuration (contact email, branding, feature flags)
    metadata        JSONB        NOT NULL DEFAULT '{}',

    CONSTRAINT tenants_slug_unique UNIQUE (slug),
    CONSTRAINT tenants_name_unique UNIQUE (name)
);

COMMENT ON TABLE public.tenants IS
    'Master registry for all tenants across all three tenancy models.';
COMMENT ON COLUMN public.tenants.slug IS
    'URL-safe lowercase identifier used in API paths and schema names.';
COMMENT ON COLUMN public.tenants.model IS
    'Tenancy isolation model: shared_schema | schema_per_tenant | db_per_tenant';
COMMENT ON COLUMN public.tenants.schema_name IS
    'Populated for Model B tenants. Format: tenant_<slug>.';
COMMENT ON COLUMN public.tenants.db_name IS
    'Populated for Model C tenants. Format: tenant_db_<slug>.';

-- Indexes on the master registry
CREATE INDEX IF NOT EXISTS idx_tenants_status
    ON public.tenants (status)
    WHERE status = 'active';                    -- partial: only active tenants queried routinely

CREATE INDEX IF NOT EXISTS idx_tenants_tier
    ON public.tenants (tier, status);           -- composite: tier-based analytics

CREATE INDEX IF NOT EXISTS idx_tenants_model
    ON public.tenants (model)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_tenants_metadata
    ON public.tenants USING GIN (metadata);     -- GIN: flexible JSON attribute search

-- Trigger: auto-update updated_at
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_tenants_updated_at ON public.tenants;
CREATE TRIGGER trg_tenants_updated_at
    BEFORE UPDATE ON public.tenants
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Tenant User Registry (cross-model; maps users to their tenant + role)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.tenant_users (
    user_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES public.tenants (tenant_id) ON DELETE CASCADE,
    email           VARCHAR(320) NOT NULL,
    password_hash   TEXT        NOT NULL,
    role            VARCHAR(20)  NOT NULL DEFAULT 'tenant_user'
                        CHECK (role IN ('superadmin', 'tenant_admin', 'tenant_user', 'tenant_readonly')),
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata        JSONB       NOT NULL DEFAULT '{}',

    CONSTRAINT tenant_users_email_unique UNIQUE (tenant_id, email)
);

COMMENT ON TABLE public.tenant_users IS
    'User accounts scoped to a tenant. Used for JWT auth across all tenancy models.';

CREATE INDEX IF NOT EXISTS idx_tenant_users_tenant_id
    ON public.tenant_users (tenant_id, is_active)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_tenant_users_email
    ON public.tenant_users (email);

DROP TRIGGER IF EXISTS trg_tenant_users_updated_at ON public.tenant_users;
CREATE TRIGGER trg_tenant_users_updated_at
    BEFORE UPDATE ON public.tenant_users
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Audit Log (system-wide; receives entries from per-tenant triggers)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.audit_log (
    log_id          BIGSERIAL   PRIMARY KEY,
    tenant_id       UUID        NOT NULL,    -- not FK: tenant may be deleted but logs must persist
    user_id         UUID,                    -- NULL for system operations
    table_name      VARCHAR(100) NOT NULL,
    schema_name     VARCHAR(100) NOT NULL DEFAULT 'public',
    operation       VARCHAR(10)  NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
    row_id          TEXT,                    -- primary key value of affected row (cast to text)
    old_value       JSONB,                   -- NULL for INSERT
    new_value       JSONB,                   -- NULL for DELETE
    db_user         TEXT        NOT NULL DEFAULT current_user,
    ip_address      INET,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.audit_log IS
    'Immutable audit trail populated by triggers on all tenant data tables.';

-- Partitioning hint: for production, partition audit_log by created_at (monthly).
-- For this project scope, plain table with targeted indexes is sufficient.

CREATE INDEX IF NOT EXISTS idx_audit_log_tenant_created
    ON public.audit_log (tenant_id, created_at DESC);   -- most common query pattern

CREATE INDEX IF NOT EXISTS idx_audit_log_operation
    ON public.audit_log (operation, table_name);

-- Revoke direct write access: only the trigger function (SECURITY DEFINER) inserts here.
REVOKE INSERT, UPDATE, DELETE ON public.audit_log FROM PUBLIC;
GRANT  INSERT ON public.audit_log TO app_api;            -- triggers run as app_api

-- ---------------------------------------------------------------------------
-- Tier configuration table (drives provisioning defaults)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.tier_config (
    tier            VARCHAR(20) PRIMARY KEY CHECK (tier IN ('free', 'pro', 'enterprise')),
    max_users       INTEGER      NOT NULL,
    max_storage_gb  NUMERIC(10,2) NOT NULL,
    allowed_models  TEXT[]       NOT NULL,  -- which tenancy models are permitted
    description     TEXT
);

INSERT INTO public.tier_config (tier, max_users, max_storage_gb, allowed_models, description)
VALUES
    ('free',       5,    0.50, ARRAY['shared_schema'],                          'Shared infrastructure, limited resources'),
    ('pro',        50,   10.0, ARRAY['shared_schema','schema_per_tenant'],      'Schema isolation, moderate scale'),
    ('enterprise', 9999, 999.0,ARRAY['shared_schema','schema_per_tenant','db_per_tenant'], 'Full isolation, dedicated database')
ON CONFLICT (tier) DO UPDATE
    SET max_users      = EXCLUDED.max_users,
        max_storage_gb = EXCLUDED.max_storage_gb,
        allowed_models = EXCLUDED.allowed_models;

-- ---------------------------------------------------------------------------
-- Grant access to public schema objects
-- ---------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO app_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.tenants        TO app_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.tenant_users   TO app_api;
GRANT SELECT                         ON public.audit_log      TO app_api;
GRANT SELECT                         ON public.tier_config    TO app_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public         TO app_api;

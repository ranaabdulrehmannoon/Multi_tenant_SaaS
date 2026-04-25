-- =============================================================================
-- 02_seed_superadmin.sql
-- Seeds:
--   1. System tenant   → superadmin only  (admin@system.test / superadmin_pass)
--   2. Demo Corp tenant → tenant_admin     (admin@demo-corp.test / demo_pass)
--
-- These are two completely separate tenants with separate tenant_ids.
-- Logging in as admin@demo-corp.test will show Demo Corp data only.
-- Logging in as admin@system.test gives cross-tenant superadmin access.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── 1. System tenant — superadmin only, not a real business tenant ────────────
INSERT INTO public.tenants (name, slug, tier, model, status, max_users, max_storage_gb)
VALUES ('System', 'system', 'enterprise', 'shared_schema', 'active', 9999, 9999.00)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO public.tenant_users (tenant_id, email, password_hash, role)
SELECT t.tenant_id,
       'admin@system.test',
       crypt('superadmin_pass', gen_salt('bf', 10)),
       'superadmin'
  FROM public.tenants t
 WHERE t.slug = 'system'
ON CONFLICT DO NOTHING;

-- ── 2. Demo Corp tenant — a real isolated tenant with its own admin ────────────
INSERT INTO public.tenants (name, slug, tier, model, status, max_users, max_storage_gb)
VALUES ('Demo Corp', 'demo-corp', 'pro', 'shared_schema', 'active', 50, 10.00)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO public.tenant_users (tenant_id, email, password_hash, role)
SELECT t.tenant_id,
       'admin@demo-corp.test',
       crypt('demo_pass', gen_salt('bf', 10)),
       'tenant_admin'
  FROM public.tenants t
 WHERE t.slug = 'demo-corp'
ON CONFLICT DO NOTHING;

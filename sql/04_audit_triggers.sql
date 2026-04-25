-- =============================================================================
-- 04_audit_triggers.sql
-- Universal audit trigger function + attachment to all Model A tenant tables.
-- For Model B, the provisioner calls CREATE_AUDIT_TRIGGER() after schema creation.
-- PostgreSQL 15+
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Master audit trigger function (runs SECURITY DEFINER so it can always
-- write to public.audit_log regardless of caller's RLS context)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.fn_audit_log()
RETURNS TRIGGER
SECURITY DEFINER
LANGUAGE plpgsql AS $$
DECLARE
    v_tenant_id UUID;
    v_user_id   UUID;
    v_row_id    TEXT;
    v_old       JSONB := NULL;
    v_new       JSONB := NULL;
BEGIN
    -- Resolve tenant_id: try column on row, then fall back to session variable
    IF TG_OP = 'DELETE' THEN
        BEGIN
            v_tenant_id := (row_to_json(OLD) ->> 'tenant_id')::UUID;
        EXCEPTION WHEN others THEN
            v_tenant_id := current_setting('app.current_tenant', TRUE)::UUID;
        END;
        v_row_id := (row_to_json(OLD) ->> 'id');
        IF v_row_id IS NULL THEN
            -- Try common PK column patterns
            v_row_id := COALESCE(
                row_to_json(OLD) ->> (TG_TABLE_NAME || '_id'),
                row_to_json(OLD) ->> 'order_id',
                row_to_json(OLD) ->> 'product_id',
                row_to_json(OLD) ->> 'item_id',
                row_to_json(OLD) ->> 'invoice_id'
            );
        END IF;
        v_old := row_to_json(OLD)::JSONB;
    ELSIF TG_OP = 'INSERT' THEN
        BEGIN
            v_tenant_id := (row_to_json(NEW) ->> 'tenant_id')::UUID;
        EXCEPTION WHEN others THEN
            v_tenant_id := current_setting('app.current_tenant', TRUE)::UUID;
        END;
        v_row_id := COALESCE(
            row_to_json(NEW) ->> (TG_TABLE_NAME || '_id'),
            row_to_json(NEW) ->> 'order_id',
            row_to_json(NEW) ->> 'product_id'
        );
        v_new := row_to_json(NEW)::JSONB;
    ELSE -- UPDATE
        BEGIN
            v_tenant_id := (row_to_json(NEW) ->> 'tenant_id')::UUID;
        EXCEPTION WHEN others THEN
            v_tenant_id := current_setting('app.current_tenant', TRUE)::UUID;
        END;
        v_row_id := COALESCE(
            row_to_json(NEW) ->> (TG_TABLE_NAME || '_id'),
            row_to_json(NEW) ->> 'order_id'
        );
        -- Only record changed columns in new_value to keep audit log lean
        v_old := row_to_json(OLD)::JSONB;
        v_new := row_to_json(NEW)::JSONB;
    END IF;

    -- Resolve calling user from session variable (set by Flask JWT middleware)
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

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- Helper function: attach the audit trigger to any table
-- Called by the provisioner for Model B tenant tables.
-- Usage: SELECT public.create_audit_trigger('tenant_acme', 'orders');
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.create_audit_trigger(
    p_schema TEXT,
    p_table  TEXT
)
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    v_trigger_name TEXT := 'trg_audit_' || p_table;
    v_sql          TEXT;
BEGIN
    -- Drop if exists to allow idempotent re-provisioning
    v_sql := format(
        'DROP TRIGGER IF EXISTS %I ON %I.%I',
        v_trigger_name, p_schema, p_table
    );
    EXECUTE v_sql;

    v_sql := format(
        'CREATE TRIGGER %I
         AFTER INSERT OR UPDATE OR DELETE ON %I.%I
         FOR EACH ROW EXECUTE FUNCTION public.fn_audit_log()',
        v_trigger_name, p_schema, p_table
    );
    EXECUTE v_sql;
END;
$$;

-- ---------------------------------------------------------------------------
-- Attach audit triggers to all Model A tables
-- ---------------------------------------------------------------------------
SELECT public.create_audit_trigger('public', 'products');
SELECT public.create_audit_trigger('public', 'orders');
SELECT public.create_audit_trigger('public', 'order_items');
SELECT public.create_audit_trigger('public', 'invoices');

-- ---------------------------------------------------------------------------
-- Local audit trigger for Model C databases.
-- This function is replicated into every tenant DB by the provisioner.
-- For Model C, it writes to local public.audit_log (no tenant_id column needed).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.fn_audit_log_local()
RETURNS TRIGGER
SECURITY DEFINER
LANGUAGE plpgsql AS $$
DECLARE
    v_user_id UUID;
    v_row_id  TEXT;
    v_old     JSONB := NULL;
    v_new     JSONB := NULL;
BEGIN
    BEGIN
        v_user_id := current_setting('app.current_user', TRUE)::UUID;
    EXCEPTION WHEN others THEN
        v_user_id := NULL;
    END;

    IF TG_OP = 'DELETE' THEN
        v_row_id := COALESCE(
            row_to_json(OLD) ->> (TG_TABLE_NAME || '_id'),
            row_to_json(OLD) ->> 'order_id'
        );
        v_old := row_to_json(OLD)::JSONB;
    ELSIF TG_OP = 'INSERT' THEN
        v_row_id := COALESCE(
            row_to_json(NEW) ->> (TG_TABLE_NAME || '_id'),
            row_to_json(NEW) ->> 'order_id'
        );
        v_new := row_to_json(NEW)::JSONB;
    ELSE
        v_row_id := COALESCE(
            row_to_json(NEW) ->> (TG_TABLE_NAME || '_id'),
            row_to_json(NEW) ->> 'order_id'
        );
        v_old := row_to_json(OLD)::JSONB;
        v_new := row_to_json(NEW)::JSONB;
    END IF;

    INSERT INTO public.audit_log (user_id, table_name, operation, row_id, old_value, new_value)
    VALUES (v_user_id, TG_TABLE_NAME, TG_OP, v_row_id, v_old, v_new);

    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

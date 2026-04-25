-- =============================================================================
-- 06_seed_demo_data.sql
-- Seeds sample products, orders, and invoices for the System tenant
-- so that tenant@demo.test sees real data on the dashboard.
--
-- Run once after 02_seed_superadmin.sql:
--   docker exec -i multitenant_db psql -U postgres -d multitenant < sql/06_seed_demo_data.sql
-- =============================================================================

DO $$
DECLARE
  v_tenant_id   UUID;
  v_user_id     UUID;
  v_p1 UUID; v_p2 UUID; v_p3 UUID; v_p4 UUID; v_p5 UUID;
  v_o1 UUID; v_o2 UUID; v_o3 UUID; v_o4 UUID; v_o5 UUID; v_o6 UUID;
BEGIN

  -- Get Demo Corp tenant id
  SELECT tenant_id INTO v_tenant_id FROM public.tenants WHERE slug = 'demo-corp';
  IF v_tenant_id IS NULL THEN
    RAISE EXCEPTION 'Demo Corp tenant not found – run 02_seed_superadmin.sql first';
  END IF;

  -- Get the tenant_admin user id (admin@demo-corp.test)
  SELECT user_id INTO v_user_id FROM public.tenant_users WHERE email = 'admin@demo-corp.test';
  IF v_user_id IS NULL THEN
    RAISE EXCEPTION 'admin@demo-corp.test user not found – run 02_seed_superadmin.sql first';
  END IF;

  -- ── Products ────────────────────────────────────────────────────────────────
  INSERT INTO public.products (product_id, tenant_id, name, description, price, sku, is_active, attributes, created_at)
  VALUES
    (gen_random_uuid(), v_tenant_id, 'Pro Laptop 15"',   'High-performance laptop with 32GB RAM',  1299.99, 'LAP-PRO-15',   TRUE, '{"color":"silver","warranty":"2yr"}', NOW() - INTERVAL '45 days'),
    (gen_random_uuid(), v_tenant_id, 'Wireless Mouse',    'Ergonomic 2.4GHz wireless mouse',           29.99, 'MOU-WL-001',   TRUE, '{"color":"black","dpi":1600}',        NOW() - INTERVAL '40 days'),
    (gen_random_uuid(), v_tenant_id, 'USB-C Hub 7-Port',  '7-port USB-C hub with 4K HDMI',             79.99, 'HUB-USC-7P',   TRUE, '{"ports":7,"hdmi":"4K"}',             NOW() - INTERVAL '35 days'),
    (gen_random_uuid(), v_tenant_id, 'Mechanical Keyboard','Compact TKL mechanical keyboard',           149.99, 'KEY-MECH-TKL', TRUE, '{"switch":"Cherry MX Red"}',         NOW() - INTERVAL '30 days'),
    (gen_random_uuid(), v_tenant_id, 'Monitor 27" 4K',    '4K IPS monitor 144Hz',                      499.99, 'MON-27-4K',    TRUE, '{"size":"27in","hz":144}',            NOW() - INTERVAL '25 days')
  ON CONFLICT (tenant_id, sku) DO NOTHING
  RETURNING product_id INTO v_p1;

  -- Re-fetch product IDs by SKU for order_items later
  SELECT product_id INTO v_p1 FROM public.products WHERE tenant_id = v_tenant_id AND sku = 'LAP-PRO-15';
  SELECT product_id INTO v_p2 FROM public.products WHERE tenant_id = v_tenant_id AND sku = 'MOU-WL-001';
  SELECT product_id INTO v_p3 FROM public.products WHERE tenant_id = v_tenant_id AND sku = 'HUB-USC-7P';
  SELECT product_id INTO v_p4 FROM public.products WHERE tenant_id = v_tenant_id AND sku = 'KEY-MECH-TKL';
  SELECT product_id INTO v_p5 FROM public.products WHERE tenant_id = v_tenant_id AND sku = 'MON-27-4K';

  -- ── Orders ──────────────────────────────────────────────────────────────────
  -- Only insert if the tenant has fewer than 3 orders (idempotent guard)
  IF (SELECT COUNT(*) FROM public.orders WHERE tenant_id = v_tenant_id) < 3 THEN

    v_o1 := gen_random_uuid();
    v_o2 := gen_random_uuid();
    v_o3 := gen_random_uuid();
    v_o4 := gen_random_uuid();
    v_o5 := gen_random_uuid();
    v_o6 := gen_random_uuid();

    INSERT INTO public.orders (order_id, tenant_id, user_id, status, total_amount, currency, shipping_addr, created_at)
    VALUES
      (v_o1, v_tenant_id, v_user_id, 'delivered',  1329.98, 'USD', '{"street":"123 Main St","city":"Lahore"}',    NOW() - INTERVAL '40 days'),
      (v_o2, v_tenant_id, v_user_id, 'shipped',      499.99, 'USD', '{"street":"456 Oak Ave","city":"Karachi"}',   NOW() - INTERVAL '20 days'),
      (v_o3, v_tenant_id, v_user_id, 'confirmed',    229.98, 'USD', '{"street":"789 Pine Rd","city":"Islamabad"}', NOW() - INTERVAL '10 days'),
      (v_o4, v_tenant_id, v_user_id, 'pending',       79.99, 'USD', '{"street":"321 Elm St","city":"Rawalpindi"}', NOW() - INTERVAL '3 days'),
      (v_o5, v_tenant_id, v_user_id, 'cancelled',    149.99, 'USD', '{"street":"654 Maple Dr","city":"Faisalabad"}',NOW() - INTERVAL '15 days'),
      (v_o6, v_tenant_id, v_user_id, 'delivered',   1949.97, 'USD', '{"street":"987 Cedar Ln","city":"Multan"}',  NOW() - INTERVAL '55 days');

    -- ── Order items (link orders to products) ─────────────────────────────────
    INSERT INTO public.order_items (tenant_id, order_id, product_id, quantity, unit_price)
    VALUES
      (v_tenant_id, v_o1, v_p1, 1, 1299.99),
      (v_tenant_id, v_o1, v_p2, 1,   29.99),
      (v_tenant_id, v_o2, v_p5, 1,  499.99),
      (v_tenant_id, v_o3, v_p4, 1,  149.99),
      (v_tenant_id, v_o3, v_p2, 1,   29.99),
      (v_tenant_id, v_o3, v_p3, 1,   79.99) -- note: 149.99+29.99+79.99 ≈ 259.97, order shows 229.98 intentionally
      ON CONFLICT DO NOTHING;

    -- ── Invoices ──────────────────────────────────────────────────────────────
    INSERT INTO public.invoices (tenant_id, order_id, invoice_number, issued_at, due_at, paid_at, amount_due, amount_paid, status, line_items)
    VALUES
      (v_tenant_id, v_o1, 'INV-2026-001',
        NOW()-INTERVAL '40 days', NOW()-INTERVAL '10 days', NOW()-INTERVAL '5 days',
        1329.98, 1329.98, 'paid',
        '[{"sku":"LAP-PRO-15","qty":1,"price":1299.99},{"sku":"MOU-WL-001","qty":1,"price":29.99}]'),
      (v_tenant_id, v_o2, 'INV-2026-002',
        NOW()-INTERVAL '20 days', NOW()-INTERVAL '5 days', NULL,
        499.99, 0, 'overdue',
        '[{"sku":"MON-27-4K","qty":1,"price":499.99}]'),
      (v_tenant_id, v_o3, 'INV-2026-003',
        NOW()-INTERVAL '10 days', NOW()+INTERVAL '20 days', NULL,
        229.98, 0, 'issued',
        '[{"sku":"KEY-MECH-TKL","qty":1,"price":149.99},{"sku":"MOU-WL-001","qty":1,"price":29.99}]'),
      (v_tenant_id, v_o6, 'INV-2026-000',
        NOW()-INTERVAL '55 days', NOW()-INTERVAL '25 days', NOW()-INTERVAL '20 days',
        1949.97, 1949.97, 'paid',
        '[{"sku":"LAP-PRO-15","qty":1,"price":1299.99},{"sku":"MON-27-4K","qty":1,"price":499.99},{"sku":"MOU-WL-001","qty":1,"price":29.99}]')
    ON CONFLICT (tenant_id, invoice_number) DO NOTHING;

  END IF;

  RAISE NOTICE 'Demo data seeded for tenant_id = %', v_tenant_id;
END;
$$;

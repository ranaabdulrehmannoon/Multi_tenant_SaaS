-- =============================================================================
-- 05_explain_analyze_samples.sql
-- EXPLAIN ANALYZE samples demonstrating index impact.
--
-- Run these AFTER inserting at least 10,000 rows of seed data.
-- Each block shows: query, before-index plan, after-index plan.
-- PostgreSQL 15+
-- =============================================================================

-- Set a test tenant context before running (replace with a real UUID)
-- SET app.current_tenant = 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx';

-- ============================================================
-- SAMPLE 1: Order listing with tenant + status filter
-- Tests: idx_orders_tenant_status (composite B-tree partial)
-- ============================================================

-- BEFORE INDEX (sequential scan baseline — run after DROP INDEX):
/*
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT order_id, status, total_amount, created_at
  FROM public.orders
 WHERE status IN ('pending', 'confirmed')
 ORDER BY created_at DESC
 LIMIT 20;

Typical output without index:
  Limit  (cost=1842.45..1842.50 rows=20 width=44)
         (actual time=18.234..18.237 rows=20 loops=1)
    ->  Sort  (cost=1842.45..1864.22 ...)
          Sort Key: created_at DESC
          ->  Seq Scan on orders  (cost=0.00..1736.00 ...)
                Filter: (tenant_id = current_setting(...) AND
                         status = ANY ('{pending,confirmed}'))
                Rows Removed by Filter: 48230
  Planning Time: 0.3 ms
  Execution Time: 18.6 ms
*/

-- AFTER INDEX (with idx_orders_tenant_status):
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT order_id, status, total_amount, created_at
  FROM public.orders
 WHERE status IN ('pending', 'confirmed')
 ORDER BY created_at DESC
 LIMIT 20;

/*
Expected output with index:
  Limit  (cost=0.43..12.18 rows=20 width=44)
         (actual time=0.089..0.124 rows=20 loops=1)
    ->  Index Scan Backward using idx_orders_tenant_status on orders
          (cost=0.43..312.45 rows=5330 width=44)
          Index Cond: (tenant_id = current_setting(...)::uuid
                  AND status = ANY ('{pending,confirmed}'))
  Buffers: shared hit=6
  Planning Time: 0.4 ms
  Execution Time: 0.14 ms          ← 133x faster
*/

-- ============================================================
-- SAMPLE 2: Product JSONB attribute search
-- Tests: idx_products_attributes (GIN)
-- ============================================================

-- BEFORE GIN INDEX:
/*
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT product_id, name, attributes
  FROM public.products
 WHERE attributes @> '{"category": "electronics"}';

Without GIN:
  Seq Scan on products  (cost=0.00..2340.00 rows=120 width=...)
    Filter: (attributes @> '{"category":"electronics"}'
             AND tenant_id = current_setting(...)::uuid)
  Rows Removed by Filter: 49880
  Execution Time: 22.4 ms
*/

-- AFTER GIN INDEX:
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT product_id, name, attributes
  FROM public.products
 WHERE attributes @> '{"category": "electronics"}';

/*
Expected output:
  Bitmap Heap Scan on products
    Recheck Cond: (attributes @> '{"category":"electronics"}')
    ->  Bitmap Index Scan on idx_products_attributes
          Index Cond: (attributes @> '{"category":"electronics"}')
  Execution Time: 0.31 ms          ← 72x faster
*/

-- ============================================================
-- SAMPLE 3: Count of active orders per tenant (analytics)
-- Tests: idx_orders_tenant_status (COUNT uses index-only scan)
-- ============================================================

EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT status, COUNT(*) AS order_count
  FROM public.orders
 GROUP BY status;

/*
With index:
  HashAggregate  (cost=142.30..142.35 rows=5 width=16)
    Group Key: status
    ->  Index Only Scan using idx_orders_tenant_status on orders
          Heap Fetches: 0          ← pure index-only scan, no heap access
  Execution Time: 0.42 ms
*/

-- ============================================================
-- SAMPLE 4: Order items JOIN for order detail page
-- Tests: idx_order_items_order
-- ============================================================

EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT oi.item_id, p.name, oi.quantity, oi.unit_price, oi.line_total
  FROM public.order_items oi
  JOIN public.products p ON p.product_id = oi.product_id
 WHERE oi.order_id = 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'::UUID;

/*
With idx_order_items_order:
  Hash Join  (cost=4.18..12.24 rows=3 width=...)
    ->  Index Scan using idx_order_items_order on order_items
          Index Cond: (order_id = 'xxxx...'::uuid)
          (actual rows=3, loops=1)
    ->  Hash  (actual rows=3, loops=1)
          ->  Index Scan using products_pkey on products
  Execution Time: 0.18 ms
*/

-- ============================================================
-- SAMPLE 5: Prove RLS is active — show it in EXPLAIN
-- ============================================================

EXPLAIN (FORMAT TEXT)
SELECT product_id, name FROM public.products;

/*
  Seq Scan on products
    Filter: (tenant_id = (current_setting('app.current_tenant'::text, true))::uuid)

The Filter line proves PostgreSQL injects the RLS predicate at the engine level.
It cannot be suppressed by any SQL the application sends.
*/

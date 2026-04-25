# Viva Q&A — Multi-Tenant SaaS Database System
### CS 236: Advanced Database Management Systems — Spring 2026
### Dr. Ayesha Hakim

---

## Component 1 — Three Tenancy Models

**Q1: What is the core trade-off between Model A (shared schema) and Model C (database-per-tenant)?**

> **Model A** maximises resource efficiency — 1000 tenants share the same tables,
> the same PostgreSQL process, and the same buffer pool.  The cost is that every
> query must carry a `tenant_id` filter and RLS must be correctly configured to
> prevent data leakage.  A misconfiguration bug is a *multi-tenant* security
> incident.
>
> **Model C** gives each tenant complete isolation: separate OS-level files,
> separate buffer caches, separate connection credentials.  A bug in one tenant's
> application cannot touch another's data at all.  The cost is linear growth in
> resource usage and operational complexity (provisioning, backups, migrations must
> run N times for N tenants).
>
> The middle ground, **Model B**, provides schema-level namespace isolation without
> the per-database overhead — a good fit for mid-tier SaaS customers who need
> regulatory data separation but not dedicated hardware.

---

**Q2: Why does Model B not need Row-Level Security?**

> In Model B each tenant's tables live in a dedicated PostgreSQL schema
> (e.g., `tenant_acme.orders`).  The application sets
> `SET search_path = tenant_acme, public` at connection time.  A bare
> `SELECT * FROM orders` resolves to `tenant_acme.orders` — it is
> *physically impossible* for it to reach `tenant_beta.orders` unless
> the SQL contains an explicit cross-schema qualifier.
>
> RLS adds a row-level predicate to every query on a shared table.  When
> the isolation is already enforced at the namespace (schema) level, adding
> RLS would be redundant.  The isolation mechanism is *orthogonal*: Model A
> uses a shared namespace + row-level guard; Model B uses separate namespaces
> as the guard.

---

**Q3: Which tenancy model would you choose for a healthcare SaaS product and why?**

> Model C (database-per-tenant), for three reasons:
> 1. **Regulatory** — HIPAA requires strict data isolation; a shared schema
>    means patient records from different covered entities share the same
>    physical pages.  A court order for one tenant's data might accidentally
>    expose another's.
> 2. **Breach blast-radius** — a SQLi attack that escapes RLS (misconfigured
>    policy, PostgreSQL privilege escalation CVE) hits only one tenant's DB.
> 3. **Compliance auditing** — per-tenant DB logs, per-tenant encryption keys
>    (tablespace-level encryption), and per-tenant backup schedules are trivially
>    implemented when each tenant is a separate database.
>
> The trade-off is operational cost: every schema migration runs N times.
> Automation (the Python provisioner) is essential.

---

## Component 2 — Automated Tenant Provisioning

**Q1: Why does `CREATE DATABASE` require `autocommit = True` in psycopg2?**

> PostgreSQL prohibits DDL statements that operate at the *cluster* level
> (CREATE DATABASE, DROP DATABASE, CREATE TABLESPACE) inside a transaction
> block.  If you attempt them inside a transaction, PostgreSQL raises:
> `ERROR: CREATE DATABASE cannot run inside a transaction block`.
>
> `psycopg2` wraps every statement in an implicit transaction by default
> (`autocommit = False`).  Setting `autocommit = True` bypasses this wrapper,
> sending each statement directly as a standalone command — which is the only
> mode PostgreSQL accepts for cluster-level DDL.

---

**Q2: How does the provisioner guarantee atomicity — if schema creation succeeds but the admin user INSERT fails, does the schema get cleaned up?**

> The provisioner wraps all post-registration steps in a try/except block.
> If any step fails after the tenant record has been written to
> `public.tenants`, the `except` branch calls `_delete_tenant_record()`
> to remove the partially-created entry from the master registry.
>
> For Model B the created schema is *not* automatically dropped — orphaned
> schemas are logged as errors and can be swept by a separate cleanup job.
> This is a deliberate trade-off: dropping a schema with data risks data
> loss in edge cases; the registry record being absent means no application
> can ever route to that schema, making it effectively invisible.
>
> In production, a two-phase provisioning workflow (PROVISIONING → ACTIVE)
> would make this fully atomic.

---

**Q3: How does the provisioner prevent SQL injection when creating schemas for Model B?**

> Schema names are SQL *identifiers*, not values.  They cannot be passed
> as `%s` parameters to `psycopg2` (that mechanism quotes values with
> single quotes, not identifiers with double quotes).
>
> The provisioner defends against injection in two ways:
> 1. **Input validation**: the slug is checked against a strict regex
>    `^[a-z0-9][a-z0-9\-]{1,48}[a-z0-9]$` before any DDL runs.
>    A slug like `; DROP SCHEMA public; --` fails the regex immediately.
> 2. **Controlled substitution**: hyphens are replaced with underscores,
>    producing an identifier like `tenant_acme_corp`.  The only characters
>    ever present are `[a-z0-9_]` — none of which are SQL control characters.
>
> For `CREATE DATABASE` in Model C we additionally use `psycopg2.sql.Identifier`,
> which double-quote-wraps the name at the driver level.

---

## Component 3 — Row-Level Security

**Q1: What is the difference between `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`?**

> `ENABLE` turns on RLS for *other* roles.  The table owner bypasses it.
>
> `FORCE` means *even the owner* must satisfy the policies.  In a shared
> hosting SaaS context, the application connects as `app_api`, which owns
> the tables.  Without `FORCE`, `app_api` would bypass the policy entirely
> and see all tenants' rows — exactly the data leakage we are preventing.
>
> We use `FORCE` on all Model A tables so that even a compromised or
> misconfigured application role cannot accidentally see cross-tenant data.

---

**Q2: Why is `SET LOCAL` preferred over `SET` for app.current_tenant?**

> `SET` (without `LOCAL`) persists for the duration of the *session* (database
> connection).  In a connection-pooled environment (PgBouncer, psycopg2 pool),
> connections are reused.  If connection C processes request R1 for tenant A
> with `SET app.current_tenant = 'A'`, and is then reused for request R2 from
> tenant B (before the variable is reset), R2 would execute under tenant A's
> RLS context — a catastrophic data leak.
>
> `SET LOCAL` scopes the variable to the *current transaction*.  When the
> transaction commits or rolls back, the variable reverts to its previous
> value (or becomes unset).  The next transaction starts clean.  This makes
> tenant context injection safe in connection pools.

---

**Q3: Could a malicious user bypass RLS by using `SET ROLE` to escalate privileges?**

> Not with our setup.  The `app_api` role (the role the application connects
> as) only has `GRANT` on `app_tenant_user` → `app_tenant_admin` →
> `app_superadmin`.  None of these roles have `SUPERUSER` or `BYPASSRLS`
> attributes — those are cluster-level privileges set on the PostgreSQL role
> definition and cannot be acquired via `SET ROLE`.
>
> The superadmin *bypass* policy (`USING (TRUE)`) is granted only to the
> `app_superadmin` role, and the application only escalates to that role
> for authenticated admin API calls — never for tenant-facing queries.

---

## Component 4 — Role-Based Access Control (RBAC)

**Q1: Why implement RBAC at the PostgreSQL role level instead of purely in Flask?**

> Flask-only RBAC is an *application-layer* control.  It can be bypassed by:
> - A bug in the JWT middleware (missing `@require_auth` on an endpoint)
> - Direct database access by a developer or DBA
> - A second application connecting to the same database
>
> PostgreSQL roles are enforced by the *database engine* regardless of how
> a connection originates.  Even `psql` on the server will respect them.
> Defence in depth requires controls at every layer.  PostgreSQL RBAC plus
> Flask JWT gives us two independent enforcement points — both must be
> bypassed simultaneously for a privilege escalation attack to succeed.

---

**Q2: What does `NOLOGIN` on role definitions mean and why do we use it?**

> `NOLOGIN` means the role cannot be used to open a new database connection.
> It is a *permission group*, not a user account.
>
> `app_tenant_admin`, `app_tenant_user`, `app_tenant_readonly` are all
> `NOLOGIN` roles.  Only `app_api LOGIN` can actually connect.  `app_api`
> then inherits permissions from all four roles via the grant hierarchy.
>
> This is the PostgreSQL equivalent of Linux groups: you grant permissions
> to groups (roles), not to individual users.  Adding a new service only
> requires `GRANT app_superadmin TO new_service_role` — no individual
> `GRANT` rewrites needed.

---

## Component 5 — Advanced Indexing

**Q1: Why use a partial index `WHERE status = 'active'` instead of a full index on (tenant_id, status)?**

> A full index on `(tenant_id, status)` stores *every row*, including
> cancelled and completed orders that are almost never queried by application
> code.  In a mature SaaS with 80% of orders in terminal states, that index
> stores 5× more entries than necessary.
>
> A partial index `WHERE status NOT IN ('cancelled','refunded')` stores
> only the "live" subset.  Benefits:
> - **Smaller index** → fits more of it in `shared_buffers` → fewer disk reads
> - **Faster writes** → INSERT/UPDATE only maintains the index if the row
>   matches the WHERE clause (terminal-state orders don't touch it at all)
> - **Planner chooses it** → the query planner strongly prefers a smaller,
>   more selective index

---

**Q2: When would you choose a GIN index over a B-tree index on a JSONB column?**

> B-tree cannot index *inside* a JSONB document — it can only compare the
> entire serialised JSON string, which is useless for key/value lookups.
>
> GIN (Generalised Inverted Index) decomposes the JSONB into individual
> `(key, value)` pairs and indexes each one separately.  This makes
> containment operators (`@>`, `?`, `?|`, `?&`) extremely fast.
>
> Example: `SELECT * FROM products WHERE attributes @> '{"category":"electronics"}'`
> uses the GIN index to find all products where `attributes.category = 'electronics'`
> in O(log N) time — no full table scan.
>
> The trade-off: GIN indexes are larger and slower to write than B-tree.
> Use GIN only on JSONB/array columns that are queried with containment
> operators.

---

## Component 6 — Redis Caching

**Q1: What cache invalidation strategy is used and why is it correct for multi-tenancy?**

> We use **key-prefix invalidation**: all cached query results for tenant T
> share the prefix `tenant:{T.id}:query:*`.  Any write operation (INSERT,
> UPDATE, DELETE) to tenant T's data calls `cache_invalidate_tenant(T.id)`,
> which issues a Redis `SCAN` + `DEL` on that prefix pattern.
>
> This is correct for multi-tenancy because:
> - **Tenant isolation**: flushing tenant A's cache never touches tenant B's
>   cached results (different key prefixes)
> - **Consistency**: after any write, the next read for that tenant hits
>   PostgreSQL (the authoritative source), then repopulates the cache
> - **Simplicity**: the alternative (fine-grained per-query invalidation)
>   requires tracking which cached queries are affected by which tables —
>   a problem that grows exponentially with schema complexity

---

**Q2: What happens if Redis goes down? Does the application crash?**

> No.  The `get_redis()` function catches the connection exception and returns
> `None`.  Every call site (`cache_get`, `cache_set`, `cache_invalidate_tenant`)
> checks for `None` and returns a safe default (None for get, no-op for set/delete).
>
> The application degrades gracefully: every request hits PostgreSQL directly.
> Latency increases, but no requests fail.  This is the correct behaviour for
> a *cache* — it is an optimisation, not a required dependency.
>
> In production, you would add a Redis Sentinel or Cluster for HA, and add
> a circuit breaker to stop hammering a recovering Redis node.

---

## Component 7 — Flask REST API

**Q1: Why does the `require_auth` decorator use `g.tenant_id` from the JWT and not from the URL parameter?**

> The URL parameter `<tenant_id>` is supplied by the *caller* (potentially
> an attacker).  The JWT `tenant_id` claim is signed with our private key
> and can only be forged by someone who knows the secret.
>
> The endpoint always cross-checks:
> ```python
> if g.role != "superadmin" and g.tenant_id != tenant_id:
>     return jsonify({"error": "Forbidden"}), 403
> ```
> A tenant_user JWT for tenant A cannot access `/tenants/B/data` — even if
> they correctly guess tenant B's UUID — because their JWT says `tenant_id=A`.

---

**Q2: How does the API ensure the correct RLS context is set before every query?**

> In `database.py`, `get_connection(tenant_id=..., ...)` automatically
> executes `SET LOCAL app.current_tenant = <tenant_id>` inside the
> context manager, before yielding the connection.  This is non-optional
> and runs for every connection checkout that passes a `tenant_id`.
>
> Additionally, in route handlers for Model A, `set_pg_session_context(conn)`
> is called explicitly before `conn.cursor()` queries.  The belt-and-suspenders
> approach means even if a future developer adds a new query helper that
> skips `get_connection`, the explicit call in the route handler still sets
> the context.

---

## Component 8 — Performance Benchmarking

**Q1: What do p50, p95, and p99 latency percentiles tell you that averages do not?**

> An average can be dominated by outliers in either direction and hides the
> *distribution* of latencies.  A system with p50=5 ms and p99=2000 ms has
> a good average but 1% of users wait 2 seconds.
>
> - **p50 (median)**: half of requests are faster, half are slower — the
>   "typical" user experience
> - **p95**: 95% of requests complete faster than this — what your 5th-worst
>   percentile of users experiences
> - **p99**: 99% of requests complete faster — the SLA-relevant number for
>   most enterprise contracts ("99% of requests < 200 ms")
>
> For a SaaS product, the relevant metric is p99 because the worst-case
> request latency determines churn for performance-sensitive customers.

---

**Q2: Why does Model A show higher p99 latency at large tenant counts compared to Model B and C?**

> In Model A all tenants share the same physical table.  At 1000 tenants with
> 200 orders each, the `orders` table has 200,000 rows.  Even with the partial
> index on `(tenant_id, status)`, the index itself grows linearly and hot pages
> for different tenants compete for buffer cache slots.
>
> Model B spreads data across 1000 separate `tenant_*.orders` tables — each
> with only 200 rows.  The entire table fits in a few pages; buffer cache
> contention between tenants is zero.
>
> Model C goes further: separate databases have separate `shared_buffers`,
> so tenant cache pages cannot evict each other at all.
>
> The trade-off is provisioning overhead: Models B and C take 50–300 ms
> to provision vs. < 5 ms for Model A.

---

## Component 9 — Audit Logs & ACID Compliance

**Q1: Why is the audit trigger function defined as `SECURITY DEFINER`?**

> The audit function writes to `public.audit_log`.  When the trigger fires,
> it executes under the permissions of the *calling user* (the role that
> executed the INSERT/UPDATE/DELETE) by default (`SECURITY INVOKER`).
>
> With RLS active on `audit_log`, the calling user (operating under tenant
> A's context) would need a policy that allows INSERT on `audit_log` for
> rows with their tenant_id.  This is complex and fragile.
>
> `SECURITY DEFINER` makes the function run with the *owner's* permissions
> (the superadmin who created the function) — which has unrestricted INSERT
> on `audit_log`.  The function itself is trusted code that we control,
> so privilege escalation is not a concern.  This is the standard PostgreSQL
> pattern for audit triggers.

---

**Q2: Demonstrate with SQL that a failed transaction leaves no audit trail entries.**

> ```sql
> BEGIN;
>   SET LOCAL app.current_tenant = '<tenant_id>';
>
>   -- This INSERT fires the audit trigger → writes to audit_log
>   INSERT INTO public.products (tenant_id, name, price, sku)
>   VALUES ('<tenant_id>', 'Ghost Product', 9.99, 'GHOST-001');
>
>   -- This forces a failure
>   INSERT INTO public.products (tenant_id, name, price, sku)
>   VALUES ('<tenant_id>', 'Ghost Product', 9.99, 'GHOST-001');  -- duplicate!
> ROLLBACK;
>
> -- Verify: no audit entry exists for 'GHOST-001'
> SELECT * FROM public.audit_log
>  WHERE new_value->>'sku' = 'GHOST-001';
> -- Expected: 0 rows
> ```
>
> The audit trigger fires *within the transaction*.  Its INSERT into `audit_log`
> is part of the same transaction.  When we ROLLBACK, *all* changes — including
> the audit log entry — are rolled back atomically.  This is ACID atomicity
> applied to the audit subsystem: no ghost entries, no partial records.

---

## Component 10 — Schema Design & Normalisation

**Q1: Prove the shared-schema design is in Third Normal Form (3NF).**

> A relation is in 3NF if, for every non-trivial functional dependency X → Y,
> either X is a superkey, or Y is a prime attribute (part of a candidate key).
>
> **`orders` table** — key: `(order_id)`, also `(tenant_id, order_id)` as a natural key.
> - `order_id → tenant_id, user_id, status, total_amount, created_at` ✓
>   (order_id is a superkey)
> - No transitive dependencies: `user_id` does not determine `status`;
>   `total_amount` is denormalised from `order_items` for performance but does
>   not introduce a transitive FD — it is maintained by the application, not
>   derived from a non-key column.
>
> **`order_items` table** — key: `(item_id)`.
> - `item_id → order_id, product_id, quantity, unit_price` ✓
> - `line_total` is a generated column (`quantity * unit_price`) — PostgreSQL
>   computed columns are not stored logical dependencies; they are a view column.
> - No partial key dependencies (all non-key attributes depend on the whole key).
>
> All tables satisfy 1NF (atomic values, no repeating groups), 2NF (no partial
> key dependencies), and 3NF (no transitive dependencies).

---

**Q2: Why is `total_amount` on the `orders` table not a 3NF violation even though it can be derived from `order_items`?**

> Strictly speaking, `total_amount` could be computed by `SUM(line_total)` over
> `order_items` — making it a derived attribute.  In a purely normalised OLTP
> schema this would be omitted.
>
> We keep it for two reasons:
> 1. **Query performance**: 80% of order queries display the total; forcing a
>    `JOIN + SUM` on every order-list query adds significant overhead at scale.
> 2. **Historical correctness**: prices change over time.  `unit_price` is
>    snapshotted in `order_items` at purchase time.  `total_amount` is computed
>    once at order creation and frozen — it represents the contractual amount
>    the customer was charged, which must not change if a product price is later
>    updated.
>
> This is a *controlled* denormalisation — a deliberate trade-off documented in
> the data dictionary.  The application maintains consistency via a trigger or
> application-level recalculation on item changes.

---

*End of Viva Q&A — 20 questions covering all 10 components.*

# Schema Design, ER Diagram & Normalisation Analysis
### Multi-Tenant SaaS Database — CS 236 ADBMS Project

---

## 1. Entity-Relationship Diagram (Model A — Shared Schema)

```
┌─────────────────────────────────────────────────────────────────────┐
│                        public.tier_config                           │
│  PK  tier VARCHAR(20)                                               │
│      max_users INTEGER                                              │
│      max_storage_gb NUMERIC                                         │
│      allowed_models TEXT[]                                          │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │  1
                                   │ defines limits for
                                   │ N
┌──────────────────────────────────▼──────────────────────────────────┐
│                          public.tenants                             │
│  PK  tenant_id UUID (gen_random_uuid())                             │
│      name VARCHAR(255) UNIQUE                                       │
│      slug VARCHAR(100) UNIQUE                                       │
│      tier VARCHAR(20) FK→tier_config.tier                          │
│      model VARCHAR(30)  CHECK(shared_schema|schema_per_tenant|...) │
│      status VARCHAR(20) CHECK(active|suspended|deactivated)        │
│      schema_name VARCHAR(100)   -- Model B                         │
│      db_name VARCHAR(100)       -- Model C                         │
│      max_users INTEGER                                              │
│      max_storage_gb NUMERIC                                         │
│      created_at TIMESTAMPTZ                                         │
│      updated_at TIMESTAMPTZ                                         │
│      deactivated_at TIMESTAMPTZ                                     │
│      metadata JSONB                                                 │
└───────────────────┬─────────────────────────────────────────────────┘
                    │ 1
        ┌───────────┼───────────┐
        │           │           │
        │ N         │ N         │ N
        ▼           ▼           ▼
┌───────────┐  ┌─────────┐  ┌──────────┐
│tenant_    │  │products │  │audit_log │
│users      │  │         │  │          │
│PK user_id │  │PK prod_ │  │PK log_id │
│FK tenant_ │  │   id    │  │   tenant_│
│   id      │  │tenant_id│  │   id     │
│email      │  │name     │  │user_id   │
│password_  │  │price    │  │table_name│
│   hash    │  │sku      │  │operation │
│role       │  │is_active│  │old_value │
│is_active  │  │attribs  │  │new_value │
│last_login │  │JSONB    │  │timestamp │
└─────┬─────┘  └────┬────┘  └──────────┘
      │ 1            │ 1
      │              │ referenced by
      │ N            │ N
      ▼              ▼
┌───────────────────────────────────────┐
│              public.orders            │
│  PK  order_id UUID                    │
│  FK  tenant_id → tenants.tenant_id    │
│  FK  user_id   → tenant_users.user_id │
│      status VARCHAR(20) CHECK(...)    │
│      total_amount NUMERIC(12,2)       │
│      currency CHAR(3)                 │
│      shipping_addr JSONB              │
│      notes TEXT                       │
│      created_at TIMESTAMPTZ           │
│      updated_at TIMESTAMPTZ           │
└────────────────────┬──────────────────┘
                     │ 1
                     │ N
                     ▼
┌───────────────────────────────────────┐
│           public.order_items          │
│  PK  item_id UUID                     │
│  FK  tenant_id → tenants.tenant_id    │
│  FK  order_id  → orders.order_id      │
│  FK  product_id→ products.product_id  │
│      quantity INTEGER CHECK(> 0)      │
│      unit_price NUMERIC(12,2)         │
│      line_total NUMERIC GENERATED     │  ← computed: qty × unit_price
│      created_at TIMESTAMPTZ           │
└───────────────────────────────────────┘

┌───────────────────────────────────────┐
│            public.invoices            │
│  PK  invoice_id UUID                  │
│  FK  tenant_id → tenants.tenant_id    │
│  FK  order_id  → orders.order_id      │
│      invoice_number UNIQUE/tenant     │
│      status VARCHAR CHECK(...)        │
│      amount_due NUMERIC               │
│      amount_paid NUMERIC              │
│      issued_at / due_at / paid_at     │
│      line_items JSONB                 │
└───────────────────────────────────────┘
```

### Cardinalities
| Relationship | Type |
|---|---|
| tier_config → tenants | 1 : N (one tier, many tenants) |
| tenants → tenant_users | 1 : N (one tenant, many users) |
| tenants → products | 1 : N (one tenant, many products) |
| tenants → orders | 1 : N (one tenant, many orders) |
| tenant_users → orders | 1 : N (one user, many orders) |
| orders → order_items | 1 : N (one order, many line items) |
| products → order_items | 1 : N (one product, many line items) |
| orders → invoices | 1 : 1 (one order, one invoice) |
| tenants → audit_log | 1 : N (one tenant, many audit entries) |

---

## 2. Normalisation Analysis

### 2.1 — First Normal Form (1NF)
**Requirement**: atomic values in every cell; no repeating groups.

| Table | 1NF Status | Notes |
|---|---|---|
| tenants | ✓ | `metadata JSONB` is a single atomic column (JSON object is one value) |
| products | ✓ | `attributes JSONB` — same as above |
| orders | ✓ | `shipping_addr JSONB` — structured but atomic |
| order_items | ✓ | All columns single-valued |
| invoices | ✓ | `line_items JSONB` — denormalised snapshot for archival; treated as one value |
| audit_log | ✓ | `old_value`/`new_value` are JSONB snapshots, not repeated groups |

All tables satisfy 1NF.

---

### 2.2 — Second Normal Form (2NF)
**Requirement**: in 1NF + every non-prime attribute is fully functionally dependent on the *whole* primary key (no partial dependencies). Only relevant when PK is composite.

| Table | PK | Partial Deps? | 2NF |
|---|---|---|---|
| tenants | `tenant_id` (single) | N/A | ✓ |
| products | `product_id` (single) | N/A | ✓ |
| orders | `order_id` (single) | N/A | ✓ |
| order_items | `item_id` (single) | N/A | ✓ |
| invoices | `invoice_id` (single) | N/A | ✓ |
| tenant_users | `user_id` (single) | N/A | ✓ |

All PKs are single-column UUIDs → no partial dependencies possible → all tables in 2NF.

---

### 2.3 — Third Normal Form (3NF)
**Requirement**: in 2NF + no transitive functional dependencies (no non-prime attr determines another non-prime attr).

#### `orders` table — detailed analysis
| FD | Valid? | Reason |
|---|---|---|
| `order_id → tenant_id` | ✓ | superkey → attribute |
| `order_id → user_id` | ✓ | superkey → attribute |
| `order_id → status` | ✓ | superkey → attribute |
| `order_id → total_amount` | ✓ | denormalised for perf; maintained by app |
| `user_id → tenant_id`? | No transitive dep | user_id is a FK, not a determinant of tenant_id in this table |

No transitive FDs detected. ✓ **3NF**

#### `order_items` — detailed analysis
| FD | Valid? |
|---|---|
| `item_id → order_id` | ✓ superkey |
| `item_id → product_id` | ✓ superkey |
| `item_id → quantity, unit_price` | ✓ superkey |
| `line_total` is `GENERATED ALWAYS AS (quantity * unit_price)` — a computed column, not a stored functional dependency in the relational sense |

No transitive FDs. ✓ **3NF**

#### `products` table
| FD | Valid? |
|---|---|
| `product_id → name, price, sku` | ✓ superkey |
| `sku → price`? | No: `sku` is not a determinant of `price` in our domain — same product can have different prices per tenant |
| `product_id → tenant_id` | ✓ superkey |

✓ **3NF**

**Conclusion**: All tables are in 3NF. The schema is not in BCNF only for `tenant_users` where `(tenant_id, email)` is also a candidate key — both candidate keys include non-prime attributes, which is the definition of a BCNF violation. However, this is a well-known BCNF anomaly that does not cause update anomalies in practice, and decomposing would eliminate the natural `(tenant_id, email)` uniqueness constraint.

---

## 3. Data Dictionary

### `public.tenants`
| Column | Type | Constraints | Description |
|---|---|---|---|
| tenant_id | UUID | PK, DEFAULT gen_random_uuid() | Globally unique tenant identifier |
| name | VARCHAR(255) | NOT NULL, UNIQUE | Human-readable tenant name |
| slug | VARCHAR(100) | NOT NULL, UNIQUE | URL-safe lowercase identifier |
| tier | VARCHAR(20) | NOT NULL, CHECK(free\|pro\|enterprise) | Subscription tier |
| model | VARCHAR(30) | NOT NULL, CHECK(shared_schema\|schema_per_tenant\|db_per_tenant) | Isolation model |
| status | VARCHAR(20) | NOT NULL DEFAULT 'active', CHECK | Lifecycle status |
| schema_name | VARCHAR(100) | NULL | Set for Model B tenants |
| db_name | VARCHAR(100) | NULL | Set for Model C tenants |
| max_users | INTEGER | NOT NULL | Max concurrent users for tier |
| max_storage_gb | NUMERIC(10,2) | NOT NULL | Storage quota |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | Provisioning timestamp |
| updated_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | Last modification (trigger-maintained) |
| deactivated_at | TIMESTAMPTZ | NULL | Set when status → deactivated |
| metadata | JSONB | NOT NULL DEFAULT '{}' | Flexible configuration (branding, feature flags) |

### `public.tenant_users`
| Column | Type | Constraints | Description |
|---|---|---|---|
| user_id | UUID | PK | Unique user identifier |
| tenant_id | UUID | NOT NULL, FK→tenants, CASCADE | Owning tenant |
| email | VARCHAR(320) | NOT NULL, UNIQUE(tenant_id,email) | Login email |
| password_hash | TEXT | NOT NULL | bcrypt hash |
| role | VARCHAR(20) | NOT NULL, CHECK(tenant_admin\|tenant_user\|tenant_readonly) | RBAC role |
| is_active | BOOLEAN | NOT NULL DEFAULT TRUE | Account enabled flag |
| last_login_at | TIMESTAMPTZ | NULL | Updated on successful login |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ | NOT NULL | Trigger-maintained |
| metadata | JSONB | NOT NULL DEFAULT '{}' | User preferences |

### `public.products`
| Column | Type | Constraints | Description |
|---|---|---|---|
| product_id | UUID | PK | |
| tenant_id | UUID | NOT NULL, FK→tenants, CASCADE | RLS isolation column |
| name | VARCHAR(255) | NOT NULL | Product display name |
| description | TEXT | NULL | Long description |
| price | NUMERIC(12,2) | NOT NULL, CHECK(≥0) | Current list price |
| sku | VARCHAR(100) | NOT NULL, UNIQUE(tenant_id,sku) | Stock-keeping unit |
| is_active | BOOLEAN | NOT NULL DEFAULT TRUE | Soft-delete flag |
| attributes | JSONB | NOT NULL DEFAULT '{}' | Flexible product attributes (GIN-indexed) |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ | NOT NULL | Trigger-maintained |

### `public.orders`
| Column | Type | Constraints | Description |
|---|---|---|---|
| order_id | UUID | PK | |
| tenant_id | UUID | NOT NULL, FK→tenants | RLS isolation column |
| user_id | UUID | NOT NULL, FK→tenant_users | Placing user |
| status | VARCHAR(20) | NOT NULL DEFAULT 'pending', CHECK | Order lifecycle state |
| total_amount | NUMERIC(12,2) | NOT NULL DEFAULT 0, CHECK(≥0) | Denormalised sum of line items |
| currency | CHAR(3) | NOT NULL DEFAULT 'USD' | ISO 4217 currency code |
| shipping_addr | JSONB | NOT NULL DEFAULT '{}' | Structured address |
| notes | TEXT | NULL | Internal notes |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ | NOT NULL | Trigger-maintained |

### `public.order_items`
| Column | Type | Constraints | Description |
|---|---|---|---|
| item_id | UUID | PK | |
| tenant_id | UUID | NOT NULL, FK→tenants | RLS isolation column |
| order_id | UUID | NOT NULL, FK→orders CASCADE | Parent order |
| product_id | UUID | NOT NULL, FK→products | Referenced product |
| quantity | INTEGER | NOT NULL, CHECK(>0) | Units ordered |
| unit_price | NUMERIC(12,2) | NOT NULL, CHECK(≥0) | Price at time of order |
| line_total | NUMERIC(12,2) | GENERATED ALWAYS AS (qty×price) STORED | Computed total |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |

### `public.invoices`
| Column | Type | Constraints | Description |
|---|---|---|---|
| invoice_id | UUID | PK | |
| tenant_id | UUID | NOT NULL, FK→tenants | RLS isolation column |
| order_id | UUID | NOT NULL, FK→orders | Source order |
| invoice_number | VARCHAR(50) | NOT NULL, UNIQUE(tenant_id,invoice_number) | Human-readable invoice ID |
| issued_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | Issue date |
| due_at | TIMESTAMPTZ | NULL | Payment due date |
| paid_at | TIMESTAMPTZ | NULL | NULL until payment confirmed |
| amount_due | NUMERIC(12,2) | NOT NULL | Total owed |
| amount_paid | NUMERIC(12,2) | NOT NULL DEFAULT 0 | Total received so far |
| status | VARCHAR(20) | NOT NULL DEFAULT 'draft', CHECK | Invoice lifecycle |
| line_items | JSONB | NOT NULL DEFAULT '[]' | Archived snapshot of items |

### `public.audit_log`
| Column | Type | Constraints | Description |
|---|---|---|---|
| log_id | BIGSERIAL | PK | Monotonic log sequence |
| tenant_id | UUID | NOT NULL (no FK — intentional) | Owning tenant |
| user_id | UUID | NULL | Acting user (NULL for system ops) |
| table_name | VARCHAR(100) | NOT NULL | Affected table |
| schema_name | VARCHAR(100) | NOT NULL DEFAULT 'public' | Affected schema |
| operation | VARCHAR(10) | NOT NULL, CHECK(INSERT\|UPDATE\|DELETE) | DML type |
| row_id | TEXT | NULL | PK of affected row (text cast) |
| old_value | JSONB | NULL | Full row before change (NULL for INSERT) |
| new_value | JSONB | NULL | Full row after change (NULL for DELETE) |
| db_user | TEXT | NOT NULL DEFAULT current_user | PostgreSQL role at time of operation |
| ip_address | INET | NULL | Application-supplied client IP |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | Log entry timestamp |

---

## 4. Index Inventory

| Index Name | Table | Columns | Type | Partial? | Purpose |
|---|---|---|---|---|---|
| idx_tenants_status | tenants | status | B-tree | WHERE active | Active-tenant lookup |
| idx_tenants_tier | tenants | tier, status | B-tree | No | Tier analytics |
| idx_tenants_metadata | tenants | metadata | GIN | No | JSON attribute search |
| idx_products_tenant_active | products | tenant_id, is_active, created_at | B-tree | WHERE active | Product listings |
| idx_products_attributes | products | attributes | GIN | No | JSONB containment queries |
| idx_orders_tenant_status | orders | tenant_id, status, created_at | B-tree | No | Order dashboards |
| idx_orders_tenant_user | orders | tenant_id, user_id | B-tree | WHERE not terminal | My orders view |
| idx_order_items_order | order_items | tenant_id, order_id | B-tree | No | Order detail page |
| idx_order_items_product | order_items | tenant_id, product_id | B-tree | No | Product sales history |
| idx_invoices_tenant_status | invoices | tenant_id, status | B-tree | WHERE issued/overdue | AR dashboard |
| idx_audit_log_tenant_created | audit_log | tenant_id, created_at DESC | B-tree | No | Audit queries |
| idx_audit_log_operation | audit_log | operation, table_name | B-tree | No | Operation filtering |
| idx_tenant_users_tenant_id | tenant_users | tenant_id, is_active | B-tree | WHERE active | User auth lookup |
| idx_tenant_users_email | tenant_users | email | B-tree | No | Login by email |

# 🏢 Multi-Tenant SaaS Platform

A **production-grade multi-tenant e-commerce platform** demonstrating advanced database concepts with three different isolation strategies: Row-Level Security (RLS), Schema-Per-Tenant, and Database-Per-Tenant models.

## 🎯 Project Overview

This platform showcases enterprise-level multi-tenancy patterns with complete data isolation, security, and scalability. Perfect for learning advanced PostgreSQL features, Flask architecture, and cloud-native application design.

### Key Features

✅ **3 Multi-Tenancy Models**
- Model A: Shared Schema with Row-Level Security (RLS)
- Model B: Schema-Per-Tenant isolation
- Model C: Database-Per-Tenant complete isolation

✅ **Enterprise Security**
- JWT authentication (HS256)
- bcrypt password hashing (cost factor 10)
- Role-Based Access Control (RBAC)
- 4-layer data isolation strategy
- Comprehensive audit logging

✅ **Advanced Database Features**
- PostgreSQL 15 with RLS policies
- Trigger-based audit trails
- Generated columns for computed values
- JSONB for flexible attributes
- Connection pooling

✅ **Caching & Performance**
- Redis caching layer
- Configurable TTL strategies
- LRU eviction policies
- Query optimization

✅ **Containerized Deployment**
- Docker & Docker Compose
- Multi-service orchestration
- Easy local development setup
- Production-ready configuration

---

## 🏗️ Architecture

### System Overview

```
┌─────────────────────────────────────────────────┐
│           Load Balancer / Proxy                 │
└────────────────┬────────────────────────────────┘
                 │
    ┌────────────┼────────────┐
    │            │            │
┌───▼────┐  ┌────▼────┐  ┌───▼────┐
│  Flask │  │  Flask │  │ Flask  │  API Instances
│  API 1 │  │  API 2 │  │ API 3  │
└───┬────┘  └────┬────┘  └───┬────┘
    │            │            │
    └────────────┼────────────┘
                 │
    ┌────────────┴──────────────┐
    │                           │
┌───▼─────────┐        ┌────────▼────┐
│ PostgreSQL  │        │    Redis    │
│ (Tenants)   │        │   Cache     │
└─────────────┘        └─────────────┘
    │
    ├── Model A: Shared DB with RLS
    ├── Model B: Tenant Schemas
    └── Model C: Tenant Databases
```

### Multi-Tenancy Models Comparison

| Criterion | Model A (Shared) | Model B (Schema) | Model C (Database) |
|-----------|-----------------|-----------------|-----------------|
| **Infrastructure Cost** | Low | Medium | High |
| **Data Isolation** | RLS Policy | Schema Boundary | Physical DB |
| **Performance Isolation** | None | Partial | Complete |
| **Cross-Tenant Analytics** | Easy | Medium | Hard |
| **Compliance / Audit** | Good | Better | Best |
| **Recommended For** | Free / Startup | Pro tier | Enterprise |

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.11+**
- **PostgreSQL 15+**
- **Redis 7+**
- **Docker & Docker Compose** (optional, for containerized setup)
- **Git**

### Installation

#### 1. Clone the Repository
```bash
git clone https://github.com/your-username/multi-tenant-saas.git
cd multi-tenant-saas
```

#### 2. Setup Python Environment
```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

#### 3. Configure Environment
```bash
# Copy example environment file
cp .env.example .env

# Edit .env with your settings
# (Instructions in the file)
nano .env
```

#### 4. Setup Database

**Option A: Using Docker Compose (Recommended)**
```bash
docker-compose up -d
# This starts PostgreSQL and Redis automatically
```

**Option B: Manual Setup**
```bash
# Start PostgreSQL and Redis on your system
# Then initialize the database:
psql -U superadmin -d multitenant_saas -f sql/00_master_registry.sql
psql -U superadmin -d multitenant_saas -f sql/01_model_a_shared_schema.sql
psql -U superadmin -d multitenant_saas -f sql/02_seed_superadmin.sql
```

#### 5. Run the Application
```bash
# Start Flask development server
flask run
# Server runs at http://localhost:5000
```

---

## 📚 Project Structure

```
multi-tenant-saas/
├── api/                          # Flask application
│   ├── __init__.py
│   ├── app.py                    # Main Flask app
│   ├── auth.py                   # JWT authentication
│   └── routes/                   # API endpoints
│       ├── __init__.py
│       ├── tenants.py            # Tenant management
│       ├── products.py           # Product CRUD
│       ├── orders.py             # Order processing
│       └── auth_routes.py        # Login/logout
│
├── sql/                          # Database schemas
│   ├── 00_master_registry.sql    # Tenants table
│   ├── 01_model_a_shared_schema.sql  # RLS setup
│   ├── 02_model_b_template.sql   # Schema-per-tenant
│   ├── 03_model_c_template.sql   # Database-per-tenant
│   ├── 04_audit_triggers.sql     # Audit logging
│   ├── 05_explain_analyze_samples.sql
│   └── 06_seed_demo_data.sql     # Sample data
│
├── cache/                        # Caching layer
│   ├── __init__.py
│   └── redis_client.py           # Redis integration
│
├── provisioning/                 # Tenant provisioning
│   ├── __init__.py
│   └── tenant_provisioner.py     # Provisioning logic
│
├── tests/                        # Test suite
│   ├── __init__.py
│   ├── test_acid.py             # ACID compliance
│   ├── test_rls_isolation.py    # RLS validation
│   └── test_provisioner.py      # Provisioning tests
│
├── static/                       # Frontend assets
│   ├── css/
│   ├── js/
│   └── images/
│
├── templates/                    # HTML templates
│   ├── base.html
│   ├── login.html
│   ├── dashboard.html
│   └── ...
│
├── benchmarks/                   # Performance benchmarks
│   └── load_testing.py
│
├── docker-compose.yml            # Docker services
├── Dockerfile                    # Container image
├── requirements.txt              # Python dependencies
├── .env.example                  # Environment template
├── .gitignore                    # Git ignore rules
└── README.md                     # This file
```

---

## 🔧 Configuration

### Environment Variables (.env)

```bash
# PostgreSQL Configuration
POSTGRES_HOST=localhost          # Database host
POSTGRES_PORT=5432              # Database port
POSTGRES_USER=superadmin         # Database user
POSTGRES_PASSWORD=supersecret    # Database password (CHANGE THIS!)
POSTGRES_DB=multitenant_saas     # Database name

# Redis Configuration
REDIS_HOST=localhost             # Redis host
REDIS_PORT=6379                  # Redis port
REDIS_PASSWORD=                  # Redis password (optional)
REDIS_DB=0                       # Redis database number

# Flask Configuration
FLASK_ENV=development            # Environment (development/production)
FLASK_SECRET_KEY=your-secret-key # Change to random string!
DEBUG=True                       # Enable debug mode

# JWT Configuration
JWT_SECRET_KEY=your-jwt-secret   # Change to random string!
JWT_EXPIRY_SECONDS=3600          # Token expiry (1 hour)

# Connection Pool
PG_POOL_MIN=2                    # Minimum pool connections
PG_POOL_MAX=20                   # Maximum pool connections

# Cache
CACHE_TTL_SECONDS=60             # Cache time-to-live
```

**⚠️ Security Warning:** Never commit `.env` files with real credentials. Use `.env.example` as a template.

---

## 📖 Core Functionalities

### 1. Authentication & Authorization

**JWT-Based Authentication**
- Stateless token generation
- HS256 signature algorithm
- Configurable token expiry
- Role-Based Access Control (RBAC)

```python
# Example: Login
POST /auth/login
{
  "email": "user@example.com",
  "password": "password123"
}

# Response: JWT Token
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expiry": 3600
}
```

**RBAC Hierarchy**
```
app_api (base role)
├── app_superadmin (all access)
├── app_tenant_admin (tenant admin)
├── app_tenant_user (regular user)
└── app_tenant_readonly (read-only)
```

### 2. Tenant Provisioning

**Automatic Tenant Setup**
- Create tenant with subscription tier
- Provision database/schema based on model
- Initialize user accounts
- Setup audit logging

```python
# Example: Create Tenant
POST /tenants
{
  "name": "ACME Corp",
  "slug": "acme-corp",
  "tier": "pro",
  "model": "shared|schema|database",
  "admin_email": "admin@acme.com"
}
```

### 3. Multi-Tenancy Models

#### Model A: Shared Schema with RLS
- All tenants share same tables
- PostgreSQL RLS policies enforce isolation
- Lowest cost, suitable for free tier

```sql
-- RLS Policy
CREATE POLICY tenant_isolation_policy ON products
  USING (tenant_id = current_setting('app.current_tenant')::UUID)
  WITH CHECK (tenant_id = current_setting('app.current_tenant')::UUID);
```

#### Model B: Schema-Per-Tenant
- Each tenant gets dedicated schema
- No tenant_id column needed
- Better isolation than Model A

```sql
-- Create tenant schema
CREATE SCHEMA tenant_acme_corp;
SET search_path = tenant_acme_corp, public;
```

#### Model C: Database-Per-Tenant
- Complete physical isolation
- Separate connection pools
- Best for enterprise customers

```sql
-- Create tenant database
CREATE DATABASE multitenant_acme_corp;
```

### 4. Data Management

**Products**
- CRUD operations
- Tenant-scoped access
- Flexible attributes (JSONB)
- Redis caching (60s TTL)

```python
# Example: List Products
GET /tenants/{tenant_id}/products
# Returns cached product list for tenant
```

**Orders**
- Order creation with line items
- Order status tracking
- Invoice generation
- Audit trail

```python
# Example: Create Order
POST /tenants/{tenant_id}/orders
{
  "user_id": "uuid",
  "items": [
    {"product_id": "uuid", "quantity": 2, "price": 99.99}
  ]
}
```

### 5. Security Features

**4-Layer Data Isolation**
1. **Application Layer:** g.tenant_id validation
2. **RLS Layer:** PostgreSQL policies (Model A)
3. **Schema Layer:** Namespace separation (Model B)
4. **Physical Layer:** Database isolation (Model C)

**Audit Logging**
- Trigger-based tracking
- Before/after JSONB values
- Timestamp and user tracking
- Query history

```sql
-- Audit table structure
CREATE TABLE audit_log (
  id UUID PRIMARY KEY,
  table_name VARCHAR,
  operation VARCHAR,
  old_values JSONB,
  new_values JSONB,
  user_id UUID,
  tenant_id UUID,
  created_at TIMESTAMPTZ
);
```

### 6. Performance Optimization

**Caching Strategy**
| Key Pattern | TTL | Use Case |
|-------------|-----|----------|
| `tenant:{id}:products` | 60s | Product listings |
| `tenant:{id}:user:{uid}:role` | 60s | User permissions |
| `tenant:{id}:orders` | 60s | Order lists |

**Query Optimization**
- Composite indexes on (tenant_id, entity_id)
- Generated columns for totals
- Connection pooling (2-20 connections)
- Prepared statements via psycopg2

---

## 🧪 Testing

### Run All Tests
```bash
pytest -v
```

### Run Specific Test Suite
```bash
# ACID Compliance Tests
pytest tests/test_acid.py -v

# RLS Isolation Tests
pytest tests/test_rls_isolation.py -v

# Tenant Provisioning Tests
pytest tests/test_provisioner.py -v
```

### Test Coverage
```bash
pytest --cov=api --cov-report=html
# Coverage report in htmlcov/index.html
```

### Test Categories

**ACID Compliance (test_acid.py)**
- Transaction isolation levels
- Rollback consistency
- Concurrent operation handling

**RLS Isolation (test_rls_isolation.py)**
- Cross-tenant data access prevention
- Policy enforcement
- Superadmin audit trail visibility

**Provisioning (test_provisioner.py)**
- Tenant creation for all 3 models
- Schema generation
- Database provisioning

---

## 📊 API Endpoints Reference

### Authentication
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/login` | Login with email/password |
| POST | `/auth/logout` | Logout and invalidate token |
| POST | `/auth/refresh` | Refresh JWT token |

### Tenants
| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/tenants` | superadmin | Create tenant |
| GET | `/tenants` | superadmin | List all tenants |
| GET | `/tenants/{id}` | @require_auth | Get tenant details |
| DELETE | `/tenants/{id}` | superadmin | Deactivate tenant |

### Products
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/tenants/{tid}/products` | List products (cached) |
| POST | `/tenants/{tid}/products` | Create product |
| GET | `/tenants/{tid}/products/{pid}` | Get product details |
| PUT | `/tenants/{tid}/products/{pid}` | Update product |
| DELETE | `/tenants/{tid}/products/{pid}` | Delete product |

### Orders
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/tenants/{tid}/orders` | List orders |
| POST | `/tenants/{tid}/orders` | Create order |
| GET | `/tenants/{tid}/orders/{oid}` | Get order details |
| PUT | `/tenants/{tid}/orders/{oid}` | Update order status |

### Invoices
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/tenants/{tid}/invoices` | List invoices |
| GET | `/tenants/{tid}/invoices/{iid}` | Get invoice |
| POST | `/tenants/{tid}/invoices` | Generate invoice |

---

## 🐳 Docker Deployment

### Using Docker Compose

**Start Services**
```bash
docker-compose up -d
```

**Available Services**
- PostgreSQL: localhost:5432
- Redis: localhost:6379
- Flask API: http://localhost:5000

**Stop Services**
```bash
docker-compose down
```

**View Logs**
```bash
docker-compose logs -f api
```

### Docker Compose Configuration

```yaml
version: '3.9'

services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: multitenant_saas
      POSTGRES_USER: superadmin
      POSTGRES_PASSWORD: supersecret
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  api:
    build: .
    ports:
      - "5000:5000"
    depends_on:
      - postgres
      - redis
    environment:
      POSTGRES_HOST: postgres
      REDIS_HOST: redis
    volumes:
      - .:/app

volumes:
  postgres_data:
```

---

## 📈 Performance Benchmarks

### Query Performance

**Shared Schema (Model A) - With RLS**
```
SELECT * FROM products WHERE tenant_id = ? LIMIT 100
Time: ~2-5ms (cached: <1ms)
```

**Schema-Per-Tenant (Model B)**
```
SET search_path = tenant_schema, public;
SELECT * FROM products LIMIT 100
Time: ~1-3ms (cached: <1ms)
```

**Database-Per-Tenant (Model C)**
```
SELECT * FROM products LIMIT 100
Time: ~0.5-2ms (cached: <1ms)
```

### Load Testing

Run load tests:
```bash
python benchmarks/load_testing.py --users=100 --duration=60
```

---

## 🔐 Security Best Practices

### Environment Variables
- ✅ Use `.env.example` as template
- ✅ Never commit `.env` with real credentials
- ✅ Use strong, random secrets (32+ characters)
- ✅ Rotate secrets regularly

### JWT Security
- ✅ Use HS256 or RS256 algorithms
- ✅ Set reasonable token expiry (1 hour typical)
- ✅ Store tokens in httpOnly cookies
- ✅ Validate token signature on every request

### Database Security
- ✅ Use strong PostgreSQL passwords
- ✅ Enable SSL for database connections
- ✅ Use least-privilege roles
- ✅ Enable audit logging

### Password Security
- ✅ Use bcrypt with cost factor 10+
- ✅ Never store plain passwords
- ✅ Enforce strong password policies
- ✅ Implement account lockout

---

## 🚨 Troubleshooting

### Common Issues

**PostgreSQL Connection Error**
```
psycopg2.OperationalError: could not translate host name "localhost" to address
```
Solution: Ensure PostgreSQL is running on localhost:5432

**Redis Connection Error**
```
ConnectionError: Error -3 while connecting to redis
```
Solution: Ensure Redis is running on localhost:6379

**JWT Token Invalid**
```
jwt.InvalidTokenError: Signature verification failed
```
Solution: Check JWT_SECRET_KEY matches between .env and token generation

**RLS Policy Not Working**
```
ERROR: Permission denied
```
Solution: Verify app.current_tenant setting is set before queries

### Debug Mode
```bash
# Enable debug logging
export FLASK_DEBUG=1
flask run
```

---

## 📚 Database Schema

### Master Tables
```sql
-- Tenants Registry
CREATE TABLE public.tenants (
  tenant_id UUID PRIMARY KEY,
  name VARCHAR(255) UNIQUE NOT NULL,
  slug VARCHAR(100) UNIQUE NOT NULL,
  tier VARCHAR(20) NOT NULL,
  model VARCHAR(30) NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tenant Users
CREATE TABLE public.tenant_users (
  user_id UUID PRIMARY KEY,
  tenant_id UUID REFERENCES public.tenants,
  email VARCHAR(255) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role VARCHAR(50) NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Tenant Tables (Model A)
```sql
-- Products
CREATE TABLE public.products (
  product_id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  name VARCHAR(255) NOT NULL,
  price NUMERIC(12,2) NOT NULL,
  attributes JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Orders
CREATE TABLE public.orders (
  order_id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  user_id UUID NOT NULL,
  status VARCHAR(20) DEFAULT 'pending',
  total_amount NUMERIC(12,2) DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Invoices
CREATE TABLE public.invoices (
  invoice_id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  order_id UUID REFERENCES public.orders,
  amount NUMERIC(12,2) NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Audit Logging
```sql
CREATE TABLE public.audit_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  table_name VARCHAR(100) NOT NULL,
  operation VARCHAR(10) NOT NULL,
  old_values JSONB,
  new_values JSONB,
  user_id UUID,
  tenant_id UUID NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 🤝 Contributing

Contributions are welcome! Please follow these guidelines:

1. **Fork the repository**
2. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature
   ```
3. **Make your changes**
4. **Write/update tests**
5. **Commit with clear messages**
   ```bash
   git commit -m "Add feature: description"
   ```
6. **Push to your branch**
7. **Create a Pull Request**

---

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

---

## 📞 Support & Contact

- **Issues:** GitHub Issues
- **Discussions:** GitHub Discussions

---

## 🙏 Acknowledgments

- PostgreSQL documentation and RLS features
- Flask framework and community
- Redis for caching layer
- Docker and containerization best practices

---

## 📝 Change Log

### Version 1.0.0 (2026-05-13)
- Initial release
- 3 multi-tenancy models
- JWT authentication
- Redis caching
- Docker deployment
- Comprehensive test suite

---

## 🗺️ Roadmap

- [ ] OAuth 2.0 / OpenID Connect support
- [ ] Two-Factor Authentication (2FA)
- [ ] Multi-currency support
- [ ] Subscription billing integration
- [ ] API webhooks
- [ ] GraphQL API option
- [ ] Kubernetes deployment
- [ ] Advanced analytics dashboard
- [ ] Rate limiting per tenant
- [ ] S3 integration for file storage

---

**Happy coding! 🚀**

For more information, visit [project documentation](./docs) or check the [wiki](../../wiki).

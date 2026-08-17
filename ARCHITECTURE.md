# 🏗️ Arquitetura do Sistema

Documentação técnica da arquitetura do Fleet Management Platform Backend.

## 📐 Visão Geral

O sistema foi desenvolvido como um **monolito modular**, priorizando simplicidade, manutenibilidade e caminho claro para escalabilidade futura.

### Decisões Arquiteturais

- ✅ **Monolito Modular** vs Microserviços: Mais simples para começar, escala verticalmente bem
- ✅ **Async/Await**: FastAPI com SQLAlchemy async para máxima performance
- ✅ **Particionamento Nativo PostgreSQL**: Dados históricos particionados por dia
- ✅ **Cloud Agnostic**: Funciona em qualquer ambiente com Docker
- ✅ **Observability First**: Métricas e logs estruturados desde o dia 1

## 🔧 Stack Tecnológica

### Backend
- **FastAPI 0.109+**: Framework web assíncrono e moderno
- **Python 3.11+**: Linguagem principal
- **Pydantic v2**: Validação de dados e serialização
- **SQLAlchemy 2.0**: ORM com suporte async
- **Alembic**: Gerenciamento de migrations (planejado para novas tabelas)

### Banco de Dados
- **Aurora PostgreSQL 15** (AWS): Banco relacional principal
- **Particionamento Nativo**: Tabelas particionadas por dia (`_pYYYYMMDD`)
- **Schema**: `mova.*` (integração com banco legado)
- **Redis 7**: Cache para dados frequentes

### Monitoramento
- **Prometheus**: Coleta de métricas via endpoint `/metrics` e scraping
- **Structured Logging**: Logs JSON para análise
- **Sentry**: Rastreamento de erros (opcional, configurável)
- **Health Checks**: Endpoint `/health` para monitoring

## 📁 Estrutura Modular

```
app/
├── core/              # Núcleo da aplicação
│   ├── config.py      # Configurações (Pydantic Settings)
│   ├── database.py    # Setup de banco de dados
│   ├── logging.py     # Configuração de logs
│   ├── security.py    # JWT, hashing, API keys
│   ├── redis.py       # Cliente Redis
│   └── access_control.py # Filtros de grupo/subgrupo
│
├── models/            # Modelos SQLAlchemy (Database)
│   ├── vehicle.py     # tracked_unit (veículos)
│   ├── history.py     # dev_status_30 (telemetria)
│   └── __init__.py
│
├── schemas/           # Schemas Pydantic (API)
│   ├── history.py     # Schemas para histórico
│   ├── driver_reports.py # Schemas para relatórios de motoristas
│   └── __init__.py
│
├── api/               # Endpoints REST
│   └── v1/
│       ├── endpoints/
│       │   ├── auth.py       # Login, refresh, logout
│       │   ├── vehicles.py   # CRUD veículos
│       │   └── reports.py    # Relatórios (history + drivers)
│       └── api.py            # Router aggregator
│
├── middleware/        # Middlewares customizados
│   ├── auth.py        # Autenticação JWT/API Key
│   └── monitoring.py  # Métricas Prometheus
│
└── main.py            # Aplicação FastAPI
```

## 🔐 Sistema de Autenticação

### Dual Authentication System

O sistema suporta dois tipos de autenticação:

#### 1. JWT (Usuários da Plataforma)
```
┌─────────┐                ┌─────────┐
│  User   │───── Login ────▶│   API   │
└─────────┘                └─────────┘
                                │
                                ▼
                     ┌────────────────────┐
                     │ Access Token (15m) │
                     │ Refresh Token (7d) │
                     └────────────────────┘
```

**Características:**
- Access Token: 15 minutos (stateless JWT)
- Refresh Token: 7 dias (configurável)
- Renovação automática via `/auth/refresh`
- Revogação via `/auth/logout`

#### 2. API Keys (Integrações Terceiras)
```
┌──────────┐                ┌─────────┐
│  Client  │─── API Key ───▶│   API   │
└──────────┘                └─────────┘
                                │
                                ▼
                     ┌──────────────────┐
                     │ Verify Hash      │
                     │ Check Permissions│
                     │ Rate Limiting    │
                     └──────────────────┘
```

**Características:**
- Formato: `sk_live_xxxxx` ou `sk_test_xxxxx`
- Armazenamento: Hash bcrypt no banco
- Permissões granulares: `read:vehicles`, `write:reports`
- Rate limiting: Configurável por key

### Autorização

**Role-Based (Platform Users):**
- `admin`: Acesso total
- `operator`: Leitura e escrita
- `viewer`: Somente leitura

**Permission-Based (API Keys):**
- Permissões explícitas: `{action}:{resource}`
- Exemplo: `["read:vehicles", "write:positions"]`

## 🗄️ Modelo de Dados

### Estrutura Real do Banco (Schema: mova.*)

```
tracked_unit (Veículos/Unidades Rastreadas)
├── id, label, label2, obs
├── group_id, subgroup_id (controle de acesso por grupo)
├── imei, serial_number
└── relacionamentos:
    ├── dev_status_30_pYYYYMMDD (histórico de telemetria)
    └── con_driver_h_km (métricas agregadas de motoristas)

dev_status_30_pYYYYMMDD (Partições Diárias de Telemetria)
├── Particionamento: Uma partição por dia (_p20250114, _p20250115, etc)
├── Campos principais:
│   ├── id (BIGINT, primary key)
│   ├── unit_id → tracked_unit.id
│   ├── local_time (timestamp com timezone)
│   ├── time_write (timestamp de gravação)
│   ├── latitude, longitude (coordenadas GPS)
│   ├── speed (velocidade em km/h)
│   ├── ignition (boolean)
│   ├── rpm (rotação do motor)
│   ├── odom, odom_total (hodômetro)
│   └── address (geocoding reverso)
└── Indexes: (unit_id, local_time), (id DESC) para paginação

con_driver_h_km (Consolidado de Métricas de Motoristas)
├── Agregações diárias por motorista e unidade
├── Campos:
│   ├── dt (date), unit_id, driver, driver_id
│   ├── label, group_id, subgroup_id
│   ├── distance_traveled_hist (metros)
│   ├── used_fuel_hist (ml)
│   ├── time_traveled_hist (segundos)
└── GROUP BY: dt, label, unit_id, group_id, subgroup_id, driver, driver_id
```

### Controle de Acesso por Grupo

```python
# Cada usuário tem acesso a grupos/subgrupos específicos
user.group_access = [(group_id_1, subgroup_id_1), (group_id_2, subgroup_id_2), ...]

# Filtros automáticos aplicados em todas as queries:
WHERE group_id = ANY(accessible_groups)
  AND (subgroup_id IS NULL OR subgroup_id = ANY(accessible_subgroups))
```

**Regras:**
- `subgroup_id = NULL`: Recurso compartilhado entre todos do grupo
- `subgroup_id != NULL`: Recurso específico do subgrupo
- Usuários só veem dados dos seus grupos/subgrupos

## 📊 Sistema de Relatórios

### Arquitetura de Queries Otimizadas

#### 1. Cursor Pagination (Web UI)
```python
# Paginação eficiente usando ID composto (timestamp + row_id)
# Evita OFFSET que fica lento em datasets grandes

SELECT * FROM (
    SELECT *, ROW_NUMBER() OVER (ORDER BY local_time DESC, id) as row_id
    FROM dev_status_30_pYYYYMMDD
    WHERE ...
) WHERE (local_time < cursor_time) OR (local_time = cursor_time AND row_id > cursor_id)
ORDER BY local_time DESC
LIMIT 1000
```

**Performance:**
- Primeira página: ~300-500ms
- Páginas subsequentes: ~200-300ms (constante)
- Melhor que OFFSET que cresce linearmente

#### 2. CSV Streaming (Exports)
```python
# Streaming em chunks para evitar consumir memória
async def stream_csv():
    cursor = None
    while True:
        chunk = await fetch_chunk(cursor, size=5000)
        if not chunk:
            break
        yield csv_rows(chunk)
        cursor = chunk[-1].id
```

**Benefícios:**
- Memória constante (~50MB)
- Download inicia imediatamente
- Suporta datasets ilimitados

#### 3. Estimativas (UX)
```python
# Count rápido com GROUP BY quando necessário
# Driver reports: conta registros agregados, não raw rows
SELECT COUNT(*) FROM (
    SELECT 1
    FROM con_driver_h_km
    WHERE ...
    GROUP BY dt, label, unit_id, driver_id
) AS aggregated
```

### Endpoints de Relatórios

**History (Histórico de Telemetria):**
- `GET /api/v1/reports/history/cursor` - Paginação com cursor
- `GET /api/v1/reports/history/export/csv` - Export CSV streaming
- `POST /api/v1/reports/history/export/estimate` - Estimativa

**Driver Reports (Métricas de Motoristas):**
- `GET /api/v1/reports/driver-km-fuel-hours/cursor` - Paginação
- `GET /api/v1/reports/driver-km-fuel-hours/export/csv` - Export CSV
- `POST /api/v1/reports/driver-km-fuel-hours/export/estimate` - Estimativa

## 📈 Performance Targets

### SLA Goals (Production)

- **Availability:** 99.9% uptime
- **Response Time (P95):** < 200ms (endpoints simples)
- **Response Time (P95):** < 500ms (relatórios com paginação)
- **Throughput:** 1000 req/s per instance
- **CSV Export:** 100k registros em < 15 segundos
- **Database Queries:** < 300ms (P95)

### Optimization Strategies

1. **Database**
   - Connection pooling (10-20 connections)
   - Query optimization com EXPLAIN ANALYZE
   - Indexes em (unit_id, local_time), (group_id, subgroup_id)
   - Particionamento automático por dia

2. **Caching**
   - Redis para dados frequentes
   - Query result caching (futuro)

3. **Async Processing**
   - FastAPI async/await para I/O bound
   - SQLAlchemy async para queries concorrentes

## 🔄 Fluxo de Requisição

```
1. HTTP Request
   │
   ▼
2. MonitoringMiddleware (start timer, increment counters)
   │
   ▼
3. Router → Endpoint Function
   │
   ▼
4. AuthMiddleware (JWT/API Key validation)
   │
   ▼
5. Permission Check (roles/permissions)
   │
   ▼
6. Access Control Filter (group_id, subgroup_id)
   │
   ▼
7. Database Query (async SQLAlchemy)
   │
   ▼
8. Response Serialization (Pydantic schemas)
   │
   ▼
9. MonitoringMiddleware (log request, record metrics)
```

## 🚀 Estratégia de Escalabilidade

### Fase 1: Monolito Vertical (Atual - V0)
- 1 instância da API
- 1 instância Redis
- Aurora PostgreSQL (scaling automático)
- **Escala até:** ~10k ativos, 1M posições/dia

### Fase 2: Escala Horizontal (Futuro)
```bash
# Múltiplas réplicas da API
docker-compose up -d --scale api=3
# + Load balancer (Nginx/ALB)
```
- **Escala até:** ~100k ativos, 10M posições/dia

### Fase 3: Separação de Serviços (se necessário)
```
┌─────────────┐
│   Gateway   │
└──────┬──────┘
       │
       ├──▶ API Service (CRUD operations)
       ├──▶ Reports Service (heavy queries)
       ├──▶ Ingestion Service (telemetry ingestion)
       └──▶ Background Service (Celery tasks)
```

## 🔒 Segurança

### Camadas de Segurança

1. **Network Level**
   - CORS configurável
   - Rate limiting (planejado com Redis)
   - Reverse proxy com SSL/TLS

2. **Authentication**
   - JWT com expiração curta (15min)
   - Refresh tokens
   - API keys com hash bcrypt

3. **Authorization**
   - RBAC (platform users)
   - Permission-based (API keys)
   - Resource ownership validation (group_access)

4. **Data**
   - Passwords: bcrypt
   - API keys: bcrypt
   - SQL injection prevention: SQLAlchemy prepared statements
   - Input validation: Pydantic schemas

5. **Monitoring**
   - Structured logging para audit trail
   - Sentry para error tracking
   - Prometheus metrics para anomalias

## 📚 Padrões de Código

### Design Patterns

- **Dependency Injection**: FastAPI Depends
- **Repository Pattern**: Queries isoladas (futuro)
- **Service Layer**: Lógica de negócio (futuro)
- **Factory Pattern**: Configuração e setup
- **Soft Delete Pattern**: Remoção lógica ao invés de física

### Best Practices

- Type hints em todo código
- Docstrings em funções públicas
- Logging estruturado (JSON)
- Error handling consistente
- API versioning (`/api/v1`)
- Async/await para I/O operations
- **Soft delete obrigatório** em tabelas com campo `status`

### CRUD Standards

**Soft Delete (Padrão Obrigatório):**
- Todas as tabelas com campo `status` devem implementar soft delete
- Status: `1` = ativo, `0` = inativo, `-1` = removido
- DELETE endpoint atualiza: `status=-1`, `user_removed`, `date_removed`, `user_modif`, `date_modif`
- DELETE retorna o recurso atualizado (200 OK) ao invés de 204 NO_CONTENT
- LIST exclui registros com `status=-1` por padrão

**Auditoria:**
- CREATE: definir `user_add`, `date_add`, `status=1`
- UPDATE: atualizar `user_modif`, `date_modif`
- DELETE: atualizar `user_removed`, `date_removed`, `user_modif`, `date_modif`

📖 **Documentação completa**: [docs/CRUD_STANDARDS.md](docs/CRUD_STANDARDS.md)

## 🔮 Roadmap Técnico

### Próximas Implementações

**Alta Prioridade:**
- [ ] WebSocket para dados em tempo real
- [ ] Geofencing service
- [ ] Notification service (email, SMS)

**Média Prioridade:**
- [ ] API rate limiting per user
- [ ] Audit logging detalhado
- [ ] Health check avançado (db lag, redis status)
- [ ] Backup/restore automation

**Baixa Prioridade:**
- [ ] GraphQL endpoint (opcional)
- [ ] Multi-tenancy isolado
- [ ] Background job processing (se necessário)

---

**Última atualização:** 2025-11-16
**Versão da arquitetura:** 0.3.0

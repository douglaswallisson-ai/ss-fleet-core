> ⚠️ **Para rodar a plataforma nova, siga o [COMO-RODAR.md](COMO-RODAR.md).** O passo a passo abaixo (Docker) é do projeto original e não funciona sem login na AWS.

# Fleet Management Platform

Backend Python completo para plataforma de rastreamento e gerenciamento de frotas, com suporte a dispositivos Virloc 6/8, telemetria em tempo real, relatórios avançados e API RESTful .

---

## 🚀 Quick Start

### Pré-requisitos
- Docker & Docker Compose
- Acesso a banco Aurora PostgreSQL na AWS
- Python 3.11+ (opcional, para desenvolvimento local)

### 1. Configurar ambiente
```bash
# Copiar arquivo de configuração
cp .env.example .env

# Editar .env com suas credenciais Aurora
nano .env
```

### 2. Iniciar aplicação
```bash
# Subir todos os serviços
docker-compose up -d

# Verificar saúde
curl http://localhost:8000/health

# IMPORTANTE: Validar porta (sempre deve ser 8000)
bash .scripts/validate-port.sh
```

### 3. Acessar
- **API**: http://localhost:8000 ⚠️ **PORTA FIXA - NUNCA ALTERAR**
- **Docs**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/health
- **Prometheus**: http://localhost:9091
- **Redis**: localhost:6380 (internal: 6379)

> ⚠️ **POLÍTICA CRÍTICA DE PORTA**: A API **SEMPRE** deve rodar na porta **8000**. Esta porta é fixa e não pode ser alterada devido a:
> - Integrações externas configuradas para porta 8000
> - Regras de firewall (AWS Security Groups)
> - Documentação de clientes
> - Contratos de serviço (SLAs)
>
> **O script de deploy (`scripts/deploy.sh`) agora garante automaticamente** que:
> 1. Qualquer processo usando a porta 8000 será parado
> 2. A aplicação sempre iniciará na porta 8000
> 3. O `docker-compose.yml` nunca será modificado
>
> **Validação**: Use `bash .scripts/validate-port.sh` para confirmar que a porta está correta.
> **Documentação completa**: Ver [.docs/PORT_8000_POLICY.md](.docs/PORT_8000_POLICY.md)

### 4. Login inicial
```bash
# Credenciais padrão
Email: admin@fleet.com
Senha: Fleet@2025
```

---

## 📋 Funcionalidades

### Core Features
- ✅ **Autenticação Dual**: JWT (usuários) + API Keys (integrações)
- ✅ **RBAC**: Roles (admin, operator, viewer) com permissões granulares
- ✅ **CRUD Completo**: Usuários, Veículos, Dispositivos, Eventos
- ✅ **Time-Series**: Particionamento otimizado para histórico GPS
- ✅ **Real-time**: Monitoramento de ativos ao vivo

### Telemetria e Relatórios
- 📡 Integração com dispositivos Virloc 6/8
- 🗺️ Tracking GPS com histórico particionado por dia
- 📊 Telemetria CAN Bus (RPM, combustível, temperatura, etc.)
- 📈 **Relatórios Avançados**: Histórico de telemetria e métricas de motoristas
- 📄 **Exports Otimizados**: CSV streaming para datasets grandes (100k+ registros)
- 🔄 **Cursor Pagination**: Queries eficientes com paginação por ID composto

### Observabilidade
- 📊 **Métricas**: Prometheus metrics endpoint (/metrics)
- 🐛 **Sentry**: Error tracking (opcional)
- 🏥 **Health Checks**: Monitoramento de saúde da aplicação
- 📝 **Structured Logging**: Logs JSON para análise

---

## 🏗️ Arquitetura

### Stack Tecnológica
- **Framework**: FastAPI (Python 3.11)
- **Database**: Aurora PostgreSQL 15 (AWS) com particionamento nativo
- **Cache**: Redis 7
- **Monitoring**: Prometheus metrics + Structured logging

### Estrutura do Projeto
```
ss-fleet-core/
├── app/
│   ├── api/              # Endpoints REST
│   ├── core/             # Config, security, database
│   ├── models/           # SQLAlchemy models
│   ├── schemas/          # Pydantic schemas
│   ├── middleware/       # Auth, monitoring
│   └── services/         # Business logic
├── alembic/              # Database migrations
├── monitoring/           # Prometheus configs
├── scripts/              # Utility scripts
└── docs/                 # Additional documentation
```

### Modelo de Dados (Schema: mova.*)
```
tracked_unit (Veículos/Unidades)
├── id, label, label2, obs
├── group_id, subgroup_id (controle de acesso)
└── relaciona com:
    ├── dev_status_30_pYYYYMMDD (histórico GPS particionado por dia)
    └── con_driver_h_km (métricas agregadas de motoristas)

dev_status_30_pYYYYMMDD (Partições diárias de telemetria)
├── id, unit_id, local_time, time_write
├── latitude, longitude, speed, ignition
├── rpm, odom, odom_total
└── address (geocoding reverso)

con_driver_h_km (Consolidado de motoristas)
├── dt, unit_id, driver, driver_id
├── distance_traveled_hist (km)
├── used_fuel_hist (litros)
└── time_traveled_hist (horas)
```

---

## 🔐 Autenticação

### JWT Tokens (Usuários da plataforma)
```bash
# Login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@fleet.com","password":"Fleet@2025"}'

# Usar token
curl http://localhost:8000/api/v1/users/me \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### API Keys (Integrações)
```bash
# Criar API Key
curl -X POST http://localhost:8000/api/v1/api-keys \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Integração ERP",
    "permissions": ["read:vehicles", "write:positions"],
    "rate_limit_per_hour": 5000
  }'

# Usar API Key
curl http://localhost:8000/api/v1/vehicles \
  -H "X-API-Key: YOUR_API_KEY"
```

---

## 🗄️ Database

### Aurora PostgreSQL AWS
A aplicação utiliza Aurora PostgreSQL na AWS com as seguintes características:

- **Engine**: PostgreSQL 15 compatível
- **Schema**: `mova.*` (banco legado integrado)
- **Particionamento**: Tabela `dev_status_30_pYYYYMMDD` particionada por dia
- **Indexes**: Otimizados para queries de histórico e relatórios
- **Consolidação**: Tabelas agregadas (con_driver_h_km) para métricas

### Migrations
```bash
# Ver status
docker-compose exec api alembic current

# Criar nova migration
docker-compose exec api alembic revision --autogenerate -m "description"

# Aplicar migrations
docker-compose exec api alembic upgrade head

# Reverter última migration
docker-compose exec api alembic downgrade -1
```

---

## 📡 API Endpoints

### Autenticação
- `POST /api/v1/auth/login` - Login de usuário
- `POST /api/v1/auth/refresh` - Renovar token
- `POST /api/v1/auth/register` - Registrar usuário

### Usuários
- `GET /api/v1/users/me` - Dados do usuário autenticado
- `GET /api/v1/users` - Listar usuários (admin)
- `POST /api/v1/users` - Criar usuário (admin)
- `PUT /api/v1/users/{id}` - Atualizar usuário
- `DELETE /api/v1/users/{id}` - Deletar usuário

### Veículos
- `GET /api/v1/vehicles` - Listar veículos
- `POST /api/v1/vehicles` - Cadastrar veículo
- `GET /api/v1/vehicles/{id}` - Detalhes do veículo
- `PUT /api/v1/vehicles/{id}` - Atualizar veículo
- `DELETE /api/v1/vehicles/{id}` - Soft delete veículo (status=-1)

### Dispositivos
- `GET /api/v1/devices` - Listar dispositivos
- `POST /api/v1/devices` - Cadastrar dispositivo
- `GET /api/v1/devices/{id}` - Detalhes do dispositivo
- `PUT /api/v1/devices/{id}` - Atualizar dispositivo
- `DELETE /api/v1/devices/{id}` - Deletar dispositivo

### Relatórios (Reports)
**History (Histórico de Telemetria):**
- `GET /api/v1/reports/history/cursor` - Paginação eficiente com cursor
- `GET /api/v1/reports/history/export/csv` - Export CSV streaming
- `POST /api/v1/reports/history/export/estimate` - Estimar tamanho do export

**Driver Reports (KM/Combustível/Horas por Motorista):**
- `GET /api/v1/reports/driver-km-fuel-hours/cursor` - Paginação com cursor
- `GET /api/v1/reports/driver-km-fuel-hours/export/csv` - Export CSV streaming
- `POST /api/v1/reports/driver-km-fuel-hours/export/estimate` - Estimar tamanho

> 📚 **Documentação Completa**: Ver [.docs/Reports/](.docs/Reports/)
> - [History.md](.docs/Reports/History.md) - Histórico de telemetria
> - [DriverKmFuel.md](.docs/Reports/DriverKmFuel.md) - Métricas por motorista
> - [Telemetry.md](.docs/Reports/Telemetry.md) - Telemetria avançada (em desenvolvimento)

---

## 🔧 Desenvolvimento

### Setup Local (sem Docker)
```bash
# Instalar dependências
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Configurar .env
cp .env.example .env

# Aplicar migrations
alembic upgrade head

# Rodar aplicação
uvicorn app.main:app --reload
```

### Testes
```bash
# Rodar testes
pytest

# Com coverage
pytest --cov=app --cov-report=html

# Testes específicos
pytest tests/test_auth.py
```

### Linting & Formatting
```bash
# Black (formatação)
black app/

# Flake8 (linting)
flake8 app/

# MyPy (type checking)
mypy app/
```

---

## 📊 Monitoramento

### Prometheus Metrics
```bash
# Acessar métricas
curl http://localhost:8000/metrics
```

Métricas disponíveis:
- `http_requests_total` - Total de requisições HTTP
- `http_request_duration_seconds` - Latência de requisições
- `http_requests_in_progress` - Requisições em andamento
- `http_response_size_bytes` - Tamanho das respostas

### Logs Estruturados
```bash
# Ver logs em tempo real
docker-compose logs -f api

# Logs de componentes específicos
docker-compose logs api          # API FastAPI
docker-compose logs redis        # Cache
docker-compose logs prometheus   # Metrics
```

Logs são estruturados em JSON para facilitar análise e busca.

---

## 🚀 Deploy

### ⚠️ REQUISITO CRÍTICO: PORTA 8000

**A API SEMPRE DEVE RODAR NA PORTA 8000**

Esta porta é **FIXA** e **NÃO PODE SER ALTERADA** em nenhuma circunstância:
- Integrações externas dependem da porta 8000
- Configurações de rede e firewall estão configuradas para porta 8000
- Documentação de clientes referencia porta 8000

**Validação obrigatória após cada deployment:**
```bash
bash .scripts/validate-port.sh
```

### Deployment Automatizado via CodeCommit

O projeto possui deployment automatizado via Lambda + CodeCommit:

```bash
# Setup inicial (executar uma vez)
chmod +x scripts/aws_setup.sh
./scripts/aws_setup.sh

# Deploy automático (a cada push para main)
git push origin main
# Lambda executa deployment automaticamente no servidor
```

**Fluxo de deployment:**
1. Push para branch `main` no CodeCommit
2. Lambda `fleet-codecommit-deploy-trigger` acionada automaticamente
3. Conecta via SSH ao servidor EC2
4. Executa `scripts/deploy.sh` com verificação de portas
5. Git pull → Docker build → Docker up → Health check
6. **Validação da porta 8000** (obrigatória)
7. Rollback automático em caso de falha

**Servidor de produção:**
- IP: 34.236.90.56 (público), 172.31.85.75 (privado)
- Path: `/home/ubuntu/ss-fleet-core`
- Usuário: `ubuntu`
- **Porta API**: 8000 (FIXA - NÃO ALTERAR)
- Outras aplicações: Monitoramento de vídeo + Gateway Virloc 8

> 📚 **Documentação Completa**: Ver [DEPLOYMENT.md](DEPLOYMENT.md)

### Checklist de Produção
- [ ] Executar `scripts/aws_setup.sh` para configurar infraestrutura AWS
- [ ] Configurar `.env` no servidor com valores de produção
- [ ] Gerar SECRET_KEY e JWT_SECRET_KEY com alta entropia
- [ ] Verificar conexão com Aurora PostgreSQL
- [ ] Configurar CORS_ORIGINS com domínio real
- [ ] ⚠️ **VALIDAR PORTA 8000** com `bash .scripts/validate-port.sh`
- [ ] Testar health check: `http://servidor:8000/health`
- [ ] Configurar backup automático do Aurora
- [ ] Verificar logs no CloudWatch e servidor
- [ ] Testar deployment automático com push para main

---

## 🛡️ Segurança

### Boas Práticas Implementadas
- ✅ Senhas hasheadas com bcrypt
- ✅ JWT tokens com expiração
- ✅ CORS configurável
- ✅ Rate limiting por endpoint
- ✅ SQL injection prevention (SQLAlchemy ORM)
- ✅ API Keys com permissões granulares
- ✅ Logs estruturados para auditoria

### Recomendações Adicionais
- Configure HTTPS/TLS em produção
- Use AWS Secrets Manager para credenciais
- Ative AWS WAF no load balancer
- Configure VPC e Security Groups restritivos
- Implemente rotação automática de secrets

---

## 🤝 Contribuindo

1. Fork o projeto
2. Crie uma branch (`git checkout -b feature/nova-funcionalidade`)
3. Commit suas mudanças (`git commit -m 'Adiciona nova funcionalidade'`)
4. Push para a branch (`git push origin feature/nova-funcionalidade`)
5. Abra um Pull Request

---

## 📚 Documentação

### 📖 Documentação Principal (Root)

A documentação principal está organizada na raiz do projeto para fácil acesso:

| Documento | Propósito | Quando Consultar |
|-----------|-----------|------------------|
| [README.md](README.md) | Overview do projeto, quick start | Primeira leitura, onboarding |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Decisões arquiteturais, stack, padrões | Design de features, refatoração |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Deploy AWS (Lambda + CodeCommit) | Configuração de CI/CD, troubleshooting |
| [SECURITY.md](SECURITY.md) | ACL, vulnerabilidades, best practices | Implementação de segurança, auditorias |
| [.docs/Reports/](.docs/Reports/) | Guias completos da API de relatórios | Integração com endpoints de relatórios |

### 📂 Documentação Técnica Adicional (docs/)

Documentação técnica específica para desenvolvedores:

- [docs/CRUD_STANDARDS.md](docs/CRUD_STANDARDS.md) - **Padrões de CRUD e Soft Delete (obrigatório)**
- [docs/PORT_8000_POLICY.md](docs/PORT_8000_POLICY.md) - **Política CRÍTICA da porta 8000**
- [docs/SECURITY_USAGE_GUIDE.md](docs/SECURITY_USAGE_GUIDE.md) - Guia prático de uso seguro
- [docs/TESTING_AUTOMATION_SIMPLE.md](docs/TESTING_AUTOMATION_SIMPLE.md) - Guia de testes automáticos
- [docs/GIT_HOOKS_GUIDE.md](docs/GIT_HOOKS_GUIDE.md) - Configuração de Git Hooks

> 💡 **Para IA/Manutenção**: A documentação está organizada por propósito. Cada arquivo na raiz cobre um aspecto único do sistema sem duplicação.

---

## 📝 Licença

Copyright © 2025 - Todos os direitos reservados

---

## 📞 Suporte

- **Documentação API**: http://localhost:8000/docs
- **Issues**: GitHub Issues
- **Email**: suporte@fleet.com

---

**Status**: ✅ Produção-Ready | **Versão**: 0.1.0 | **Python**: 3.11+
# Test deployment Mon Nov 17 10:51:14 -03 2025

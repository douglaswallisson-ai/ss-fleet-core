# 📚 Fleet Management Platform - Documentation Index

## 🎯 Para Começar (Start Here!)

### Novos Desenvolvedores
1. **[DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md)** 📖
   - Guia completo de desenvolvimento
   - Arquitetura do sistema
   - Padrões de código obrigatórios
   - Como criar CRUDs
   - Testing e Git workflow
   - **LEITURA OBRIGATÓRIA!**

2. **[CRUD_STANDARDS.md](CRUD_STANDARDS.md)** ⚙️
   - Padrões detalhados de CRUD
   - Soft Delete Pattern
   - Access Control
   - Audit Trail
   - Schema Patterns
   - **REFERÊNCIA OBRIGATÓRIA!**

3. **[API_USAGE.md](API_USAGE.md)** 🔌
   - Guia de uso da API REST
   - Exemplos de requisições
   - Autenticação (JWT)
   - Endpoints de Vehicles, Devices, Drivers
   - Associações de dispositivos
   - Workflows completos

4. **[git-hooks.md](git-hooks.md)** 🪝
   - Git Pre-Commit Hook
   - Testes automatizados
   - Como funciona
   - Bypass em emergências

---

## 📋 Documentação por Tópico

### 🏗️ Arquitetura e Fundamentos

#### [DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md)
- **Visão Geral**: Stack tecnológica, estrutura do projeto
- **Arquitetura**: Modelo de dados, fluxo de requisições
- **Padrões**: Soft delete, audit trail, type hints, async/await

#### [../ARCHITECTURE.md](../ARCHITECTURE.md)
- Decisões arquiteturais
- Padrões de código
- Design patterns utilizados

---

### ⚙️ Desenvolvimento de CRUDs

#### [CRUD_STANDARDS.md](CRUD_STANDARDS.md)
- **Soft Delete Pattern**: Como implementar deletação lógica
- **Access Control**: Validação de acesso por grupo/subgrupo
- **Audit Trail**: Campos de auditoria obrigatórios
- **Schema Patterns**: Como estruturar Pydantic schemas
- **Endpoint Patterns**: Templates para LIST, GET, CREATE, UPDATE, DELETE
- **Validation Rules**: Regras de negócio e validações
- **Error Handling**: Como retornar erros consistentes
- **Testing Standards**: Como escrever testes para CRUDs

#### [DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md) - Seção "CRUD Operations"
- Template completo de CRUD
- Model SQLAlchemy
- Schemas Pydantic
- Endpoints FastAPI
- Registro de routers

---

### 🔐 Controle de Acesso

#### [CRUD_STANDARDS.md](CRUD_STANDARDS.md) - Seção "Access Control"
- Modelo de grupos e subgrupos
- Validação por endpoint (LIST, GET, CREATE, UPDATE, DELETE)
- Helper functions
- Recursos sem subgrupo

#### [DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md) - Seção "Controle de Acesso"
- Implementação completa de access control
- Exemplos de código
- Padrões de validação

#### [../SECURITY.md](../SECURITY.md)
- Segurança geral do sistema
- Boas práticas
- Autenticação e autorização

---

### 🔌 Uso da API

#### [API_USAGE.md](API_USAGE.md)
- **Autenticação**: Login JWT, usar tokens
- **Vehicles API**: LIST, GET, CREATE, UPDATE, DELETE
- **Devices API**: CRUD completo de dispositivos
- **Drivers API**: CRUD completo de motoristas
- **Associations**: Device Associations e Video-Device
- **Error Responses**: Códigos HTTP e mensagens
- **Best Practices**: Como usar a API corretamente
- **Workflows**: Exemplos completos de uso

---

### 📊 Reports API

#### [Reports/History.md](Reports/History.md)
- **Histórico de telemetria** (GPS, velocidade, RPM, odômetro)
- Cursor pagination eficiente para 100k+ registros
- Streaming CSV export
- Export size estimation
- Performance benchmarks (2-3s para 100k records)
- Filtros por veículo

#### [Reports/DriverKmFuel.md](Reports/DriverKmFuel.md)
- **Métricas agregadas por motorista**
- KM percorridos, combustível (L), horas trabalhadas
- Dados diários consolidados
- Cursor pagination eficiente para 40k+ registros
- Streaming CSV export
- Export size estimation
- Filtros por motorista, veículo, subgrupo

#### [Reports/Telemetry.md](Reports/Telemetry.md)
- **Dados avançados de telemetria** (CAN Bus)
- Análise de comportamento do motorista
- Manutenção preventiva
- Eficiência de combustível
- 🚧 Em desenvolvimento

---

### 🧪 Testes

#### [DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md) - Seção "Testing"
- Estrutura de testes
- Como executar testes
- Como escrever novos testes
- Coverage atual e meta

#### [CRUD_STANDARDS.md](CRUD_STANDARDS.md) - Seção "Testing Standards"
- Test structure
- Coverage requirements
- Naming conventions

#### [git-hooks.md](git-hooks.md)
- Pre-commit hook
- Testes automáticos
- Como funciona com Docker
- Bypass em emergências

#### [../docs/TESTING_AUTOMATION_SIMPLE.md](../docs/TESTING_AUTOMATION_SIMPLE.md)
- Guia simples de testes
- Configuração básica

---

### 🚀 Deploy e Infraestrutura

#### [../DEPLOYMENT.md](../DEPLOYMENT.md)
- Deployment automatizado
- CodeCommit + Lambda
- Servidor EC2
- Rollback automático
- Monitoramento

#### [git-hooks.md](git-hooks.md)
- Workflow de desenvolvimento
- Convenções de commit
- Branch strategy

---

### 🔄 Associações de Dispositivos

#### [DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md) - Seção "Associações de Dispositivos"
- Padrão de Associação 1:1
- TrackedUnitDevice (GPS)
- VcmsUnitDevice (Vídeo)
- Regras de negócio
- Implementação completa

#### [API_USAGE.md](API_USAGE.md) - Seção "Device Associations"
- Como criar associações
- Como deletar (soft delete)
- Validações 1:1
- Erros comuns

---

### 🐛 Troubleshooting

#### [DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md) - Seção "Troubleshooting"
- Erro: "Tests failed - commit cancelled"
- Erro: "Field required - group_id"
- Erro: "No access to target group/subgroup"
- Erro: "Vehicle already has an active device"
- Erro: "Cannot run tests - Docker not running"

#### [git-hooks.md](git-hooks.md) - Seção "Troubleshooting"
- Problemas com pre-commit hook
- Docker não rodando
- Pytest não instalado

---

## 📊 Referência Rápida

### Comandos Úteis

```bash
# Docker
docker compose up -d              # Iniciar todos serviços
docker compose down               # Parar todos serviços
docker exec fleet_api bash        # Acessar container

# Testes
docker exec fleet_api python3 -m pytest tests/ -v
docker exec fleet_api python3 -m pytest tests/ -v --cov=app

# Git
git commit -m "feat: add feature"  # Testes executam automaticamente
git commit --no-verify             # Bypass hook (emergência)

# Database
docker compose exec api alembic upgrade head
docker compose exec api alembic revision --autogenerate -m "description"

# Logs
docker compose logs -f api
docker compose logs redis
```

### Estrutura de Código Padrão

```python
# Model
class Resource(Base):
    __tablename__ = "resource"
    __table_args__ = {'schema': 'mova'}

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    group_id = Column(Integer, nullable=False)
    subgroup_id = Column(Integer, nullable=True)
    status = Column(Integer, default=1)
    user_add = Column(Integer)
    date_add = Column(DateTime, server_default=func.now())

# Schema Create
class ResourceCreate(BaseModel):
    name: str
    group_id: int
    subgroup_id: int

# Endpoint CREATE
@router.post("/")
async def create_resource(
    data: ResourceCreate,
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(require_permission("resources", "write"))
):
    # Validar acesso ao grupo
    target = (data.group_id, data.subgroup_id)
    if target not in user.group_access:
        raise HTTPException(403, detail="No access")

    # Criar
    resource = Resource(**data.model_dump(), status=1, user_add=user.user_id)
    db.add(resource)
    await db.commit()
    return resource
```

### Checklist de Novo CRUD

- [ ] Model com campos obrigatórios (id, group_id, status, audit)
- [ ] Schema Base, Create, Update, Response
- [ ] Endpoint LIST com access control
- [ ] Endpoint GET com access control
- [ ] Endpoint CREATE com validação de acesso
- [ ] Endpoint UPDATE com validação de grupo novo
- [ ] Endpoint DELETE (soft delete)
- [ ] Testes para cada endpoint
- [ ] Registrar router em api.py
- [ ] Documentar no README se necessário

---

## 🎓 Roadmap de Aprendizado

### Semana 1: Fundamentos
1. Ler [DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md) completo
2. Ler [CRUD_STANDARDS.md](CRUD_STANDARDS.md) completo
3. Setup ambiente Docker
4. Executar testes existentes
5. Estudar código de `vehicles.py`

### Semana 2: Prática
1. Criar um CRUD simples seguindo template
2. Escrever testes para o novo CRUD
3. Fazer commit e ver hook funcionando
4. Ler [API_USAGE.md](API_USAGE.md)
5. Testar API com Postman/curl

### Semana 3: Avançado
1. Implementar associação customizada
2. Adicionar validações de negócio
3. Otimizar queries
4. Aumentar coverage de testes
5. Contribuir para documentação

---

## 📞 Suporte

- **Documentação**: Comece pelo [DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md)
- **Padrões**: Consulte [CRUD_STANDARDS.md](CRUD_STANDARDS.md)
- **API**: Veja exemplos em [API_USAGE.md](API_USAGE.md)
- **Dúvidas**: Abrir issue no repositório
- **Bugs**: Reportar via GitHub Issues

---

## 🔄 Última Atualização

- **Data**: Novembro 2025
- **Versão da Documentação**: 1.0.0
- **Status**: ✅ Completa e Atualizada

---

## ✅ Checklist para Desenvolvedores Novos

- [ ] Li o [DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md) completo
- [ ] Li o [CRUD_STANDARDS.md](CRUD_STANDARDS.md) completo
- [ ] Configurei ambiente Docker e executei testes
- [ ] Estudei código de um CRUD existente (ex: vehicles.py)
- [ ] Criei meu primeiro CRUD seguindo template
- [ ] Escrevi testes para meu CRUD (>80% coverage)
- [ ] Fiz commit e validei que hook executou testes
- [ ] Li [API_USAGE.md](API_USAGE.md) e testei endpoints
- [ ] Conheço padrões de soft delete e access control
- [ ] Sei como fazer troubleshooting de erros comuns

**Parabéns!** Você está pronto para contribuir com o projeto! 🎉

---

**Mantenha esta documentação atualizada** ao adicionar novos recursos ou padrões!

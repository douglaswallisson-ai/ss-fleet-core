# 📋 Padrões de CRUD - Fleet Management Platform

Documentação dos padrões de desenvolvimento para operações CRUD no sistema.

## 🗑️ Soft Delete - Padrão Obrigatório

### Regra Geral

**TODAS as tabelas que possuem campo `status` DEVEM implementar soft delete ao invés de hard delete.**

### O Que é Soft Delete?

Soft delete é a prática de marcar um registro como "removido" sem deletá-lo fisicamente do banco de dados. Isso permite:

- ✅ Auditoria completa de quem removeu e quando
- ✅ Recuperação de dados removidos acidentalmente
- ✅ Manutenção da integridade referencial
- ✅ Histórico completo para compliance e rastreabilidade

### Implementação Padrão

#### 1. Estrutura de Tabelas com Soft Delete

Todas as tabelas que suportam soft delete devem ter:

```python
class MyModel(Base):
    # Campo de status (obrigatório para soft delete)
    status = Column('status', Integer, nullable=False)
    # 1 = ativo
    # 0 = inativo
    # -1 = removido (soft deleted)

    # Campos de auditoria de criação
    user_add = Column('user_add', Integer, nullable=True)
    date_add = Column('date_add', DateTime, default=datetime.now, nullable=True)

    # Campos de auditoria de modificação
    user_modif = Column('user_modif', Integer, nullable=True)
    date_modif = Column('date_modif', DateTime, nullable=True)

    # Campos de auditoria de remoção
    user_removed = Column('user_removed', Integer, nullable=True)
    date_removed = Column('date_removed', DateTime, nullable=True)
```

#### 2. Endpoint DELETE (Soft Delete)

```python
@router.delete("/{resource_id}", response_model=ResourceResponse)
async def delete_resource(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("resource", "write"))
):
    """
    Soft delete resource by ID.
    Sets status to -1 and updates removal audit fields.
    """
    # 1. Validar acesso do usuário
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # 2. Aplicar filtro de acesso por grupo/subgrupo
    access_filter = build_group_subgroup_filter(current_user.group_access)(Resource)

    # 3. Buscar o recurso
    result = await db.execute(
        select(Resource).where(
            Resource.id == resource_id,
            access_filter
        )
    )
    resource = result.scalar_one_or_none()

    if not resource:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found"
        )

    # 4. SOFT DELETE: Atualizar status e campos de auditoria
    from datetime import datetime
    resource.status = -1
    resource.user_modif = current_user.user_id
    resource.date_modif = datetime.now()
    resource.user_removed = current_user.user_id
    resource.date_removed = datetime.now()

    # 5. Commit e retornar o recurso atualizado
    await db.commit()
    await db.refresh(resource)

    return resource
```

#### 3. Response Model

O endpoint DELETE deve retornar o recurso atualizado (status 200 OK) ao invés de 204 NO_CONTENT:

```python
@router.delete("/{resource_id}", response_model=ResourceResponse)
# ✅ Retorna o objeto atualizado com status -1

# ❌ NÃO USAR:
@router.delete("/{resource_id}", status_code=status.HTTP_204_NO_CONTENT)
```

### Exemplo Completo: Veículos

Implementação de referência em [app/api/v1/endpoints/vehicles.py](../app/api/v1/endpoints/vehicles.py):

```python
@router.delete("/{vehicle_id}", response_model=VehicleResponse)
async def delete_vehicle(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "write"))
):
    """
    Soft delete vehicle by ID (only if user has access to vehicle's group/subgroup).
    Sets status to -1 and updates removal audit fields.
    """
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    result = await db.execute(
        select(Vehicle).where(
            Vehicle.id == vehicle_id,
            access_filter
        )
    )
    vehicle = result.scalar_one_or_none()

    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vehicle not found"
        )

    # Soft delete: update status and audit fields
    from datetime import datetime
    vehicle.status = -1
    vehicle.user_modif = current_user.user_id
    vehicle.date_modif = datetime.now()
    vehicle.user_removed = current_user.user_id
    vehicle.date_removed = datetime.now()

    await db.commit()
    await db.refresh(vehicle)

    return vehicle
```

### Recuperação de Registros Soft Deleted

Para recuperar um registro removido:

```python
@router.post("/{resource_id}/restore", response_model=ResourceResponse)
async def restore_resource(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("resource", "write"))
):
    """Restore a soft-deleted resource by setting status back to 1."""
    # Buscar incluindo registros com status -1
    result = await db.execute(
        select(Resource).where(
            Resource.id == resource_id,
            Resource.status == -1
        )
    )
    resource = result.scalar_one_or_none()

    if not resource:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deleted resource not found"
        )

    # Restaurar
    from datetime import datetime
    resource.status = 1
    resource.user_modif = current_user.user_id
    resource.date_modif = datetime.now()

    await db.commit()
    await db.refresh(resource)

    return resource
```

## 📝 Padrões de CREATE

### Campos Obrigatórios

Ao criar um novo registro, sempre definir:

```python
resource = Resource(
    **resource_data.model_dump(),
    # Controle de acesso
    group_id=first_access[0],
    subgroup_id=first_access[1],
    account_id=current_user.account_id,
    # Status inicial
    status=1,  # Ativo por padrão
    # Auditoria de criação
    user_add=current_user.user_id,
    date_add=datetime.now()
)
```

## 🔄 Padrões de UPDATE

### Campos de Auditoria

Em toda operação de UPDATE, atualizar os campos de modificação:

```python
# Atualizar campos do request
update_data = resource_data.model_dump(exclude_unset=True)
for field, value in update_data.items():
    setattr(resource, field, value)

# SEMPRE atualizar campos de auditoria
from datetime import datetime
resource.user_modif = current_user.user_id
resource.date_modif = datetime.now()

await db.commit()
await db.refresh(resource)
```

## 📊 Filtros em Queries (LIST)

### Excluir Registros Soft Deleted por Padrão

Ao listar recursos, **sempre** excluir registros com `status = -1` por padrão:

```python
@router.get("/", response_model=List[ResourceResponse])
async def list_resources(
    include_deleted: bool = Query(False, description="Include soft-deleted records"),
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("resource", "read"))
):
    """List resources (excludes soft-deleted by default)."""
    access_filter = build_group_subgroup_filter(current_user.group_access)(Resource)

    # Filtro de status
    status_filter = Resource.status != -1 if not include_deleted else True

    result = await db.execute(
        select(Resource)
        .where(access_filter, status_filter)
        .order_by(Resource.date_add.desc())
    )

    return result.scalars().all()
```

## 🚫 Hard Delete - Quando Usar?

Hard delete (remoção física do banco) **só deve ser usado** em casos específicos:

### Casos Permitidos:

1. **Dados temporários**: Sessões, tokens expirados, cache
2. **Dados sensíveis**: Informações que devem ser permanentemente removidas por compliance (LGPD/GDPR)
3. **Limpeza de desenvolvimento**: Dados de teste em ambientes não-produtivos

### Implementação de Hard Delete (casos especiais):

```python
@router.delete("/{resource_id}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def permanent_delete_resource(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("resource", "admin"))
):
    """
    PERMANENT DELETE - Use with extreme caution!
    Only for admin users and specific compliance requirements.
    """
    # Requerer role admin
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can permanently delete records"
        )

    # Buscar e deletar
    result = await db.execute(
        select(Resource).where(Resource.id == resource_id)
    )
    resource = result.scalar_one_or_none()

    if not resource:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found"
        )

    # Log de auditoria crítico
    logger.warning(
        "PERMANENT_DELETE",
        extra={
            "resource_type": "Resource",
            "resource_id": resource_id,
            "user_id": current_user.user_id,
            "timestamp": datetime.now().isoformat()
        }
    )

    await db.delete(resource)
    await db.commit()
```

## 🧪 Testes - Padrão Obrigatório

### Por Que Testar?

**IMPORTANTE**: Estamos trabalhando com base de produção. Testes garantem que:
- ✅ Nenhum dado real seja alterado ou deletado acidentalmente
- ✅ Soft delete funcione corretamente (não delete físico)
- ✅ Controle de acesso seja respeitado
- ✅ Campos de auditoria sejam sempre atualizados

### Estrutura de Testes

**TODA nova API de CRUD DEVE ter arquivo de teste correspondente:**

```
tests/
├── test_access_control_security.py  # Testes de segurança (existente)
├── test_vehicles_api.py             # Testes de veículos (referência)
├── test_drivers_api.py              # Próximo CRUD
└── test_devices_api.py              # Próximo CRUD
```

### Testes Obrigatórios para Cada CRUD

Cada arquivo de teste DEVE incluir as seguintes suítes:

#### 1. TestResourceList (GET /)
```python
class TestResourceList:
    """Test suite for GET /api/v1/resources - List resources."""

    @pytest.mark.asyncio
    async def test_list_excludes_soft_deleted_by_default(self):
        """Verify status=-1 records are excluded by default."""
        # Test implementation

    @pytest.mark.asyncio
    async def test_list_respects_group_access_control(self):
        """Verify only accessible groups/subgroups are returned."""
        # Test implementation

    @pytest.mark.asyncio
    async def test_list_with_null_subgroup_sees_shared_only(self):
        """Verify NULL subgroup users only see shared resources."""
        # Test implementation
```

#### 2. TestResourceGet (GET /{id})
```python
class TestResourceGet:
    """Test suite for GET /api/v1/resources/{id}."""

    @pytest.mark.asyncio
    async def test_get_returns_404_if_not_found(self):
        """Verify 404 when resource doesn't exist."""
        # Test implementation

    @pytest.mark.asyncio
    async def test_get_returns_404_if_soft_deleted(self):
        """Verify soft-deleted resources return 404."""
        # Test implementation
```

#### 3. TestResourceCreate (POST /)
```python
class TestResourceCreate:
    """Test suite for POST /api/v1/resources."""

    @pytest.mark.asyncio
    async def test_create_sets_default_fields(self):
        """Verify status=1, user_add, date_add are set."""
        # Test implementation

    @pytest.mark.asyncio
    async def test_create_rejects_duplicates(self):
        """Verify duplicate validation works."""
        # Test implementation
```

#### 4. TestResourceUpdate (PUT /{id})
```python
class TestResourceUpdate:
    """Test suite for PUT /api/v1/resources/{id}."""

    @pytest.mark.asyncio
    async def test_update_sets_audit_fields(self):
        """Verify user_modif and date_modif are updated."""
        # Test implementation

    @pytest.mark.asyncio
    async def test_update_respects_access_control(self):
        """Verify access control prevents unauthorized updates."""
        # Test implementation
```

#### 5. TestResourceSoftDelete (DELETE /{id})
```python
class TestResourceSoftDelete:
    """Test suite for DELETE /api/v1/resources/{id}."""

    @pytest.mark.asyncio
    async def test_soft_delete_sets_status_to_minus_one(self):
        """Verify status=-1 and audit fields are set."""
        # Verify: status=-1, user_removed, date_removed, user_modif, date_modif
        # Test implementation

    @pytest.mark.asyncio
    async def test_soft_delete_returns_updated_resource(self):
        """Verify DELETE returns 200 OK with resource body."""
        # Test implementation

    @pytest.mark.asyncio
    async def test_soft_delete_respects_access_control(self):
        """Verify access control prevents unauthorized deletes."""
        # Test implementation

    @pytest.mark.asyncio
    async def test_cannot_soft_delete_already_deleted(self):
        """Verify already deleted resources return 404."""
        # Test implementation
```

#### 6. TestResourceAuditTrail
```python
class TestResourceAuditTrail:
    """Test audit trail validation."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_audit_trail(self):
        """Test CREATE → UPDATE → DELETE audit trail."""
        # Verify all audit fields throughout lifecycle
        # Test implementation
```

### Exemplo Completo de Teste

Ver arquivo de referência: [tests/test_vehicles_api.py](../tests/test_vehicles_api.py)

```python
"""
Resource API Tests - CRUD Operations with Soft Delete.

IMPORTANT: These tests use mocking to prevent modifications to production database.
All database operations are mocked to ensure data safety.
"""

import pytest
from datetime import datetime
from app.models.resource import Resource

class TestResourceSoftDelete:
    @pytest.mark.asyncio
    async def test_soft_delete_sets_status_to_minus_one(self):
        """Test: Soft delete should set status to -1."""
        current_user_id = 123

        resource = Resource(
            id=1,
            name="Test Resource",
            status=1,  # Active
            user_add=100
        )

        # Simulate soft delete
        resource.status = -1
        resource.user_modif = current_user_id
        resource.date_modif = datetime.now()
        resource.user_removed = current_user_id
        resource.date_removed = datetime.now()

        # Assertions
        assert resource.status == -1
        assert resource.user_removed == current_user_id
        assert resource.date_removed is not None
        assert resource.user_modif == current_user_id
        assert resource.date_modif is not None
        assert resource is not None  # Still exists (not physically deleted)
```

### Rodando os Testes

#### Manual
```bash
# Todos os testes
pytest tests/ -v

# Teste específico
pytest tests/test_vehicles_api.py -v

# Com coverage
pytest tests/ --cov=app --cov-report=html
```

#### Automático (Git Hooks)

Os testes rodam automaticamente:
- **Pre-commit**: Roda todos os testes antes de cada commit
- **Pre-push**: Roda todos os testes antes de cada push

```bash
# Commit (testes rodam automaticamente)
git add .
git commit -m "Add new CRUD"

# Se os testes falharem, commit é bloqueado
# Corrija os erros e tente novamente

# Para pular (emergência apenas)
git commit --no-verify -m "hotfix"
```

### Configuração dos Hooks

Já configurado! Veja:
- [.git/hooks/pre-commit](.git/hooks/pre-commit) - Testes de segurança + CRUD
- [.git/hooks/pre-push](.git/hooks/pre-push) - Todos os testes

## ✅ Checklist de Implementação

Ao criar um novo endpoint CRUD:

### Código
- [ ] Modelo tem campo `status`?
- [ ] Modelo tem campos de auditoria (`user_add`, `date_add`, `user_modif`, `date_modif`, `user_removed`, `date_removed`)?
- [ ] CREATE define `status=1` e `user_add`/`date_add`?
- [ ] UPDATE atualiza `user_modif`/`date_modif`?
- [ ] DELETE implementa soft delete (status=-1) ao invés de hard delete?
- [ ] DELETE atualiza `user_removed`/`date_removed`?
- [ ] DELETE retorna o recurso atualizado (200 OK) ao invés de 204?
- [ ] LIST exclui registros com `status=-1` por padrão?
- [ ] Existe endpoint `/restore` para recuperação (opcional)?
- [ ] Controle de acesso por grupo/subgrupo está aplicado?

### Testes (Obrigatório!)
- [ ] Criado arquivo `tests/test_<resource>_api.py`?
- [ ] Testes de LIST (soft delete, access control, NULL subgroup)?
- [ ] Testes de GET (404, soft deleted)?
- [ ] Testes de CREATE (campos default, duplicatas)?
- [ ] Testes de UPDATE (audit fields, access control)?
- [ ] Testes de SOFT DELETE (status=-1, audit fields, access control)?
- [ ] Teste de audit trail completo (CREATE→UPDATE→DELETE)?
- [ ] Todos os testes passando localmente (`pytest tests/test_<resource>_api.py -v`)?
- [ ] Git hook configurado e testado?

## 📚 Referências

- Implementação de referência: [app/api/v1/endpoints/vehicles.py](../app/api/v1/endpoints/vehicles.py)
- Modelo de referência: [app/models/vehicle.py](../app/models/vehicle.py)
- Controle de acesso: [app/core/access_control.py](../app/core/access_control.py)
- Segurança: [SECURITY.md](../SECURITY.md)

---

**Criado**: 2025-11-16
**Última atualização**: 2025-11-16
**Versão**: 1.0.0

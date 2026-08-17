# CRUD Standards - Fleet Management Platform

## 📋 Índice

1. [Visão Geral](#visão-geral)
2. [Soft Delete Pattern](#soft-delete-pattern)
3. [Access Control](#access-control)
4. [Audit Trail](#audit-trail)
5. [Schema Patterns](#schema-patterns)
6. [Endpoint Patterns](#endpoint-patterns)
7. [Validation Rules](#validation-rules)
8. [Error Handling](#error-handling)
9. [Testing Standards](#testing-standards)

---

## Visão Geral

Este documento define os **padrões obrigatórios** para todas operações CRUD no sistema Fleet Management Platform.

### Princípios Fundamentais

1. **Soft Delete**: NUNCA delete fisicamente registros
2. **Access Control**: SEMPRE validar acesso por grupo/subgrupo
3. **Audit Trail**: SEMPRE preencher campos de auditoria
4. **Type Safety**: SEMPRE usar type hints completos
5. **Async/Await**: SEMPRE usar operações assíncronas

---

## Soft Delete Pattern

### Regra de Ouro

> **NUNCA delete fisicamente registros do banco de dados**

### Status Values

```python
status = 1   # Ativo (active)
status = 0   # Inativo (inactive, disabled)
status = -1  # Deletado (soft deleted)
```

### Implementação Correta

```python
# ✅ CORRETO - Soft Delete
@router.delete("/{resource_id}")
async def delete_resource(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("resources", "delete"))
):
    # Buscar recurso
    result = await db.execute(
        select(Resource).where(
            Resource.id == resource_id,
            Resource.status != -1  # Excluir já deletados
        )
    )
    resource = result.scalar_one_or_none()

    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    # Soft delete
    resource.status = -1
    resource.user_modif = current_user.user_id
    resource.date_modif = datetime.now()

    await db.commit()
    await db.refresh(resource)

    return resource


# ❌ ERRADO - Hard Delete (NUNCA FAZER)
@router.delete("/{resource_id}")
async def delete_resource(resource_id: int, db: AsyncSession = Depends(get_db)):
    resource = await db.get(Resource, resource_id)
    await db.delete(resource)  # ❌ DELETE FÍSICO
    await db.commit()
```

### Filtrar Soft-Deleted em Queries

```python
# ✅ LIST - Excluir soft-deleted por padrão
@router.get("/")
async def list_resources(
    include_deleted: bool = Query(False),  # Parâmetro opcional
    db: AsyncSession = Depends(get_db)
):
    query = select(Resource)

    if not include_deleted:
        query = query.where(Resource.status != -1)

    result = await db.execute(query)
    return result.scalars().all()


# ✅ GET - Sempre excluir soft-deleted
@router.get("/{resource_id}")
async def get_resource(resource_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Resource).where(
            Resource.id == resource_id,
            Resource.status != -1  # ✅ OBRIGATÓRIO
        )
    )
    resource = result.scalar_one_or_none()

    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    return resource
```

### Reativação de Registros

Se necessário reativar um registro soft-deleted:

```python
@router.put("/{resource_id}/reactivate")
async def reactivate_resource(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("resources", "write"))
):
    # Buscar incluindo soft-deleted
    result = await db.execute(
        select(Resource).where(Resource.id == resource_id)
    )
    resource = result.scalar_one_or_none()

    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    if resource.status != -1:
        raise HTTPException(status_code=400, detail="Resource is not deleted")

    # Reativar
    resource.status = 1
    resource.user_modif = current_user.user_id
    resource.date_modif = datetime.now()

    await db.commit()
    await db.refresh(resource)

    return resource
```

---

## Access Control

### Modelo de Grupos e Subgrupos

Cada recurso pertence a um `(group_id, subgroup_id)` e cada usuário tem acesso a uma lista de pares:

```python
# Usuário com múltiplos acessos
current_user.group_access = [
    (14330, 15812),  # Grupo 14330, Subgrupo 15812 (acesso específico)
    (14330, 15813),  # Grupo 14330, Subgrupo 15813 (acesso específico)
    (14331, None),   # Grupo 14331, TODOS subgrupos (acesso amplo)
]
```

### Validação de Acesso - Checklist

Para CADA endpoint CRUD, validar:

- [ ] **LIST**: Filtrar apenas registros acessíveis
- [ ] **GET**: Verificar acesso ao registro específico
- [ ] **CREATE**: Validar acesso ao grupo/subgrupo alvo
- [ ] **UPDATE**: Validar acesso ao registro atual E ao novo grupo (se alterado)
- [ ] **DELETE**: Validar acesso ao registro antes de deletar

### Padrão de Implementação

#### 1. LIST - Filtrar Registros Acessíveis

```python
@router.get("/", response_model=List[ResourceResponse])
async def list_resources(
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("resources", "read"))
):
    # ✅ 1. Verificar se usuário tem algum acesso
    if not current_user.group_access:
        return []  # Sem acesso = lista vazia

    # ✅ 2. Construir filtro de acesso
    access_filter = build_group_subgroup_filter(current_user.group_access)(Resource)

    # ✅ 3. Aplicar filtro em query
    query = select(Resource).where(
        access_filter,
        Resource.status != -1  # Excluir soft-deleted
    )

    result = await db.execute(query)
    return result.scalars().all()
```

#### 2. GET - Validar Acesso ao Registro

```python
@router.get("/{resource_id}", response_model=ResourceResponse)
async def get_resource(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("resources", "read"))
):
    # ✅ 1. Verificar acesso
    if not current_user.group_access:
        raise HTTPException(status_code=403, detail="No group access")

    # ✅ 2. Buscar COM filtro de acesso
    access_filter = build_group_subgroup_filter(current_user.group_access)(Resource)

    result = await db.execute(
        select(Resource).where(
            Resource.id == resource_id,
            access_filter,           # ✅ OBRIGATÓRIO
            Resource.status != -1    # ✅ OBRIGATÓRIO
        )
    )
    resource = result.scalar_one_or_none()

    # ✅ 3. 404 se não encontrado OU sem acesso
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    return resource
```

#### 3. CREATE - Validar Acesso ao Grupo Alvo

```python
@router.post("/", response_model=ResourceResponse, status_code=201)
async def create_resource(
    resource_data: ResourceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("resources", "write"))
):
    # ✅ 1. Verificar acesso
    if not current_user.group_access:
        raise HTTPException(status_code=403, detail="No group access")

    # ✅ 2. Validar acesso ao grupo/subgrupo alvo
    target_group = (resource_data.group_id, resource_data.subgroup_id)
    if target_group not in current_user.group_access:
        raise HTTPException(
            status_code=403,
            detail="No access to target group/subgroup"
        )

    # ✅ 3. Criar recurso
    resource = Resource(
        **resource_data.model_dump(),
        account_id=539,
        status=1,
        user_add=current_user.user_id
    )

    db.add(resource)
    await db.commit()
    await db.refresh(resource)

    return resource
```

#### 4. UPDATE - Validar Acesso ao Registro E ao Novo Grupo

```python
@router.put("/{resource_id}", response_model=ResourceResponse)
async def update_resource(
    resource_id: int,
    resource_data: ResourceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("resources", "write"))
):
    # ✅ 1. Verificar acesso
    if not current_user.group_access:
        raise HTTPException(status_code=403, detail="No group access")

    # ✅ 2. Buscar recurso COM validação de acesso
    access_filter = build_group_subgroup_filter(current_user.group_access)(Resource)

    result = await db.execute(
        select(Resource).where(
            Resource.id == resource_id,
            access_filter,
            Resource.status != -1
        )
    )
    resource = result.scalar_one_or_none()

    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    # ✅ 3. Se mudando grupo/subgrupo, validar acesso ao novo grupo
    update_data = resource_data.model_dump(exclude_unset=True)

    if 'group_id' in update_data or 'subgroup_id' in update_data:
        new_group_id = update_data.get('group_id', resource.group_id)
        new_subgroup_id = update_data.get('subgroup_id', resource.subgroup_id)
        target_group = (new_group_id, new_subgroup_id)

        if target_group not in current_user.group_access:
            raise HTTPException(
                status_code=403,
                detail="No access to target group/subgroup"
            )

    # ✅ 4. Aplicar updates
    for key, value in update_data.items():
        setattr(resource, key, value)

    # ✅ 5. Audit trail
    resource.user_modif = current_user.user_id
    resource.date_modif = datetime.now()

    await db.commit()
    await db.refresh(resource)

    return resource
```

#### 5. DELETE - Validar Acesso Antes de Soft Delete

```python
@router.delete("/{resource_id}", response_model=ResourceResponse)
async def delete_resource(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("resources", "delete"))
):
    # ✅ 1. Verificar acesso
    if not current_user.group_access:
        raise HTTPException(status_code=403, detail="No group access")

    # ✅ 2. Buscar COM validação de acesso
    access_filter = build_group_subgroup_filter(current_user.group_access)(Resource)

    result = await db.execute(
        select(Resource).where(
            Resource.id == resource_id,
            access_filter,
            Resource.status != -1
        )
    )
    resource = result.scalar_one_or_none()

    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    # ✅ 3. Soft delete com audit trail
    resource.status = -1
    resource.user_modif = current_user.user_id
    resource.date_modif = datetime.now()

    await db.commit()
    await db.refresh(resource)

    return resource
```

### Access Control para Recursos Sem Subgrupo

Alguns recursos (ex: `devices`) têm apenas `group_id`, sem `subgroup_id`:

```python
# Para recursos com apenas group_id
target_group_id = resource_data.group_id

accessible_group_ids = [
    group_id for group_id, subgroup_id in current_user.group_access
]

if target_group_id not in accessible_group_ids:
    raise HTTPException(status_code=403, detail="No access to target group")
```

---

## Audit Trail

### Campos Obrigatórios

Todos os modelos devem ter:

```python
class Resource(Base):
    # ... outros campos ...

    # Audit fields - OBRIGATÓRIOS
    user_add = Column(Integer, nullable=True)      # Quem criou
    date_add = Column(DateTime, server_default=func.now())  # Quando criou
    user_modif = Column(Integer, nullable=True)    # Quem modificou
    date_modif = Column(DateTime, onupdate=func.now())      # Quando modificou
```

### Preencher Audit Trail

#### CREATE
```python
resource = Resource(
    **resource_data.model_dump(),
    user_add=current_user.user_id,  # ✅ OBRIGATÓRIO
    # date_add preenchido automaticamente pelo DB
)
```

#### UPDATE
```python
# Aplicar updates
for key, value in update_data.items():
    setattr(resource, key, value)

# Audit trail - ✅ OBRIGATÓRIO
resource.user_modif = current_user.user_id
resource.date_modif = datetime.now()
```

#### DELETE (Soft Delete)
```python
resource.status = -1
resource.user_modif = current_user.user_id  # ✅ OBRIGATÓRIO
resource.date_modif = datetime.now()        # ✅ OBRIGATÓRIO
```

---

## Schema Patterns

### Estrutura de Schemas

Para cada recurso, criar 4 schemas Pydantic:

1. **Base** - Campos compartilhados
2. **Create** - Para criação (POST)
3. **Update** - Para atualização (PUT)
4. **Response** - Para resposta (GET)

### Exemplo Completo

```python
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

# 1. Base - Campos compartilhados entre Create e outros
class ResourceBase(BaseModel):
    """Campos de negócio compartilhados."""
    name: str = Field(..., min_length=1, max_length=255, description="Resource name")
    description: Optional[str] = Field(None, max_length=500)
    active: bool = Field(default=True)


# 2. Create - Campos obrigatórios para criação
class ResourceCreate(ResourceBase):
    """
    Schema para criação de recurso.

    Campos gerenciados automaticamente (NÃO enviar):
    - id: Auto-incremento
    - account_id: Fixo (539)
    - status: Sempre 1 (ativo)
    - user_add: Do JWT token
    - date_add: NOW()
    """
    # Usuário DEVE escolher grupo/subgrupo
    group_id: int = Field(..., description="Group ID (user must have access)")
    subgroup_id: int = Field(..., description="Subgroup ID (user must have access)")


# 3. Update - Todos campos opcionais
class ResourceUpdate(BaseModel):
    """Schema para atualização de recurso."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=500)
    active: Optional[bool] = None

    # Permitir mudança de grupo/subgrupo (com validação de acesso)
    group_id: Optional[int] = Field(None, description="Group ID (user must have access)")
    subgroup_id: Optional[int] = Field(None, description="Subgroup ID (user must have access)")

    # Permitir mudança de status (para reativar registros)
    status: Optional[int] = Field(None, ge=-1, le=1, description="-1=deleted, 0=inactive, 1=active")


# 4. Response - Todos campos do modelo
class ResourceResponse(BaseModel):
    """Schema para resposta."""
    # Primary Key
    id: int

    # Business Fields
    name: str
    description: Optional[str]
    active: bool

    # Access Control
    group_id: int
    subgroup_id: int
    account_id: int

    # Status
    status: int

    # Audit Trail
    user_add: Optional[int]
    date_add: Optional[datetime]
    user_modif: Optional[int]
    date_modif: Optional[datetime]

    # Pydantic v2 config
    model_config = {"from_attributes": True}
```

### Validações Customizadas

Use `field_validator` do Pydantic v2:

```python
from pydantic import field_validator

class ResourceUpdate(BaseModel):
    release_date: Optional[datetime] = None
    association_date: Optional[datetime] = None

    @field_validator('release_date')
    @classmethod
    def validate_release_date(cls, v, info):
        """Validar que release_date é depois de association_date."""
        if v is not None and 'association_date' in info.data:
            association_date = info.data['association_date']
            if association_date and v <= association_date:
                raise ValueError('release_date must be after association_date')
        return v
```

---

## Endpoint Patterns

### URL Structure

```
/api/v1/{resource}              # Lista (GET), Criar (POST)
/api/v1/{resource}/{id}         # Get (GET), Update (PUT), Delete (DELETE)
/api/v1/{resource}/{id}/action  # Ações específicas
```

### Response Status Codes

```python
# Sucesso
200 - OK (GET, PUT)
201 - Created (POST)
204 - No Content (DELETE)

# Erros Cliente
400 - Bad Request (validação falhou)
401 - Unauthorized (sem autenticação)
403 - Forbidden (sem permissão/acesso)
404 - Not Found (registro não existe ou sem acesso)

# Erros Servidor
500 - Internal Server Error (erro não tratado)
```

### Pagination Pattern

Para endpoints LIST:

```python
@router.get("/", response_model=List[ResourceResponse])
async def list_resources(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Max records to return"),
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("resources", "read"))
):
    # ... access control ...

    query = (
        select(Resource)
        .where(access_filter, Resource.status != -1)
        .order_by(Resource.date_add.desc())  # Ordenar por data
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(query)
    return result.scalars().all()
```

### Filtering Pattern

```python
@router.get("/", response_model=List[ResourceResponse])
async def list_resources(
    group_id: int = Query(None, description="Filter by group ID"),
    status: int = Query(None, ge=-1, le=1, description="Filter by status"),
    include_deleted: bool = Query(False, description="Include soft-deleted"),
    db: AsyncSession = Depends(get_db)
):
    query = select(Resource).where(access_filter)

    # Filtros opcionais
    if group_id:
        query = query.where(Resource.group_id == group_id)

    if status is not None:
        query = query.where(Resource.status == status)

    if not include_deleted:
        query = query.where(Resource.status != -1)

    result = await db.execute(query)
    return result.scalars().all()
```

---

## Validation Rules

### Business Rule Validation

Validações de regras de negócio devem ocorrer nos endpoints:

```python
@router.post("/", response_model=ResourceResponse, status_code=201)
async def create_resource(
    resource_data: ResourceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("resources", "write"))
):
    # ✅ 1. Validar unicidade
    existing = await db.execute(
        select(Resource).where(
            Resource.name == resource_data.name,
            Resource.group_id == resource_data.group_id,
            Resource.status != -1
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="Resource name already exists in this group"
        )

    # ✅ 2. Validar referências existem
    if resource_data.category_id:
        category = await db.get(Category, resource_data.category_id)
        if not category or category.status != 1:
            raise HTTPException(
                status_code=404,
                detail="Category not found or not active"
            )

    # ✅ 3. Validar regras de negócio
    if resource_data.min_value > resource_data.max_value:
        raise HTTPException(
            status_code=400,
            detail="min_value cannot be greater than max_value"
        )

    # Criar recurso
    resource = Resource(**resource_data.model_dump(), ...)
    # ...
```

### Uniqueness Constraints

```python
# Unique per group
existing = await db.execute(
    select(Resource).where(
        Resource.identifier == resource_data.identifier,
        Resource.group_id == resource_data.group_id,
        Resource.status != -1
    )
)
if existing.scalar_one_or_none():
    raise HTTPException(
        status_code=400,
        detail="Identifier already exists in this group"
    )
```

---

## Error Handling

### HTTPException Pattern

Use `HTTPException` do FastAPI para erros:

```python
from fastapi import HTTPException, status

# 400 - Bad Request (validação)
raise HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST,
    detail="Invalid data: name cannot be empty"
)

# 403 - Forbidden (sem permissão)
raise HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="No access to target group/subgroup"
)

# 404 - Not Found
raise HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Resource not found"
)
```

### Error Messages

Mensagens de erro devem ser:
- **Claras**: Explicar exatamente o que está errado
- **Específicas**: Detalhar o campo/valor problemático
- **Acionáveis**: Indicar como corrigir

```python
# ✅ BOM
"No access to target group/subgroup"
"Vehicle already has an active device. Release it first."
"CPF already exists in this group"

# ❌ RUIM
"Error"
"Invalid"
"Forbidden"
```

---

## Testing Standards

### Test Structure

Organize testes por operação CRUD:

```python
# tests/test_resources_api.py

class TestResourceList:
    """Testes para GET /api/v1/resources"""

    @pytest.mark.asyncio
    async def test_list_excludes_soft_deleted_by_default(self):
        # ...

    @pytest.mark.asyncio
    async def test_list_respects_access_control(self):
        # ...


class TestResourceCreate:
    """Testes para POST /api/v1/resources"""

    @pytest.mark.asyncio
    async def test_create_sets_default_fields(self):
        # ...

    @pytest.mark.asyncio
    async def test_create_validates_access_to_target_group(self):
        # ...


class TestResourceSoftDelete:
    """Testes para DELETE /api/v1/resources/{id}"""

    @pytest.mark.asyncio
    async def test_soft_delete_sets_status_to_minus_one(self):
        # ...

    @pytest.mark.asyncio
    async def test_soft_delete_respects_access_control(self):
        # ...
```

### Test Coverage Requirements

Cada CRUD deve ter testes para:

- [ ] **LIST**: Filtrar soft-deleted, aplicar access control, paginação
- [ ] **GET**: Encontrar registro, 404 se não existe, validar access control
- [ ] **CREATE**: Criar com defaults corretos, validar access control, validar unicidade
- [ ] **UPDATE**: Atualizar campos, audit trail, validar mudança de grupo
- [ ] **DELETE**: Soft delete, não hard delete, audit trail, access control

### Naming Convention

```python
def test_{operation}_{what_it_tests}():
    """
    Test: {Operation} should {expected behavior}.

    Expected:
        - {specific expectation 1}
        - {specific expectation 2}
    """
```

Exemplo:
```python
@pytest.mark.asyncio
async def test_create_vehicle_sets_default_fields(self):
    """
    Test: Create vehicle should set default fields correctly.

    Expected:
        - status = 1 (active)
        - user_add = current_user.user_id
        - account_id = 539
    """
```

---

## Checklist de Implementação

Ao criar um novo CRUD, verificar:

### Model
- [ ] Herda de `Base`
- [ ] Schema correto (`mova.*`)
- [ ] Campos `group_id`, `subgroup_id` (se aplicável)
- [ ] Campo `status` (Integer, default=1)
- [ ] Campos audit: `user_add`, `date_add`, `user_modif`, `date_modif`
- [ ] Indexes apropriados

### Schemas
- [ ] `Base` schema com campos compartilhados
- [ ] `Create` schema com `group_id`/`subgroup_id` obrigatórios
- [ ] `Update` schema com todos campos opcionais
- [ ] `Response` schema com todos campos do model
- [ ] `model_config = {"from_attributes": True}` no Response

### Endpoints
- [ ] **LIST**: Access control + filtrar soft-deleted + paginação
- [ ] **GET**: Access control + filtrar soft-deleted + 404
- [ ] **CREATE**: Validar access control ao target group + audit trail
- [ ] **UPDATE**: Validar access control ao registro e novo grupo + audit trail
- [ ] **DELETE**: Soft delete + access control + audit trail
- [ ] Type hints completos
- [ ] Async/await em todas operações I/O
- [ ] HTTPException com status codes corretos

### Tests
- [ ] Testes para LIST (access control, soft delete)
- [ ] Testes para GET (404, access control)
- [ ] Testes para CREATE (defaults, access control, validação)
- [ ] Testes para UPDATE (audit trail, mudança de grupo)
- [ ] Testes para DELETE (soft delete, access control)
- [ ] Coverage mínimo de 80% para o novo endpoint

### Documentation
- [ ] Docstrings em todos endpoints
- [ ] Descrições em Field() dos schemas
- [ ] Atualizar README.md se necessário

---

**Última Atualização**: Novembro 2025
**Versão**: 1.0.0
**Status**: ✅ Padrão Obrigatório

# Guia de Desenvolvimento - Fleet Management Platform

## 📚 Índice

1. [Visão Geral](#visão-geral)
2. [Arquitetura do Sistema](#arquitetura-do-sistema)
3. [Padrões de Código](#padrões-de-código)
4. [Controle de Acesso](#controle-de-acesso)
5. [CRUD Operations](#crud-operations)
6. [Associações de Dispositivos](#associações-de-dispositivos)
7. [Testing](#testing)
8. [Git Workflow](#git-workflow)
9. [Troubleshooting](#troubleshooting)

---

## Visão Geral

Este é um sistema completo de gerenciamento de frotas construído com:
- **FastAPI** (Python 3.11+) - Framework web assíncrono
- **SQLAlchemy 2.0** - ORM assíncrono
- **Pydantic v2** - Validação de dados
- **PostgreSQL/Aurora** - Banco de dados relacional
- **Redis** - Cache e sessões
- **Docker** - Containerização

### Características Principais

- ✅ **Soft Delete Pattern**: Todos os registros usam `status=-1` para deleção lógica
- ✅ **Access Control**: Controle granular por grupo/subgrupo
- ✅ **Audit Trail**: Campos automáticos `user_add`, `date_add`, `user_modif`, `date_modif`
- ✅ **Async/Await**: Todas operações de I/O são assíncronas
- ✅ **Type Safety**: Type hints em todo código-base
- ✅ **Test Coverage**: 91 testes automatizados com 37% de cobertura

---

## Arquitetura do Sistema

### Estrutura de Diretórios

```
app/
├── api/                        # Camada de API
│   └── v1/
│       ├── api.py             # Router principal
│       └── endpoints/         # Endpoints por recurso
│           ├── auth.py        # Autenticação
│           ├── vehicles.py    # Veículos
│           ├── devices.py     # Dispositivos
│           ├── drivers.py     # Motoristas
│           ├── tracked_unit_devices.py    # Associações veículo-dispositivo
│           ├── vcms_unit_devices.py       # Associações vídeo
│           └── reports.py     # Relatórios
│
├── core/                      # Núcleo do sistema
│   ├── config.py             # Configurações (Settings)
│   ├── database.py           # Conexão DB e sessões
│   ├── security.py           # Hashing, JWT, validações
│   ├── access_control.py     # Helpers de controle de acesso
│   └── query_filters.py      # Filtros SQL reutilizáveis
│
├── middleware/               # Middlewares
│   ├── auth.py              # JWT e API Key auth
│   ├── access_control.py    # Validação de permissões
│   └── monitoring.py        # Métricas e logging
│
├── models/                   # SQLAlchemy Models
│   ├── user.py              # Usuário
│   ├── vehicle.py           # Veículo (tracked_unit)
│   ├── device.py            # Dispositivo
│   ├── driver.py            # Motorista (con_driver)
│   ├── tracked_unit_device.py    # Associação veículo-dispositivo
│   └── vcms_unit_device.py       # Associação vídeo
│
├── schemas/                  # Pydantic Schemas
│   ├── user.py              # Request/Response schemas
│   ├── vehicle.py           # VehicleCreate, VehicleUpdate, VehicleResponse
│   ├── device.py            # DeviceCreate, DeviceUpdate, DeviceResponse
│   ├── driver.py            # DriverCreate, DriverUpdate, DriverResponse
│   └── ...
│
└── services/                 # Business Logic (futuro)
    └── ...

tests/                        # Testes automatizados
├── test_access_control_security.py
├── test_vehicles_api.py
├── test_devices_api.py
├── test_driver_api.py
├── test_tracked_unit_device_api.py
└── test_vcms_unit_device_api.py
```

### Fluxo de Requisição

```
Request → Middleware (Auth) → Endpoint → Database → Response
          ↓
          Access Control Validation
          ↓
          Permission Check
```

### Database Schema (mova.*)

O sistema utiliza o schema `mova.*` do banco legado:

```sql
-- Tabelas principais
tracked_unit           -- Veículos/unidades rastreadas
device                -- Dispositivos de rastreamento
con_driver            -- Motoristas (condutores)
tracked_unit_device   -- Associação veículo ↔ dispositivo (1:1)
vcms_unit_device      -- Associação veículo ↔ dispositivo de vídeo (1:1)

-- Tabelas de telemetria
dev_status_30_pYYYYMMDD  -- Histórico GPS (particionado por dia)
con_driver_h_km          -- Métricas agregadas por motorista

-- Tabelas de suporte
user                  -- Usuários do sistema
user_group_access     -- Controle de acesso por grupo/subgrupo
driver_function       -- Funções de motoristas
```

---

## Padrões de Código

### 1. Soft Delete Pattern

**NUNCA delete fisicamente registros do banco**. Sempre use soft delete:

```python
# ❌ ERRADO - DELETE físico
await db.delete(vehicle)

# ✅ CORRETO - Soft delete
vehicle.status = -1
vehicle.user_modif = current_user.user_id
vehicle.date_modif = datetime.now()
await db.commit()
```

**Valores de status:**
- `1` = Ativo
- `0` = Inativo (desabilitado, mas não deletado)
- `-1` = Deletado (soft delete)

**Sempre filtrar soft-deleted por padrão:**

```python
# Em queries LIST
query = select(Vehicle).where(Vehicle.status != -1)

# Em queries GET
query = select(Vehicle).where(
    Vehicle.id == vehicle_id,
    Vehicle.status != -1
)
```

### 2. Audit Trail (Campos de Auditoria)

Todos os modelos têm campos de auditoria que devem ser preenchidos:

```python
# Na criação
vehicle = Vehicle(
    **vehicle_data.model_dump(),
    account_id=539,           # Fixo
    status=1,                 # Ativo
    user_add=current_user.user_id,      # Quem criou
    date_add=datetime.now()             # Quando criou (auto pelo DB)
)

# Na atualização
vehicle.user_modif = current_user.user_id
vehicle.date_modif = datetime.now()

# Na deleção (soft delete)
vehicle.status = -1
vehicle.user_modif = current_user.user_id
vehicle.date_modif = datetime.now()
```

### 3. Type Hints Obrigatórios

Todo código deve ter type hints completos:

```python
# ✅ CORRETO
async def get_vehicle(
    vehicle_id: int,
    db: AsyncSession,
    current_user: AuthenticatedUser
) -> Vehicle:
    result = await db.execute(
        select(Vehicle).where(Vehicle.id == vehicle_id)
    )
    return result.scalar_one_or_none()

# ❌ ERRADO - sem type hints
async def get_vehicle(vehicle_id, db, current_user):
    result = await db.execute(...)
    return result.scalar_one_or_none()
```

### 4. Async/Await Pattern

Todas operações de I/O devem ser assíncronas:

```python
# ✅ CORRETO - async/await
@router.get("/{vehicle_id}")
async def get_vehicle(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Vehicle).where(Vehicle.id == vehicle_id)
    )
    return result.scalar_one_or_none()

# ❌ ERRADO - sync
@router.get("/{vehicle_id}")
def get_vehicle(vehicle_id: int, db: Session = Depends(get_db)):
    return db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
```

### 5. Pydantic Schemas

Use Pydantic v2 com configurações adequadas:

```python
class VehicleBase(BaseModel):
    """Base schema - campos compartilhados."""
    label: str = Field(..., min_length=1, max_length=255)
    timezone: int = Field(default=-3)

class VehicleCreate(VehicleBase):
    """Schema para criação - campos obrigatórios do usuário."""
    group_id: int = Field(..., description="Group ID (user must have access)")
    subgroup_id: int = Field(..., description="Subgroup ID (user must have access)")

class VehicleUpdate(BaseModel):
    """Schema para atualização - todos campos opcionais."""
    label: Optional[str] = Field(None, min_length=1, max_length=255)
    group_id: Optional[int] = None
    status: Optional[int] = Field(None, ge=0, le=1)

class VehicleResponse(BaseModel):
    """Schema para resposta - todos campos do modelo."""
    id: int
    label: str
    group_id: int
    status: int
    date_add: Optional[datetime]

    model_config = {"from_attributes": True}  # Pydantic v2
```

---

## Controle de Acesso

### Modelo de Grupos e Subgrupos

Cada usuário tem acesso a uma lista de pares `(group_id, subgroup_id)`:

```python
# Usuário com acesso a múltiplos grupos/subgrupos
current_user.group_access = [
    (14330, 15812),  # Grupo 14330, Subgrupo 15812
    (14330, 15813),  # Grupo 14330, Subgrupo 15813
    (14331, None),   # Grupo 14331, TODOS os subgrupos (NULL)
]
```

**Regras:**
- `(group_id, subgroup_id)` = Acesso específico a um subgrupo
- `(group_id, None)` = Acesso a TODOS os subgrupos daquele grupo
- Usuário sem `group_access` = SEM ACESSO a nada

### Validação de Acesso em Endpoints

#### 1. LIST - Filtrar apenas registros acessíveis

```python
@router.get("/", response_model=List[VehicleResponse])
async def list_vehicles(
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "read"))
):
    # Verificar se usuário tem algum acesso
    if not current_user.group_access:
        return []

    # Construir filtro de acesso
    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    # Aplicar filtro
    query = select(Vehicle).where(
        access_filter,
        Vehicle.status != -1  # Excluir soft-deleted
    )

    result = await db.execute(query)
    return result.scalars().all()
```

#### 2. GET - Verificar acesso ao registro específico

```python
@router.get("/{vehicle_id}", response_model=VehicleResponse)
async def get_vehicle(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "read"))
):
    if not current_user.group_access:
        raise HTTPException(status_code=403, detail="No group access")

    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    result = await db.execute(
        select(Vehicle).where(
            Vehicle.id == vehicle_id,
            access_filter,
            Vehicle.status != -1
        )
    )
    vehicle = result.scalar_one_or_none()

    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    return vehicle
```

#### 3. CREATE - Validar acesso ao grupo/subgrupo alvo

```python
@router.post("/", response_model=VehicleResponse, status_code=201)
async def create_vehicle(
    vehicle_data: VehicleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "write"))
):
    if not current_user.group_access:
        raise HTTPException(status_code=403, detail="No group access")

    # Validar acesso ao grupo/subgrupo alvo
    target_group = (vehicle_data.group_id, vehicle_data.subgroup_id)
    if target_group not in current_user.group_access:
        raise HTTPException(
            status_code=403,
            detail="No access to target group/subgroup"
        )

    # Criar veículo
    vehicle = Vehicle(
        **vehicle_data.model_dump(),
        account_id=539,
        status=1,
        user_add=current_user.user_id
    )

    db.add(vehicle)
    await db.commit()
    await db.refresh(vehicle)

    return vehicle
```

#### 4. UPDATE - Validar acesso ao registro E ao novo grupo (se alterado)

```python
@router.put("/{vehicle_id}", response_model=VehicleResponse)
async def update_vehicle(
    vehicle_id: int,
    vehicle_data: VehicleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "write"))
):
    if not current_user.group_access:
        raise HTTPException(status_code=403, detail="No group access")

    # Buscar veículo (valida acesso ao registro atual)
    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)
    result = await db.execute(
        select(Vehicle).where(
            Vehicle.id == vehicle_id,
            access_filter,
            Vehicle.status != -1
        )
    )
    vehicle = result.scalar_one_or_none()

    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    # Se mudando grupo/subgrupo, validar acesso ao novo grupo
    update_data = vehicle_data.model_dump(exclude_unset=True)
    if 'group_id' in update_data or 'subgroup_id' in update_data:
        new_group_id = update_data.get('group_id', vehicle.group_id)
        new_subgroup_id = update_data.get('subgroup_id', vehicle.subgroup_id)
        target_group = (new_group_id, new_subgroup_id)

        if target_group not in current_user.group_access:
            raise HTTPException(
                status_code=403,
                detail="No access to target group/subgroup"
            )

    # Aplicar updates
    for key, value in update_data.items():
        setattr(vehicle, key, value)

    vehicle.user_modif = current_user.user_id
    vehicle.date_modif = datetime.now()

    await db.commit()
    await db.refresh(vehicle)

    return vehicle
```

#### 5. DELETE - Validar acesso antes de soft delete

```python
@router.delete("/{vehicle_id}", response_model=VehicleResponse)
async def delete_vehicle(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "delete"))
):
    if not current_user.group_access:
        raise HTTPException(status_code=403, detail="No group access")

    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    result = await db.execute(
        select(Vehicle).where(
            Vehicle.id == vehicle_id,
            access_filter,
            Vehicle.status != -1
        )
    )
    vehicle = result.scalar_one_or_none()

    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    # Soft delete
    vehicle.status = -1
    vehicle.user_modif = current_user.user_id
    vehicle.date_modif = datetime.now()

    await db.commit()
    await db.refresh(vehicle)

    return vehicle
```

### Helper Functions de Acesso

Use o helper `build_group_subgroup_filter` para construir filtros SQL:

```python
from app.core.access_control import build_group_subgroup_filter

# Construir filtro para um modelo
access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

# Usar em query
query = select(Vehicle).where(access_filter)
```

---

## CRUD Operations

### Template Completo de CRUD

Ao criar um novo recurso, siga este template:

#### 1. Model (SQLAlchemy)

```python
# app/models/example.py
from sqlalchemy import Column, Integer, String, DateTime, func
from app.core.database import Base

class Example(Base):
    __tablename__ = "example"
    __table_args__ = {'schema': 'mova'}

    # Primary Key
    id = Column(Integer, primary_key=True, index=True)

    # Business Fields
    name = Column(String(255), nullable=False)
    description = Column(String(500))

    # Access Control
    group_id = Column(Integer, nullable=False, index=True)
    subgroup_id = Column(Integer, nullable=True, index=True)
    account_id = Column(Integer, nullable=False, default=539)

    # Status (1=active, 0=inactive, -1=deleted)
    status = Column(Integer, nullable=False, default=1)

    # Audit Trail
    user_add = Column(Integer)
    date_add = Column(DateTime, server_default=func.now())
    user_modif = Column(Integer)
    date_modif = Column(DateTime, onupdate=func.now())
```

#### 2. Schemas (Pydantic)

```python
# app/schemas/example.py
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

class ExampleBase(BaseModel):
    """Campos compartilhados entre Create e Update."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=500)

class ExampleCreate(ExampleBase):
    """Schema para criação - usuário escolhe grupo/subgrupo."""
    group_id: int = Field(..., description="Group ID (user must have access)")
    subgroup_id: int = Field(..., description="Subgroup ID (user must have access)")

class ExampleUpdate(BaseModel):
    """Schema para atualização - todos campos opcionais."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=500)
    group_id: Optional[int] = None
    subgroup_id: Optional[int] = None
    status: Optional[int] = Field(None, ge=0, le=1)

class ExampleResponse(BaseModel):
    """Schema para resposta."""
    id: int
    name: str
    description: Optional[str]
    group_id: int
    subgroup_id: int
    account_id: int
    status: int
    user_add: Optional[int]
    date_add: Optional[datetime]
    user_modif: Optional[int]
    date_modif: Optional[datetime]

    model_config = {"from_attributes": True}
```

#### 3. Endpoints (FastAPI)

```python
# app/api/v1/endpoints/examples.py
from typing import List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.access_control import build_group_subgroup_filter
from app.middleware.auth import AuthenticatedUser, require_permission
from app.models.example import Example
from app.schemas.example import ExampleCreate, ExampleUpdate, ExampleResponse

router = APIRouter()


@router.get("/", response_model=List[ExampleResponse])
async def list_examples(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    include_deleted: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("examples", "read"))
):
    """List all examples accessible by current user."""
    if not current_user.group_access:
        return []

    access_filter = build_group_subgroup_filter(current_user.group_access)(Example)

    query = select(Example).where(access_filter)

    if not include_deleted:
        query = query.where(Example.status != -1)

    query = query.order_by(Example.date_add.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{example_id}", response_model=ExampleResponse)
async def get_example(
    example_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("examples", "read"))
):
    """Get example by ID."""
    if not current_user.group_access:
        raise HTTPException(status_code=403, detail="No group access")

    access_filter = build_group_subgroup_filter(current_user.group_access)(Example)

    result = await db.execute(
        select(Example).where(
            Example.id == example_id,
            access_filter,
            Example.status != -1
        )
    )
    example = result.scalar_one_or_none()

    if not example:
        raise HTTPException(status_code=404, detail="Example not found")

    return example


@router.post("/", response_model=ExampleResponse, status_code=status.HTTP_201_CREATED)
async def create_example(
    example_data: ExampleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("examples", "write"))
):
    """Create new example."""
    if not current_user.group_access:
        raise HTTPException(status_code=403, detail="No group access")

    # Validate access to target group/subgroup
    target_group = (example_data.group_id, example_data.subgroup_id)
    if target_group not in current_user.group_access:
        raise HTTPException(
            status_code=403,
            detail="No access to target group/subgroup"
        )

    example = Example(
        **example_data.model_dump(),
        account_id=539,
        status=1,
        user_add=current_user.user_id
    )

    db.add(example)
    await db.commit()
    await db.refresh(example)

    return example


@router.put("/{example_id}", response_model=ExampleResponse)
async def update_example(
    example_id: int,
    example_data: ExampleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("examples", "write"))
):
    """Update example by ID."""
    if not current_user.group_access:
        raise HTTPException(status_code=403, detail="No group access")

    access_filter = build_group_subgroup_filter(current_user.group_access)(Example)

    result = await db.execute(
        select(Example).where(
            Example.id == example_id,
            access_filter,
            Example.status != -1
        )
    )
    example = result.scalar_one_or_none()

    if not example:
        raise HTTPException(status_code=404, detail="Example not found")

    update_data = example_data.model_dump(exclude_unset=True)

    # Validate access to new group/subgroup if changing
    if 'group_id' in update_data or 'subgroup_id' in update_data:
        new_group_id = update_data.get('group_id', example.group_id)
        new_subgroup_id = update_data.get('subgroup_id', example.subgroup_id)
        target_group = (new_group_id, new_subgroup_id)

        if target_group not in current_user.group_access:
            raise HTTPException(
                status_code=403,
                detail="No access to target group/subgroup"
            )

    for key, value in update_data.items():
        setattr(example, key, value)

    example.user_modif = current_user.user_id
    example.date_modif = datetime.now()

    await db.commit()
    await db.refresh(example)

    return example


@router.delete("/{example_id}", response_model=ExampleResponse)
async def delete_example(
    example_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("examples", "delete"))
):
    """Soft delete example by ID."""
    if not current_user.group_access:
        raise HTTPException(status_code=403, detail="No group access")

    access_filter = build_group_subgroup_filter(current_user.group_access)(Example)

    result = await db.execute(
        select(Example).where(
            Example.id == example_id,
            access_filter,
            Example.status != -1
        )
    )
    example = result.scalar_one_or_none()

    if not example:
        raise HTTPException(status_code=404, detail="Example not found")

    # Soft delete
    example.status = -1
    example.user_modif = current_user.user_id
    example.date_modif = datetime.now()

    await db.commit()
    await db.refresh(example)

    return example
```

#### 4. Registrar Router

```python
# app/api/v1/api.py
from fastapi import APIRouter
from app.api.v1.endpoints import examples

api_router = APIRouter()

api_router.include_router(
    examples.router,
    prefix="/examples",
    tags=["examples"]
)
```

---

## Associações de Dispositivos

### Padrão de Associação 1:1

O sistema possui dois tipos de associações de dispositivos com veículos:

1. **TrackedUnitDevice** - Dispositivos de rastreamento GPS
2. **VcmsUnitDevice** - Dispositivos de monitoramento de vídeo

Ambos seguem o mesmo padrão:
- **Relação 1:1**: Um veículo pode ter apenas UM dispositivo ativo de cada tipo
- **Soft Delete**: `status=-1` para deleção
- **Auto-dates**: `association_date` e `release_date` gerenciadas automaticamente

### Regras de Negócio

1. **Criação de Associação**:
   - Usuário NÃO pode enviar `association_date` (auto-set para NOW())
   - Usuário NÃO pode enviar `release_date` (NULL na criação)
   - Usuário DEVE ter acesso ao grupo do veículo E do dispositivo
   - Sistema valida se veículo já tem dispositivo ativo (1:1)
   - Sistema valida se dispositivo já está associado (1:1)

2. **Deleção de Associação** (Soft Delete):
   - `status = -1`
   - `release_date = NOW()` (auto-set se NULL)
   - Mantém registro para auditoria

### Exemplo de Implementação

```python
@router.post("/", response_model=TrackedUnitDeviceResponse, status_code=201)
async def create_association(
    association_data: TrackedUnitDeviceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "write"))
):
    """Create new vehicle-device association."""
    if not current_user.group_access:
        raise HTTPException(status_code=403, detail="No group access")

    # 1. Validar veículo existe, está ativo, e usuário tem acesso
    vehicle_access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)
    vehicle_result = await db.execute(
        select(Vehicle).where(
            Vehicle.id == association_data.tracked_unit_id,
            vehicle_access_filter,
            Vehicle.status == 1
        )
    )
    vehicle = vehicle_result.scalar_one_or_none()

    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found or not active")

    # 2. Validar dispositivo existe e está ativo
    device_result = await db.execute(
        select(Device).where(
            Device.id == association_data.device_id,
            Device.status == 1
        )
    )
    device = device_result.scalar_one_or_none()

    if not device:
        raise HTTPException(status_code=404, detail="Device not found or not active")

    # 3. Validar usuário tem acesso ao grupo do dispositivo
    accessible_group_ids = [group_id for group_id, subgroup_id in current_user.group_access]
    if device.group_id not in accessible_group_ids:
        raise HTTPException(status_code=403, detail="No access to device's group")

    # 4. Validar veículo não tem dispositivo ativo (1:1)
    existing_vehicle_device = await db.execute(
        select(TrackedUnitDevice).where(
            TrackedUnitDevice.tracked_unit_id == association_data.tracked_unit_id,
            TrackedUnitDevice.status == 1,
            TrackedUnitDevice.release_date.is_(None)
        )
    )
    if existing_vehicle_device.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="Vehicle already has an active device. Release it first."
        )

    # 5. Validar dispositivo não está associado a outro veículo (1:1)
    existing_device_vehicle = await db.execute(
        select(TrackedUnitDevice).where(
            TrackedUnitDevice.device_id == association_data.device_id,
            TrackedUnitDevice.status == 1,
            TrackedUnitDevice.release_date.is_(None)
        )
    )
    if existing_device_vehicle.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="Device is already associated with another vehicle. Release it first."
        )

    # 6. Criar associação (association_date auto-set)
    association = TrackedUnitDevice(
        **association_data.model_dump(),
        association_date=datetime.now(),  # Auto-set
        user_id=current_user.user_id,
        status=1,
        device_primary=1
    )

    db.add(association)
    await db.commit()
    await db.refresh(association)

    return association


@router.delete("/{association_id}", status_code=204)
async def delete_association(
    association_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "delete"))
):
    """Soft delete association."""
    if not current_user.group_access:
        raise HTTPException(status_code=403, detail="No group access")

    vehicle_access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    result = await db.execute(
        select(TrackedUnitDevice)
        .join(Vehicle, TrackedUnitDevice.tracked_unit_id == Vehicle.id)
        .where(
            TrackedUnitDevice.id == association_id,
            vehicle_access_filter,
            TrackedUnitDevice.status != -1
        )
    )
    association = result.scalar_one_or_none()

    if not association:
        raise HTTPException(status_code=404, detail="Association not found")

    # Soft delete (auto-set release_date)
    association.status = -1
    association.device_primary = 0
    if association.release_date is None:
        association.release_date = datetime.now()

    await db.commit()

    return None
```

---

## Testing

### Estrutura de Testes

Todos os testes estão em `tests/` e usam:
- **pytest** - Framework de testes
- **pytest-asyncio** - Suporte para async/await
- **pytest-cov** - Cobertura de código
- **Mocking** - Para evitar modificações no banco de produção

### Executar Testes

```bash
# No Docker (recomendado)
docker exec fleet_api python3 -m pytest tests/ -v

# Com coverage
docker exec fleet_api python3 -m pytest tests/ -v --cov=app --cov-report=term-missing

# Teste específico
docker exec fleet_api python3 -m pytest tests/test_vehicles_api.py -v

# Localmente (requer pytest instalado)
python3 -m pytest tests/ -v
```

### Escrever Novos Testes

Template para testes de CRUD:

```python
# tests/test_examples_api.py
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from app.models.example import Example
from app.schemas.example import ExampleCreate, ExampleUpdate, ExampleResponse

class TestExampleCreate:
    """Test suite for POST /api/v1/examples - Create example."""

    @pytest.mark.asyncio
    async def test_create_example_sets_default_fields(self):
        """Test: Create example should set default fields correctly."""
        current_user_id = 123

        example_data = ExampleCreate(
            name="Test Example",
            group_id=14330,
            subgroup_id=15812
        )

        # Simulate creation
        example = Example(
            **example_data.model_dump(),
            id=1,
            account_id=539,
            status=1,
            user_add=current_user_id,
            date_add=datetime.now()
        )

        assert example.status == 1, "New example should have status=1 (active)"
        assert example.user_add == current_user_id
        assert example.account_id == 539
        assert example.date_add is not None

class TestExampleSoftDelete:
    """Test suite for DELETE /api/v1/examples/{id} - Soft delete."""

    @pytest.mark.asyncio
    async def test_soft_delete_sets_status_to_minus_one(self):
        """Test: Soft delete should set status=-1."""
        example = Example(
            id=1,
            name="Test",
            group_id=14330,
            subgroup_id=15812,
            status=1
        )

        # Simulate soft delete
        example.status = -1
        example.user_modif = 123
        example.date_modif = datetime.now()

        assert example.status == -1, "Soft deleted example should have status=-1"
        assert example.user_modif == 123
        assert example.date_modif is not None
```

### Coverage Atual

```
TOTAL: 2549 statements, 1616 missed, 37% coverage
```

**Meta**: Aumentar coverage para 80%+ gradualmente.

---

## Git Workflow

### Pre-Commit Hook

O sistema possui um **pre-commit hook** que executa TODOS os testes automaticamente antes de permitir commits:

```bash
# Hook localizado em: .git/hooks/pre-commit

# Funciona automaticamente
git commit -m "feat: add new feature"
# → Testes executam automaticamente
# → Se TODOS passarem: commit permitido ✅
# → Se ALGUM falhar: commit cancelado ❌
```

### Como Funciona o Hook

1. Detecta se container Docker `fleet_api` está rodando
2. Se SIM: executa testes dentro do container
3. Se NÃO: tenta executar localmente
4. Se nenhum disponível: cancela com instruções

### Bypass do Hook (Emergências)

```bash
# NÃO RECOMENDADO - usar apenas em emergências
git commit --no-verify -m "emergency fix"
```

### Workflow de Desenvolvimento

```bash
# 1. Criar branch
git checkout -b feature/nova-funcionalidade

# 2. Fazer alterações
# ... editar código ...

# 3. Executar testes manualmente (opcional)
docker exec fleet_api python3 -m pytest tests/ -v

# 4. Adicionar mudanças
git add .

# 5. Commit (testes executam automaticamente)
git commit -m "feat: add nova funcionalidade"

# 6. Push
git push origin feature/nova-funcionalidade

# 7. Criar Pull Request
# ... via GitHub/GitLab ...
```

### Convenções de Commit

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add new vehicle endpoint
fix: correct soft delete in drivers
docs: update API documentation
test: add tests for device associations
refactor: improve access control logic
chore: update dependencies
```

---

## Troubleshooting

### Erro: "Tests failed - commit cancelled"

**Causa**: Hook detectou testes falhando.

**Solução**:
```bash
# 1. Ver qual teste falhou
docker exec fleet_api python3 -m pytest tests/ -v

# 2. Corrigir o teste ou código

# 3. Tentar commit novamente
git commit -m "fix: corrige teste"
```

### Erro: "Field required - group_id"

**Causa**: Schema `Create` agora requer `group_id` e `subgroup_id`.

**Solução**:
```python
# ❌ ERRADO
vehicle_data = VehicleCreate(
    label="ABC-1234",
    unit_category_id=1
)

# ✅ CORRETO
vehicle_data = VehicleCreate(
    label="ABC-1234",
    group_id=14330,
    subgroup_id=15812,
    unit_category_id=1
)
```

### Erro: "No access to target group/subgroup"

**Causa**: Usuário tentando criar/atualizar em grupo sem acesso.

**Solução**: Verificar `current_user.group_access` e usar grupo acessível.

### Erro: "Vehicle already has an active device"

**Causa**: Tentando associar segundo dispositivo (violação 1:1).

**Solução**: Deletar (soft delete) associação existente primeiro.

### Erro: "Cannot run tests - Docker not running"

**Causa**: Hook não encontrou container Docker nem pytest local.

**Solução**:
```bash
# Opção 1: Iniciar Docker
docker compose up -d

# Opção 2: Instalar pytest localmente
pip install -r requirements-dev.txt

# Opção 3: Bypass (NÃO RECOMENDADO)
git commit --no-verify
```

---

## Próximos Passos

### Para Desenvolvedores Novos

1. ✅ Ler este guia completamente
2. ✅ Configurar ambiente Docker
3. ✅ Executar testes para verificar setup
4. ✅ Estudar código de um CRUD existente (ex: `vehicles.py`)
5. ✅ Criar um CRUD simples seguindo o template
6. ✅ Escrever testes para o novo CRUD
7. ✅ Fazer commit (hook validará automaticamente)

### Recursos Adicionais

- 📚 [Git Hooks Guide](.docs/git-hooks.md) - Documentação completa do hook
- 📚 [CRUD Standards](.docs/CRUD_STANDARDS.md) - Padrões detalhados de CRUD
- 📚 [API Usage](.docs/API_USAGE.md) - Exemplos de uso da API
- 📚 FastAPI Docs: https://fastapi.tiangolo.com/
- 📚 SQLAlchemy 2.0 Docs: https://docs.sqlalchemy.org/en/20/
- 📚 Pydantic v2 Docs: https://docs.pydantic.dev/latest/

---

**Última Atualização**: Novembro 2025
**Versão**: 1.0.0
**Autor**: Sistema Fleet Management

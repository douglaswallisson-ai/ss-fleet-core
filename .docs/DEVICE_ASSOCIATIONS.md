# Device Associations - Fleet Management Platform

## 📋 Índice

1. [Visão Geral](#visão-geral)
2. [TrackedUnitDevice (GPS)](#trackedunitdevice-gps)
3. [VcmsUnitDevice (Video)](#vcmsunitdevice-video)
4. [Regras de Negócio](#regras-de-negócio)
5. [Implementation Guide](#implementation-guide)
6. [Common Patterns](#common-patterns)
7. [Testing](#testing)
8. [Troubleshooting](#troubleshooting)

---

## Visão Geral

O sistema possui dois tipos de associações entre veículos e dispositivos:

### 1. TrackedUnitDevice (Dispositivos GPS)
- Associa veículos (`tracked_unit`) com dispositivos de rastreamento GPS (`device`)
- Tabela: `mova.tracked_unit_device`
- Relação: **1:1** (um veículo = um dispositivo GPS ativo)

### 2. VcmsUnitDevice (Dispositivos de Vídeo)
- Associa veículos (`tracked_unit`) com dispositivos de monitoramento de vídeo (`device`)
- Tabela: `mova.vcms_unit_device`
- Relação: **1:1** (um veículo = um dispositivo de vídeo ativo)

### Características Comuns

Ambos seguem os mesmos padrões:

- ✅ **Soft Delete**: `status=-1` para deleção lógica
- ✅ **Auto-dates**: `association_date` e `release_date` gerenciadas pelo sistema
- ✅ **Access Control**: Usuário deve ter acesso ao veículo E ao dispositivo
- ✅ **1:1 Constraint**: Validação de unicidade em aplicação (não no DB)
- ✅ **Audit Trail**: `user_id` registra quem criou associação

---

## TrackedUnitDevice (GPS)

### Database Schema

```sql
CREATE TABLE mova.tracked_unit_device (
    id INTEGER PRIMARY KEY,
    tracked_unit_id INTEGER NOT NULL,  -- FK para tracked_unit
    device_id INTEGER NOT NULL,        -- FK para device
    association_date TIMESTAMP,        -- Quando associado
    release_date TIMESTAMP,            -- Quando liberado (NULL = ativo)
    status INTEGER DEFAULT 1,          -- 1=ativo, -1=deletado
    device_primary INTEGER DEFAULT 1,  -- 1=primário, 0=secundário
    user_id INTEGER                    -- Quem criou associação
);
```

### Model (SQLAlchemy)

```python
# app/models/tracked_unit_device.py
from sqlalchemy import Column, Integer, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base

class TrackedUnitDevice(Base):
    __tablename__ = "tracked_unit_device"
    __table_args__ = {'schema': 'mova'}

    id = Column(Integer, primary_key=True, index=True)
    tracked_unit_id = Column(Integer, ForeignKey('mova.tracked_unit.id'), nullable=False, index=True)
    device_id = Column(Integer, ForeignKey('mova.device.id'), nullable=False, index=True)
    association_date = Column(DateTime, nullable=False)
    release_date = Column(DateTime, nullable=True)
    status = Column(Integer, nullable=False, default=1)
    device_primary = Column(Integer, nullable=False, default=1)
    user_id = Column(Integer, nullable=False)

    # Relationships
    vehicle = relationship("Vehicle", foreign_keys=[tracked_unit_id])
    device = relationship("Device", foreign_keys=[device_id])

    @property
    def is_active(self) -> bool:
        """Association is active if status=1 and release_date is NULL."""
        return self.status == 1 and self.release_date is None
```

### Schemas (Pydantic)

```python
# app/schemas/tracked_unit_device.py
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

class TrackedUnitDeviceBase(BaseModel):
    """Base schema - apenas IDs são enviados pelo usuário."""
    tracked_unit_id: int = Field(..., description="Vehicle ID")
    device_id: int = Field(..., description="Device ID")


class TrackedUnitDeviceCreate(TrackedUnitDeviceBase):
    """
    Schema para criação.

    Campos auto-gerenciados (NÃO enviar):
    - association_date: Auto-set para NOW()
    - release_date: NULL (associação ativa)
    - status: 1 (ativo)
    - device_primary: 1 (primário)
    - user_id: Do JWT token
    """
    pass


class TrackedUnitDeviceUpdate(BaseModel):
    """Schema para atualização."""
    association_date: Optional[datetime] = None
    release_date: Optional[datetime] = None
    status: Optional[int] = None


class TrackedUnitDeviceResponse(TrackedUnitDeviceBase):
    """Schema para resposta."""
    id: int
    association_date: datetime
    release_date: Optional[datetime]
    status: int
    device_primary: int
    user_id: int
    is_active: bool  # Virtual property

    model_config = {"from_attributes": True}


class TrackedUnitDeviceWithDetails(TrackedUnitDeviceResponse):
    """Schema com detalhes do veículo e dispositivo."""
    vehicle_label: str
    device_identifier: str
    duration_days: Optional[int]  # Calculado
```

---

## VcmsUnitDevice (Video)

### Database Schema

```sql
CREATE TABLE mova.vcms_unit_device (
    id INTEGER PRIMARY KEY,
    unit_id INTEGER NOT NULL,      -- FK para tracked_unit
    device_id INTEGER NOT NULL,    -- FK para device
    association_date TIMESTAMP,    -- Quando associado
    release_date TIMESTAMP,        -- Quando liberado (NULL = ativo)
    status INTEGER DEFAULT 1,      -- 1=ativo, -1=deletado
    user_id INTEGER                -- Quem criou associação
);
```

### Model (SQLAlchemy)

```python
# app/models/vcms_unit_device.py
from sqlalchemy import Column, Integer, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base

class VcmsUnitDevice(Base):
    __tablename__ = "vcms_unit_device"
    __table_args__ = {'schema': 'mova'}

    id = Column(Integer, primary_key=True, index=True)
    unit_id = Column(Integer, ForeignKey('mova.tracked_unit.id'), nullable=False, index=True)
    device_id = Column(Integer, ForeignKey('mova.device.id'), nullable=False, index=True)
    association_date = Column(DateTime, nullable=False)
    release_date = Column(DateTime, nullable=True)
    status = Column(Integer, nullable=False, default=1)
    user_id = Column(Integer, nullable=False)

    # Relationships
    vehicle = relationship("Vehicle", foreign_keys=[unit_id])
    device = relationship("Device", foreign_keys=[device_id])

    @property
    def is_active(self) -> bool:
        """Association is active if status=1 and release_date is NULL."""
        return self.status == 1 and self.release_date is None
```

### Schemas (Pydantic)

Similar a `TrackedUnitDevice`, mas usando `unit_id` ao invés de `tracked_unit_id`.

---

## Regras de Negócio

### 1. Relação 1:1 Obrigatória

**Regra**: Um veículo pode ter apenas UM dispositivo ativo de cada tipo.

```python
# Validação em CREATE
existing_vehicle_device = await db.execute(
    select(TrackedUnitDevice).where(
        TrackedUnitDevice.tracked_unit_id == vehicle_id,
        TrackedUnitDevice.status == 1,
        TrackedUnitDevice.release_date.is_(None)
    )
)
if existing_vehicle_device.scalar_one_or_none():
    raise HTTPException(
        status_code=400,
        detail="Vehicle already has an active device. Release it first."
    )

# Validação de dispositivo
existing_device_vehicle = await db.execute(
    select(TrackedUnitDevice).where(
        TrackedUnitDevice.device_id == device_id,
        TrackedUnitDevice.status == 1,
        TrackedUnitDevice.release_date.is_(None)
    )
)
if existing_device_vehicle.scalar_one_or_none():
    raise HTTPException(
        status_code=400,
        detail="Device is already associated with another vehicle. Release it first."
    )
```

### 2. Campos Auto-Gerenciados

#### CREATE
```python
# ❌ ERRADO - Usuário NÃO deve enviar
{
  "tracked_unit_id": 1,
  "device_id": 2,
  "association_date": "2025-01-15T10:00:00",  # ❌ NÃO ENVIAR
  "release_date": null,                        # ❌ NÃO ENVIAR
  "status": 1,                                 # ❌ NÃO ENVIAR
  "user_id": 123                               # ❌ NÃO ENVIAR
}

# ✅ CORRETO - Apenas IDs
{
  "tracked_unit_id": 1,
  "device_id": 2
}

# Sistema auto-seta
association = TrackedUnitDevice(
    tracked_unit_id=1,
    device_id=2,
    association_date=datetime.now(),  # ✅ Auto
    release_date=None,                # ✅ Auto
    status=1,                         # ✅ Auto
    device_primary=1,                 # ✅ Auto
    user_id=current_user.user_id     # ✅ Auto
)
```

#### DELETE (Soft Delete)
```python
# Auto-set em DELETE
association.status = -1
association.device_primary = 0
if association.release_date is None:
    association.release_date = datetime.now()  # ✅ Auto
```

### 3. Access Control

Usuário deve ter acesso a AMBOS: veículo E dispositivo.

```python
# 1. Validar acesso ao veículo
vehicle_access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)
vehicle_result = await db.execute(
    select(Vehicle).where(
        Vehicle.id == vehicle_id,
        vehicle_access_filter,
        Vehicle.status == 1
    )
)
vehicle = vehicle_result.scalar_one_or_none()

if not vehicle:
    raise HTTPException(status_code=404, detail="Vehicle not found")

# 2. Validar acesso ao dispositivo
accessible_group_ids = [group_id for group_id, _ in current_user.group_access]
if device.group_id not in accessible_group_ids:
    raise HTTPException(status_code=403, detail="No access to device's group")
```

### 4. Soft Delete Pattern

```python
# ✅ CORRETO - Soft Delete
@router.delete("/{association_id}")
async def delete_association(...):
    association.status = -1
    association.device_primary = 0
    if association.release_date is None:
        association.release_date = datetime.now()

    await db.commit()
    return None

# ❌ ERRADO - Hard Delete (NUNCA FAZER)
await db.delete(association)
```

---

## Implementation Guide

### Template Completo de Endpoint CREATE

```python
@router.post("/", response_model=TrackedUnitDeviceResponse, status_code=201)
async def create_association(
    association_data: TrackedUnitDeviceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "write"))
):
    """
    Create new vehicle-device association.

    Business Rules:
    1. Vehicle must exist, be active, and user must have access
    2. Device must exist, be active, and user must have access
    3. Vehicle cannot already have an active device (1:1)
    4. Device cannot already be associated with another vehicle (1:1)
    """
    if not current_user.group_access:
        raise HTTPException(status_code=403, detail="No group access")

    # 1. Validate vehicle
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
        raise HTTPException(
            status_code=404,
            detail="Vehicle not found or not active"
        )

    # 2. Validate device
    device_result = await db.execute(
        select(Device).where(
            Device.id == association_data.device_id,
            Device.status == 1
        )
    )
    device = device_result.scalar_one_or_none()

    if not device:
        raise HTTPException(
            status_code=404,
            detail="Device not found or not active"
        )

    # 3. Validate user has access to device's group
    accessible_group_ids = [group_id for group_id, _ in current_user.group_access]
    if device.group_id not in accessible_group_ids:
        raise HTTPException(
            status_code=403,
            detail="No access to device's group"
        )

    # 4. Check vehicle doesn't have active device (1:1)
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

    # 5. Check device is not associated with another vehicle (1:1)
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

    # 6. Create association (auto-set fields)
    association = TrackedUnitDevice(
        **association_data.model_dump(),
        association_date=datetime.now(),  # Auto
        user_id=current_user.user_id,    # Auto
        status=1,                         # Auto
        device_primary=1                  # Auto
    )

    db.add(association)
    await db.commit()
    await db.refresh(association)

    return association
```

### Template Completo de Endpoint DELETE

```python
@router.delete("/{association_id}", status_code=204)
async def delete_association(
    association_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "delete"))
):
    """
    Soft delete association (sets status=-1, release_date=NOW).
    """
    if not current_user.group_access:
        raise HTTPException(status_code=403, detail="No group access")

    # Get association with access control
    vehicle_access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    result = await db.execute(
        select(TrackedUnitDevice)
        .join(Vehicle, TrackedUnitDevice.tracked_unit_id == Vehicle.id)
        .where(
            TrackedUnitDevice.id == association_id,
            vehicle_access_filter,
            TrackedUnitDevice.status != -1  # Exclude soft-deleted
        )
    )
    association = result.scalar_one_or_none()

    if not association:
        raise HTTPException(
            status_code=404,
            detail="Association not found"
        )

    # Soft delete
    association.status = -1
    association.device_primary = 0
    if association.release_date is None:
        association.release_date = datetime.now()

    await db.commit()

    return None  # 204 No Content
```

---

## Common Patterns

### 1. Release and Reassociate Device

```python
# Workflow: Mover dispositivo de veículo A para veículo B

# Passo 1: Buscar associação atual
GET /api/v1/device-associations?device_id=5&active_only=true
# → { "id": 10, "tracked_unit_id": 1, ... }

# Passo 2: Liberar dispositivo (soft delete)
DELETE /api/v1/device-associations/10
# → 204 No Content

# Passo 3: Associar a novo veículo
POST /api/v1/device-associations
{
  "tracked_unit_id": 2,
  "device_id": 5
}
# → { "id": 11, ... }
```

### 2. Check Active Association

```python
# Query para verificar se veículo tem dispositivo ativo
active_device = await db.execute(
    select(TrackedUnitDevice).where(
        TrackedUnitDevice.tracked_unit_id == vehicle_id,
        TrackedUnitDevice.status == 1,
        TrackedUnitDevice.release_date.is_(None)
    )
)
association = active_device.scalar_one_or_none()

if association:
    print(f"Vehicle has device: {association.device_id}")
else:
    print("Vehicle has no active device")
```

### 3. List All Active Associations

```python
@router.get("/active")
async def list_active_associations(
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "read"))
):
    """List only currently active associations."""
    vehicle_access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    query = (
        select(TrackedUnitDevice)
        .join(Vehicle, TrackedUnitDevice.tracked_unit_id == Vehicle.id)
        .where(
            vehicle_access_filter,
            TrackedUnitDevice.status == 1,
            TrackedUnitDevice.release_date.is_(None)
        )
    )

    result = await db.execute(query)
    return result.scalars().all()
```

---

## Testing

### Test Cases Obrigatórios

```python
# tests/test_tracked_unit_device_api.py

class TestTrackedUnitDeviceBusinessRules:
    """Testes de regras de negócio."""

    @pytest.mark.asyncio
    async def test_device_primary_must_match_status(self):
        """Test: device_primary=1 when status=1, device_primary=0 when status=-1."""
        # ...

    @pytest.mark.asyncio
    async def test_is_currently_associated_property(self):
        """Test: is_active property returns correct value."""
        # ...


class TestTrackedUnitDeviceOneToOneConstraint:
    """Testes de constraint 1:1."""

    @pytest.mark.asyncio
    async def test_vehicle_cannot_have_multiple_active_devices(self):
        """Test: Cannot create second active association for same vehicle."""
        # Mock: veículo já tem dispositivo ativo
        # Expect: 400 Bad Request
        # ...

    @pytest.mark.asyncio
    async def test_device_cannot_be_associated_with_multiple_vehicles(self):
        """Test: Cannot associate device that's already in use."""
        # Mock: dispositivo já associado a outro veículo
        # Expect: 400 Bad Request
        # ...


class TestTrackedUnitDeviceAccessControl:
    """Testes de access control."""

    @pytest.mark.asyncio
    async def test_user_must_have_access_to_vehicle_and_device(self):
        """Test: User must have access to both vehicle and device."""
        # Mock: usuário sem acesso ao dispositivo
        # Expect: 403 Forbidden
        # ...


class TestTrackedUnitDeviceSoftDelete:
    """Testes de soft delete."""

    @pytest.mark.asyncio
    async def test_delete_sets_status_minus_one_and_release_date(self):
        """Test: DELETE sets status=-1, device_primary=0, release_date=NOW."""
        # ...
```

---

## Troubleshooting

### Erro: "Vehicle already has an active device"

**Causa**: Tentando associar segundo dispositivo a um veículo que já tem associação ativa.

**Solução**:
```bash
# 1. Listar associações do veículo
GET /api/v1/device-associations?tracked_unit_id=1&active_only=true

# 2. Liberar associação existente
DELETE /api/v1/device-associations/{association_id}

# 3. Criar nova associação
POST /api/v1/device-associations
```

### Erro: "Device is already associated with another vehicle"

**Causa**: Dispositivo já está associado a outro veículo.

**Solução**:
```bash
# 1. Buscar associação atual do dispositivo
GET /api/v1/device-associations?device_id=5&active_only=true

# 2. Liberar dispositivo
DELETE /api/v1/device-associations/{association_id}

# 3. Associar ao novo veículo
POST /api/v1/device-associations
```

### Erro: "No access to device's group"

**Causa**: Usuário não tem acesso ao grupo do dispositivo.

**Solução**: Usar dispositivo de grupo acessível ou solicitar acesso ao grupo.

### Associação não aparece como ativa

**Causa**: `release_date` não é NULL ou `status != 1`.

**Debug**:
```python
# Verificar campos
print(f"Status: {association.status}")           # Deve ser 1
print(f"Release Date: {association.release_date}") # Deve ser None
print(f"Is Active: {association.is_active}")      # Deve ser True
```

---

## Checklist de Implementação

Ao criar novo tipo de associação:

- [ ] Model com campos obrigatórios (ids, dates, status, user_id)
- [ ] Relationship com Vehicle e Device
- [ ] Property `is_active`
- [ ] Schema Base, Create, Update, Response, WithDetails
- [ ] Endpoint LIST com access control e filtros
- [ ] Endpoint GET com detalhes (vehicle_label, device_identifier)
- [ ] Endpoint CREATE com validações 1:1 e access control
- [ ] Endpoint UPDATE com validação de datas
- [ ] Endpoint DELETE (soft delete)
- [ ] Testes para 1:1 constraint
- [ ] Testes para access control
- [ ] Testes para soft delete
- [ ] Testes para is_active property
- [ ] Documentar no README

---

**Última Atualização**: Novembro 2025
**Versão**: 1.0.0
**Status**: ✅ Padrão Obrigatório para Associações

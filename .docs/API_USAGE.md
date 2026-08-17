# API Usage Guide - Fleet Management Platform

## 📋 Índice

1. [Autenticação](#autenticação)
2. [Vehicles API](#vehicles-api)
3. [Devices API](#devices-api)
4. [Drivers API](#drivers-api)
5. [Device Associations](#device-associations)
6. [Video Device Associations](#video-device-associations)
7. [Error Responses](#error-responses)
8. [Best Practices](#best-practices)

---

## Autenticação

### Login (JWT)

```bash
POST /api/v1/auth/login
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "password123"
}
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 900
}
```

### Usar Token em Requests

```bash
GET /api/v1/vehicles
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

---

## Vehicles API

### List Vehicles

```bash
GET /api/v1/vehicles?skip=0&limit=100
Authorization: Bearer {token}
```

**Query Parameters:**
- `skip` (int): Registros a pular (default: 0)
- `limit` (int): Máximo de registros (default: 100, max: 1000)
- `group_id` (int): Filtrar por grupo
- `subgroup_id` (int): Filtrar por subgrupo
- `include_deleted` (bool): Incluir soft-deleted (default: false)

**Response:**
```json
[
  {
    "id": 1,
    "label": "ABC-1234",
    "label2": "Caminhão Scania",
    "model": "Scania R450",
    "group_id": 14330,
    "subgroup_id": 15812,
    "account_id": 539,
    "unit_category_id": 1,
    "unit_type_id": 2,
    "vehicle_model_id": 10,
    "status": 1,
    "timezone": -3,
    "dst": true,
    "initial_odometer": 150000,
    "initial_horimeter": 8000,
    "max_speed": 90,
    "driver_id": null,
    "obs": "Veículo em operação",
    "date_add": "2025-01-15T10:30:00",
    "date_modif": null
  }
]
```

### Get Vehicle by ID

```bash
GET /api/v1/vehicles/1
Authorization: Bearer {token}
```

**Response:**
```json
{
  "id": 1,
  "label": "ABC-1234",
  "label2": "Caminhão Scania",
  "group_id": 14330,
  "subgroup_id": 15812,
  "status": 1,
  ...
}
```

**Error (404):**
```json
{
  "detail": "Vehicle not found"
}
```

### Create Vehicle

```bash
POST /api/v1/vehicles
Authorization: Bearer {token}
Content-Type: application/json

{
  "label": "XYZ-5678",
  "label2": "Caminhão Mercedes",
  "model": "Mercedes Actros",
  "group_id": 14330,
  "subgroup_id": 15812,
  "unit_category_id": 1,
  "unit_type_id": 2,
  "vehicle_model_id": 11,
  "timezone": -3,
  "dst": true,
  "initial_odometer": 0,
  "max_speed": 90,
  "obs": "Veículo novo"
}
```

**Required Fields:**
- `label` - Placa/identificação do veículo
- `group_id` - ID do grupo (usuário deve ter acesso)
- `subgroup_id` - ID do subgrupo (usuário deve ter acesso)
- `unit_category_id` - ID da categoria

**Auto-set Fields:**
- `account_id = 539`
- `status = 1`
- `user_add = current_user.user_id`
- `date_add = NOW()`

**Response (201):**
```json
{
  "id": 2,
  "label": "XYZ-5678",
  "group_id": 14330,
  "subgroup_id": 15812,
  "account_id": 539,
  "status": 1,
  "user_add": 123,
  "date_add": "2025-01-15T14:22:00",
  ...
}
```

**Error (403 - No Access):**
```json
{
  "detail": "No access to target group/subgroup"
}
```

### Update Vehicle

```bash
PUT /api/v1/vehicles/1
Authorization: Bearer {token}
Content-Type: application/json

{
  "label2": "Caminhão Scania Atualizado",
  "max_speed": 85,
  "obs": "Velocidade máxima reduzida"
}
```

**Note**: Todos os campos são opcionais. Envie apenas os que deseja atualizar.

**Change Group/Subgroup:**
```json
{
  "group_id": 14331,
  "subgroup_id": 15820
}
```
*Usuário deve ter acesso ao grupo/subgrupo atual E ao novo grupo/subgrupo.*

**Response (200):**
```json
{
  "id": 1,
  "label": "ABC-1234",
  "label2": "Caminhão Scania Atualizado",
  "max_speed": 85,
  "user_modif": 123,
  "date_modif": "2025-01-15T15:30:00",
  ...
}
```

### Delete Vehicle (Soft Delete)

```bash
DELETE /api/v1/vehicles/1
Authorization: Bearer {token}
```

**Response (200):**
```json
{
  "id": 1,
  "label": "ABC-1234",
  "status": -1,
  "user_modif": 123,
  "date_modif": "2025-01-15T16:00:00",
  ...
}
```

**Note**: Veículo não é deletado fisicamente, apenas `status=-1`.

---

## Devices API

### List Devices

```bash
GET /api/v1/devices?skip=0&limit=100
Authorization: Bearer {token}
```

**Query Parameters:**
- `skip`, `limit`: Paginação
- `group_id`: Filtrar por grupo
- `device_model_id`: Filtrar por modelo
- `include_deleted`: Incluir soft-deleted

**Response:**
```json
[
  {
    "id": 1,
    "identifier": "867123456789012",
    "internal_id": "10867123456789012",
    "group_id": 14330,
    "account_id": 539,
    "device_model_id": 10,
    "status": 1,
    "phone_number": "+5511999999999",
    "iccid": "89550532190123456789",
    "chip_operator": "Claro",
    "firmware_version": "2.3.4",
    "hardware_version": "VL8-2024",
    "date_add": "2025-01-10T09:00:00",
    ...
  }
]
```

### Create Device

```bash
POST /api/v1/devices
Authorization: Bearer {token}
Content-Type: application/json

{
  "identifier": "867987654321098",
  "group_id": 14330,
  "device_model_id": 10,
  "phone_number": "+5511988888888",
  "iccid": "89550532190987654321",
  "chip_operator": "Vivo",
  "firmware_version": "2.3.5",
  "hardware_version": "VL8-2024"
}
```

**Required Fields:**
- `identifier` - IMEI ou serial do dispositivo
- `group_id` - Grupo (usuário deve ter acesso)
- `device_model_id` - Modelo do dispositivo

**Auto-set Fields:**
- `internal_id = device_model_id + identifier`
- `account_id = 539`
- `status = 1`
- `user_add = current_user.user_id`

**Uniqueness**: `(identifier, device_model_id)` deve ser único por grupo.

---

## Drivers API

### List Drivers

```bash
GET /api/v1/drivers?skip=0&limit=100
Authorization: Bearer {token}
```

**Query Parameters:**
- `skip`, `limit`: Paginação
- `group_id`: Filtrar por grupo
- `subgroup_id`: Filtrar por subgrupo
- `function_id`: Filtrar por função (motorista, operador, etc)
- `cnh_expired`: Filtrar por CNH vencida (true/false)
- `include_deleted`: Incluir soft-deleted

**Response:**
```json
[
  {
    "id": 1,
    "name": "João Silva",
    "login": "joao.silva",
    "cpf": "12345678901",
    "email": "joao@example.com",
    "matricula": "M001",
    "group_id": 14330,
    "subgroup_id": 15812,
    "driver_function_id": 1,
    "cnh": "12345678900",
    "cnh_validate": "2025-12-31",
    "status": 1,
    "date_add": "2025-01-05T10:00:00",
    ...
  }
]
```

### Create Driver

```bash
POST /api/v1/drivers
Authorization: Bearer {token}
Content-Type: application/json

{
  "name": "Maria Santos",
  "login": "maria.santos",
  "password_apps": "senha123",
  "cpf": "98765432100",
  "email": "maria@example.com",
  "matricula": "M002",
  "group_id": 14330,
  "subgroup_id": 15812,
  "driver_function_id": 1,
  "cnh": "98765432100",
  "cnh_validate": "2026-06-30",
  "phone": "+5511977777777"
}
```

**Unique Fields per Group:**
- `login` - Login único por grupo
- `cpf` - CPF único por grupo
- `email` - Email único por grupo
- `matricula` - Matrícula única por grupo

**Auto-hash**: `password_apps` é automaticamente hasheado com SHA1.

**Response (201):**
```json
{
  "id": 2,
  "name": "Maria Santos",
  "login": "maria.santos",
  "password_apps": "40bd001563085fc35165329ea1ff5c5ecbdbbeef",
  "cpf": "98765432100",
  "status": 1,
  ...
}
```

---

## Device Associations

### List Associations

```bash
GET /api/v1/device-associations?skip=0&limit=100
Authorization: Bearer {token}
```

**Query Parameters:**
- `skip`, `limit`: Paginação
- `tracked_unit_id`: Filtrar por veículo
- `device_id`: Filtrar por dispositivo
- `active_only`: Apenas associações ativas (true/false)
- `include_deleted`: Incluir soft-deleted

**Response:**
```json
[
  {
    "id": 1,
    "tracked_unit_id": 1,
    "device_id": 1,
    "association_date": "2025-01-10T08:00:00",
    "release_date": null,
    "status": 1,
    "device_primary": 1,
    "user_id": 123,
    "is_active": true
  }
]
```

### Get Association with Details

```bash
GET /api/v1/device-associations/1
Authorization: Bearer {token}
```

**Response:**
```json
{
  "id": 1,
  "tracked_unit_id": 1,
  "device_id": 1,
  "association_date": "2025-01-10T08:00:00",
  "release_date": null,
  "status": 1,
  "device_primary": 1,
  "user_id": 123,
  "is_active": true,
  "vehicle_label": "ABC-1234",
  "device_identifier": "867123456789012",
  "duration_days": 5
}
```

### Create Association

```bash
POST /api/v1/device-associations
Authorization: Bearer {token}
Content-Type: application/json

{
  "tracked_unit_id": 2,
  "device_id": 2
}
```

**Fields:**
- `tracked_unit_id` - ID do veículo
- `device_id` - ID do dispositivo

**Auto-set:**
- `association_date = NOW()`
- `status = 1`
- `device_primary = 1`
- `user_id = current_user.user_id`

**Business Rules:**
1. Usuário deve ter acesso ao veículo E ao dispositivo
2. Veículo não pode ter outro dispositivo ativo (1:1)
3. Dispositivo não pode estar associado a outro veículo ativo (1:1)

**Response (201):**
```json
{
  "id": 2,
  "tracked_unit_id": 2,
  "device_id": 2,
  "association_date": "2025-01-15T16:30:00",
  "release_date": null,
  "status": 1,
  "device_primary": 1,
  "user_id": 123,
  "is_active": true
}
```

**Errors:**

*Vehicle already has active device:*
```json
{
  "detail": "Vehicle already has an active device. Release it first."
}
```

*Device already associated:*
```json
{
  "detail": "Device is already associated with another vehicle. Release it first."
}
```

### Update Association

```bash
PUT /api/v1/device-associations/1
Authorization: Bearer {token}
Content-Type: application/json

{
  "status": -1
}
```

**Common Use Cases:**
- Desativar associação: `{"status": -1}`
- Reativar associação: `{"status": 1, "release_date": null}`
- Alterar data de associação: `{"association_date": "2025-01-12T10:00:00"}`

### Delete Association (Soft Delete)

```bash
DELETE /api/v1/device-associations/1
Authorization: Bearer {token}
```

**Auto-set:**
- `status = -1`
- `device_primary = 0`
- `release_date = NOW()` (se NULL)

**Response (204):** No Content

---

## Video Device Associations

Similar a Device Associations, mas para dispositivos de vídeo:

### Create Video Association

```bash
POST /api/v1/video-devices
Authorization: Bearer {token}
Content-Type: application/json

{
  "unit_id": 1,
  "device_id": 3
}
```

**Business Rules:**
1. Veículo não pode ter múltiplos dispositivos de vídeo ativos (1:1)
2. Dispositivo de vídeo não pode estar em múltiplos veículos ativos (1:1)
3. Usuário deve ter acesso ao veículo E ao dispositivo

**Auto-set:**
- `association_date = NOW()`
- `status = 1`
- `user_id = current_user.user_id`

---

## Error Responses

### Standard Error Format

```json
{
  "detail": "Error message here"
}
```

### Common HTTP Status Codes

**400 - Bad Request**
```json
{
  "detail": "CPF already exists in this group"
}
```

**401 - Unauthorized**
```json
{
  "detail": "Could not validate credentials"
}
```

**403 - Forbidden**
```json
{
  "detail": "No access to target group/subgroup"
}
```

**404 - Not Found**
```json
{
  "detail": "Vehicle not found"
}
```

**422 - Validation Error**
```json
{
  "detail": [
    {
      "loc": ["body", "group_id"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

---

## Best Practices

### 1. Sempre Use Access Token

```bash
# ✅ CORRETO
curl -H "Authorization: Bearer {token}" http://localhost:8000/api/v1/vehicles

# ❌ ERRADO - sem autenticação
curl http://localhost:8000/api/v1/vehicles
```

### 2. Handle Errors Gracefully

```javascript
try {
  const response = await fetch('/api/v1/vehicles', {
    headers: {
      'Authorization': `Bearer ${token}`
    }
  });

  if (!response.ok) {
    const error = await response.json();
    console.error('API Error:', error.detail);
    return;
  }

  const vehicles = await response.json();
  // Processar veículos...

} catch (error) {
  console.error('Network Error:', error);
}
```

### 3. Paginate Large Lists

```bash
# ✅ CORRETO - com paginação
GET /api/v1/vehicles?skip=0&limit=100

# ❌ EVITAR - sem limit (pode retornar milhares de registros)
GET /api/v1/vehicles
```

### 4. Use Filters to Reduce Response Size

```bash
# ✅ CORRETO - filtrar por grupo específico
GET /api/v1/vehicles?group_id=14330&limit=50

# ❌ EVITAR - buscar tudo e filtrar no cliente
GET /api/v1/vehicles?limit=10000
```

### 5. Don't Send Unnecessary Fields in Updates

```bash
# ✅ CORRETO - enviar apenas campos alterados
PUT /api/v1/vehicles/1
{
  "max_speed": 85
}

# ❌ EVITAR - enviar todos os campos
PUT /api/v1/vehicles/1
{
  "label": "ABC-1234",
  "label2": "Caminhão",
  "model": "Scania",
  "max_speed": 85,
  ...
}
```

### 6. Check Access Control Errors

```javascript
const response = await fetch('/api/v1/vehicles', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify(vehicleData)
});

if (response.status === 403) {
  alert('Você não tem acesso a este grupo/subgrupo');
  return;
}
```

### 7. Validate Before Create/Update

```javascript
// Validar campos obrigatórios antes de enviar
if (!vehicleData.label || !vehicleData.group_id || !vehicleData.subgroup_id) {
  alert('Campos obrigatórios: label, group_id, subgroup_id');
  return;
}

// Enviar para API
const response = await createVehicle(vehicleData);
```

### 8. Handle Soft Delete Properly

```bash
# Listar apenas ativos (padrão)
GET /api/v1/vehicles

# Incluir deletados se necessário
GET /api/v1/vehicles?include_deleted=true

# Soft delete (não hard delete)
DELETE /api/v1/vehicles/1
```

---

## Examples - Complete Workflows

### Workflow 1: Create Vehicle and Associate Device

```bash
# 1. Login
POST /api/v1/auth/login
{
  "email": "user@example.com",
  "password": "password123"
}
# → Response: { "access_token": "..." }

# 2. Create Vehicle
POST /api/v1/vehicles
Authorization: Bearer {token}
{
  "label": "DEF-9999",
  "group_id": 14330,
  "subgroup_id": 15812,
  "unit_category_id": 1
}
# → Response: { "id": 5, ... }

# 3. Create Association
POST /api/v1/device-associations
Authorization: Bearer {token}
{
  "tracked_unit_id": 5,
  "device_id": 10
}
# → Response: { "id": 3, ... }
```

### Workflow 2: Update Driver and Check CNH Expiration

```bash
# 1. List drivers with expired CNH
GET /api/v1/drivers?cnh_expired=true
Authorization: Bearer {token}
# → Response: [ { "id": 2, "cnh_validate": "2024-12-31", ... } ]

# 2. Update CNH date
PUT /api/v1/drivers/2
Authorization: Bearer {token}
{
  "cnh_validate": "2026-12-31"
}
# → Response: { "id": 2, "cnh_validate": "2026-12-31", ... }
```

### Workflow 3: Release Device and Associate to New Vehicle

```bash
# 1. Find current association
GET /api/v1/device-associations?device_id=5&active_only=true
Authorization: Bearer {token}
# → Response: [ { "id": 4, "tracked_unit_id": 3, ... } ]

# 2. Release device (soft delete association)
DELETE /api/v1/device-associations/4
Authorization: Bearer {token}
# → Response: 204 No Content

# 3. Associate to new vehicle
POST /api/v1/device-associations
Authorization: Bearer {token}
{
  "tracked_unit_id": 7,
  "device_id": 5
}
# → Response: { "id": 5, ... }
```

---

**Última Atualização**: Novembro 2025
**Base URL**: http://localhost:8000/api/v1
**Documentação Interativa**: http://localhost:8000/docs

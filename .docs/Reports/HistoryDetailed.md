# Reports API - History Detailed (Histórico Completo com CAN Bus)

## Overview

Endpoints otimizados para consulta de histórico completo de telemetria com **todos os dados CAN Bus** em estrutura JSON aninhada.

1. **Cursor Pagination** - Paginação eficiente para visualização web
2. **Streaming CSV** - Exports diretos para download
3. **Estimation** - Estimativa de tamanho antes de exportar

---

## Authentication

Todos os endpoints requerem autenticação JWT:

```bash
Authorization: Bearer <your_jwt_token>
```

---

## 1. Cursor Pagination

**Endpoint:** `GET /api/v1/reports/history/detailed/cursor`

### Features:
- ✅ Dados CAN Bus completos (60+ campos)
- ✅ Estrutura JSON aninhada (unit, driver, device, event, etc.)
- ✅ Cursor pagination eficiente para partições
- ✅ Auto-filtrado por group/subgroup access
- ✅ Máximo 31 dias de range

### Example: First Page

```bash
curl -X GET "http://localhost:8000/api/v1/reports/history/detailed/cursor?\
start_date=2025-01-01%2000:00:00&\
end_date=2025-01-31%2023:59:59&\
limit=1000" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "data": [
    {
      "id": 15054598630,
      "unit": {
        "id": 47395,
        "label": "RVC-6C23",
        "label2": "753",
        "obs": "",
        "group": {
          "id": 14071,
          "name": "ANSAL - GRUPO CSC",
          "subgroup": {
            "id": 15059,
            "name": "ANSAL - JUIZ DE FORA"
          }
        }
      },
      "driver": {
        "id": 71439,
        "name": "ALENCAR PEREIRA DOS SANTOS - 284176",
        "login": "1720675929",
        "group": {
          "id": 14071,
          "name": "ANSAL - GRUPO CSC",
          "subgroup": {
            "id": 15059,
            "name": "ANSAL - JUIZ DE FORA"
          }
        }
      },
      "local_time": "2025-12-19 11:59:40",
      "time_write": "2025-12-19 13:11:36",
      "latitude": -21.86241,
      "longitude": -43.53115,
      "ign": true,
      "speed": 28,
      "odom": 339127270,
      "rpm": 1347,
      "address": "Estrada de Torreões - Anna Pereira Delgado - Juiz de Fora - MG",
      "poi": {
        "id": null,
        "name": null,
        "distance": null
      },
      "cerca": {
        "id": null,
        "name": null
      },
      "device": {
        "id": 52812,
        "identifier": "C684",
        "model": "VIRLOC 8"
      },
      "event": {
        "id": 1,
        "name": "TRACKING"
      },
      "electrical_data": {
        "voltage": 28.17,
        "battery": 4.29,
        "can_control_module_voltage": 28.17
      },
      "location": {
        "latitude": -21.86241,
        "longitude": -43.53115,
        "altitude": 816,
        "direction": 6,
        "gps": true
      },
      "engine": {
        "rpm": 1347,
        "can_rpm": 1347,
        "can_accel_pedal_percent": 0,
        "can_engine_torque_percent": 0,
        "can_retarder_torque": 1,
        "can_engine_oil_pressure": 0,
        "can_turbo_charger_pressure": null
      },
      "speed_distance": {
        "speed": 28,
        "can_speed": 28,
        "odom": 339127270,
        "can_total_odometer": 339127270
      },
      "fuel": {
        "can_fuel_level_percent": 95,
        "can_def_level_percent": 0,
        "can_total_used_fuel": 106440736
      },
      "temperature": {
        "can_engine_coolant_temp": 85,
        "can_engine_coolant_level": 0,
        "external_sensor_temperature": null
      },
      "transmission": {
        "can_gear": -125,
        "faixa": 10
      },
      "pneumatic": {
        "can_pneumatic_system1_pressure": 9,
        "can_pneumatic_system2_pressure": 0
      },
      "hourmeter": {
        "hourmeter": 0,
        "can_engine_hourmeter": 0
      },
      "status_flags": {
        "ignition": true,
        "in5": 0,
        "in6": 0,
        "in7": 0,
        "in8": 0,
        "can_cruise_control_state": 0,
        "can_break_pedal_state": 1,
        "can_parking_brake_state": 0,
        "can_retarder_in_use": 0,
        "connection": 0
      }
    }
  ],
  "next_cursor": "eyJ0IjogIjIwMjUtMTItMTkgMTE6NTk6NDAiLCAiaSI6IDE1MDU0NTk4NjMwfQ==",
  "has_more": true,
  "total_returned": 1000
}
```

### Response Structure

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | int (BigInt) | ID do registro |
| `unit` | object | Informações do veículo |
| `driver` | object/null | Informações do motorista |
| `local_time` | string | Timestamp local (YYYY-MM-DD HH:MI:SS) |
| `time_write` | string | Timestamp de escrita no servidor |
| `latitude` | number | Latitude GPS |
| `longitude` | number | Longitude GPS |
| `ign` | boolean | Status da ignição |
| `speed` | int | Velocidade (km/h) |
| `odom` | int | Odômetro (metros) |
| `rpm` | int | Rotação do motor |
| `address` | string | Endereço geocodificado |
| `poi` | object | Ponto de interesse |
| `cerca` | object | Cerca virtual/área |
| `device` | object | Dispositivo rastreador |
| `event` | object | Evento do rastreador |
| `electrical_data` | object | Dados elétricos |
| `location` | object | Localização detalhada |
| `engine` | object | Dados do motor (CAN) |
| `speed_distance` | object | Velocidade/Distância (CAN) |
| `fuel` | object | Combustível (CAN) |
| `temperature` | object | Temperatura (CAN) |
| `transmission` | object | Transmissão (CAN) |
| `pneumatic` | object | Sistema pneumático (CAN) |
| `hourmeter` | object | Horímetro |
| `status_flags` | object | Flags de status e entradas |

---

## 2. Streaming CSV Export

**Endpoint:** `GET /api/v1/reports/history/detailed/export/csv`

### Example

```bash
curl -X GET "http://localhost:8000/api/v1/reports/history/detailed/export/csv?\
start_date=2025-01-01%2000:00:00&\
end_date=2025-01-31%2023:59:59" \
  -H "Authorization: Bearer <token>" \
  -o history_detailed.csv
```

### CSV Columns (68 campos)

```
ID, Local Time, Time Write,
Unit ID, Unit Label, Unit Label2, Unit Obs, Unit Group ID, Unit Group Name, Unit Subgroup ID, Unit Subgroup Name,
Driver ID, Driver Name, Driver Login, Driver Group ID, Driver Group Name, Driver Subgroup ID, Driver Subgroup Name,
Latitude, Longitude, Ignition, Speed, Odometer, RPM, Address,
POI ID, POI Name, POI Distance,
Area ID, Area Name,
Device ID, Device Identifier, Device Model,
Event ID, Event Name,
Voltage, Battery, CAN Control Module Voltage,
Altitude, Direction, GPS,
CAN RPM, CAN Accel Pedal %, CAN Engine Torque %, CAN Retarder Torque, CAN Engine Oil Pressure, CAN Turbo Charger Pressure,
CAN Speed, CAN Total Odometer,
CAN Fuel Level %, CAN DEF Level %, CAN Total Used Fuel,
CAN Engine Coolant Temp, CAN Engine Coolant Level, External Sensor Temp,
CAN Gear, Faixa,
CAN Pneumatic System1 Pressure, CAN Pneumatic System2 Pressure,
Hourmeter, CAN Engine Hourmeter,
IN5, IN6, IN7, IN8,
CAN Cruise Control State, CAN Brake Pedal State, CAN Parking Brake State, CAN Retarder In Use, Connection
```

---

## 3. Export Size Estimation

**Endpoint:** `POST /api/v1/reports/history/detailed/export/estimate`

### Example

```bash
curl -X POST "http://localhost:8000/api/v1/reports/history/detailed/export/estimate?\
start_date=2025-01-01%2000:00:00&\
end_date=2025-01-31%2023:59:59" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "estimated_rows": 150000,
  "estimated_size_mb": 71.5,
  "estimated_time_seconds": 3,
  "date_range_days": 31
}
```

---

## CAN Bus Data Details

### electrical_data
| Campo | Descrição |
|-------|-----------|
| voltage | Tensão da bateria principal (V) |
| battery | Tensão da bateria backup (V) |
| can_control_module_voltage | Tensão do módulo de controle (V) |

### engine
| Campo | Descrição |
|-------|-----------|
| rpm | RPM do motor |
| can_rpm | RPM via CAN Bus |
| can_accel_pedal_percent | Pedal acelerador (%) |
| can_engine_torque_percent | Torque do motor (%) |
| can_retarder_torque | Torque do retarder |
| can_engine_oil_pressure | Pressão do óleo (kPa) |
| can_turbo_charger_pressure | Pressão do turbo (kPa) |

### fuel
| Campo | Descrição |
|-------|-----------|
| can_fuel_level_percent | Nível de combustível (%) |
| can_def_level_percent | Nível de ARLA/DEF (%) |
| can_total_used_fuel | Consumo total (litros) |

### temperature
| Campo | Descrição |
|-------|-----------|
| can_engine_coolant_temp | Temperatura líquido arrefecimento (°C) |
| can_engine_coolant_level | Nível líquido arrefecimento |
| external_sensor_temperature | Sensor externo temperatura |

### transmission
| Campo | Descrição |
|-------|-----------|
| can_gear | Marcha atual |
| faixa | Faixa de RPM (1-5) |

### pneumatic
| Campo | Descrição |
|-------|-----------|
| can_pneumatic_system1_pressure | Pressão sistema 1 (bar) |
| can_pneumatic_system2_pressure | Pressão sistema 2 (bar) |

### status_flags
| Campo | Descrição |
|-------|-----------|
| ignition | Ignição ligada |
| in5, in6, in7, in8 | Entradas digitais |
| can_cruise_control_state | Cruise control ativo |
| can_break_pedal_state | Pedal de freio pressionado |
| can_parking_brake_state | Freio de estacionamento |
| can_retarder_in_use | Retarder em uso |
| connection | Status de conexão |

---

## Access Control

Todos os endpoints automaticamente filtram dados baseado nas permissões do usuário:
- ✅ Filtra por `group_id` do usuário
- ✅ Respeita filtros de `subgroup_id`
- ✅ HTTP 403 se usuário não tem acesso

---

## Validation Rules

### Date Range
- **Máximo:** 31 dias entre `start_date` e `end_date`
- **Formato:** `YYYY-MM-DD HH:MM:SS`

### Vehicle IDs
- **Formato:** Comma-separated integers (`1234,5678`)

---

## Data Source

| Feature | Details |
|---------|---------|
| **Data Source** | mova.dev_status_30 (particionada) |
| **Granularity** | Por registro/evento |
| **Date Format** | YYYY-MM-DD HH:MM:SS |
| **Performance** | Parallel partition processing |

---

## Comparação: History vs History Detailed

| Aspecto | /history/cursor | /history/detailed/cursor |
|---------|-----------------|--------------------------|
| Campos | 13 | 68+ |
| CAN Bus | Básico | Completo |
| Estrutura | Flat | JSON aninhado |
| JOINs | 1 (tracked_unit) | 7 (unit, driver, device, event, groups) |
| Performance | Mais rápido | Mais completo |
| Uso | Mapas, tracking simples | Análise completa, relatórios |

---

**Última atualização:** 2026-01-19
**Versão:** 1.0.0

# Reports API - RPM Band Time (Tempo de Faixa)

## Overview

Endpoints para consulta de métricas de tempo por faixa de RPM, agregadas por dia, veículo e motorista.

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

**Endpoint:** `GET /api/v1/reports/rpm-band-time/cursor`

### Features:
- ✅ Métricas agregadas diárias por veículo/motorista
- ✅ Tempos em cada faixa de RPM (segundos)
- ✅ Cursor pagination eficiente
- ✅ Auto-filtrado por group/subgroup access

### Example: First Page

```bash
curl -X GET "http://localhost:8000/api/v1/reports/rpm-band-time/cursor?\
start_date=2025-01-01&\
end_date=2025-01-31&\
limit=1000" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "data": [
    {
      "day": "2025-01-31",
      "unit_id": 1234,
      "group_id": 10,
      "subgroup_id": 5,
      "driver_id": 42,
      "stop_engine_on": 3600,
      "blue": 7200,
      "green": 14400,
      "yellow": 1800,
      "red": 600,
      "inercia": 900,
      "total_time": 28500
    }
  ],
  "next_cursor": "eyJ0IjogIjIwMjUtMDEtMzEiLCAiaSI6IDEyMzR9",
  "has_more": true,
  "total_returned": 1000
}
```

### Response Fields:
- `day`: Data (YYYY-MM-DD)
- `unit_id`: ID do veículo
- `group_id`: ID do grupo
- `subgroup_id`: ID do subgrupo
- `driver_id`: ID do motorista (pode ser NULL)
- `stop_engine_on`: Tempo parado com motor ligado incluindo tempo produtivo (segundos) - soma de `time_stop_engine_on` + `time_stop_engine_on_productive`
- `blue`: Tempo na faixa azul/econômica (segundos)
- `green`: Tempo na faixa verde/ótima (segundos) - inclui `time_extra_eco`
- `yellow`: Tempo na faixa amarela/alerta (segundos)
- `red`: Tempo na faixa vermelha/excesso (segundos)
- `inercia`: Tempo em inércia/banguela (segundos)
- `total_time`: Tempo total (segundos) - soma de todas as faixas (inclui `time_stop_engine_on_productive`)

### RPM Band Explanation:
| Faixa | Cor | Descrição | Campos do BD |
|-------|-----|-----------|--------------|
| Parado Motor Ligado | - | Motor ligado sem movimento (inclui tempo produtivo) | `time_stop_engine_on` + `time_stop_engine_on_productive` |
| Blue | Azul | RPM econômico baixo | `time_blue` |
| Green | Verde | RPM ótimo (inclui extra-eco) | `time_green` + `time_extra_eco` |
| Yellow | Amarelo | RPM em alerta | `time_yellow` |
| Red | Vermelho | RPM em excesso | `time_red` |
| Inércia | - | Veículo em movimento sem aceleração | `time_inercia` |

---

## 2. Streaming CSV Export

**Endpoint:** `GET /api/v1/reports/rpm-band-time/export/csv`

### Example

```bash
curl -X GET "http://localhost:8000/api/v1/reports/rpm-band-time/export/csv?\
start_date=2025-01-01&\
end_date=2025-01-31" \
  -H "Authorization: Bearer <token>" \
  -o rpm_band_time.csv
```

### CSV Output Format

```csv
Date,Unit ID,Group ID,Subgroup ID,Driver ID,Stop Engine On (s),Blue (s),Green (s),Yellow (s),Red (s),Inertia (s),Total (s)
2025-01-31,1234,10,5,42,3600,7200,14400,1800,600,900,28500
2025-01-30,1234,10,5,42,3200,6800,13200,1600,500,800,26100
```

---

## 3. Export Size Estimation

**Endpoint:** `POST /api/v1/reports/rpm-band-time/export/estimate`

### Example

```bash
curl -X POST "http://localhost:8000/api/v1/reports/rpm-band-time/export/estimate?\
start_date=2025-01-01&\
end_date=2025-01-31" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "estimated_rows": 15000,
  "estimated_size_mb": 2.2,
  "estimated_time_seconds": 1.5,
  "date_range_days": 31
}
```

---

## 🔐 Access Control

Todos os endpoints automaticamente filtram dados baseado nas permissões do usuário:
- ✅ Filtra por `group_id` do usuário
- ✅ Respeita filtros de `subgroup_id`
- ✅ HTTP 403 se usuário não tem acesso

---

## ✅ Validation Rules

### Date Range
- **Máximo:** 31 dias entre `start_date` e `end_date`
- **Formato:** `YYYY-MM-DD`

### Filter IDs
- **Formato:** Comma-separated integers (`1234,5678`)
- **Filters disponíveis:** unit_ids, driver_ids, subgroup_ids

---

## 📊 Data Source

| Feature | Details |
|---------|---------|
| **Data Source** | mova.con_telemetry_day |
| **Granularity** | Por dia agregado |
| **Date Format** | YYYY-MM-DD |
| **Use Case** | Análise de tempo por faixa de RPM |

---

**Última atualização:** 2026-01-21
**Versão:** 1.1.0

### Changelog

#### v1.1.0 (2026-01-21)
- **Alteração:** Campo `stop_engine_on` agora inclui `time_stop_engine_on_productive` na soma
- **Alteração:** Campo `total_time` agora também inclui `time_stop_engine_on_productive` na soma

#### v1.0.0 (2026-01-19)
- Versão inicial do endpoint

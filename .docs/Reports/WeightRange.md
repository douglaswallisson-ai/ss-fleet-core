# Reports API - Weight Range (Metas e Pesos)

## Overview

Endpoints para consulta de metas e pesos por faixa, agregados por grupo e subgrupo.

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

**Endpoint:** `GET /api/v1/reports/weight-range/cursor`

### Features:
- Métricas agregadas por range/grupo/subgrupo
- Peso médio e meta média por faixa
- Cursor pagination eficiente
- Auto-filtrado por group/subgroup access

### Example: First Page

```bash
curl -X GET "http://localhost:8000/api/v1/reports/weight-range/cursor?\
limit=1000" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "data": [
    {
      "range_id": 1,
      "group_id": 10,
      "subgroup_id": 5,
      "weight": 15.5,
      "goal": 20.0
    },
    {
      "range_id": 2,
      "group_id": 10,
      "subgroup_id": 5,
      "weight": 25.0,
      "goal": 30.0
    }
  ],
  "next_cursor": "eyJpIjogMTIzNH0",
  "has_more": true,
  "total_returned": 1000
}
```

### Response Fields:
- `range_id`: Identificador da faixa (1-6 para diferentes métricas)
- `group_id`: ID do grupo
- `subgroup_id`: ID do subgrupo
- `weight`: Peso médio para esta faixa
- `goal`: Meta/objetivo médio para esta faixa

### Range ID Explanation:
| Range ID | Descrição |
|----------|-----------|
| 1 | Faixa de RPM Azul (Blue) |
| 2 | Faixa de RPM Verde (Green) |
| 3 | Faixa de RPM Amarelo (Yellow) |
| 4 | Faixa de RPM Vermelho (Red) |
| 5 | Inércia (Coasting) |
| 6 | Motor Ligado Parado (Stop Engine On) |

---

## 2. Streaming CSV Export

**Endpoint:** `GET /api/v1/reports/weight-range/export/csv`

### Example

```bash
curl -X GET "http://localhost:8000/api/v1/reports/weight-range/export/csv" \
  -H "Authorization: Bearer <token>" \
  -o weight_range.csv
```

### CSV Output Format

```csv
Range ID,Group ID,Subgroup ID,Weight,Goal
1,10,5,15.5,20.0
2,10,5,25.0,30.0
3,10,5,10.0,15.0
```

---

## 3. Export Size Estimation

**Endpoint:** `POST /api/v1/reports/weight-range/export/estimate`

### Example

```bash
curl -X POST "http://localhost:8000/api/v1/reports/weight-range/export/estimate" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "estimated_rows": 150,
  "estimated_size_mb": 0.01,
  "estimated_time_seconds": 0.1
}
```

---

## Access Control

Todos os endpoints automaticamente filtram dados baseado nas permissões do usuário:
- Filtra por `group_id` do usuário
- Respeita filtros de `subgroup_id`
- HTTP 403 se usuário não tem acesso

---

## Validation Rules

### Filter IDs
- **Formato:** Comma-separated integers (`1234,5678`)
- **Filters disponíveis:** subgroup_ids

---

## Data Source

| Feature | Details |
|---------|---------|
| **Data Source** | mova.weight_range |
| **Granularity** | Por range/grupo/subgrupo |
| **Aggregation** | AVG(weight), AVG(goal) |
| **Use Case** | Configuração de metas e pesos por faixa |

---

**Última atualização:** 2026-01-21
**Versão:** 1.0.0

### Changelog

#### v1.0.0 (2026-01-21)
- Versão inicial do endpoint

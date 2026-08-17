# Reports API - Heatmap (Mapa de Calor)

## Overview

Endpoints para consulta de contagem de eventos por hora, agregados por veículo e motorista. Ideal para criar mapas de calor de comportamento de direção.

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

**Endpoint:** `GET /api/v1/reports/heatmap/cursor`

### Features:
- ✅ Contagem de eventos agregados por hora
- ✅ Dados por veículo e motorista
- ✅ Cursor pagination eficiente
- ✅ Auto-filtrado por group/subgroup access

### Example: First Page

```bash
curl -X GET "http://localhost:8000/api/v1/reports/heatmap/cursor?\
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
      "data_hora": "2025-01-31 14:00",
      "unit_id": 1234,
      "label": "ABC-1234",
      "label2": "Caminhão 01",
      "group_id": 10,
      "subgroup_id": 5,
      "driver_id": 42,
      "driver_name": "João Silva",
      "faixa_amarela": 5,
      "faixa_vermelha": 2,
      "batendo_transmissao": 1,
      "parado_acelerando": 3,
      "excesso_velocidade": 0
    }
  ],
  "next_cursor": "eyJ0IjogIjIwMjUtMDEtMzEgMTQ6MDAiLCAiaSI6IDEyMzR9",
  "has_more": true,
  "total_returned": 1000
}
```

### Response Fields:
- `data_hora`: Data e hora truncada (YYYY-MM-DD HH:00)
- `unit_id`: ID do veículo
- `label`: Placa do veículo
- `label2`: Identificação secundária
- `group_id`: ID do grupo
- `subgroup_id`: ID do subgrupo
- `driver_id`: ID do motorista (pode ser NULL)
- `driver_name`: Nome do motorista
- `faixa_amarela`: Eventos de faixa amarela de RPM
- `faixa_vermelha`: Eventos de faixa vermelha de RPM
- `batendo_transmissao`: Eventos de batendo transmissão
- `parado_acelerando`: Eventos de parado acelerando
- `excesso_velocidade`: Eventos de excesso de velocidade

### Event Types:
| Evento | Descrição |
|--------|-----------|
| Faixa Amarela | RPM na zona de alerta |
| Faixa Vermelha | RPM na zona de excesso |
| Batendo Transmissão | Troca de marcha inadequada |
| Parado Acelerando | Motor acelerando com veículo parado |
| Excesso de Velocidade | Velocidade acima do limite |

---

## 2. Streaming CSV Export

**Endpoint:** `GET /api/v1/reports/heatmap/export/csv`

### Example

```bash
curl -X GET "http://localhost:8000/api/v1/reports/heatmap/export/csv?\
start_date=2025-01-01%2000:00:00&\
end_date=2025-01-31%2023:59:59" \
  -H "Authorization: Bearer <token>" \
  -o heatmap.csv
```

### CSV Output Format

```csv
Date/Hour,Unit ID,Label,Label2,Group ID,Subgroup ID,Driver ID,Driver Name,Yellow Band,Red Band,Transmission Hitting,Stopped Accelerating,Speeding
2025-01-31 14:00,1234,ABC-1234,Caminhão 01,10,5,42,João Silva,5,2,1,3,0
2025-01-31 13:00,1234,ABC-1234,Caminhão 01,10,5,42,João Silva,3,1,0,2,1
```

---

## 3. Export Size Estimation

**Endpoint:** `POST /api/v1/reports/heatmap/export/estimate`

### Example

```bash
curl -X POST "http://localhost:8000/api/v1/reports/heatmap/export/estimate?\
start_date=2025-01-01%2000:00:00&\
end_date=2025-01-31%2023:59:59" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "estimated_rows": 25000,
  "estimated_size_mb": 4.8,
  "estimated_time_seconds": 5,
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
- **Formato:** `YYYY-MM-DD HH:MM:SS`

### Filter IDs
- **Formato:** Comma-separated integers (`1234,5678`)
- **Filters disponíveis:** unit_ids, driver_ids, subgroup_ids

---

## 📊 Data Source

| Feature | Details |
|---------|---------|
| **Data Source** | mova.heatmap |
| **Granularity** | Por hora agregado |
| **Date Format** | YYYY-MM-DD HH:MM:SS |
| **Use Case** | Mapa de calor de eventos de direção |

---

## 💡 Use Cases

### Mapa de Calor por Hora do Dia
Agrupe os dados por hora para identificar padrões:
- Horários de pico de infrações
- Períodos de maior excesso de velocidade
- Momentos de maior desgaste de transmissão

### Ranking de Motoristas
Some os eventos por motorista para criar rankings de comportamento.

### Dashboard de Frota
Use os dados agregados para dashboards em tempo real.

---

**Última atualização:** 2026-01-19
**Versão:** 1.0.0

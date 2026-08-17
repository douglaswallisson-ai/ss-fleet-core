# Reports API - Driver KM/Fuel/Hours (Métricas por Motorista)

## Overview

Endpoints otimizados para consulta de métricas agregadas por motorista (distância, combustível, tempo) com suporte para grandes volumes de dados:

1. **Cursor Pagination** - Paginação eficiente para visualização web
2. **Streaming CSV** - Exports diretos para download
3. **Estimation** - Estimativa de tamanho antes de exportar

---

## Authentication

Todos os endpoints requerem autenticação JWT:

```bash
# Obter JWT token
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"login": "your_user", "password": "your_password"}'

# Usar token nas requisições
Authorization: Bearer <your_jwt_token>
```

---

## 1. Cursor Pagination

**Best for:** Visualização de métricas agregadas por motorista

**Endpoint:** `GET /api/v1/reports/driver-km-fuel-hours/cursor`

### Features:
- ✅ Métricas agregadas diárias por motorista
- ✅ Dados consolidados (distância, combustível, tempo)
- ✅ Cursor pagination eficiente
- ✅ Formato de data simplificado (YYYY-MM-DD)
- ✅ Auto-filtrado por group/subgroup access

### Example: First Page

```bash
curl -X GET "http://localhost:8000/api/v1/reports/driver-km-fuel-hours/cursor?\
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
      "dt": "2025-01-31",
      "label": "ABC-1234",
      "unit_id": 1234,
      "group_id": 10,
      "subgroup_id": 5,
      "driver": "João Silva",
      "driver_id": 42,
      "distance_traveled_hist": 245.8,
      "used_fuel_hist": 28.5,
      "time_traveled_hist": 8.5,
      "distance_traveled_hist_filtrado": 240.2,
      "is_estimated": false
    }
    // ... more records
  ],
  "next_cursor": "eyJ0IjogIjIwMjUtMDEtMzEiLCAiaSI6IDEyMzR9",
  "has_more": true,
  "total_returned": 1000
}
```

### Response Fields:
- `dt`: Data (YYYY-MM-DD)
- `label`: Placa do veículo
- `unit_id`: ID da unidade/veículo
- `group_id`: ID do grupo
- `subgroup_id`: ID do subgrupo
- `driver`: Nome do motorista (pode ser NULL)
- `driver_id`: ID do motorista (pode ser NULL)
- `distance_traveled_hist`: Distância percorrida em KM
- `used_fuel_hist`: Combustível utilizado em litros
- `time_traveled_hist`: Tempo viajado em horas
- `distance_traveled_hist_filtrado`: Distância filtrada em KM (somente quando combustível > 0 e < 500L)
- `is_estimated`: Indica se dados estimados foram usados (true quando combustível real = 0 ou NULL)

### Data Fallback Logic:
Quando `used_fuel_hist` original é NULL ou 0, o sistema automaticamente usa os campos estimados:
- `distance_traveled_hist_estimated` → `distance_traveled_hist`
- `used_fuel_hist_estimated` → `used_fuel_hist`
- O campo `is_estimated` será `true` para indicar que dados estimados foram utilizados

### Example: Filter Specific Drivers

```bash
curl -X GET "http://localhost:8000/api/v1/reports/driver-km-fuel-hours/cursor?\
start_date=2025-01-01&\
end_date=2025-01-31&\
driver_ids=42,87,105&\
limit=1000" \
  -H "Authorization: Bearer <token>"
```

### Example: Next Page

```bash
curl -X GET "http://localhost:8000/api/v1/reports/driver-km-fuel-hours/cursor?\
start_date=2025-01-01&\
end_date=2025-01-31&\
cursor=eyJ0IjogIjIwMjUtMDEtMzEiLCAiaSI6IDEyMzR9&\
limit=1000" \
  -H "Authorization: Bearer <token>"
```

### Client-side Implementation (JavaScript)

```javascript
async function fetchDriverMetrics(startDate, endDate) {
  const allData = [];
  let cursor = null;
  let hasMore = true;

  while (hasMore) {
    const params = new URLSearchParams({
      start_date: startDate,
      end_date: endDate,
      limit: 1000
    });

    if (cursor) {
      params.append('cursor', cursor);
    }

    const response = await fetch(
      `/api/v1/reports/driver-km-fuel-hours/cursor?${params}`,
      { headers: { 'Authorization': `Bearer ${token}` } }
    );

    const result = await response.json();

    allData.push(...result.data);
    cursor = result.next_cursor;
    hasMore = result.has_more;

    // Update UI progressively
    console.log(`Loaded ${allData.length} records...`);
  }

  return allData;
}
```

---

## 2. Streaming CSV Export

**Best for:** Downloads CSV, imports Excel, exportações de dados

**Endpoint:** `GET /api/v1/reports/driver-km-fuel-hours/export/csv`

### Features:
- ✅ Memory efficient (streaming em chunks)
- ✅ Download inicia imediatamente (sem espera)
- ✅ Suporta contagens ilimitadas de registros
- ✅ UTF-8 encoding com headers
- ✅ Máximo 31 dias de range

### Example: Download Driver Metrics CSV

```bash
curl -X GET "http://localhost:8000/api/v1/reports/driver-km-fuel-hours/export/csv?\
start_date=2025-01-01&\
end_date=2025-01-31" \
  -H "Authorization: Bearer <token>" \
  -o driver_metrics.csv
```

### Example: Filter Specific Drivers

```bash
curl -X GET "http://localhost:8000/api/v1/reports/driver-km-fuel-hours/export/csv?\
start_date=2025-01-01&\
end_date=2025-01-31&\
driver_ids=42,87,105" \
  -H "Authorization: Bearer <token>" \
  -o filtered_driver_metrics.csv
```

### Browser Download (JavaScript)

```javascript
async function downloadDriverMetricsCSV(startDate, endDate) {
  const params = new URLSearchParams({
    start_date: startDate,
    end_date: endDate
  });

  const response = await fetch(
    `/api/v1/reports/driver-km-fuel-hours/export/csv?${params}`,
    { headers: { 'Authorization': `Bearer ${token}` } }
  );

  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `driver_metrics_${startDate}_${endDate}.csv`;
  a.click();
}
```

### CSV Output Format

```csv
Date,Vehicle Label,Unit ID,Group ID,Subgroup ID,Driver Name,Driver ID,Distance (km),Fuel (L),Hours,Filtered Distance (km),Is Estimated
2025-01-31,ABC-1234,1234,10,5,João Silva,42,245.80,28.50,8.50,240.20,False
2025-01-31,XYZ-5678,5678,10,5,Maria Santos,87,180.20,22.30,7.20,175.50,False
2025-01-30,ABC-1234,1234,10,5,João Silva,42,210.50,25.80,7.80,205.30,True
```

> **Nota:** A coluna `Is Estimated` indica se os dados de distância e combustível vieram das colunas `*_estimated` (quando o combustível real era 0 ou NULL).

---

## 3. Export Size Estimation

**Best for:** Mostrar estimativa ao usuário antes de exportar

**Endpoint:** `POST /api/v1/reports/driver-km-fuel-hours/export/estimate`

### Example

```bash
curl -X POST "http://localhost:8000/api/v1/reports/driver-km-fuel-hours/export/estimate" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2025-01-01",
    "end_date": "2025-01-31"
  }'
```

**Response:**
```json
{
  "estimated_rows": 38971,
  "estimated_size_mb": 3.2,
  "estimated_time_seconds": 3,
  "recommended_method": "streaming"
}
```

---

## 🔐 Access Control

Todos os endpoints automaticamente filtram dados baseado nas permissões do usuário:

- ✅ Retorna apenas veículos onde `group_id` corresponde ao acesso do usuário
- ✅ Respeita filtros de `subgroup_id` (NULL = recursos compartilhados)
- ✅ HTTP 403 se usuário não tem acesso a grupos
- ✅ HTTP 404 se não há veículos acessíveis

**Como funciona:**
```python
# Usuário tem acesso a:
user.group_access = [(10, 5), (10, 7), (15, None)]

# Query automaticamente filtra:
WHERE group_id IN (10, 15)
  AND (subgroup_id IS NULL OR subgroup_id IN (5, 7))
```

---

## ✅ Validation Rules

### Date Range
- **Máximo:** 31 dias entre `start_date` e `end_date`
- **Formato:** `YYYY-MM-DD` (não precisa especificar HH:MM:SS)
- **Error:** HTTP 400 se range exceder 31 dias

### Filter IDs
- **Opcional:** Filtrar por IDs específicos
- **Formato:** Comma-separated integers (`1234,5678,9012`)
- **Validação:** Apenas IDs acessíveis são consultados
- **Filters disponíveis:** vehicle_ids, driver_ids, unit_ids, subgroup_ids

### Pagination
- **Limit:** 1-5000 registros por página (default: 1000)
- **Cursor:** String base64 do response anterior

---

## ⚡ Performance Benchmarks

### Cursor Pagination:
- 40k records agregados: ~1-2 segundos total
- Primeira página: <400ms
- Páginas subsequentes: <250ms cada

### Streaming CSV:
- 40k records: ~3-5 segundos
- Download inicia: Imediatamente
- Uso de memória: ~30MB constante

---

## ❌ Error Responses

### 400 Bad Request
```json
{
  "detail": "Date range cannot exceed 31 days. Current range: 45 days"
}
```

### 403 Forbidden
```json
{
  "detail": "No group access"
}
```

### 404 Not Found
```json
{
  "detail": "No accessible vehicles found"
}
```

### 500 Internal Server Error
```json
{
  "detail": "Database query timeout"
}
```

---

## 💡 Tips & Best Practices

1. **Use cursor pagination para UI**: Progressive loading proporciona melhor UX
2. **Use streaming CSV para exports**: Mais rápido e eficiente para datasets grandes
3. **Limite date ranges**: Ranges menores = queries mais rápidas
4. **Filtre por IDs quando possível**: Reduz result set
5. **Cache results client-side**: Evite re-buscar os mesmos dados
6. **Use estimativas antes de exports grandes**: Mostre ao usuário o que esperar
7. **Formato de data simplificado**: Apenas YYYY-MM-DD (não precisa HH:MM:SS)

---

## 📊 Data Source

| Feature | Details |
|---------|---------|
| **Data Source** | con_driver_h_km |
| **Granularity** | Por dia agregado |
| **Date Format** | YYYY-MM-DD |
| **Typical Size** | 10k-50k records/month |
| **Use Case** | Métricas de desempenho de motoristas |
| **Filters** | vehicle_ids, driver_ids, unit_ids, subgroup_ids |

---

## 📚 OpenAPI Documentation

Documentação interativa disponível em:
- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

Teste todos os endpoints diretamente no browser com autenticação!

---

**Última atualização:** 2026-02-02
**Versão:** 2.2.0 (fallback para dados estimados + is_estimated flag)

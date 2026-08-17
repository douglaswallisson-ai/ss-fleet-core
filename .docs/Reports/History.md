# Reports API - History (Histórico de Telemetria)

## Overview

Endpoints otimizados para consulta de histórico completo de telemetria (GPS, velocidade, RPM, etc) com suporte para 100k+ registros:

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

**Best for:** Visualização em tempo real, infinite scroll, progressive loading

**Endpoint:** `GET /api/v1/reports/history/cursor`

### Features:
- ✅ Eficiente para 100k+ registros (sem overhead de OFFSET)
- ✅ Resultados consistentes durante inserções concorrentes
- ✅ Performance constante independente da profundidade da página
- ✅ Máximo 31 dias de range
- ✅ Auto-filtrado por group/subgroup access do usuário

### Example: First Page

```bash
curl -X GET "http://localhost:8000/api/v1/reports/history/cursor?\
start_date=2025-01-01 00:00:00&\
end_date=2025-01-31 23:59:59&\
limit=1000" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "data": [
    {
      "id": 123456789,
      "label": "ABC-1234",
      "label2": "Caminhão 01",
      "obs": "Veículo principal",
      "local_time": "2025-01-31 23:59:45",
      "time_write": "2025-01-31 23:59:50",
      "latitude": -23.5505,
      "longitude": -46.6333,
      "ign": true,
      "speed": 60.5,
      "odom": 125000,
      "rpm": 1800,
      "address": "Av. Paulista, São Paulo"
    }
    // ... 999 more records
  ],
  "next_cursor": "eyJ0IjogIjIwMjUtMDEtMzEgMjM6NTk6NDUiLCAiaSI6IDEyMzQ1Njc4OX0=",
  "has_more": true,
  "total_returned": 1000
}
```

### Example: Next Page

```bash
curl -X GET "http://localhost:8000/api/v1/reports/history/cursor?\
start_date=2025-01-01 00:00:00&\
end_date=2025-01-31 23:59:59&\
cursor=eyJ0IjogIjIwMjUtMDEtMzEgMjM6NTk6NDUiLCAiaSI6IDEyMzQ1Njc4OX0=&\
limit=1000" \
  -H "Authorization: Bearer <token>"
```

### Client-side Implementation (JavaScript)

```javascript
async function fetchHistoryData(startDate, endDate) {
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
      `/api/v1/reports/history/cursor?${params}`,
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

**Endpoint:** `GET /api/v1/reports/history/export/csv`

### Features:
- ✅ Memory efficient (streaming em chunks)
- ✅ Download inicia imediatamente (sem espera)
- ✅ Suporta contagens ilimitadas de registros
- ✅ UTF-8 encoding com headers
- ✅ Máximo 31 dias de range

### Example: Download CSV

```bash
curl -X GET "http://localhost:8000/api/v1/reports/history/export/csv?\
start_date=2025-01-01 00:00:00&\
end_date=2025-01-31 23:59:59" \
  -H "Authorization: Bearer <token>" \
  -o history_report.csv
```

### Example: Filter Specific Vehicles

```bash
curl -X GET "http://localhost:8000/api/v1/reports/history/export/csv?\
start_date=2025-01-01 00:00:00&\
end_date=2025-01-31 23:59:59&\
vehicle_ids=1234,5678" \
  -H "Authorization: Bearer <token>" \
  -o filtered_report.csv
```

### Browser Download (JavaScript)

```javascript
async function downloadHistoryCSV(startDate, endDate) {
  const params = new URLSearchParams({
    start_date: startDate,
    end_date: endDate
  });

  const response = await fetch(
    `/api/v1/reports/history/export/csv?${params}`,
    { headers: { 'Authorization': `Bearer ${token}` } }
  );

  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `history_${startDate}_${endDate}.csv`;
  a.click();
}
```

### CSV Output Format

```csv
ID,Label,Label2,Obs,Local Time,Time Write,Latitude,Longitude,Ignition,Speed,Odometer,RPM,Address
123456789,ABC-1234,Caminhão 01,Veículo principal,2025-01-31 23:59:45,2025-01-31 23:59:50,-23.5505,-46.6333,True,60.5,125000,1800,"Av. Paulista, São Paulo"
123456788,XYZ-5678,Van 02,Veículo secundário,2025-01-31 23:59:30,2025-01-31 23:59:35,-23.5506,-46.6334,True,45.0,98000,1500,"Rua Augusta, São Paulo"
```

---

## 3. Export Size Estimation

**Best for:** Mostrar estimativa ao usuário antes de exportar

**Endpoint:** `POST /api/v1/reports/history/export/estimate`

### Example

```bash
curl -X POST "http://localhost:8000/api/v1/reports/history/export/estimate" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2025-01-01 00:00:00",
    "end_date": "2025-01-31 23:59:59"
  }'
```

**Response:**
```json
{
  "estimated_rows": 150000,
  "estimated_size_mb": 45.5,
  "estimated_time_seconds": 12,
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
- **Formato:** `YYYY-MM-DD HH:MM:SS`
- **Error:** HTTP 400 se range exceder 31 dias

### Vehicle IDs
- **Opcional:** Filtrar por IDs específicos
- **Formato:** Comma-separated integers (`1234,5678,9012`)
- **Validação:** Apenas IDs acessíveis são consultados

### Pagination
- **Limit:** 1-5000 registros por página (default: 1000)
- **Cursor:** String base64 do response anterior

---

## ⚡ Performance Benchmarks

### Cursor Pagination:
- 100k records: ~2-3 segundos total (100 páginas × ~300ms)
- Primeira página: <500ms
- Páginas subsequentes: <300ms cada

### Streaming CSV:
- 100k records: ~10-15 segundos
- Download inicia: Imediatamente (primeiro chunk)
- Uso de memória: ~50MB constante (chunked processing)

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

---

## 📊 Data Source

| Feature | Details |
|---------|---------|
| **Data Source** | dev_status_30_pYYYYMMDD |
| **Granularity** | Por segundo/registro |
| **Date Format** | YYYY-MM-DD HH:MM:SS |
| **Typical Size** | 100k-500k records/month |
| **Use Case** | Tracking detalhado, GPS |
| **Filters** | vehicle_ids |

---

## 📚 OpenAPI Documentation

Documentação interativa disponível em:
- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

Teste todos os endpoints diretamente no browser com autenticação!

---

**Última atualização:** 2025-01-26
**Versão:** 2.0.0

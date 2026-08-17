# Reports API - Telemetry (Dados de Telemetria Avançada)

## Overview

Endpoints para consulta de dados avançados de telemetria em tempo real, incluindo informações detalhadas do CAN Bus e sensores do veículo.

**Status:** 🚧 Em desenvolvimento

---

## Dados Disponíveis

### Dados do CAN Bus
- Pedal Acelerador (%)
- Rotação do Motor (RPM)
- Temperatura do Motor (°C)
- Pressão do Óleo (KPa)
- Nível de Combustível (%)
- Consumo Total (L)
- Velocidade (km/h)
- Torque do Motor (%)
- Freio Motor (%)
- Cruise Control
- Embreagem (ON/OFF)
- Freio Estacionamento (ON/OFF)
- Freio Serviço (ON/OFF)

### Dados de Hodômetro e Horímetro
- Hodômetro (metros)
- Tempo de Horímetro (minutos)

---

## Formato de Data

- **Date Format:** `YYYY-MM-DD HH:MM:SS`
- **Granularity:** Por segundo (dados em tempo real)
- **Max Range:** 31 dias

---

## 🔐 Access Control

Todos os endpoints automaticamente filtram dados baseado nas permissões do usuário:

- ✅ Retorna apenas veículos onde `group_id` corresponde ao acesso do usuário
- ✅ Respeita filtros de `subgroup_id` (NULL = recursos compartilhados)
- ✅ HTTP 403 se usuário não tem acesso a grupos
- ✅ HTTP 404 se não há veículos acessíveis

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

## 📊 Data Source

| Feature | Details |
|---------|---------|
| **Data Source** | dev_status_30_pYYYYMMDD (campos específicos de telemetria) |
| **Granularity** | Por segundo/registro |
| **Date Format** | YYYY-MM-DD HH:MM:SS |
| **Typical Size** | Varia por uso |
| **Use Case** | Análise detalhada de comportamento do veículo |

---

## 🚧 Endpoints Planejados

### 1. Cursor Pagination
**Endpoint:** `GET /api/v1/reports/telemetry/cursor`

Paginação eficiente para visualização de dados de telemetria em tempo real.

### 2. Streaming CSV Export
**Endpoint:** `GET /api/v1/reports/telemetry/export/csv`

Export direto de dados de telemetria para análise offline.

### 3. Export Size Estimation
**Endpoint:** `POST /api/v1/reports/telemetry/export/estimate`

Estimativa de tamanho antes de exportar dados de telemetria.

---

## 💡 Use Cases

1. **Análise de Comportamento do Motorista:**
   - Uso excessivo do freio
   - Acelerações bruscas
   - Uso inadequado da embreagem
   - Idle prolongado

2. **Manutenção Preventiva:**
   - Temperatura do motor elevada
   - Pressão do óleo baixa
   - Padrões anormais de RPM

3. **Eficiência de Combustível:**
   - Correlação RPM x velocidade
   - Uso do cruise control
   - Padrões de consumo

4. **Segurança:**
   - Uso correto do freio de estacionamento
   - Excesso de velocidade
   - Torque excessivo

---

## 📚 OpenAPI Documentation

Documentação interativa disponível em:
- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

---

## 🔄 Roadmap

- [ ] Implementar endpoint de cursor pagination
- [ ] Implementar endpoint de streaming CSV export
- [ ] Implementar endpoint de estimation
- [ ] Adicionar filtros avançados (por tipo de dado)
- [ ] Implementar agregações customizadas
- [ ] Adicionar suporte para gráficos em tempo real

---

**Última atualização:** 2025-01-26
**Versão:** 1.0.0 (Placeholder)
**Status:** 🚧 Em desenvolvimento

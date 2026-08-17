# 📊 Reports API Documentation

Esta seção contém toda a documentação dos endpoints de relatórios da Fleet Management Platform.

## 📁 Estrutura de Documentação

```
Reports/
├── README.md          ← Este arquivo
├── History.md         ← Histórico de Telemetria
├── DriverKmFuel.md    ← Métricas de Motoristas
└── Telemetry.md       ← Telemetria Avançada (em desenvolvimento)
```

---

## 📖 Documentação Disponível

### [History.md](History.md) - Histórico de Telemetria
**Status:** ✅ Produção

Relatório completo de histórico de telemetria com dados de GPS, velocidade, RPM e muito mais.

**Endpoints:**
- `GET /api/v1/reports/history/cursor` - Cursor pagination
- `GET /api/v1/reports/history/export/csv` - Streaming CSV export
- `POST /api/v1/reports/history/export/estimate` - Size estimation

**Características:**
- Suporta 100k+ registros
- Performance: 2-3s para 100k records
- Formato: `YYYY-MM-DD HH:MM:SS`
- Máximo: 31 dias de range
- Filtros: vehicle_ids

**Use Cases:**
- Tracking detalhado de veículos
- Análise de rotas (GPS)
- Monitoramento em tempo real
- Exports para análise offline

---

### [DriverKmFuel.md](DriverKmFuel.md) - Métricas por Motorista
**Status:** ✅ Produção

Relatório de métricas agregadas por motorista com dados de distância, combustível e horas trabalhadas.

**Endpoints:**
- `GET /api/v1/reports/driver-km-fuel-hours/cursor` - Cursor pagination
- `GET /api/v1/reports/driver-km-fuel-hours/export/csv` - Streaming CSV export
- `POST /api/v1/reports/driver-km-fuel-hours/export/estimate` - Size estimation

**Características:**
- Suporta 40k+ registros agregados
- Performance: 1-2s para 40k records
- Formato: `YYYY-MM-DD` (apenas data)
- Máximo: 31 dias de range
- Filtros: vehicle_ids, driver_ids, unit_ids, subgroup_ids

**Use Cases:**
- Avaliação de desempenho de motoristas
- Métricas de eficiência
- Relatórios de combustível
- Controle de jornada de trabalho

---

### [Telemetry.md](Telemetry.md) - Telemetria Avançada
**Status:** 🚧 Em Desenvolvimento

Relatório de dados avançados de telemetria do CAN Bus para análise detalhada de comportamento do veículo.

**Dados Planejados:**
- Pedal Acelerador (%)
- Rotação do Motor (RPM)
- Temperatura do Motor (°C)
- Pressão do Óleo (KPa)
- Nível de Combustível (%)
- Torque do Motor (%)
- Status de Freios
- Cruise Control

**Use Cases Planejados:**
- Análise de comportamento do motorista
- Manutenção preventiva
- Eficiência de combustível
- Segurança operacional

---

## 🎯 Padrões de Nomenclatura

Todos os novos relatórios devem seguir este padrão de organização:

### Estrutura de Arquivo:
```
.docs/Reports/[ReportName].md
```

### Nomenclatura:
- **PascalCase** para nomes de arquivo (ex: `History.md`, `DriverKmFuel.md`)
- Nome descritivo do tipo de relatório
- Sem prefixos ou sufixos genéricos

### Conteúdo Obrigatório:
1. **Overview** - Descrição geral do relatório
2. **Authentication** - Como autenticar
3. **Endpoints** - Lista de endpoints disponíveis
4. **Examples** - Exemplos práticos de uso
5. **Access Control** - Regras de controle de acesso
6. **Validation Rules** - Regras de validação
7. **Performance Benchmarks** - Métricas de performance
8. **Error Responses** - Códigos de erro possíveis
9. **Tips & Best Practices** - Boas práticas
10. **Data Source** - Fonte de dados e características

---

## 🚀 Como Adicionar Novo Relatório

### 1. Criar Arquivo de Documentação
```bash
touch .docs/Reports/NovoRelatorio.md
```

### 2. Seguir Template Padrão
Use [History.md](History.md) ou [DriverKmFuel.md](DriverKmFuel.md) como template.

### 3. Atualizar INDEX.md
Adicione referência no arquivo [../INDEX.md](../INDEX.md) na seção "📊 Reports API".

### 4. Atualizar Este README
Adicione entrada na seção "📖 Documentação Disponível".

---

## 📚 Links Úteis

- **[INDEX.md](../INDEX.md)** - Índice geral da documentação
- **[API_USAGE.md](../API_USAGE.md)** - Guia de uso geral da API
- **[CRUD_STANDARDS.md](../CRUD_STANDARDS.md)** - Padrões de CRUD

---

## 🔄 Última Atualização

- **Data:** 26 de Novembro de 2025
- **Versão:** 1.0.0
- **Responsável:** Fleet Management Team

---

## ✅ Checklist para Novos Reports

Ao criar um novo relatório, certifique-se de:

- [ ] Criar arquivo `.md` em `Reports/` com nome descritivo
- [ ] Seguir template padrão (seções obrigatórias)
- [ ] Incluir exemplos práticos de uso (curl + JavaScript)
- [ ] Documentar todos os endpoints disponíveis
- [ ] Especificar regras de access control
- [ ] Adicionar performance benchmarks
- [ ] Listar possíveis error responses
- [ ] Documentar filtros disponíveis
- [ ] Atualizar INDEX.md
- [ ] Atualizar Reports/README.md
- [ ] Incluir data de última atualização e versão

---

**Mantenha esta documentação sempre atualizada!** 🎯
